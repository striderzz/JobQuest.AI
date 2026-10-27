"""Gmail over IMAP/SMTP with an app password.

Reading is read-only (BODY.PEEK, readonly mailbox) and only proposes tracker updates; nothing changes until you
click Apply. Sending happens one message at a time, only when you press Send.
"""
import email
import imaplib
import re
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime

from . import db, settings

IMAP_HOST, SMTP_HOST = "imap.gmail.com", "smtp.gmail.com"
ADDR_VAR, PASS_VAR = "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"
EMAIL_RX = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
SEARCH = ('newer_than:{days}d (application OR applying OR interview OR assessment OR offer OR unfortunately OR '
          'candidate OR candidacy OR recruiter OR hiring OR "next steps" OR hackerrank OR codility OR shortlisted)')

# (status, phrases) - checked in this order, so a rejection that mentions "interview" still counts as a rejection
RULES = [
    ("offer", ("offer letter", "pleased to offer", "offer of employment", "extend an offer", "extend you an offer")),
    ("rejected", ("unfortunately", "not moving forward", "not be moving forward", "regret to inform", "other candidates",
                  "not be proceeding", "not to proceed", "position has been filled", "decided not to", "will not be progressing",
                  "not selected", "no longer under consideration", "pursue other candidates")),
    ("interviewing", ("interview invitation", "invite you to interview", "schedule an interview", "schedule your interview",
                      "interview schedule", "next round", "technical interview", "onsite interview", "panel interview",
                      "interview with", "calendly.com", "your availability for")),
    ("oa", ("online assessment", "coding assessment", "coding challenge", "hackerrank", "codility", "codesignal",
            "hackerearth", "mettl", "take-home", "take home assignment", "online test", "assessment link", "aptitude test")),
    ("screening", ("phone screen", "screening call", "recruiter call", "introductory call", "quick call", "initial call",
                   "shortlisted", "short-listed", "would like to connect", "discuss your application")),
    ("applied", ("thank you for applying", "thanks for applying", "application received", "received your application",
                 "application has been received", "application was submitted", "application submitted",
                 "your application for")),
]
STAGE = {"saved": 0, "applied": 1, "screening": 2, "oa": 3, "interviewing": 4, "offer": 5}
ATS_DOMAINS = ("greenhouse", "lever.co", "ashbyhq", "smartrecruiters", "myworkday", "workday", "icims", "taleo",
               "successfactors", "jobvite", "naukri", "linkedin", "indeed", "instahyre", "wellfound", "hirist")


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


def _imap(addr, pw):
    try:
        m = imaplib.IMAP4_SSL(IMAP_HOST, timeout=30)
        m.login(addr, _clean(pw))
        return m
    except imaplib.IMAP4.error as e:
        why = e.args[0].decode(errors="replace") if e.args and isinstance(e.args[0], bytes) else str(e)
        raise MailError("Gmail refused the login (" + why.strip()[:120] + "). Use a 16-letter app password from "
                        "myaccount.google.com/apppasswords - your normal Gmail password won't work - and check the "
                        "address is the same Google account.") from e
    except OSError as e:
        raise MailError(f"Couldn't reach Gmail: {e}") from e


def connect(addr, pw):
    addr, pw = addr.strip(), pw.strip()
    if not EMAIL_RX.match(addr):
        raise MailError("That doesn't look like an email address.")
    pw = _clean(pw)
    if not re.fullmatch(r"[A-Za-z]{16}", pw):
        raise MailError(f"That's {len(pw)} characters, so it isn't a Gmail app password. Google app passwords are "
                        "exactly 16 letters (shown as 4 groups of 4). Turn on 2-Step Verification, then create one at "
                        "myaccount.google.com/apppasswords and paste it here.")
    _imap(addr, pw).logout()
    settings.write_env({ADDR_VAR: addr, PASS_VAR: pw})


def disconnect():
    settings.write_env({ADDR_VAR: None, PASS_VAR: None})


# ---------------------------------------------------------------- reading

def _text(msg):
    """Plain text of a message (HTML stripped), first ~4000 characters."""
    parts = msg.walk() if msg.is_multipart() else [msg]
    plain = html = ""
    for p in parts:
        ctype = p.get_content_type()
        if p.get_content_maintype() == "multipart" or p.get_filename():
            continue
        try:
            body = p.get_payload(decode=True) or b""
            body = body.decode(p.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and not plain:
            plain = body
        elif ctype == "text/html" and not html:
            html = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
            html = re.sub(r"<[^>]+>", " ", html)
    text = plain or html
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:4000]


def classify(subject, text):
    hay = f"{subject} {text}".lower()
    for status, phrases in RULES:
        if any(p in hay for p in phrases):
            return status
    return None


def _company_keys(name):
    n = db.norm_company(name)
    return [k for k in {n, n.split(" ")[0] if n else ""} if len(k) >= 3]


def match_company(from_name, from_addr, subject, text, companies):
    """The tracked company this mail is about, or None. `companies` maps normalised name -> display name."""
    domain = from_addr.split("@")[-1].lower()
    head = f"{from_name} {subject}".lower()
    body = text[:1500].lower()
    best = None
    for key, display in companies.items():
        for k in _company_keys(display):
            rx = re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])")
            if (not any(a in domain for a in ATS_DOMAINS) and k.replace(" ", "") in domain.replace("-", "")) or rx.search(head):
                return display
            if best is None and rx.search(body):
                best = display
    return best


