"""Google OAuth login + signed JWT cookie sessions."""
import os
import time

import jwt
from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from sqlalchemy import select

from web.db import Session, User

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALG = "HS256"
COOKIE_NAME = "vsm_session"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"

# Local-dev escape hatch so the app is runnable before Google credentials exist.
ALLOW_DEV_LOGIN = os.environ.get("ALLOW_DEV_LOGIN", "0") == "1"

# Restrict sign-in to an approved set of emails (private deployment). When empty,
# sign-in is open to any Google account — set this in production.
ALLOWED_EMAILS = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}


def is_email_allowed(email: str) -> bool:
    if not ALLOWED_EMAILS:
        return True  # open mode (no allowlist configured)
    return (email or "").lower() in ALLOWED_EMAILS

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_CONFIGURED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

oauth = OAuth()
if GOOGLE_CONFIGURED:
    oauth.register(
        name="google",
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        client_kwargs={"scope": "openid email profile"},
    )


def make_token(user_id: int) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "iat": now, "exp": now + COOKIE_MAX_AGE}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> int | None:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return int(data["sub"])
    except Exception:
        return None


def set_session_cookie(response, user_id: int) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_token(user_id),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


async def upsert_user(google_sub: str, email: str, name: str | None, picture: str | None) -> User:
    async with Session() as s:
        user = (await s.execute(select(User).where(User.google_sub == google_sub))).scalar_one_or_none()
        is_new = user is None
        if user is None:
            user = User(google_sub=google_sub, email=email, name=name, picture=picture, config={})
            s.add(user)
            # Permanent all-time signup record (survives account deletion).
            from web.db import SignupLog
            seen = (await s.execute(select(SignupLog).where(
                SignupLog.google_sub == google_sub))).scalar_one_or_none()
            if seen is None:
                s.add(SignupLog(google_sub=google_sub, email=email))
        else:
            user.email = email
            user.name = name
            user.picture = picture
        await s.commit()
        await s.refresh(user)
        user._is_new = is_new  # transient flag for welcome email
        return user


async def get_user_from_request(request: Request) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    uid = decode_token(token)
    if uid is None:
        return None
    async with Session() as s:
        return await s.get(User, uid)
