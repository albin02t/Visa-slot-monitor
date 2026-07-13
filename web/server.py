"""Multi-tenant FastAPI server for Visa Slot Monitor.

Each user authenticates with Google, stores their own config in Postgres, and
runs their own monitor process in an isolated per-user data dir. Alerts are
delivered through a shared Telegram bot (see web/tgbot.py).
"""
import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from authlib.integrations.starlette_client import OAuthError
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from web import auth, mailer, tgbot
from web.db import Alert, CvsCheck, Session, TgEvent, User, init_db

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
USER_DATA_ROOT = DATA_DIR / "users"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
# Cap total accounts — each active user runs a monitor subprocess, so open
# signup on a small host needs a ceiling.
MAX_USERS = int(os.environ.get("MAX_USERS", "25"))

# Config keys a user is allowed to set (everything else is ignored).
# Note: LLM_* / TELEGRAM_BOT_TOKEN are NOT user-configurable — they are set
# globally by the deployment and inherited by each subprocess.
CONFIG_KEYS = {
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_CHANNELS",
    "CVS_API_KEY", "CVS_LOCATIONS", "CVS_POLL_INTERVAL", "CVS_DURATION_DAYS",
}

# Minimum config required before the monitor process is useful.
REQUIRED_MONITOR_KEYS = {
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_CHANNELS",
    "CVS_API_KEY",
}


# ---------------------------------------------------------------------------
# Per-user filesystem
# ---------------------------------------------------------------------------
def user_dir(uid: int) -> Path:
    d = USER_DATA_ROOT / str(uid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_user_env(uid: int, config: dict) -> None:
    lines = [f"{k}={v}" for k, v in config.items() if k in CONFIG_KEYS and v != ""]
    (user_dir(uid) / ".env").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Per-user process + log state
# ---------------------------------------------------------------------------
processes: dict[int, dict] = defaultdict(lambda: {"monitor": None})
log_history: dict[int, list] = defaultdict(list)
log_subs: dict[int, list[asyncio.Queue]] = defaultdict(list)
_slot_accum: dict[int, bool] = defaultdict(bool)
MAX_LOG = 1000


def _push_log(uid: int, event: dict) -> None:
    hist = log_history[uid]
    hist.append(event)
    if len(hist) > MAX_LOG:
        hist.pop(0)
    for q in log_subs[uid][:]:
        try:
            q.put_nowait(event)
        except Exception:
            pass


async def _parse(uid: int, line: str) -> None:
    now = datetime.utcnow()
    async with Session() as s:
        if "Alert sent —" in line:
            s.add(Alert(user_id=uid, ts=now,
                        source="api" if "source: api" in line else "telegram", body=line))
        elif "checkvisaslots.com — no open slots" in line:
            _slot_accum[uid] = False
            s.add(CvsCheck(user_id=uid, ts=now, status="no_slots", detail="No open slots"))
        elif "checkvisaslots.com — slots still open" in line:
            _slot_accum[uid] = False
            s.add(CvsCheck(user_id=uid, ts=now, status="already_alerted",
                           detail="Slots open — already alerted"))
        elif "checkvisaslots.com — API calls remaining:" in line:
            m = re.search(r"remaining: (\S+)", line)
            s.add(CvsCheck(user_id=uid, ts=now, status="checked", detail=line,
                           api_remaining=m.group(1) if m else None))
        elif "Slots available on checkvisaslots.com" in line:
            _slot_accum[uid] = True
            s.add(CvsCheck(user_id=uid, ts=now, status="slots_found", detail=line))
        elif _slot_accum[uid] and line.strip().startswith("•"):
            row = (await s.execute(
                select(CvsCheck).where(CvsCheck.user_id == uid, CvsCheck.status == "slots_found")
                .order_by(CvsCheck.id.desc()).limit(1))).scalar_one_or_none()
            if row:
                row.detail = (row.detail or "") + "\n" + line.strip()
        elif "checkvisaslots.com poll failed" in line:
            _slot_accum[uid] = False
            s.add(CvsCheck(user_id=uid, ts=now, status="error", detail=line))
        elif re.search(r"\[[^\]]*\] New message:", line):
            _slot_accum[uid] = False
            # [^\]]* keeps the match to the bracket right before "New message:",
            # so "[INFO] [channel] New message:" captures just the channel.
            m = re.search(r"\[([^\]]*)\] New message: (.*)", line)
            if m:
                s.add(TgEvent(user_id=uid, ts=now, channel=m.group(1), preview=m.group(2)[:300]))
        elif "LLM verdict:" in line:
            m = re.search(r"LLM verdict: (\w+)", line)
            if m:
                row = (await s.execute(
                    select(TgEvent).where(TgEvent.user_id == uid, TgEvent.verdict == "pending")
                    .order_by(TgEvent.id.desc()).limit(1))).scalar_one_or_none()
                if row:
                    row.verdict = m.group(1)
        else:
            _slot_accum[uid] = False
        await s.commit()


async def _tail(uid: int, name: str, proc) -> None:
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, proc.stdout.readline)
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip()
        if not text:
            continue
        _push_log(uid, {"ts": datetime.utcnow().isoformat(), "source": name, "text": text})
        try:
            await _parse(uid, text)
        except Exception:
            pass
    _push_log(uid, {"ts": datetime.utcnow().isoformat(), "source": name, "text": f"[{name} exited]"})


