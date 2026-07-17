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


def send_email(to: str, subject: str, body: str, html: str | None = None) -> bool:
    """Send an email (plain text, with an optional HTML alternative).
    Returns True on success, False (logged) otherwise.
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
        if html:
            msg.add_alternative(html, subtype="html")

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


def send_access_email(to: str) -> bool:
    """Waitlist graduation email — dark radar-themed HTML with a plain-text
    fallback. Email-client-safe: tables + inline styles only."""
    app_url = os.environ.get("PUBLIC_BASE_URL", "https://slots.toulelabs.dev")
    text = (
        "Good news — a spot opened up and it's yours.\n\n"
        f"Sign in with Google and run the 5-minute setup: {app_url}\n\n"
        "Your radar will scan checkvisaslots.com and Telegram channels 24/7 "
        "and ping your Telegram the instant F1 visa slots open.\n\n"
        "— Toule Labs · Visa Slot Monitor"
    )
    html = f"""\
<div style="margin:0;padding:0;background-color:#07100b;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#07100b" style="background-color:#07100b;">
  <tr><td align="center" style="padding:40px 16px;">
    <table role="presentation" width="520" cellpadding="0" cellspacing="0" border="0" style="max-width:520px;width:100%;">
      <!-- header badge -->
      <tr><td align="center" style="padding-bottom:24px;">
        <span style="display:inline-block;padding:8px 18px;border:1px solid #1e4033;border-radius:999px;
                     font-family:Arial,Helvetica,sans-serif;font-size:12px;letter-spacing:3px;color:#4ade80;">
          &#128225;&nbsp; TOULE LABS &middot; TRANSMISSION
        </span>
      </td></tr>
      <!-- card -->
      <tr><td style="background-color:#0c1912;border:1px solid #1e4033;border-radius:20px;padding:44px 36px;" align="center">
        <p style="margin:0;font-family:'Courier New',monospace;font-size:13px;letter-spacing:4px;color:#4ade80;">
          &gt;&gt; ACCESS GRANTED &lt;&lt;
        </p>
        <h1 style="margin:18px 0 0;font-family:Arial,Helvetica,sans-serif;font-weight:800;font-size:34px;line-height:1.15;color:#f4fff8;">
          Your spot on the<br>
          <span style="color:#4ade80;">radar is ready</span>
        </h1>
        <p style="margin:20px 0 0;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#9db8aa;">
          You made it off the waitlist. Sign in, run the 5-minute setup, and your
          personal radar starts scanning checkvisaslots.com and Telegram channels
          around the clock &mdash; pinging your Telegram the instant F1 slots open.
        </p>
        <!-- CTA -->
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:32px auto 0;">
          <tr><td align="center" bgcolor="#4ade80" style="border-radius:14px;">
            <a href="{app_url}" style="display:inline-block;padding:15px 34px;font-family:Arial,Helvetica,sans-serif;
               font-size:16px;font-weight:bold;color:#052e16;text-decoration:none;">
              Activate your radar &nbsp;&rarr;
            </a>
          </td></tr>
        </table>
        <p style="margin:26px 0 0;font-family:'Courier New',monospace;font-size:12px;letter-spacing:2px;color:#3d5c4c;">
          FREE &middot; 5-MIN SETUP &middot; ALERTS ON TELEGRAM
        </p>
      </td></tr>
      <!-- footer -->
      <tr><td align="center" style="padding-top:26px;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#3d5c4c;">
        Toule Labs &middot; tiny lab, ridiculously useful tools<br>
        <a href="https://toulelabs.dev" style="color:#4ade80;text-decoration:none;">toulelabs.dev</a>
      </td></tr>
    </table>
  </td></tr>
</table>
</div>"""
    return send_email(to, "📡 Access granted — your slot radar is ready", text, html=html)


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
