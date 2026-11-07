"""Off-PC backups and multi-PC sync through your own MongoDB Atlas cluster.

Each one is the normal backup zip (database and resumes, never API keys), AES-encrypted here with your passphrase before
it leaves the PC, so Atlas only ever stores unreadable data. The connection string and passphrase sit in the encrypted
vault. Lose the passphrase and the cloud copies can't be opened, by design.

Sync: each PC pulls the newest snapshot, merges it row by row (the most recent edit of an application wins, deletions
carry over, nothing else is dropped), then uploads again if anything changed here. Every PC keeps its own full database,
so the app works offline. A new, empty PC simply restores the newest snapshot.
"""
import hashlib
import json
import re
import sqlite3
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import backup, db, settings

URI_VAR, PASS_VAR = "MONGODB_URI", "MONGODB_BACKUP_PASSWORD"
DATABASE, BUCKET = "jobquest", "backups"
KEEP = 7                          # keeps well inside Atlas's free 512 MB tier
MIN_PASSPHRASE = 10
SYNC_HOURS = 6
JOB_FIELDS = ("status", "status_at", "notes", "applied_on", "job_type", "app_source", "last_update", "next_step", "contact", "company",
              "title", "location", "url", "salary_min", "salary_max", "salary_currency", "salary_src", "edited_at")
_syncing = threading.Lock()


class CloudError(Exception):
    pass


def _uri():
    return settings.read_env().get(URI_VAR, "")


def _passphrase():
    return settings.read_env().get(PASS_VAR, "")


def connected():
    return bool(_uri() and _passphrase())


def _host(uri):
    """The cluster name to show. The user name and password in the string are never displayed."""
    return urlsplit(uri).hostname or ""


def _clean_error(e, uri=""):
    """Driver errors can quote the connection string; make sure the password in it can't reach the screen or a log."""
    text = re.sub(r"mongodb(\+srv)?://\S+", "mongodb://…", str(e)).split(", Timeout:")[0]
    pw = urlsplit(uri).password if uri else None
    return (text.replace(pw, "…") if pw else text)[:200]


def _client(uri):
    from pymongo import MongoClient
    return MongoClient(uri, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000, appname="JobQuest.AI")


def _bucket(client):
    import gridfs
    return gridfs.GridFSBucket(client[DATABASE], bucket_name=BUCKET)


def _sync_file():
    return settings.HOME / "cloud_sync.json"


def _sync_state():
    """Kept in a file, not the database, because restoring a snapshot replaces the database."""
    try:
        return json.loads(_sync_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_sync(**kw):
    st = _sync_state() | kw
    if "device" not in st:
        st["device"] = uuid.uuid4().hex
    _sync_file().write_text(json.dumps(st), encoding="utf-8")
    return st


def state():
    uri, st = _uri(), _sync_state()
    return {"connected": connected(), "host": _host(uri) if uri else "", "auto": bool(st.get("auto")), "hours": SYNC_HOURS,
            "last": st.get("last_sync"), "pulled": st.get("pulled"), "summary": st.get("summary", ""), "keep": KEEP}


def connect(uri, passphrase):
    uri, passphrase = uri.strip(), passphrase.strip()
    if not re.match(r"mongodb(\+srv)?://", uri):
        raise CloudError("That isn't a MongoDB connection string. It starts with mongodb+srv://")
    if "<" in uri or ">" in uri:
        raise CloudError("Replace <db_password> in the string with your database user's password.")
    if len(passphrase) < MIN_PASSPHRASE:
        raise CloudError(f"Use a backup passphrase of at least {MIN_PASSPHRASE} characters.")
    try:
        client = _client(uri)
        try:
            client.admin.command("ping")
            client[DATABASE]["connection_check"].insert_one({"at": db.now_iso()})     # proves this user can write
            client[DATABASE]["connection_check"].drop()
        finally:
            client.close()
    except Exception as e:
        raise CloudError(f"Couldn't connect to Atlas: {_clean_error(e, uri)}. Check the password, and that this PC's IP "
                         "is on the Network Access list.") from e
    settings.write_env({URI_VAR: uri, PASS_VAR: passphrase})
    _save_sync(auto=True, seen="", pushed="")


def disconnect():
    """Forget the connection on this PC. Backups already in Atlas stay there."""
    settings.write_env({URI_VAR: None, PASS_VAR: None})
    _sync_file().unlink(missing_ok=True)


def set_auto(on):
    _save_sync(auto=bool(on))


def _files(bucket):
    return sorted(bucket.find({}), key=lambda f: f.upload_date, reverse=True)


def _info(f):
    return {"id": str(f._id), "name": f.filename, "size_kb": round(f.length / 1024),
            "created": f.upload_date.replace(tzinfo=timezone.utc).isoformat(timespec="seconds"),
            "counts": (f.metadata or {}).get("counts", {})}


def _with_bucket(work):
    uri = _uri()
    if not connected():
        raise CloudError("Connect MongoDB Atlas in Settings first.")
    try:
        client = _client(uri)
        try:
            return work(_bucket(client))
        finally:
            client.close()
    except (CloudError, backup.BackupError):
        raise
    except Exception as e:
        raise CloudError(f"Atlas: {_clean_error(e, uri)}") from e


def listing():
    return _with_bucket(lambda b: [_info(f) for f in _files(b)])


def _upload(bucket, kind):
    made = backup.create(kind, _passphrase())
    with open(backup.BACKUPS / made["name"], "rb") as fh:
        fid = bucket.upload_from_stream(made["name"], fh, metadata={"counts": made["counts"], "app": "JobQuest.AI",
                                                                    "device": _save_sync()["device"]})
    for old in _files(bucket)[KEEP:]:
        bucket.delete(old._id)
    _save_sync(seen=str(fid), pushed=fingerprint())
    return made


def backup_now():
    """Make an encrypted backup, upload it and keep the newest few. The local copy stays in ~/.jobhuntai/backups too."""
    out = _with_bucket(lambda bucket: _upload(bucket, "manual"))
    _save_sync(last_sync=db.now_iso())
    return out


def restore(file_id):
    """Bring a cloud backup back. backup.restore saves a safety copy of the current data first."""
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        oid = ObjectId(file_id)
    except InvalidId:
        raise CloudError("No such backup.") from None
    backup.BACKUPS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=backup.BACKUPS) as tmp:
        path = Path(tmp) / "cloud.zip"

        def work(bucket):
            with open(path, "wb") as fh:
                bucket.download_to_stream(oid, fh)
        _with_bucket(work)
        return backup.restore(path, _passphrase())


