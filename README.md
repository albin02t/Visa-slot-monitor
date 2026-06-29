# Visa Slot Monitor

A multi-user web app that monitors US F1 visa interview slot availability and sends WhatsApp alerts the moment slots appear. Each user signs in with Google, configures their own Telegram / Twilio / checkvisaslots.com credentials through a guided setup wizard, and runs their own monitor.

| Source | Method |
|---|---|
| **checkvisaslots.com API** | Polls on a configurable interval for confirmed open slots |
| **Telegram channels** | Watches channels live; a local LLM (Ollama) filters real announcements from noise |

Alerts are sent via Twilio's WhatsApp sandbox. An optional **Forwarder** relays each alert to additional numbers and sends a daily keepalive to maintain the Twilio session.

---

## Architecture

```
                         ┌──────────────────────────────┐
   Browser ── Google ──▶ │  FastAPI app (web/server.py) │
   sign-in               │  • Google OAuth + JWT cookie │
                         │  • per-user config (Postgres)│
                         │  • spawns per-user processes │
                         └───────┬───────────────┬──────┘
                                 │               │
                    per-user     ▼               ▼   per-user
                  monitor.py (Telegram+CVS)   forwarder.js (WhatsApp Web)
                                 │               │
                                 ▼               ▼
                            Ollama LLM      Chromium session
```

- **Postgres** stores users (Google identity) and each user's config + event history.
- Each user's `monitor.py` and `forwarder.js` run as isolated subprocesses with their own data directory (`/data/users/<id>/`) for Telegram and WhatsApp Web sessions.

> **Scaling note:** every active user needs their own Telegram session and a full Chromium instance for WhatsApp Web. A single host handles a modest number of concurrent users. To scale further, move the per-user processes to dedicated worker containers / a job queue — the data model and APIs are already per-user, so that's the natural next step.

---

## Deploy with Docker (recommended)

Everything — app, Postgres, and Ollama — runs from one compose file.

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | Database password |
| `JWT_SECRET` | Long random string for signing session cookies |
| `PUBLIC_BASE_URL` | The URL users reach the app at (e.g. `https://slots.example.com`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth credentials |
| `COOKIE_SECURE` | `1` when served over HTTPS |
| `SMTP_*` | Optional — for the welcome email |

### 2. Set up Google OAuth

1. Go to [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
2. Create an **OAuth 2.0 Client ID** (type: Web application)
3. Add an **Authorized redirect URI**: `<PUBLIC_BASE_URL>/api/auth/callback`
   (e.g. `http://localhost:8000/api/auth/callback`)
4. Copy the Client ID and Secret into `.env`

### 3. Launch

```bash
docker compose up --build -d
```

### 4. Pull the LLM (one-time)

```bash
docker compose exec ollama ollama pull gemma3:1b
```

Open **`PUBLIC_BASE_URL`** in your browser and sign in with Google.

---

## Using the app

1. **Sign in** with Google.
2. **Setup wizard** — walk through Telegram, Twilio, Forwarder, checkvisaslots.com, and Ollama. Save.
3. **Telegram login** — in the Telegram setup step, enter your phone, receive an OTP in your Telegram app, and verify (handles 2FA). No terminal needed.
4. **Start processes** from the sidebar (Monitor / Forwarder).
5. **Dashboard** shows live alerts, checkvisaslots.com poll history, and Telegram activity. **Live Logs** streams real-time output.

For the Forwarder, scan the WhatsApp Web QR code shown in **Live Logs** on first run.

---

## Local development (without Docker)

You'll need a local Postgres and Ollama running.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
npm install

export DATABASE_URL="postgresql+asyncpg://visa:visa@localhost:5432/visa"
export ALLOW_DEV_LOGIN=1          # enables a "Dev login" button, no Google needed
export OLLAMA_URL="http://localhost:11434/api/chat"

uvicorn web.server:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 and click **Dev login**.

---

## How alerting works

### checkvisaslots.com (primary)

Polls the API every `CVS_POLL_INTERVAL` seconds. Triggers when slots with count > 0 are found for your locations within the duration window:

```
🟢 Visa Slot Confirmed — checkvisaslots.com

Slots available on checkvisaslots.com:
• CHENNAI: 3 slot(s) from 15 Jun 2026

Book now: https://checkvisaslots.com
```

### Telegram (secondary)

Each incoming message is passed to `gemma3:1b` via Ollama along with the last 5 messages as context. The model replies `YES` only for a present-tense, assertive confirmation that slots are available right now — not speculation, questions, or general discussion.

### WhatsApp Forwarder

`forwarder.js` connects to WhatsApp Web using your personal account, watches for messages from the Twilio sandbox, and forwards each one to the numbers in `FORWARD_TO`. It also sends `join lovely-rest` to the Twilio sandbox every day at 08:00 IST to keep the session active.

---

## File overview

| File | Purpose |
|---|---|
| `web/server.py` | FastAPI app: auth, per-user config, process orchestration, event APIs |
| `web/db.py` | Postgres models (User, Alert, CvsCheck, TgEvent) + async engine |
| `web/auth.py` | Google OAuth + JWT cookie sessions |
| `web/mailer.py` | SMTP transactional email |
| `web/static/index.html` | Single-page UI (landing, dashboard, setup wizard, logs) |
| `monitor.py` | Per-user process: Telegram listener + CVS poller + WhatsApp alerts |
| `slot_checker.py` | checkvisaslots.com polling logic |
| `forwarder.js` | WhatsApp Web relay + daily Twilio keepalive |
| `Dockerfile` / `docker-compose.yml` | Containerized deployment |

---

## Notes

- Per-user secrets live in Postgres; `.env` holds only deployment-level config.
- The `app_data` Docker volume persists per-user Telegram and WhatsApp Web sessions — back it up.
- Behind a reverse proxy, forward `Host` / `X-Forwarded-Proto` and set `COOKIE_SECURE=1` and the correct `PUBLIC_BASE_URL`.
