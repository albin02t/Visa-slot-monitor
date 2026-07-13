"""Best-effort transactional email over SMTP (Gmail/custom)."""
import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger("mailer")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

SMTP_CONFIGURED = bool(SMTP_HOST and SMTP_FROM)


def send_email(to: str, subject: str, body: str) -> bool:
    """Send an email. Returns True on success, False (logged) otherwise.
    Safe to call when SMTP is unconfigured — it just no-ops."""
    if not SMTP_CONFIGURED:
        log.info("SMTP not configured — skipping email to %s", to)
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to
        msg.set_content(body)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            if SMTP_PORT in (587, 25):
                server.starttls()
                server.ehlo()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        log.info("Sent email to %s", to)
        return True
    except Exception as e:
        log.error("Failed to send email to %s: %s", to, e)
        return False


def send_welcome(to: str, name: str | None) -> None:
    send_email(
        to,
        "Welcome to Visa Slot Monitor",
        f"Hi {name or 'there'},\n\n"
        "Your Visa Slot Monitor account is ready. Sign in, complete the setup wizard "
        "(Telegram, checkvisaslots.com, alerts), and your monitor will begin "
        "sending you Telegram alerts the moment visa interview slots open.\n\n"
        "— Visa Slot Monitor",
    )
