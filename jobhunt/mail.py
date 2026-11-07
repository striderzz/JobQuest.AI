"""Sends cold email through Gmail with an app password. The app never reads your inbox.

Sending happens one message at a time, only after you press Send, within a daily limit (outbox.py sends a reviewed batch).
"""
import re
import smtplib
import threading
from datetime import date, datetime, timezone
from email.message import EmailMessage

from . import db, settings

SMTP_HOST = "smtp.gmail.com"
ADDR_VAR, PASS_VAR = "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"
EMAIL_RX = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
DAILY_CAP = 50                  # well under Gmail's own limits, to keep cold email from looking like a bulk sender
_log_lock = threading.Lock()


class MailError(Exception):
    pass


def _clean(pw):
    """Google shows app passwords in groups of 4; copying them often brings non-breaking spaces along."""
    return re.sub(r"[\s ​-‍﻿]", "", pw or "")


def account():
    env = settings.read_env()
    return env.get(ADDR_VAR, ""), env.get(PASS_VAR, "")


def connected():
    a, p = account()
    return bool(a and p)


def _login(addr, pw):
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=30) as s:
            s.login(addr, pw)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("Gmail refused the login. Gmail needs an app password here, not your normal password: create one "
                        "at myaccount.google.com/apppasswords, and check the address is the same Google account.") from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Couldn't reach Gmail: {e}") from e


def connect(addr, pw):
    addr, pw = addr.strip(), pw.strip()
    if not EMAIL_RX.match(addr):
        raise MailError("That doesn't look like an email address.")
    pw = _clean(pw)
    if not pw:
        raise MailError("Paste your app password first.")
    _login(addr, pw)
    settings.write_env({ADDR_VAR: addr, PASS_VAR: pw})


def disconnect():
    settings.write_env({ADDR_VAR: None, PASS_VAR: None})


# ---------------------------------------------------------------- sending

def build_message(sender, to, subject, body, attachments=()):
    """The MIME message; each attachment is a file path (your resume PDF/DOCX)."""
    import mimetypes
    from pathlib import Path
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject.strip()[:200]
    msg.set_content(body.strip()[:20000])
    for a in attachments or ():
        p = Path(a)
        if not p.is_file() or p.stat().st_size > 10_000_000:
            raise MailError(f"Can't attach {p.name}.")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=main, subtype=sub, filename=p.name)
    return msg


def sent_today():
    today = date.today().isoformat()
    return sum(1 for m in db.get_setting("mail_sent") or []
               if datetime.fromisoformat(m["at"]).astimezone().date().isoformat() == today)


def send(to, subject, body, job_id=None, attachments=()):
    addr, pw = account()
    if not (addr and pw):
        raise MailError("Connect Gmail in Settings first.")
    if sent_today() >= DAILY_CAP:
        raise MailError(f"You've reached today's limit of {DAILY_CAP} emails. This keeps your Gmail account safe; try tomorrow.")
    to = to.strip()
    if not EMAIL_RX.match(to):
        raise MailError("Enter one valid recipient address.")
    if not subject.strip() or not body.strip():
        raise MailError("Add a subject and a message.")
    msg = build_message(addr, to, subject, body, attachments)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=30) as s:
            s.login(addr, pw)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("Gmail refused the login. Reconnect with a fresh app password in Settings.") from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Gmail didn't send it: {e}") from e
    if job_id and db.get_job(job_id):
        j = db.get_job(job_id)
        note = f"[{date.today().isoformat()}] Sent email to {to}: {subject.strip()[:120]}"
        fields = {"notes": ((j["notes"] or "") + "\n" + note).strip(), "last_update": date.today().isoformat()}
        if not (j["contact"] or "").strip():
            fields["contact"] = to
        db.edit_entry(job_id, fields)
    with _log_lock:
        log = db.get_setting("mail_sent") or []
        db.set_setting("mail_sent", (log + [{"to": to, "subject": subject.strip()[:200],
                                             "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                             "job_id": job_id}])[-200:])
    return True
