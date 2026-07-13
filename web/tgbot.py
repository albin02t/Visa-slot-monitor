"""Telegram alert bot: users link their chat via a /start deep-link, and the
monitor delivers alerts through the official Bot API (free, no ToS issues)."""
import asyncio
import logging
import os
import secrets
import time

import httpx
from sqlalchemy import select

from web.db import Session, User

log = logging.getLogger("tgbot")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
BOT_CONFIGURED = bool(BOT_TOKEN)
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

_bot_username: str | None = None
# one-time link codes: code -> {user_id, expires}
_link_codes: dict[str, dict] = {}
LINK_TTL = 15 * 60


async def get_bot_username() -> str | None:
    global _bot_username
    if not BOT_CONFIGURED:
        return None
    if _bot_username:
        return _bot_username
    try:
        async with httpx.AsyncClient(timeout=10) as hc:
            r = await hc.get(f"{API}/getMe")
            _bot_username = r.json()["result"]["username"]
    except Exception as e:
        log.warning("getMe failed: %s", e)
    return _bot_username


def new_link_code(user_id: int) -> str:
    # Drop expired + any previous code for this user
    now = time.time()
    for c in [c for c, v in _link_codes.items() if v["expires"] < now or v["user_id"] == user_id]:
        _link_codes.pop(c, None)
    code = secrets.token_urlsafe(12)
    _link_codes[code] = {"user_id": user_id, "expires": now + LINK_TTL}
    return code


async def send_message(chat_id: str, text: str) -> bool:
    if not BOT_CONFIGURED:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as hc:
            r = await hc.post(f"{API}/sendMessage", json={
                "chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
            return r.status_code == 200
    except Exception as e:
        log.error("sendMessage failed: %s", e)
        return False


async def poll_updates() -> None:
    """Long-poll getUpdates to catch '/start <code>' link messages."""
    if not BOT_CONFIGURED:
        log.info("TELEGRAM_BOT_TOKEN not set — alert bot disabled")
        return
    offset = 0
    log.info("Alert bot polling started")
    while True:
        try:
            async with httpx.AsyncClient(timeout=40) as hc:
                r = await hc.get(f"{API}/getUpdates",
                                 params={"offset": offset, "timeout": 30,
                                         "allowed_updates": '["message"]'})
                updates = r.json().get("result", [])
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                if not chat_id or not text.startswith("/start"):
                    continue
                parts = text.split(maxsplit=1)
                code = parts[1].strip() if len(parts) > 1 else ""
                entry = _link_codes.pop(code, None)
                if entry and entry["expires"] > time.time():
                    async with Session() as s:
                        user = await s.get(User, entry["user_id"])
                        if user:
                            user.alert_chat_id = chat_id
                            await s.commit()
                    await send_message(chat_id,
                        "✅ *Connected!* You'll receive visa slot alerts here the moment slots open.")
                    log.info("Linked chat %s to user %s", chat_id, entry["user_id"])
                else:
                    await send_message(chat_id,
                        "This link has expired. Open the app and click *Connect Telegram alerts* again.")
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("poll_updates error: %s", e)
            await asyncio.sleep(5)


async def linked_chat_id(user_id: int) -> str | None:
    async with Session() as s:
        user = await s.get(User, user_id)
        return user.alert_chat_id if user else None
