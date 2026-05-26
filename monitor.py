import os
import re
import asyncio
import logging
from datetime import datetime

from dotenv import load_dotenv
from telethon import TelegramClient, events
from twilio.rest import Client as TwilioClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TELEGRAM_API_ID = int(os.environ["TELEGRAM_API_ID"])
TELEGRAM_API_HASH = os.environ["TELEGRAM_API_HASH"]
CHANNELS = [c.strip() for c in os.environ["TELEGRAM_CHANNELS"].split(",")]

TWILIO_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM = os.environ["TWILIO_WHATSAPP_FROM"]
ALERT_TO = os.environ["ALERT_TO"]

# Keywords that strongly suggest slots have opened
POSITIVE_PATTERNS = [
    r"\bslots?\s+(are\s+)?(?:open|available|released|showing|live)\b",
    r"\bappointment\s+(?:slots?\s+)?(?:are\s+)?(?:open|available|released|showing|live)\b",
    r"\bdates?\s+(?:are\s+)?(?:available|open|showing)\b",
    r"\bvisa\s+(?:interview\s+)?(?:slots?\s+)?(?:are\s+)?(?:open|available|released)\b",
    r"\bbook(?:ing)?\s+(?:now|open|available)\b",
    r"\bslots?\s+(?:just\s+)?(?:dropped|opened|appeared)\b",
    r"\bnew\s+(?:slots?|dates?|appointments?)\s+(?:available|open|added)\b",
    r"\bF-?1\s+(?:slots?|dates?|appointments?)\s+(?:available|open)\b",
    r"\bconsulate\s+(?:slots?|dates?|appointments?)\s+(?:available|open)\b",
    r"\bhurry\b.{0,60}(?:slot|appointment|date)",
    r"(?:slot|appointment|date).{0,60}\bhurry\b",
]

# Keywords that indicate the opposite (to reduce false positives)
NEGATIVE_PATTERNS = [
    r"\bno\s+slots?\b",
    r"\bslots?\s+(?:not|aren['’]t)\s+(?:available|open)\b",
    r"\b(?:fully\s+)?booked\b",
    r"\bunavailable\b",
]

_positive_re = [re.compile(p, re.IGNORECASE) for p in POSITIVE_PATTERNS]
_negative_re = [re.compile(p, re.IGNORECASE) for p in NEGATIVE_PATTERNS]

twilio = TwilioClient(TWILIO_SID, TWILIO_TOKEN)


def is_slot_alert(text: str) -> bool:
    if not text:
        return False
    if any(r.search(text) for r in _negative_re):
        return False
    return any(r.search(text) for r in _positive_re)


def send_whatsapp_alert(channel: str, message_text: str) -> None:
    preview = message_text[:300].replace("\n", " ")
    body = (
        f"🚨 *F1 Visa Slot Alert*\n\n"
        f"Channel: {channel}\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"Message:\n{preview}"
    )
    twilio.messages.create(from_=TWILIO_FROM, to=ALERT_TO, body=body)
    log.info("WhatsApp alert sent for channel: %s", channel)


async def main() -> None:
    client = TelegramClient("visa_monitor_session", TELEGRAM_API_ID, TELEGRAM_API_HASH)

    await client.start()
    log.info("Telegram client started")

    # Resolve channels once at startup to validate they exist
    channel_entities = []
    for ch in CHANNELS:
        try:
            entity = await client.get_entity(ch)
            channel_entities.append(entity)
            log.info("Monitoring channel: %s (id=%s)", ch, entity.id)
        except Exception as e:
            log.error("Could not resolve channel '%s': %s", ch, e)

    if not channel_entities:
        log.error("No valid channels found. Exiting.")
        return

    @client.on(events.NewMessage(chats=channel_entities))
    async def handler(event):
        text = event.message.text or ""
        chat = await event.get_chat()
        channel_name = getattr(chat, "username", None) or getattr(chat, "title", str(chat.id))

        log.info("[%s] New message: %s", channel_name, text[:120].replace("\n", " "))

        if is_slot_alert(text):
            log.info("SLOT ALERT detected in %s — sending WhatsApp notification", channel_name)
            try:
                send_whatsapp_alert(channel_name, text)
            except Exception as e:
                log.error("Failed to send WhatsApp alert: %s", e)

    log.info("Listening for messages. Press Ctrl+C to stop.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
