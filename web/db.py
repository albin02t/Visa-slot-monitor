"""Postgres-backed multi-tenant data layer (SQLAlchemy 2.0 async)."""
import os
from datetime import datetime
from urllib.parse import quote_plus

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _database_url() -> str:
    """Prefer an explicit DATABASE_URL; otherwise build one from POSTGRES_*
    parts. Building from parts URL-encodes the password so special characters
    (@, :, /, etc.) don't corrupt host parsing."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    user = os.environ.get("POSTGRES_USER", "visa")
    pw = quote_plus(os.environ.get("POSTGRES_PASSWORD", "visa"))
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    name = os.environ.get("POSTGRES_DB", "visa")
    return f"postgresql+asyncpg://{user}:{pw}@{host}:{port}/{name}"


DATABASE_URL = _database_url()

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-user .env-style configuration (TELEGRAM_*, CVS_*, etc.)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # Telegram chat id the alert bot delivers to (set via the bot /start link flow)
    alert_chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # When the one-time "finish your setup" nudge email was sent (NULL = never)
    nudged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str | None] = mapped_column(String(32))
    body: Mapped[str | None] = mapped_column(Text)


class CvsCheck(Base):
    __tablename__ = "cvs_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str | None] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(Text)
    api_remaining: Mapped[str | None] = mapped_column(String(32))


class TgEvent(Base):
    __tablename__ = "tg_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    channel: Mapped[str | None] = mapped_column(String(255))
    preview: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")


class Waitlist(Base):
    __tablename__ = "waitlist"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # When the access-granted invitation email was sent (NULL = still waiting).
    # Rows are kept after inviting so the full lifecycle stays auditable:
    # waiting -> invited -> signed up (joined against users by email).
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def init_db(retries: int = 30, delay: float = 2.0) -> None:
    """Create tables, waiting for Postgres to become reachable on startup."""
    import asyncio
    import logging

    log = logging.getLogger("db")
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                # Lightweight migration for pre-existing installs (create_all
                # doesn't alter existing tables).
                from sqlalchemy import text
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS alert_chat_id VARCHAR(32)"))
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS nudged_at TIMESTAMPTZ"))
                await conn.execute(text(
                    "ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS invited_at TIMESTAMPTZ"))
            log.info("Database ready (attempt %d)", attempt)
            return
        except Exception as e:  # noqa: BLE001 — DNS/connection not ready yet
            last_err = e
            log.warning("Waiting for database (attempt %d/%d): %s", attempt, retries, e)
            await asyncio.sleep(delay)
    raise RuntimeError(f"Database not reachable after {retries} attempts") from last_err
