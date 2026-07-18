"""One-off: send the 'setup rebuilt' update to stalled users.

Targets users who signed up >12h ago and haven't completed setup (no linked
alert bot or missing required config). Marks them nudged so the automatic
24h nudge never double-emails them. Safe to re-run: pass --dry-run to preview.

Run inside the app container:
    python scripts/send_setup_update.py [--dry-run]
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from web import mailer
from web.db import Session, User

REQUIRED = {"TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_CHANNELS", "CVS_API_KEY"}


def complete(u: User) -> bool:
    cfg = u.config or {}
    return bool(u.alert_chat_id) and REQUIRED.issubset({k for k, v in cfg.items() if v})


async def main(dry: bool) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
    async with Session() as s:
        users = (await s.execute(select(User).order_by(User.id))).scalars().all()
        targets = [u for u in users if not complete(u) and u.created_at < cutoff]
        print(f"{len(targets)} stalled users (of {len(users)} total):")
        for u in targets:
            if dry:
                print(f"  would send -> {u.email}")
                continue
            if mailer.send_setup_update(u.email, u.name):
                u.nudged_at = datetime.now(timezone.utc)
                print(f"  sent -> {u.email}")
            else:
                print(f"  FAILED -> {u.email}")
        if not dry:
            await s.commit()


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
