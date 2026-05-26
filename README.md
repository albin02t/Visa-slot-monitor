# Visa Slot Monitor

Monitors US F1 visa interview slot availability through two sources simultaneously:

1. **checkvisaslots.com API** — polls every 5 minutes for confirmed open slots (primary source)
2. **Telegram channels** — uses a local LLM to detect slot announcements in real-time (secondary source)

Sends a WhatsApp alert via Twilio when slots are detected, with different messages depending on the source.

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running
- A Telegram account
- A Twilio account with WhatsApp sandbox enabled
- A checkvisaslots.com API key (get one at [checkvisaslots.com](https://checkvisaslots.com))

---

## Setup

### 1. Clone the repo

```bash
git clone <repo-url>
cd Visa-slot-monitor
```

### 2. Create a virtual environment and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Pull the LLM model

```bash
ollama pull gemma3:1b
```

### 4. Get your Telegram API credentials

1. Go to [my.telegram.org/apps](https://my.telegram.org/apps) and log in
2. Create a new application (URL can be left blank or set to `https://localhost`)
3. Copy the **App api_id** and **App api_hash**

### 5. Set up Twilio WhatsApp

1. Sign up at [twilio.com](https://twilio.com)
2. Go to **Messaging → Try it out → Send a WhatsApp message**
3. Follow the instructions to join the sandbox (send the join code from your WhatsApp to the sandbox number)
4. Copy your **Account SID** and **Auth Token** from the Twilio dashboard

### 6. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
# Telegram
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_CHANNELS=@yourchannel

# Twilio WhatsApp
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
ALERT_TO=whatsapp:+1XXXXXXXXXX

# checkvisaslots.com
CVS_API_KEY=your_api_key
CVS_LOCATIONS=CHENNAI,NEW DELHI    # leave empty to monitor all locations
CVS_POLL_INTERVAL=300              # seconds between API polls (default: 5 min)
CVS_DURATION_DAYS=120              # only alert for slots within this many days
```

**Key variables:**
- `TELEGRAM_CHANNELS` — Telegram channel username (e.g. `@F1_Visa_Slots_Group`). Comma-separate for multiple.
- `TWILIO_WHATSAPP_FROM` — the Twilio sandbox number (keep the `whatsapp:` prefix)
- `ALERT_TO` — your WhatsApp number with country code (e.g. `whatsapp:+917994741413`)
- `CVS_LOCATIONS` — comma-separated list of consulate names to filter (e.g. `CHENNAI,NEW DELHI`). Leave blank to alert for all locations.

---

## Run

```bash
source venv/bin/activate
python3 monitor.py
```

On the first run, Telethon will ask for your Telegram phone number and a one-time OTP sent to your Telegram app. After that, a session file is saved and you won't need to log in again.

You'll see logs like:

```
2026-05-27 10:00:00 [INFO] Monitoring channel: F1_Visa_Slots_Group (id=...)
2026-05-27 10:00:00 [INFO] checkvisaslots.com poller started — interval 300s, locations: CHENNAI, NEW DELHI
2026-05-27 10:00:01 [INFO] Listening for messages. Press Ctrl+C to stop.
2026-05-27 10:00:01 [INFO] checkvisaslots.com — API calls remaining: 42
2026-05-27 10:00:01 [INFO] checkvisaslots.com — no open slots found
2026-05-27 10:05:23 [INFO] [F1_Visa_Slots_Group] New message: slots are open for Chennai!
2026-05-27 10:05:24 [INFO] LLM verdict: YES
2026-05-27 10:05:24 [INFO] WhatsApp alert sent — source: telegram
```

---

## How alerting works

### checkvisaslots.com (primary)

Polls the checkvisaslots.com API every 5 minutes. When slots with a count > 0 are found for your configured locations and within your duration window, you get a WhatsApp message:

```
🟢 Visa Slot Confirmed — checkvisaslots.com

Slots available on checkvisaslots.com:
• CHENNAI: 3 slot(s) from 15 Jun 2026

Book now: https://checkvisaslots.com
```

### Telegram (secondary)

Each incoming message is evaluated by `gemma3:1b` running locally via Ollama, using the last 5 messages as context. The model replies `YES` only for a **present-tense, assertive confirmation** that slots are open now — not speculation, questions, or general discussion. When triggered:

```
💬 Possible Slot Alert — Telegram

slots just dropped for Chennai, book now!!
```

---

## Notes

- Keep the script running at all times to catch alerts in real-time
- The session file (`visa_monitor_session.session`) stores your Telegram login — do not share or commit it
- Do not commit your `.env` file
