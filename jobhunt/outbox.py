"""Sending a reviewed batch of cold emails, slowly, after your one click.

Gmail watches for bursts, so messages go one at a time with a gap and a daily cap. The queue lives in memory on purpose:
if the app closes, nothing carries on or resumes by itself. Contacts already sent stay marked, so a new batch skips them.
"""
import threading

from . import db, mail

GAP_SECONDS = 45

_lock = threading.Lock()
_stop = threading.Event()
_state = {"running": False, "total": 0, "sent": 0, "failed": 0, "current": "", "error": "", "stopped": False}


class OutboxError(Exception):
    pass


def snapshot():
    with _lock:
        return dict(_state) | {"gap_seconds": GAP_SECONDS, "daily_cap": mail.DAILY_CAP, "sent_today": mail.sent_today()}


def _set(**kw):
    with _lock:
        _state.update(kw)


def ready(ids):
    """The contacts in `ids` that can really go, in order. Raises if the batch can't start at all."""
    rows = [db.contact(i) for i in ids]
    rows = [c for c in rows if c and c["email"] and not c["sent_at"] and c["draft_subject"].strip() and c["draft_body"].strip()]
    if not rows:
        raise OutboxError("None of those have an email address and a finished draft.")
    left = mail.DAILY_CAP - mail.sent_today()
    if len(rows) > left:
        raise OutboxError(f"That's {len(rows)} emails, but only {max(left, 0)} are left of today's limit of {mail.DAILY_CAP}. "
                          "Untick some, or send the rest tomorrow.")
    return rows


def start(ids, deliver, gap=None):
    """Send on a background thread. `deliver(contact)` sends one message and files it in the tracker."""
    rows = ready(ids)
    if not mail.connected():
        raise OutboxError("Connect Gmail in Settings first.")
    with _lock:
        if _state["running"]:
            raise OutboxError("A batch is already sending.")
        _state.update(running=True, total=len(rows), sent=0, failed=0, current="", error="", stopped=False)
    _stop.clear()
    wait = GAP_SECONDS if gap is None else gap

    def work():
        try:
            for n, c in enumerate(rows):
                if _stop.is_set():
                    break
                _set(current=c["email"])
                db.update_contact(c["id"], status="sending", error="")
                try:
                    deliver(c)
                except mail.MailError as e:
                    db.update_contact(c["id"], status="failed", error=str(e)[:300])
                    with _lock:
                        _state["failed"] += 1
                        _state["error"] = str(e)[:300]
                    if "refused the login" in str(e) or "limit" in str(e):
                        break                                # every later message would fail the same way
                else:
                    db.update_contact(c["id"], status="sent", error="")
                    with _lock:
                        _state["sent"] += 1
                if n < len(rows) - 1 and _stop.wait(wait):
                    break
        finally:
            _set(running=False, current="", stopped=_stop.is_set())

    threading.Thread(target=work, daemon=True, name="outbox").start()
    return snapshot()


def stop():
    _stop.set()
    return snapshot()
