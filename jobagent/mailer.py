"""Steps 5 and 6 - Send approved emails (Gmail SMTP) and detect replies (Gmail IMAP).

Uses a Gmail App Password (Google Account -> Security -> 2-Step Verification ->
App passwords). No Google Cloud project or OAuth setup is needed.
"""
from __future__ import annotations

import email
import imaplib
import logging
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from pathlib import Path

log = logging.getLogger(__name__)


def build_message(sender: str, sender_name: str, to: str, subject: str, body: str,
                  attachment: Path | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{sender_name} <{sender}>" if sender_name else sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg.set_content(body)
    if attachment and attachment.exists():
        msg.add_attachment(attachment.read_bytes(), maintype="application", subtype="pdf",
                           filename=attachment.name)
    return msg


def send_email(msg: EmailMessage, user: str, app_password: str,
               host: str = "smtp.gmail.com", port: int = 465, use_ssl: bool = True) -> str:
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=60) as s:
            s.login(user, app_password)
            s.send_message(msg)
    else:  # used by tests with a local SMTP server
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.send_message(msg)
    return msg["Message-ID"]


FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "yahoo.co.in", "rediffmail.com", "icloud.com"}


def reply_key(address: str) -> str:
    """Company domain for work addresses (so a reply from a colleague counts), full address for free mail."""
    address = address.lower()
    domain = address.split("@")[-1]
    return address if domain in FREE_MAIL else domain


def find_replies(user: str, app_password: str, addresses: list[str], since_days: int = 30,
                 host: str = "imap.gmail.com") -> dict[str, str]:
    """Return {reply_key: received_date} for inbox mail matching any of `addresses`."""
    wanted = {reply_key(a) for a in addresses if a}
    if not wanted:
        return {}
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
    found: dict[str, str] = {}
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, app_password)
        imap.select("INBOX", readonly=True)
        _, ids = imap.search(None, f'(SINCE "{since}")')
        for num in ids[0].split()[-500:]:
            _, data = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM DATE)])")
            hdr = email.message_from_bytes(data[0][1])
            addr = parseaddr(hdr.get("From", ""))[1].lower()
            if addr and addr != user.lower() and reply_key(addr) in wanted:
                found.setdefault(reply_key(addr), hdr.get("Date", ""))
    return found
