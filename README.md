# Visa Slot Monitor

A multi-user web app that monitors US F1 visa interview slot availability and sends **instant Telegram alerts** the moment slots appear. Each user signs in with Google, completes a guided setup wizard, connects the alert bot with one tap, and their personal monitor runs 24/7.

| Source | Method |
|---|---|
| **checkvisaslots.com API** | Polls on a configurable interval for confirmed open slots |
| **Telegram channels** | Watches channels live; a cloud LLM filters real announcements from noise |

Alerts are delivered through the official **Telegram Bot API** — free, instant, no expiry, no third-party messaging accounts.

---

## Architecture

```
                         ┌──────────────────────────────┐
   Browser ── Google ──▶ │  FastAPI app (web/server.py) │
   sign-in               │  • Google OAuth + JWT cookie │
                         │  • per-user config (Postgres)│
                         │  • spawns per-user monitors  │
                         │  • Telegram alert bot        │──▶ user's Telegram chat
                         └───────────┬──────────────────┘
                                     │ per-user
                                     ▼
                        monitor.py (Telegram channels + CVS API)
                                     │
                                     ▼
                          Cloud LLM (Groq API)
```

- **Postgres** stores users (Google identity), per-user config, and event history.
- Each user's `monitor.py` runs as an isolated subprocess with its own data directory (`/data/users/<id>/`) holding their Telegram session.
- **One shared alert bot** (created via @BotFather) delivers alerts to every user's own chat.

---

## Deploy with Docker

### 1. Configure

```bash
cp .env.example .env
```

Set at minimum:

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | Database password |
| `JWT_SECRET` | Long random string (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `PUBLIC_BASE_URL` | The URL users reach the app at |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth credentials |
| `TELEGRAM_BOT_TOKEN` | Alert bot token — message [@BotFather](https://t.me/BotFather) → `/newbot` → copy token |
| `LLM_API_KEY` | Free key from [console.groq.com](https://console.groq.com) (or any OpenAI-compatible provider via `LLM_API_URL`/`LLM_MODEL`) |
| `ALLOWED_EMAILS` | Optional: restrict sign-in to a comma-separated list (empty = open signup) |

### 2. Google OAuth

[Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) → OAuth 2.0 Client ID (Web application) → add redirect URI `<PUBLIC_BASE_URL>/api/auth/callback`. Publish the consent screen to Production.

### 3. Launch

Local / development:

```bash
docker compose up --build -d
```

Production (adds Caddy auto-HTTPS; needs `DOMAIN` + `COOKIE_SECURE=1` in `.env` and DNS pointing at the host):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

---

## User flow

1. **Sign in** with Google.
2. **Setup wizard**:
   - *Telegram* — API ID/hash from [my.telegram.org/apps](https://my.telegram.org/apps), channels to watch, then in-browser phone/OTP login (2FA supported).
   - *Alerts* — one tap opens the bot in Telegram; press **Start**; done.
   - *checkvisaslots.com* — access code, locations, poll interval.
3. **Save** — the monitor starts automatically once everything is linked (readiness checklist shows progress).
4. **Dashboard** — live alerts, poll history, channel activity, and streaming logs.

The monitor also auto-resumes for all users after a server restart.

---

## How alerting works

**checkvisaslots.com (primary):** polls every `CVS_POLL_INTERVAL` seconds; when open slots match your locations/window, you get:

> 🟢 **Visa Slot Confirmed — checkvisaslots.com**
> • CHENNAI: 3 slot(s) from 15 Jun 2026

**Telegram channels (secondary):** each incoming message (with the last 5 as context) goes to the LLM, which answers YES only for present-tense, assertive "slots are open now" confirmations — not speculation or questions.

Both alert types arrive as Telegram messages from the bot within seconds.

---

## File overview

| File | Purpose |
|---|---|
| `web/server.py` | FastAPI app: auth, per-user config, process orchestration, event APIs |
| `web/tgbot.py` | Telegram alert bot: link flow + message delivery |
| `web/db.py` | Postgres models + async engine |
| `web/auth.py` | Google OAuth + JWT cookie sessions |
| `web/mailer.py` | SMTP welcome email (optional) |
| `web/static/index.html` | Single-page UI |
| `monitor.py` | Per-user process: channel listener + CVS poller + alert sender |
| `slot_checker.py` | checkvisaslots.com polling logic |

---

## Notes

- Per-user secrets live in Postgres; `.env` holds only deployment config — never commit it.
- Back up the `pg_data` and `app_data` volumes.
- `GET /healthz` for uptime monitoring.
- Each active user runs a lightweight Telegram session; classification is offloaded to a cloud LLM, so even a small 2–4 GB VPS handles a community comfortably.