def _spawn(uid: int, name: str, cmd: list[str], config: dict, alert_chat_id: str = "") -> None:
    import subprocess
    env = {**os.environ, **{k: v for k, v in config.items() if k in CONFIG_KEYS},
           "DATA_DIR": str(user_dir(uid)),
           "ALERT_CHAT_ID": alert_chat_id}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=str(BASE_DIR), env=env, bufsize=1)
    processes[uid][name] = proc
    asyncio.create_task(_tail(uid, name, proc))


def _tg_session_path(uid: int) -> str:
    return str(user_dir(uid) / "visa_monitor_session")


async def _tg_authorized(uid: int, cfg: dict) -> bool:
    api_id, api_hash = cfg.get("TELEGRAM_API_ID"), cfg.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        return False
    if not Path(_tg_session_path(uid) + ".session").exists():
        return False
    client = TelegramClient(_tg_session_path(uid), int(api_id), api_hash)
    try:
        await asyncio.wait_for(client.connect(), timeout=10)
        ok = await client.is_user_authorized()
        await client.disconnect()
        return ok
    except Exception:
        return False


async def _ensure_running(uid: int, cfg: dict) -> None:
    """Idempotently start the user's monitor when prerequisites are met.
    The app manages this automatically — there are no manual controls."""
    cfg = cfg or {}
    write_user_env(uid, cfg)
    present = {k for k, v in cfg.items() if v}

    # Monitor needs full config, an authorized Telegram session, and a linked
    # alert chat (otherwise alerts would silently go nowhere).
    if REQUIRED_MONITOR_KEYS.issubset(present) and not _running(uid, "monitor"):
        chat_id = await tgbot.linked_chat_id(uid)
        if chat_id and await _tg_authorized(uid, cfg):
            _spawn(uid, "monitor", [sys.executable, str(BASE_DIR / "monitor.py")], cfg,
                   alert_chat_id=chat_id)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
async def _resume_all() -> None:
    """Restart processes for every already-configured user so the service is
    self-healing after a reboot/redeploy — not dependent on a user opening the page."""
    await asyncio.sleep(2)
    try:
        async with Session() as s:
            users = (await s.execute(select(User))).scalars().all()
        for u in users:
            try:
                await _ensure_running(u.id, u.config or {})
            except Exception:
                pass
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    USER_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    await init_db()
    asyncio.create_task(_resume_all())
    asyncio.create_task(tgbot.poll_updates())
    yield
    # Graceful shutdown — terminate all user subprocesses.
    for procs in processes.values():
        for p in procs.values():
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass


