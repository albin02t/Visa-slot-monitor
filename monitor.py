import os
import asyncio
import logging
import urllib.request
import json
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

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:1b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")

SYSTEM_PROMPT = (
    "You monitor Telegram messages for US F1 student visa interview slot availability. "
    "Reply YES only if the message is assertively and clearly announcing that visa interview slots are open RIGHT NOW and can be booked immediately. "
    "The message must be a present-tense, confident declaration — someone saying slots are available now, just opened, or actively showing on the booking system. "
    "Reply NO for everything else, including: "
    "speculation or predictions about when slots might open, "
    "past experiences or historical discussion, "
    "questions asking if slots are open, "
    "rumours or unverified claims, "
    "complaints about no slots, "
    "general tips or advice, "
    "news about slots expected in the future, "
    "or any message that is not a direct present-tense confirmation that slots are available to book right now. "
    "When in doubt, reply NO. Reply with exactly one word: YES or NO."
)

twilio = TwilioClient(TWILIO_SID, TWILIO_TOKEN)


def is_slot_alert(text: str) -> bool:
    if not text or len(text.strip()) < 5:
        return False

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text[:1000]},
        ],
        "stream": False,
    }).encode()

    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        answer = result["message"]["content"].strip().upper()
        log.info("LLM verdict: %s", answer)
        return answer.startswith("YES")
    except Exception as e:
        log.error("Ollama call failed: %s — skipping message", e)
        return False


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