def fetch(days=14, limit=200):
    """Recent job-related mail as dicts. Read-only."""
    addr, pw = account()
    if not (addr and pw):
        raise MailError("Connect Gmail in Settings first.")
    m = _imap(addr, pw)
    try:
        m.select("INBOX", readonly=True)
        query = SEARCH.format(days=days)
        try:                                       # Gmail's own search syntax, quoted as one IMAP string
            typ, data = m.uid("SEARCH", "X-GM-RAW", '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"')
        except imaplib.IMAP4.error:
            typ, data = "NO", [b""]
        if typ != "OK":
            since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
            typ, data = m.uid("SEARCH", None, "SINCE", since)
        uids = (data[0] or b"").split()[-limit:]
        out = []
        for uid in reversed(uids):
            typ, got = m.uid("FETCH", uid, "(BODY.PEEK[]<0.40000>)")
            raw = next((x[1] for x in got if isinstance(x, tuple)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            name, frm = parseaddr(str(msg.get("From", "")))
            try:
                when = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat(timespec="seconds")
            except Exception:
                when = None
            subj = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
            out.append({"id": (msg.get("Message-ID") or f"uid-{uid.decode()}").strip(), "from_name": name, "from": frm,
                        "subject": subj[:200], "date": when, "text": _text(msg)})
        return out
    finally:
        try:
            m.logout()
        except Exception:
            pass


def proposals_from(messages, rows=None, seen=()):
    """Turn messages into proposed tracker changes (nothing is changed here)."""
    rows = rows if rows is not None else [j for j in db.list_jobs() if j.get("status") and j["status"] != "hidden"]
    by_company = {}
    for j in rows:
        by_company.setdefault(db.norm_company(j["company"]), []).append(j)
    companies = {k: v[0]["company"] for k, v in by_company.items() if k}
    out = []
    for msg in messages:
        if msg["id"] in seen:
            continue
        status = classify(msg["subject"], msg["text"])
        if not status:
            continue
        company = match_company(msg["from_name"], msg["from"], msg["subject"], msg["text"], companies)
        base = {"msg_id": msg["id"], "from": msg["from"], "from_name": msg["from_name"], "subject": msg["subject"],
                "date": msg["date"], "snippet": msg["text"][:280], "proposed": status}
        if not company:
            continue                                          # unknown company: not something we can file safely
        jobs = sorted(by_company[db.norm_company(company)], key=lambda j: j.get("applied_on") or j.get("status_at") or "", reverse=True)
        job = jobs[0]
        cur = job["status"]
        if status == "applied" or cur == status or cur in ("offer", "withdrawn"):
            continue                                          # confirmations of tracked jobs and no-ops are skipped
        if cur == "rejected" and status != "offer":
            continue
        if status in STAGE and STAGE.get(cur, 1) > STAGE[status]:
            continue                                          # never move an application backwards
        out.append(base | {"job_id": job["id"], "company": job["company"], "role": job["title"], "current": cur})
    return out


def sync(days=14):
    """Fetch, classify and store proposals (merged with ones not yet reviewed). Returns the pending list."""
    seen = set(db.get_setting("mail_seen") or [])
    pending = {p["msg_id"]: p for p in (db.get_setting("mail_proposals") or [])}
    for p in proposals_from(fetch(days), seen=seen | set(pending)):
        pending[p["msg_id"]] = p
    items = sorted(pending.values(), key=lambda p: p.get("date") or "", reverse=True)[:100]
    db.set_setting("mail_proposals", items)
    db.set_setting("mail_last_sync", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return items


def review(msg_ids, apply):
    """Apply or dismiss proposals. Applied ones change the tracker status and note the email."""
    items = db.get_setting("mail_proposals") or []
    seen = db.get_setting("mail_seen") or []
    done = 0
    keep = []
    for p in items:
        if p["msg_id"] not in msg_ids:
            keep.append(p)
            continue
        if apply and db.get_job(p["job_id"]):
            db.set_status(p["job_id"], p["proposed"], None)
            j = db.get_job(p["job_id"])
            note = f"[{(p.get('date') or '')[:10]}] Email: {p['subject']}"
            db.edit_entry(p["job_id"], {"notes": ((j["notes"] or "") + "\n" + note).strip(),
                                        "last_update": date.today().isoformat()})
            done += 1
        seen.append(p["msg_id"])
    db.set_setting("mail_proposals", keep)
    db.set_setting("mail_seen", seen[-2000:])
    return done


# ---------------------------------------------------------------- sending

def send(to, subject, body, job_id=None):
    addr, pw = account()
    if not (addr and pw):
        raise MailError("Connect Gmail in Settings first.")
    to = to.strip()
    if not EMAIL_RX.match(to):
        raise MailError("Enter one valid recipient address.")
    if not subject.strip() or not body.strip():
        raise MailError("Add a subject and a message.")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = addr, to, subject.strip()[:200]
    msg.set_content(body.strip()[:20000])
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
    log = db.get_setting("mail_sent") or []
    db.set_setting("mail_sent", (log + [{"to": to, "subject": subject.strip()[:200], "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                         "job_id": job_id}])[-200:])
    return True