app = FastAPI(title="Visa Slot Monitor", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
app.add_middleware(SessionMiddleware, secret_key=auth.JWT_SECRET, same_site="lax")


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
async def current_user(request: Request) -> User:
    user = await auth.get_user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@app.get("/api/auth/config")
async def auth_config():
    return {"google": auth.GOOGLE_CONFIGURED, "dev_login": auth.ALLOW_DEV_LOGIN}


@app.get("/api/auth/login")
async def auth_login(request: Request):
    if not auth.GOOGLE_CONFIGURED:
        raise HTTPException(status_code=400, detail="Google OAuth not configured")
    redirect_uri = PUBLIC_BASE_URL.rstrip("/") + "/api/auth/callback"
    return await auth.oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/api/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await auth.oauth.google.authorize_access_token(request)
    except OAuthError as e:
        return RedirectResponse(f"/?error={e.error}")
    info = token.get("userinfo") or {}
    if not info.get("sub"):
        return RedirectResponse("/?error=no_userinfo")
    if not auth.is_email_allowed(info.get("email", "")):
        return RedirectResponse("/?error=not_allowed")
    # Enforce the account cap for brand-new signups (existing users always get in).
    async with Session() as s:
        existing = (await s.execute(
            select(User).where(User.google_sub == info["sub"]))).scalar_one_or_none()
        if existing is None:
            from sqlalchemy import func as sqlfunc
            count = (await s.execute(select(sqlfunc.count(User.id)))).scalar_one()
            if count >= MAX_USERS:
                return RedirectResponse("/?error=full")
    user = await auth.upsert_user(
        google_sub=info["sub"], email=info.get("email", ""),
        name=info.get("name"), picture=info.get("picture"))
    if getattr(user, "_is_new", False) and user.email:
        try:
            mailer.send_welcome(user.email, user.name)
        except Exception:
            pass
    resp = RedirectResponse("/")
    auth.set_session_cookie(resp, user.id)
    return resp


@app.post("/api/auth/dev-login")
async def auth_dev_login():
    """Local-dev only: create/login a fake account without Google."""
    if not auth.ALLOW_DEV_LOGIN:
        raise HTTPException(status_code=403, detail="Dev login disabled")
    user = await auth.upsert_user(
        google_sub="dev-local", email="dev@localhost", name="Dev User", picture=None)
    resp = JSONResponse({"ok": True})
    auth.set_session_cookie(resp, user.id)
    return resp


@app.post("/api/auth/logout")
async def auth_logout():
    resp = JSONResponse({"ok": True})
    auth.clear_session_cookie(resp)
    return resp


@app.get("/api/auth/me")
async def auth_me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "picture": user.picture}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
class ConfigPayload(BaseModel):
    data: dict[str, str]


@app.get("/api/config")
async def get_config(user: User = Depends(current_user)):
    return user.config or {}


@app.post("/api/config")
async def save_config(payload: ConfigPayload, user: User = Depends(current_user)):
    clean = {k: v for k, v in payload.data.items() if k in CONFIG_KEYS}
    async with Session() as s:
        db_user = await s.get(User, user.id)
        db_user.config = clean
        await s.commit()
    write_user_env(user.id, clean)
    await _ensure_running(user.id, clean)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Process control
# ---------------------------------------------------------------------------
def _running(uid: int, name: str) -> bool:
    p = processes[uid][name]
    return p is not None and p.poll() is None


@app.post("/api/processes/ensure")
async def ensure_processes(user: User = Depends(current_user)):
    """Start the user's processes if prerequisites are met. Safe to call repeatedly;
    used on app load to resume monitors after a server restart."""
    await _ensure_running(user.id, user.config or {})
    return {"ok": True}


@app.get("/api/status")
async def status(user: User = Depends(current_user)):
    return {"monitor": _running(user.id, "monitor"),
            "alerts_linked": bool(user.alert_chat_id),
            "bot_configured": tgbot.BOT_CONFIGURED,
            "llm": bool(os.environ.get("LLM_API_KEY"))}


# ---------------------------------------------------------------------------
# Alert bot linking
# ---------------------------------------------------------------------------
@app.post("/api/alerts/link")
async def alerts_link(user: User = Depends(current_user)):
    """Return a t.me deep-link the user opens to connect the alert bot."""
    username = await tgbot.get_bot_username()
    if not username:
        raise HTTPException(status_code=503, detail="Alert bot is not configured on this server")
    code = tgbot.new_link_code(user.id)
    return {"url": f"https://t.me/{username}?start={code}"}


@app.get("/api/alerts/status")
async def alerts_status(user: User = Depends(current_user)):
    chat_id = await tgbot.linked_chat_id(user.id)
    return {"linked": bool(chat_id)}


@app.post("/api/alerts/test")
async def alerts_test(user: User = Depends(current_user)):
    chat_id = await tgbot.linked_chat_id(user.id)
    if not chat_id:
        raise HTTPException(status_code=400, detail="Alerts not linked yet")
    ok = await tgbot.send_message(chat_id,
        "👋 *Test alert* — your Visa Slot Monitor alerts are working!")
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to send test message")
    return {"ok": True}


@app.get("/api/logs/stream")
async def logs_stream(user: User = Depends(current_user)):
    uid = user.id
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    log_subs[uid].append(q)

    async def gen():
        for ev in log_history[uid][-200:]:
            yield f"data: {json.dumps(ev)}\n\n"
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if q in log_subs[uid]:
                log_subs[uid].remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Event feeds
