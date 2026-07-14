import os
import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import httpx

from dotenv import load_dotenv
from telethon import TelegramClient, events
from slot_checker import poll_checkvisaslots

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
load_dotenv(DATA_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TELEGRAM_API_ID = int(os.environ["TELEGRAM_API_ID"])
TELEGRAM_API_HASH = os.environ["TELEGRAM_API_HASH"]
CHANNELS = [c.strip() for c in os.environ["TELEGRAM_CHANNELS"].split(",")]

# Alerts are delivered via the deployment's Telegram bot to the user's chat.
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALERT_CHAT_ID = os.environ["ALERT_CHAT_ID"]

# Cloud LLM for message classification — any OpenAI-compatible chat endpoint
# (Groq by default; also works with OpenRouter, Together, OpenAI, etc.)
LLM_API_URL = os.environ.get("LLM_API_URL", "https://api.groq.com/openai/v1/chat/completions")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.1-8b-instant")

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

recent_messages: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))


def is_slot_alert(channel: str, new_text: str) -> bool:
    if not new_text or len(new_text.strip()) < 5:
        log.info("LLM verdict: SKIPPED (message too short)")
        return False

    if not LLM_API_KEY:
        log.error("LLM_API_KEY not set — cannot classify messages")
        log.info("LLM verdict: ERROR")
        return False

    recent_messages[channel].append(new_text)
    history = list(recent_messages[channel])

    context = "\n\n".join(f"[Message {i+1}]: {msg[:500]}" for i, msg in enumerate(history))

    try:
        # httpx, not urllib — Cloudflare (fronting Groq) 403-blocks the
        # default Python-urllib User-Agent.
        r = httpx.post(
            LLM_API_URL,
            timeout=30,
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                "temperature": 0,
                "max_tokens": 3,
            },
        )
        r.raise_for_status()
        answer = r.json()["choices"][0]["message"]["content"].strip().upper()
        log.info("LLM verdict: %s", answer)
        return answer.startswith("YES")
    except Exception as e:
        log.error("LLM call failed: %s — skipping message", e)
        log.info("LLM verdict: ERROR")
        return False


def send_alert(message_text: str, source: str = "telegram", context_window: list[str] | None = None) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if source == "api":
        body = (
            f"🟢 *Visa Slot Confirmed — checkvisaslots.com*\n\n"
            f"Time: {now}\n\n"
            f"{message_text[:600]}\n\n"
            f"Book now: https://checkvisaslots.com"
        )
    else:
        if context_window:
            msgs = "\n\n".join(f"[{i+1}] {msg[:300]}" for i, msg in enumerate(context_window))
        else:
            msgs = message_text[:600]
        body = (
            f"💬 *Possible Slot Alert — Telegram*\n\n"
            f"Time: {now}\n\n"
            f"{msgs}"
        )
    r = httpx.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": ALERT_CHAT_ID, "text": body, "parse_mode": "Markdown"},
        timeout=15)
    r.raise_for_status()
    log.info("Alert sent — source: %s", source)


async def main() -> None:
    client = TelegramClient(str(DATA_DIR / "visa_monitor_session"), TELEGRAM_API_ID, TELEGRAM_API_HASH)

    # Connect WITHOUT prompting — the session must already be authorized via the
    # web Setup page (Telegram login). start() would prompt on stdin and crash.
    await client.connect()
    if not await client.is_user_authorized():
        log.error("Telegram session is not authorized. Complete the Telegram login "
                  "in the Setup page first, then start again. Exiting.")
        await client.disconnect()
        return
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
            log.info("SLOT ALERT detected in %s — sending alert", channel_name)
            try:
                window = list(recent_messages[channel_name])
                send_alert(text, source="telegram", context_window=window)
            except Exception as e:
                log.error("Failed to send alert: %s", e)

    async def on_api_slots_found(summary: str) -> None:
        try:
            send_alert(summary, source="api")
        except Exception as e:
            log.error("Failed to send API slot alert: %s", e)

    log.info("Listening for messages. Press Ctrl+C to stop.")
    await asyncio.gather(
        client.run_until_disconnected(),
        poll_checkvisaslots(on_api_slots_found),
    )


if __name__ == "__main__":
    asyncio.run(main())
