#!/usr/bin/env python3
"""Send email alert when laser machine is unattended."""

import smtplib
from email.mime.text import MIMEText


# ── 修改这里 ─────────────────────────────────────
SENDER_EMAIL    = 'your_gmail@gmail.com'
SENDER_PASSWORD = 'your_app_password'   # Gmail App Password (16 chars)
RECIPIENT_EMAIL = 'sunyhg@uw.edu'
SMTP_SERVER     = 'smtp.gmail.com'
SMTP_PORT       = 587
# ─────────────────────────────────────────────────


def send_alert(machine_info: dict):
    mins = int(machine_info.get('time_remaining', 0)) // 60
    secs = int(machine_info.get('time_remaining', 0)) % 60

    subject = f"⚠️ SAFETY ALERT: Unattended laser engraver detected!"
    body = f"""
Safety Alert from Patrol Robot

Machine   : {machine_info.get('name')}
User      : {machine_info.get('username')}
Job       : {machine_info.get('job_title')}
Time Left : {mins:02d}:{secs:02d}

The patrol robot detected that the laser engraver is operating
WITHOUT a human supervisor present.

Please return to the makerspace immediately.
"""
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From']    = SENDER_EMAIL
    msg['To']      = RECIPIENT_EMAIL

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print('[Alert] Email sent successfully.')
    except Exception as e:
        print(f'[Alert] Failed to send email: {e}')