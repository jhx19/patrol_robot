#!/usr/bin/env python3
"""Send email alert when laser machine is unattended."""

import smtplib
from email.mime.text import MIMEText
from .credentials import load_credentials

SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT   = 587

# ── Username → email mapping ──────────────────────────────────────────────────
# Add makerspace members here. Keys must match the username string returned
# by the Glowforge API exactly (case-sensitive).
# If a username is NOT in this dict, the alert falls back to the default
# recipient configured in credentials.yaml (gmail.recipient_email).
USERNAME_EMAIL_MAP: dict[str, str] = {
    'Young Pyung L': 'youngpyung@uw.edu',
    'Doris':     '995593485@qq.com',
    # 'Alice Smith': 'alice@uw.edu',
    # 'Bob Jones':   'bob@uw.edu',
}
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_recipient(username: str, fallback: str) -> str:
    """Return the email for *username*, or *fallback* if not in the map."""
    email = USERNAME_EMAIL_MAP.get(username)
    if email:
        print(f'[Alert] Recipient resolved from map: {username} → {email}')
    else:
        print(f'[Alert] "{username}" not in USERNAME_EMAIL_MAP — '
              f'falling back to default recipient: {fallback}')
        email = fallback
    return email


def send_alert(machine_info: dict):
    creds             = load_credentials()
    gmail             = creds['gmail']
    SENDER_EMAIL      = gmail['sender_email']
    SENDER_PASSWORD   = gmail['sender_password']
    DEFAULT_RECIPIENT = gmail['recipient_email']

    username        = machine_info.get('username', 'Unknown')
    RECIPIENT_EMAIL = _resolve_recipient(username, DEFAULT_RECIPIENT)

    mins = int(machine_info.get('time_remaining', 0)) // 60
    secs = int(machine_info.get('time_remaining', 0)) % 60

    subject = (f"⚠️ SAFETY ALERT: {username} left laser "
               f"{machine_info.get('name')} unattended!")
    body = f"""
Safety Alert from Patrol Robot

Machine   : {machine_info.get('name')}
User      : {username}
Job       : {machine_info.get('job_title')}
Time Left : {mins:02d}:{secs:02d}

The patrol robot detected that the laser engraver is operating
WITHOUT a human supervisor present.

Please return to the makerspace immediately.
"""
    msg            = MIMEText(body)
    msg['Subject'] = subject
    msg['From']    = SENDER_EMAIL
    msg['To']      = RECIPIENT_EMAIL

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print(f'[Alert] Email sent to {RECIPIENT_EMAIL}.')
    except Exception as e:
        print(f'[Alert] Failed to send email: {e}')