# ---------------------------------------------------------------------------
@app.get("/api/alerts")
async def get_alerts(limit: int = 25, user: User = Depends(current_user)):
    async with Session() as s:
        rows = (await s.execute(select(Alert).where(Alert.user_id == user.id)
                .order_by(Alert.id.desc()).limit(limit))).scalars().all()
    return [{"ts": r.ts.isoformat(), "source": r.source, "body": r.body} for r in rows]


@app.get("/api/cvs-checks")
async def get_cvs(limit: int = 40, user: User = Depends(current_user)):
    async with Session() as s:
        rows = (await s.execute(select(CvsCheck).where(CvsCheck.user_id == user.id)
                .order_by(CvsCheck.id.desc()).limit(limit))).scalars().all()
    return [{"ts": r.ts.isoformat(), "status": r.status, "detail": r.detail,
             "api_remaining": r.api_remaining} for r in rows]


@app.get("/api/tg-events")
async def get_tg(limit: int = 40, user: User = Depends(current_user)):
    async with Session() as s:
        rows = (await s.execute(select(TgEvent).where(TgEvent.user_id == user.id)
                .order_by(TgEvent.id.desc()).limit(limit))).scalars().all()
    return [{"ts": r.ts.isoformat(), "channel": r.channel, "preview": r.preview,
             "verdict": r.verdict} for r in rows]


# ---------------------------------------------------------------------------
# Telegram login (per user)
# ---------------------------------------------------------------------------
_tg_pending: dict[int, dict] = {}  # uid -> {client, phone, phone_code_hash}


class TgPhonePayload(BaseModel):
    phone: str

class TgCodePayload(BaseModel):
    phone: str
    code: str

class Tg2FAPayload(BaseModel):
    password: str


@app.get("/api/telegram/status")
async def tg_status(user: User = Depends(current_user)):
    cfg = user.config or {}
    api_id, api_hash = cfg.get("TELEGRAM_API_ID"), cfg.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        return {"authorized": False, "reason": "credentials_not_set"}
    if not Path(_tg_session_path(user.id) + ".session").exists():
        return {"authorized": False, "reason": "no_session"}
    # If the monitor is running it already holds an authorized session — opening
    # the same session file again would conflict and give a false negative.
    if _running(user.id, "monitor"):
        return {"authorized": True, "reason": None}
    client = TelegramClient(_tg_session_path(user.id), int(api_id), api_hash)
    try:
        await asyncio.wait_for(client.connect(), timeout=10)
        ok = await client.is_user_authorized()
        await client.disconnect()
        return {"authorized": ok, "reason": None if ok else "session_invalid"}
    except Exception as e:
        return {"authorized": False, "reason": str(e)}


@app.post("/api/telegram/send-code")
async def tg_send_code(payload: TgPhonePayload, user: User = Depends(current_user)):
    cfg = user.config or {}
    api_id = int(cfg.get("TELEGRAM_API_ID") or 0)
    api_hash = cfg.get("TELEGRAM_API_HASH") or ""
    if not api_id or not api_hash:
        raise HTTPException(status_code=400, detail="Set TELEGRAM_API_ID and TELEGRAM_API_HASH first")
    if user.id in _tg_pending:
        try:
            await _tg_pending[user.id]["client"].disconnect()
        except Exception:
            pass
    client = TelegramClient(_tg_session_path(user.id), api_id, api_hash)
    await client.connect()
    result = await client.send_code_request(payload.phone)
    _tg_pending[user.id] = {"client": client, "phone": payload.phone,
                            "phone_code_hash": result.phone_code_hash}
    return {"sent": True}


@app.post("/api/telegram/verify-code")
async def tg_verify(payload: TgCodePayload, user: User = Depends(current_user)):
    pending = _tg_pending.get(user.id)
    if not pending:
        raise HTTPException(status_code=400, detail="No pending auth — send the code first")
    try:
        await pending["client"].sign_in(phone=payload.phone, code=payload.code,
                                        phone_code_hash=pending["phone_code_hash"])
        await pending["client"].disconnect()
        del _tg_pending[user.id]
        await _ensure_running(user.id, user.config or {})
        return {"authorized": True}
    except SessionPasswordNeededError:
        return {"needs_2fa": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/telegram/2fa")
async def tg_2fa(payload: Tg2FAPayload, user: User = Depends(current_user)):
    pending = _tg_pending.get(user.id)
    if not pending:
        raise HTTPException(status_code=400, detail="No pending auth")
    try:
        await pending["client"].check_password(payload.password)
        await pending["client"].disconnect()
        del _tg_pending[user.id]
        await _ensure_running(user.id, user.config or {})
        return {"authorized": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Serve frontend — must be last
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
