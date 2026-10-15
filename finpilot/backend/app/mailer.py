"""Transactional email (password reset) over plain SMTP.

SMTP rather than a vendor SDK, so any provider works: Amazon SES, Resend,
Brevo, Zoho, Google Workspace -- each gives SMTP host, port, user and password.

Without SMTP configured (local development) nothing is sent; the message is
written to the server log instead so the flow can still be tried. Production
must configure SMTP -- the log line contains the reset link.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger("finpilot.mail")


def send_email(to: str, subject: str, text: str) -> bool:
    """Send one plain-text email. Returns False when not sent."""
    s = get_settings()
    if not s.smtp_host:
        log.warning("SMTP not configured -- email NOT sent to %s. Subject: %s\n%s", to, subject, text)
        return False
    message = EmailMessage()
    message["From"] = s.smtp_from or s.smtp_user
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        log.error("could not send email to %s: %s", to, exc)
        return False
