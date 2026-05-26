import os
import asyncio
import logging
import urllib.request
import json
from collections import defaultdict, deque
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
    "You monitor a Telegram channel for US F1 student visa interview slot availability. "
    "You will be given the last few messages from the channel as context, with the most recent message last. "
    "Based on this conversation, decide if there is a clear, confident, present-tense confirmation that visa interview slots are open RIGHT NOW and available to book. "
    "Reply YES only when the conversation makes it evident that slots are actively open and bookable at this moment — "
    "for example, someone saying they can see dates on the booking system, slots just dropped, or urging others to book now. "
    "Reply NO if the messages are: discussing when slots might open in the future, sharing past experiences, "
    "asking questions, expressing uncertainty, spreading rumours, complaining about no slots, or giving general advice. "
    "The context of earlier messages matters — a single ambiguous message in a thread of 'no slots' discussion should be NO. "
    "When in doubt, reply NO. Reply with exactly one word: YES or NO."
)

twilio = TwilioClient(TWILIO_SID, TWILIO_TOKEN)

recent_messages: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))


def is_slot_alert(channel: str, new_text: str) -> bool:
    if not new_text or len(new_text.strip()) < 5:
        return False

    recent_messages[channel].append(new_text)
    history = list(recent_messages[channel])

    context = "\n\n".join(f"[Message {i+1}]: {msg[:500]}" for i, msg in enumerate(history))

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
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

        if is_slot_alert(channel_name, text):
            log.info("SLOT ALERT detected in %s — sending WhatsApp notification", channel_name)
            try:
                send_whatsapp_alert(channel_name, text)
            except Exception as e:
                log.error("Failed to send WhatsApp alert: %s", e)

    log.info("Listening for messages. Press Ctrl+C to stop.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
