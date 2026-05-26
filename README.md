# Visa Slot Monitor

Monitors Telegram channels for US F1 visa interview slot availability. Uses keyword and regex pattern matching to detect slot-related messages and sends a WhatsApp alert via Twilio when slots open up.

## Prerequisites

- Python 3.10+
- A Telegram account
- A Twilio account with WhatsApp sandbox enabled

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
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_CHANNELS=@yourchannel

TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
ALERT_TO=whatsapp:+1XXXXXXXXXX
```

- `TELEGRAM_CHANNELS` — username of the Telegram channel to monitor (e.g. `@F1_Visa_Slots_Group`). Comma-separate multiple channels.
- `TWILIO_WHATSAPP_FROM` — the Twilio sandbox number (keep the `whatsapp:` prefix)
- `ALERT_TO` — your WhatsApp number with country code (e.g. `whatsapp:+917994741413`)

---

## Run

```bash
source venv/bin/activate
python3 monitor.py
```

On the first run, Telethon will ask for your Telegram phone number and a one-time OTP sent to your Telegram app. After that, a session file is saved and you won't need to log in again.

You'll see logs like:

```
2026-05-24 10:00:00 [INFO] Monitoring channel: F1_Visa_Slots_Group (id=...)
2026-05-24 10:00:01 [INFO] Listening for messages. Press Ctrl+C to stop.
2026-05-24 10:05:23 [INFO] [F1_Visa_Slots_Group] New message: slots are open for Chennai consulate!
2026-05-24 10:05:24 [INFO] LLM verdict: YES
2026-05-24 10:05:24 [INFO] WhatsApp alert sent for channel: F1_Visa_Slots_Group
```

---

## Notes

- Keep the script running at all times to catch alerts in real-time
- The session file (`visa_monitor_session.session`) stores your Telegram login — do not share it
- Do not commit your `.env` file