def fingerprint():
    """Changes whenever something worth syncing changes."""
    with db.conn() as c:
        parts = [c.execute("SELECT key, status, status_at, edited_at, notes FROM jobs WHERE status != '' ORDER BY key").fetchall(),
                 c.execute("SELECT id, sent_at, draft_body FROM outreach_contacts ORDER BY id").fetchall(),
                 c.execute("SELECT COUNT(*) FROM coach_messages").fetchall(), c.execute("SELECT key FROM deleted_keys ORDER BY key").fetchall(),
                 c.execute("SELECT sha, filename, target_role FROM resumes ORDER BY filename").fetchall()]
    return hashlib.sha1(json.dumps([[tuple(r) for r in p] for p in parts], default=str).encode()).hexdigest()


def _stamp(r):
    return (r["edited_at"] or r["status_at"] or r["last_update"] or "")[:19]


def merge(snapshot, resume_file=lambda name: None):
    """Fold another PC's database into this one. Returns what changed."""
    other = sqlite3.connect(snapshot)
    other.row_factory = sqlite3.Row
    out = {"added": 0, "updated": 0, "removed": 0, "contacts": 0}
    try:
        with db.conn() as c:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(jobs)") if r["name"] != "id"]
            gone = {r["key"] for r in c.execute("SELECT key FROM deleted_keys")}
            for r in other.execute("SELECT key, at FROM deleted_keys"):
                c.execute("INSERT OR REPLACE INTO deleted_keys(key, at) VALUES(?,?)", (r["key"], r["at"]))
                gone.add(r["key"])
            for k in gone:
                out["removed"] += c.execute("DELETE FROM jobs WHERE key=?", (k,)).rowcount
            ids = {}
            for r in other.execute("SELECT * FROM jobs WHERE status != ''"):
                if r["key"] in gone:
                    continue
                mine = c.execute("SELECT * FROM jobs WHERE key=?", (r["key"],)).fetchone()
                if not mine:
                    row = {k: r[k] for k in cols if k in r.keys()} | {"active": 0}
                    ids[r["id"]] = c.execute(f"INSERT INTO jobs({', '.join(row)}) VALUES({', '.join('?' * len(row))})",
                                             tuple(row.values())).lastrowid
                    out["added"] += 1
                    continue
                ids[r["id"]] = mine["id"]
                if _stamp(r) > _stamp(mine):
                    c.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in JOB_FIELDS)} WHERE id=?",
                              (*[r[k] for k in JOB_FIELDS], mine["id"]))
                    out["updated"] += 1
            for r in other.execute("SELECT * FROM status_history"):
                jid = ids.get(r["job_id"])
                if jid and not c.execute("SELECT 1 FROM status_history WHERE job_id=? AND status=? AND at=?", (jid, r["status"], r["at"])).fetchone():
                    c.execute("INSERT INTO status_history(job_id, status, at) VALUES(?,?,?)", (jid, r["status"], r["at"]))
            for r in other.execute("SELECT * FROM outreach_contacts"):
                mine = c.execute("SELECT sent_at FROM outreach_contacts WHERE id=?", (r["id"],)).fetchone()
                if not mine:
                    db._save_contact(c, dict(r))
                    out["contacts"] += 1
                elif r["sent_at"] and not mine["sent_at"]:
                    c.execute("UPDATE outreach_contacts SET sent_at=?, status=? WHERE id=?", (r["sent_at"], r["status"], r["id"]))
            for r in other.execute("SELECT * FROM coach_conversations"):
                mine = c.execute("SELECT id FROM coach_conversations WHERE created=? AND title=?", (r["created"], r["title"])).fetchone()
                cid = mine["id"] if mine else c.execute(
                    "INSERT INTO coach_conversations(title, provider, model, kind, created, updated) VALUES(?,?,?,?,?,?)",
                    (r["title"], r["provider"], r["model"], r["kind"], r["created"], r["updated"])).lastrowid
                have = c.execute("SELECT COUNT(*) n FROM coach_messages WHERE conversation_id=?", (cid,)).fetchone()["n"]
                for m in other.execute("SELECT * FROM coach_messages WHERE conversation_id=? ORDER BY id LIMIT -1 OFFSET ?", (r["id"], have)):
                    c.execute("INSERT INTO coach_messages(conversation_id, role, content, files, images, provider, model, actions, at) "
                              "VALUES(?,?,?,?,?,?,?,?,?)", (cid, m["role"], m["content"], m["files"], m["images"], m["provider"],
                                                            m["model"], m["actions"], m["at"]))
            names = {r["filename"] for r in c.execute("SELECT filename FROM resumes")}
            for r in other.execute("SELECT * FROM resumes"):
                data = resume_file(Path(r["path"] or "").name) if r["filename"] not in names else None
                if data:
                    settings.RESUMES.mkdir(parents=True, exist_ok=True)
                    path = settings.RESUMES / Path(r["path"]).name
                    path.write_bytes(data)
                    c.execute("INSERT INTO resumes(label, filename, path, parsed, ai, added, source, sha, target_role) VALUES(?,?,?,?,?,?,'',?,?)",
                              (r["label"], r["filename"], str(path), r["parsed"], r["ai"], r["added"], r["sha"], r["target_role"]))
    finally:
        other.close()
    return out


