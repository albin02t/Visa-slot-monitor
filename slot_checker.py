import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from dateutil import parser as dateparser

log = logging.getLogger(__name__)

ALERTED_SLOTS_FILE = Path(os.environ.get("DATA_DIR", Path(__file__).parent)) / ".alerted_slots.json"


def _load_alerted_slots() -> set[str]:
    if ALERTED_SLOTS_FILE.exists():
        try:
            return set(json.loads(ALERTED_SLOTS_FILE.read_text()))
        except Exception:
            pass
    return set()


def _save_alerted_slots(alerted: set[str]) -> None:
    try:
        ALERTED_SLOTS_FILE.write_text(json.dumps(list(alerted)))
    except Exception as e:
        log.warning("Could not save alerted slots: %s", e)

CVS_API_URL = "https://app.checkvisaslots.com/slots/v3"


def _get_config():
    api_key = os.environ.get("CVS_API_KEY")
    if not api_key:
        raise ValueError("CVS_API_KEY is not set in environment")
    poll_interval = int(os.environ.get("CVS_POLL_INTERVAL", "780"))  # default to 15 minutes
    locations = [
        loc.strip().upper()
        for loc in os.environ.get("CVS_LOCATIONS", "").split(",")
        if loc.strip()
    ]
    duration_days = int(os.environ.get("CVS_DURATION_DAYS", "120"))
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "extversion": "4.7.0.2",
        "origin": "chrome-extension://beepaenfejnphdgnkmccjcfiieihhogl",
        "user-agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "x-api-key": api_key,
    }
    return poll_interval, locations, duration_days, headers


def _within_duration(date_str: str, duration_days: int) -> bool:
    try:
        slot_date = dateparser.parse(date_str).date()
        return slot_date <= (datetime.now().date() + timedelta(days=duration_days))
    except Exception:
        return True


async def poll_checkvisaslots(on_slots_found) -> None:
    """
    Polls checkvisaslots.com every CVS_POLL_INTERVAL seconds.
    Calls on_slots_found(summary_text) when new open slots are detected.
    """
    poll_interval, locations, duration_days, headers = _get_config()
    log.info(
        "checkvisaslots.com poller started — interval %ds, locations: %s",
        poll_interval,
        locations or "all",
    )
    alerted_slots: set[str] = _load_alerted_slots()
    log.info("Loaded %d previously alerted slots from disk", len(alerted_slots))

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                resp = await client.get(CVS_API_URL, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                if "userActivity" in data:
                    remaining = data["userActivity"].get("remaining", "?")
                    log.info("checkvisaslots.com — API calls remaining: %s", remaining)

                open_slots = []
                for slot in data.get("slotDetails", []):
                    if slot.get("slots", 0) <= 0:
                        continue
                    location = slot.get("visa_location", "").upper()
                    start_date = slot.get("start_date", "")
                    if locations and location not in locations:
                        continue
                    if start_date and not _within_duration(start_date, duration_days):
                        continue
                    open_slots.append(slot)

                if open_slots:
                    new_slots = [
                        s for s in open_slots
                        if f"{s['visa_location']}|{s.get('start_date', '')}" not in alerted_slots
                    ]
                    if new_slots:
                        lines = [
                            f"• {s['visa_location']}: {s['slots']} slot(s) from {s.get('start_date', 'N/A')}"
                            for s in new_slots
                        ]
                        summary = "Slots available on checkvisaslots.com:\n" + "\n".join(lines)
                        log.info(summary)
                        await on_slots_found(summary)
                        for s in new_slots:
                            alerted_slots.add(f"{s['visa_location']}|{s.get('start_date', '')}")
                        _save_alerted_slots(alerted_slots)
                    else:
                        log.info("checkvisaslots.com — slots still open but already alerted")
                else:
                    log.info("checkvisaslots.com — no open slots found")
                    alerted_slots.clear()
                    _save_alerted_slots(alerted_slots)  # reset on disk too

            except Exception as e:
                log.error("checkvisaslots.com poll failed: %s", e)

            await asyncio.sleep(poll_interval)
