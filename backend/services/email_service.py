import logging
import os
import smtplib
from email.mime.text import MIMEText


logger = logging.getLogger(__name__)


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes", "on"}


def send_email_otp(to_email: str, otp: str):
    email_user = os.getenv("EMAIL_USER")
    email_pass = os.getenv("EMAIL_PASS")
    smtp_host = os.getenv("EMAIL_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("EMAIL_PORT", "587"))

    if not email_user or not email_pass:
        if _truthy(os.getenv("EMAIL_ALLOW_DEV_OTP", "false")):
            logger.warning("Development OTP enabled for %s: %s", to_email, otp)
            return True
        logger.error("OTP email is not configured; set EMAIL_USER and EMAIL_PASS")
        return False

    try:
        msg = MIMEText(
            f"Your PPU verification code is: {otp}\n\n"
            "This code expires in 10 minutes."
        )
        msg["Subject"] = "PPU Verification Code"
        msg["From"] = email_user
        msg["To"] = to_email

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("OTP email failed for %s", to_email)
        return False