def _is_empty():
    with db.conn() as c:
        return not c.execute("SELECT 1 FROM jobs WHERE status != '' LIMIT 1").fetchone() and \
            not c.execute("SELECT 1 FROM resumes LIMIT 1").fetchone()


def _pull(bucket, head):
    backup.BACKUPS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=backup.BACKUPS) as tmp:
        path = Path(tmp) / "cloud.zip"
        with open(path, "wb") as fh:
            bucket.download_to_stream(head._id, fh)
        if _is_empty():
            counts = backup.restore(path, _passphrase())
            return f"Restored {counts['tracked']} applications from the cloud"
        snap = Path(tmp) / "other.db"
        try:
            with backup._open(path, _passphrase()) as z:
                snap.write_bytes(z.read("jobhunt.db"))
                members = set(z.namelist())
                resumes = {m.split("/", 1)[1]: z.read(m) for m in members if m.startswith("resumes/") and not m.endswith("/")}
        except RuntimeError:
            raise CloudError("Your backup passphrase on this PC doesn't match the one used on your other PC.") from None
        r = merge(snap, resumes.get)
        return f"{r['added']} added, {r['updated']} updated, {r['removed']} removed"


def sync():
    """One round: bring in what other PCs saved, then save what changed here."""
    if not connected():
        raise CloudError("Connect MongoDB Atlas in Settings first.")
    if not _syncing.acquire(blocking=False):
        raise CloudError("A sync is already running.")
    try:
        def work(bucket):
            st = _save_sync()
            files = _files(bucket)
            head, summary = (files[0] if files else None), "Nothing new from other PCs"
            pulled = False
            if head and str(head._id) != st.get("seen") and (head.metadata or {}).get("device") != st["device"]:
                summary, pulled = _pull(bucket, head), True
                _save_sync(seen=str(head._id), pulled=db.now_iso())
            if not head or fingerprint() != _sync_state().get("pushed"):
                _upload(bucket, "auto")
                summary += ", saved this PC's changes"
            return {"summary": summary, "pulled": pulled}
        out = _with_bucket(work)
        _save_sync(last_sync=db.now_iso(), summary=out["summary"])
        return out
    finally:
        _syncing.release()


def sync_if_due(force=False):
    """Called when the app opens and every few minutes after. Syncs once the last one is 6 hours old."""
    st = _sync_state()
    if not st.get("auto") or not connected():
        return None
    last = st.get("last_sync")
    if not force and last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=SYNC_HOURS):
        return None
    return sync()
