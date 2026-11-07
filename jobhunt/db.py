"""SQLite storage: jobs (with your tracking status and the AI's verdict), refresh runs, resumes, settings."""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from . import salary, settings, tiers

STATUSES = ("", "saved", "applied", "screening", "oa", "interviewing", "offer", "rejected", "ghosted", "withdrawn", "hidden")
# Tracker-only columns, added to older databases on first connect.
EXTRA_COLUMNS = {"applied_on": "TEXT", "job_type": "TEXT DEFAULT ''", "app_source": "TEXT DEFAULT ''",
                 "last_update": "TEXT", "next_step": "TEXT DEFAULT ''", "contact": "TEXT DEFAULT ''"}
# Numbers, so they stay out of the text-only paths (set_fields, add_manual extras).
SALARY_COLUMNS = {"edited_at": "TEXT", "salary_min": "REAL", "salary_max": "REAL", "salary_currency": "TEXT DEFAULT ''", "salary_src": "TEXT DEFAULT ''"}
_migrated = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    source TEXT, company TEXT, title TEXT, location TEXT, cities TEXT, remote TEXT, url TEXT,
    posted TEXT, description TEXT, kind TEXT, level TEXT, min_years INTEGER, also_on TEXT,
    h_score REAL, matched TEXT, missing TEXT, reasons TEXT, best_resume TEXT,
    ai_sig TEXT, ai_json TEXT, ai_score INTEGER, ai_rejected INTEGER DEFAULT 0, ai_at TEXT, ai_error TEXT,
    score REAL,
    first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1,
    status TEXT DEFAULT '', status_at TEXT, notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS jobs_active ON jobs(active);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, ok INTEGER, raw INTEGER, candidates INTEGER,
    kept INTEGER, new INTEGER, ai_checked INTEGER, ai_in INTEGER, ai_out INTEGER, ai_cost REAL, sources TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, filename TEXT, path TEXT, parsed TEXT, ai TEXT, added TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS deleted_keys (key TEXT PRIMARY KEY, at TEXT);
CREATE TABLE IF NOT EXISTS job_verdicts (
    job_id INTEGER NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, score INTEGER, rejected INTEGER DEFAULT 0,
    verdict TEXT, reasons_json TEXT, sig TEXT, at TEXT, PRIMARY KEY (job_id, provider, model)
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, provider TEXT, model TEXT, feature TEXT,
    tokens_in INTEGER, tokens_out INTEGER, cost REAL, latency_ms INTEGER
);
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL, status TEXT, at TEXT
);
CREATE INDEX IF NOT EXISTS status_history_job ON status_history(job_id);
CREATE TABLE IF NOT EXISTS coach_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, provider TEXT DEFAULT '', model TEXT DEFAULT '',
    kind TEXT DEFAULT 'chat', job_id INTEGER, created TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS coach_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, role TEXT, content TEXT, files TEXT,
    images TEXT, provider TEXT DEFAULT '', model TEXT DEFAULT '', actions TEXT, at TEXT
);
CREATE INDEX IF NOT EXISTS coach_messages_conv ON coach_messages(conversation_id);
CREATE TABLE IF NOT EXISTS outreach_contacts (
    id TEXT PRIMARY KEY, name TEXT DEFAULT '', email TEXT DEFAULT '', title TEXT DEFAULT '', company TEXT DEFAULT '',
    job_role TEXT DEFAULT '', location TEXT DEFAULT '', notes TEXT DEFAULT '', added TEXT, sent_at TEXT DEFAULT '',
    job_id TEXT DEFAULT '', draft_subject TEXT DEFAULT '', draft_body TEXT DEFAULT '', status TEXT DEFAULT '',
    error TEXT DEFAULT '', resume_id INTEGER
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    settings.ensure_dirs()
    c = sqlite3.connect(settings.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    global _migrated
    if not _migrated:
        _backup_before_upgrade(c)
        have = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        for col, typ in (EXTRA_COLUMNS | SALARY_COLUMNS).items():
            if col not in have:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
        have_r = {r["name"] for r in c.execute("PRAGMA table_info(resumes)")}
        for col in ("source", "sha", "target_role"):           # resumes folder: original file, its hash, role tag
            if col not in have_r:
                c.execute(f"ALTER TABLE resumes ADD COLUMN {col} TEXT DEFAULT ''")
        _move_old_settings(c)
        c.commit()
        _migrated = True
    try:
        with c:
            yield c
    finally:
        c.close()


def _backup_before_upgrade(c):
    """The first run of this version changes the database, so existing data gets a backup first."""
    if _old_setting(c, "moved_v3") is None and c.execute("SELECT 1 FROM jobs LIMIT 1").fetchone():
        from . import backup
        backup.create("manual")


def reset_migration():
    global _migrated
    _migrated = False


def _old_setting(c, key):
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def _move_old_settings(c):
    """One-off: Coach history and cold email contacts move from JSON settings to their own tables. The old values are
    left in place (and marked done) so nothing is lost if you ever go back to an older version."""
    done = _old_setting(c, "moved_v3") or []
    if "coach" not in done:
        msgs = _old_setting(c, "coach_history") or []
        if msgs and not c.execute("SELECT 1 FROM coach_conversations LIMIT 1").fetchone():
            when = now_iso()
            cid = c.execute("INSERT INTO coach_conversations(title, created, updated) VALUES(?,?,?)",
                            ("Earlier chat", when, when)).lastrowid
            for m in msgs:
                c.execute("INSERT INTO coach_messages(conversation_id, role, content, files, images, provider, at) "
                          "VALUES(?,?,?,?,?,?,?)", (cid, m.get("role"), m.get("content", ""), json.dumps(m.get("files", [])),
                                                    json.dumps(m.get("images", [])), m.get("provider", ""), when))
        done.append("coach")
    if "outreach" not in done:
        for r in _old_setting(c, "outreach_contacts") or []:
            if r.get("id"):
                _save_contact(c, r)
        done.append("outreach")
    if "salary" not in done:             # read the postings already stored; no AI, no network
        for r in c.execute("SELECT id, title, description FROM jobs WHERE COALESCE(description, '') != ''").fetchall():
            pay = salary.parse(f"{r['title']}\n{r['description']}")
            if pay:
                _set_salary(c, r["id"], pay)
        done.append("salary")
    c.execute("INSERT INTO settings(key, value) VALUES('moved_v3', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (json.dumps(done),))


# ---------------------------------------------------------------- settings

def get_setting(key, default=None):
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key, value):
    with conn() as c:
        c.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))


# ---------------------------------------------------------------- resumes

def add_resume(label, filename, path, parsed, ai, source="", sha="", target_role=""):
    with conn() as c:
        cur = c.execute("INSERT INTO resumes(label, filename, path, parsed, ai, added, source, sha, target_role) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (label, filename, str(path), json.dumps(parsed, default=str), json.dumps(ai), now_iso(),
                         str(source), sha, target_role))
        return cur.lastrowid


def set_resume_role(rid, role):
    with conn() as c:
        c.execute("UPDATE resumes SET target_role=? WHERE id=?", (role[:80], rid))


def update_resume_ai(rid, ai):
    with conn() as c:
        c.execute("UPDATE resumes SET ai=? WHERE id=?", (json.dumps(ai), rid))


def resumes():
    with conn() as c:
        rows = c.execute("SELECT * FROM resumes ORDER BY id").fetchall()
    return [{"id": r["id"], "label": r["label"], "filename": r["filename"], "path": r["path"],
             "parsed": json.loads(r["parsed"]), "ai": json.loads(r["ai"]) if r["ai"] else None, "added": r["added"],
             "source": r["source"] or "", "sha": r["sha"] or "", "target_role": r["target_role"] or ""}
            for r in rows]


def delete_resume(rid):
    with conn() as c:
        row = c.execute("SELECT path FROM resumes WHERE id=?", (rid,)).fetchone()
        c.execute("DELETE FROM resumes WHERE id=?", (rid,))
    return row["path"] if row else None


# ---------------------------------------------------------------- jobs

def upsert_jobs(jobs, run_at):
    """Insert new jobs, refresh known ones (keeping status, notes, first_seen and the AI verdict).
    Jobs not seen this run become inactive. Returns the number of new jobs."""
    new = 0
    with conn() as c:
        for j in jobs:
            vals = dict(source=j.source, company=j.company, title=j.title, location=j.location,
                        cities=json.dumps(j.cities), remote=j.remote, url=j.url,
                        posted=j.posted.isoformat() if j.posted else None, description=j.description[:20000],
                        kind=j.kind, level=j.level, min_years=j.min_years, also_on=json.dumps(j.also_on),
                        h_score=j.score, matched=json.dumps(j.matched), missing=json.dumps(j.missing),
                        reasons=json.dumps(j.reasons), best_resume=j.resume, last_seen=run_at)
            pay = j.salary or salary.parse(f"{j.title}\n{j.description}")
            row = c.execute("SELECT id FROM jobs WHERE key=?", (j.key,)).fetchone()
            if row:
                sets = ", ".join(f"{k}=?" for k in vals)
                c.execute(f"UPDATE jobs SET {sets}, active=1 WHERE id=?", (*vals.values(), row["id"]))
                job_id = row["id"]
            else:
                new += 1
                cols = ", ".join(["key", *vals, "first_seen", "active", "score"])
                job_id = c.execute(f"INSERT INTO jobs({cols}) VALUES({', '.join('?' * (len(vals) + 4))})",
                                   (j.key, *vals.values(), run_at, 1, j.score)).lastrowid
            if pay:
                _set_salary(c, job_id, pay)
        c.execute("UPDATE jobs SET active=0 WHERE last_seen != ? OR last_seen IS NULL", (run_at,))
        _rescore(c)
    return new


def _rescore(c, job_id=None):
    """Final score: 70% the AI's fit score, 30% the keyword match. Keyword match alone without AI."""
    c.execute("""UPDATE jobs SET score = CASE WHEN ai_score IS NOT NULL
                 THEN ROUND(0.7 * ai_score + 0.3 * h_score, 1) ELSE h_score END""" + (" WHERE id=?" if job_id else ""),
              (job_id,) if job_id else ())


def _set_salary(c, job_id, pay, keep_yours=True):
    """Store a salary. A figure you typed yourself is never replaced by a parsed one."""
    c.execute("UPDATE jobs SET salary_min=?, salary_max=?, salary_currency=?, salary_src=? WHERE id=?" +
              (" AND COALESCE(salary_src, '') != 'you'" if keep_yours else ""),
              (pay["salary_min"], pay["salary_max"], pay["salary_currency"], pay["salary_src"], job_id))


def set_salary(job_id, pay):
    with conn() as c:
        _set_salary(c, job_id, pay, keep_yours=False)
        c.execute("UPDATE jobs SET edited_at=? WHERE id=?", (now_iso(), job_id))


def recheck_rejections(rule):
    """Re-apply the reject rule to stored AI verdicts (no API calls) - used when the rule itself changes."""
    with conn() as c:
        for r in c.execute("SELECT id, ai_json FROM jobs WHERE ai_json IS NOT NULL").fetchall():
            c.execute("UPDATE jobs SET ai_rejected=? WHERE id=?", (int(rule(json.loads(r["ai_json"]))), r["id"]))


def jobs_for_ai(sig, limit):
    """Active jobs the AI hasn't checked against the current profile yet, newest first."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM jobs WHERE active=1 AND status != 'hidden' AND (ai_sig IS NULL OR ai_sig != ?) "
            "ORDER BY posted DESC LIMIT ?", (sig, limit)).fetchall()]


def save_ai(job_id, sig, result, rejected, error=None):
    with conn() as c:
        if error:
            c.execute("UPDATE jobs SET ai_error=? WHERE id=?", (error[:300], job_id))
            return
        c.execute("UPDATE jobs SET ai_sig=?, ai_json=?, ai_score=?, ai_rejected=?, ai_at=?, ai_error=NULL WHERE id=?",
                  (sig, json.dumps(result), result.get("fit_score"), int(rejected), now_iso(), job_id))
        _rescore(c, job_id)


def set_status(job_id, status=None, notes=None, at=None):
    """Change a status. `at` is when it really happened (a reply's email date), for the reply-time statistics."""
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    today = date.today().isoformat()          # your local day, so a late-night application lands on the right date
    with conn() as c:
        if status is not None:
            old = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            c.execute("UPDATE jobs SET status=?, status_at=?, last_update=?, edited_at=? WHERE id=?",
                      (status, now_iso(), today, now_iso(), job_id))
            if old and (old["status"] or "") != status:
                c.execute("INSERT INTO status_history(job_id, status, at) VALUES(?,?,?)", (job_id, status, at or now_iso()))
            if status == "applied":           # the first time you mark it applied is the applied date
                c.execute("UPDATE jobs SET applied_on=? WHERE id=? AND applied_on IS NULL", (today, job_id))
        if notes is not None:
            c.execute("UPDATE jobs SET notes=?, edited_at=? WHERE id=?", (notes[:5000], now_iso(), job_id))
        return c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone() is not None


def set_fields(job_id, **fields):
    fields = {k: v[:500] for k, v in fields.items() if v is not None and k in EXTRA_COLUMNS}
    if fields:
        with conn() as c:
            c.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in fields)}, last_update=? WHERE id=?",
                      (*fields.values(), date.today().isoformat(), job_id))


_SUFFIXES = re.compile(r"\b(?:pvt|private|ltd|limited|llp|llc|inc|incorporated|corp|corporation|co|company|plc|gmbh|"
                       r"technologies|technology|india|the)\b")


def norm_company(name):
    s = re.sub(r"[^a-z0-9]+", " ", (name or "").lower())
    return re.sub(r"\s+", " ", _SUFFIXES.sub(" ", s)).strip() or s.strip()


def norm_role(role):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#]+", " ", (role or "").lower())).strip()


def norm_url(u):
    return re.sub(r"[?#].*$", "", (u or "").strip().lower()).rstrip("/")


def same_application(company_a, role_a, url_a, company_b, role_b, url_b):
    """Same company + role counts as the same application, unless both have job links and the links differ
    (two different postings with the same title)."""
    if norm_company(company_a) != norm_company(company_b) or norm_role(role_a) != norm_role(role_b):
        return False
    ua, ub = norm_url(url_a), norm_url(url_b)
    return not (ua and ub and ua != ub)


def find_duplicate(company, role, url="", exclude_id=None):
    """An entry already in the tracker that this one would duplicate, or None."""
    with conn() as c:
        rows = c.execute("SELECT id, company, title, url, status, applied_on, status_at FROM jobs "
                         "WHERE status NOT IN ('', 'hidden')").fetchall()
    for r in rows:
        if r["id"] != exclude_id and same_application(company, role, url, r["company"], r["title"], r["url"]):
            return dict(r)
    return None


def describe(row):
    when = (row.get("applied_on") or (row.get("status_at") or "")[:10])
    return f"{row['company']} – {row['title']}" + (f" (applied {when}, {row['status']})" if when else f" ({row['status']})")


def add_manual(company, role, applied_on, location="", url="", status="applied", notes="", extra=None):
    """A tracker entry you add yourself (or import from Excel). Always a new row - duplicates are allowed."""
    import uuid
    if status not in STATUSES or not status:
        status = "applied"
    location = (location or "").strip() or "India"          # every search is India-only, so blank means India
    extra = {k: (v or "") for k, v in (extra or {}).items() if k in EXTRA_COLUMNS}
    when = f"{applied_on}T00:00:00+00:00" if len(applied_on) == 10 else applied_on
    base = dict(key=f"manual|{uuid.uuid4().hex}", source="Added by you", company=company, title=role, cities="[]",
                remote="", posted=None, description="", kind="", level="", also_on="[]", h_score=0, matched="[]",
                missing="[]", reasons="[]", best_resume="", score=0, first_seen=now_iso(), last_seen=now_iso(), active=0,
                status=status, status_at=when, applied_on=applied_on[:10], notes=notes, url=url, location=location,
                **extra)
    with conn() as c:
        cur = c.execute(f"INSERT INTO jobs({', '.join(base)}) VALUES({', '.join('?' * len(base))})", tuple(base.values()))
        return cur.lastrowid


def status_history():
    """Every recorded status change, oldest first: {job_id: [(status, at), ...]}."""
    with conn() as c:
        rows = c.execute("SELECT job_id, status, at FROM status_history ORDER BY at, id").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["job_id"], []).append((r["status"], r["at"]))
    return out


EDITABLE = {"company": 120, "title": 200, "location": 120, "url": 500, "notes": 5000, "applied_on": 10,
            "job_type": 60, "app_source": 80, "last_update": 10, "next_step": 300, "contact": 200}


def edit_entry(job_id, fields):
    """Change any tracker field. Unknown fields are ignored; status goes through set_status."""
    fields = {k: (v or "").strip()[:EDITABLE[k]] for k, v in fields.items() if k in EDITABLE and v is not None}
    if "company" in fields and not fields["company"] or "title" in fields and not fields["title"]:
        raise ValueError("Company and role can't be empty.")
    if not fields:
        return
    with conn() as c:
        c.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in fields)}, edited_at=? WHERE id=?",
                  (*fields.values(), now_iso(), job_id))


def delete_entries(ids):
    """Remove entries from the tracker. Rows you added are deleted; jobs found by the search just leave the tracker
    (so the next search doesn't bring them back as new). Returns snapshots for undo."""
    snaps = []
    with conn() as c:
        for jid in ids:
            r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if not r:
                continue
            snaps.append(dict(r))
            if r["source"] == "Added by you":
                c.execute("DELETE FROM jobs WHERE id=?", (jid,))
                c.execute("INSERT OR REPLACE INTO deleted_keys(key, at) VALUES(?,?)", (r["key"], now_iso()))   # so sync doesn't bring it back
            else:
                c.execute("UPDATE jobs SET status='', status_at=?, edited_at=? WHERE id=?", (now_iso(), now_iso(), jid))
    return snaps


def undelete(snaps):
    """Put deleted entries back exactly as they were (same id, so links and order survive)."""
    with conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        for snap in snaps:
            c.execute("DELETE FROM deleted_keys WHERE key=?", (snap.get("key"),))
            row = {k: v for k, v in snap.items() if k in cols}
            if c.execute("SELECT 1 FROM jobs WHERE id=?", (row.get("id"),)).fetchone():
                sets = {k: v for k, v in row.items() if k != "id"}
                c.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), row["id"]))
            else:
                c.execute(f"INSERT INTO jobs({', '.join(row)}) VALUES({', '.join('?' * len(row))})", tuple(row.values()))
    return len(snaps)


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60]


def get_job(job_id):
    with conn() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(r) if r else None


def list_jobs(include_inactive_tracked=True):
    """Active jobs, plus anything you've tracked even after it drops out of the search window."""
    q = "SELECT * FROM jobs WHERE active=1" + (" OR status NOT IN ('', 'hidden')" if include_inactive_tracked else "")
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY posted DESC").fetchall()]


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
NOT_HIRING = re.compile(r"privacy|accommodat|accessib|no-?reply|donotreply|legal|dpo|gdpr|compliance|security|"
                        r"abuse|support|help|benefits|ethics|whistle|unsubscribe|press|media|investor|example\.", re.I)


def posting_contacts(description):
    """Email addresses written in a job description that look like hiring contacts (not privacy/legal/etc.)."""
    seen, out = set(), []
    for m in EMAIL.finditer(description or ""):
        email = m.group(0).rstrip(".")
        if email.lower() in seen or NOT_HIRING.search(email):
            continue
        sentence = re.split(r"[.;!?\n]\s", description[max(0, m.start() - 120): m.start()])[-1]
        if NOT_HIRING.search(sentence):          # "for accommodations, email x@..." is not a hiring contact
            continue
        seen.add(email.lower())
        out.append({"name": "", "role": "", "email": email, "phone": ""})
    return out


def _contacts(r, ai):
    """AI-extracted contacts first (they carry names and roles), then any other emails found in the text."""
    out = [c for c in (ai or {}).get("contacts", []) if c.get("email") or c.get("phone")]
    known = {c["email"].lower() for c in out if c.get("email")}
    out += [c for c in posting_contacts(r.get("description")) if c["email"].lower() not in known]
    return out


def public_job(r, now=None, detail=False, verdicts=None):
    from .stats import role_family
    now = now or datetime.now(timezone.utc)
    posted = datetime.fromisoformat(r["posted"]) if r.get("posted") else None
    first = datetime.fromisoformat(r["first_seen"]) if r.get("first_seen") else None
    ai = json.loads(r["ai_json"]) if r.get("ai_json") else None
    years = ai.get("years_required") if ai and ai.get("years_required") is not None else r.get("min_years")
    out = {
        "id": r["id"], "title": r["title"], "company": r["company"], "location": r["location"],
        "cities": json.loads(r["cities"] or "[]"), "remote": r["remote"] or "", "url": r["url"], "source": r["source"],
        "also_on": json.loads(r["also_on"] or "[]"), "posted": r["posted"],
        "age_days": max(0, (now - posted).days) if posted else None, "kind": r["kind"], "level": r["level"],
        "years": years, "score": r["score"], "keyword_score": r["h_score"],
        "matched": json.loads(r["matched"] or "[]"), "missing": json.loads(r["missing"] or "[]"),
        "reasons": json.loads(r["reasons"] or "[]"), "best_resume": r["best_resume"], "ai": ai,
        "ai_rejected": bool(r["ai_rejected"]), "ai_error": r.get("ai_error"),
        "is_new": bool(first and now - first < timedelta(hours=24)), "first_seen": r["first_seen"],
        "active": bool(r["active"]), "status": r["status"] or "", "status_at": r["status_at"], "notes": r["notes"] or "",
        "contacts": _contacts(r, ai),
        "applied_on": r.get("applied_on"), "job_type": r.get("job_type") or "", "app_source": r.get("app_source") or "",
        "last_update": r.get("last_update"), "next_step": r.get("next_step") or "", "contact": r.get("contact") or "",
        "tier": tiers.tier_of(r["company"]),
        "salary_min": r.get("salary_min"), "salary_max": r.get("salary_max"),
        "salary_currency": r.get("salary_currency") or "", "salary_src": r.get("salary_src") or "",
        "salary": salary.label(r), "models": verdicts or [], "family": role_family(r["title"]),
    }
    if detail:
        out["description"] = r["description"] or ""
    return out


# ---------------------------------------------------------------- runs

def start_run():
    with conn() as c:
        return c.execute("INSERT INTO runs(started, ok) VALUES(?, 0)", (now_iso(),)).lastrowid


def finish_run(run_id, **fields):
    fields["finished"] = now_iso()
    if "sources" in fields:
        fields["sources"] = json.dumps(fields["sources"])
    with conn() as c:
        sets = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE runs SET {sets} WHERE id=?", (*fields.values(), run_id))


def last_run(ok_only=True):
    with conn() as c:
        r = c.execute("SELECT * FROM runs " + ("WHERE ok=1 " if ok_only else "") + "ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None
    d = dict(r)
    d["sources"] = json.loads(d["sources"]) if d.get("sources") else {}
    return d


# ---------------------------------------------------------------- what each model said, and what it cost

def jobs_for_compare(provider, model, sig, limit):
    """The best-scoring open jobs this extra model hasn't judged against the current profile."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT j.* FROM jobs j LEFT JOIN job_verdicts v ON v.job_id=j.id AND v.provider=? AND v.model=? "
            "WHERE j.active=1 AND j.status != 'hidden' AND j.ai_rejected=0 AND (v.sig IS NULL OR v.sig != ?) "
            "ORDER BY j.score DESC LIMIT ?", (provider, model, sig, limit)).fetchall()]


def save_verdict(job_id, provider, model, sig, result, rejected):
    with conn() as c:
        c.execute("INSERT INTO job_verdicts(job_id, provider, model, score, rejected, verdict, reasons_json, sig, at) "
                  "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id, provider, model) DO UPDATE SET score=excluded.score, "
                  "rejected=excluded.rejected, verdict=excluded.verdict, reasons_json=excluded.reasons_json, "
                  "sig=excluded.sig, at=excluded.at",
                  (job_id, provider, model, result.get("fit_score"), int(rejected), result.get("verdict", ""),
                   json.dumps(result.get("reasons", [])), sig, now_iso()))


def verdicts_by_job():
    with conn() as c:
        rows = c.execute("SELECT * FROM job_verdicts ORDER BY provider, model").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["job_id"], []).append(
            {"provider": r["provider"], "model": r["model"], "score": r["score"], "rejected": bool(r["rejected"]),
             "verdict": r["verdict"] or "", "reasons": json.loads(r["reasons_json"] or "[]"), "at": r["at"]})
    return out


def record_usage(provider, model, feature, tokens_in, tokens_out, cost, latency_ms):
    with conn() as c:
        c.execute("INSERT INTO llm_usage(at, provider, model, feature, tokens_in, tokens_out, cost, latency_ms) "
                  "VALUES(?,?,?,?,?,?,?,?)", (now_iso(), provider, model, feature, tokens_in, tokens_out, cost, latency_ms))


def usage_rows(since=None):
    q = "SELECT * FROM llm_usage" + (" WHERE at >= ?" if since else "") + " ORDER BY at"
    with conn() as c:
        return [dict(r) for r in c.execute(q, (since,) if since else ()).fetchall()]


# ---------------------------------------------------------------- Coach conversations

def _conversation(r):
    return {"id": r["id"], "title": r["title"], "provider": r["provider"] or "", "model": r["model"] or "",
            "kind": r["kind"] or "chat", "job_id": r["job_id"], "created": r["created"], "updated": r["updated"]}


def conversations():
    with conn() as c:
        return [_conversation(r) for r in c.execute("SELECT * FROM coach_conversations ORDER BY updated DESC, id DESC")]


def conversation(cid):
    with conn() as c:
        r = c.execute("SELECT * FROM coach_conversations WHERE id=?", (cid,)).fetchone()
    return _conversation(r) if r else None


def new_conversation(title="New chat", provider="", model="", kind="chat", job_id=None):
    when = now_iso()
    with conn() as c:
        return c.execute("INSERT INTO coach_conversations(title, provider, model, kind, job_id, created, updated) "
                         "VALUES(?,?,?,?,?,?,?)", (title[:120], provider, model, kind, job_id, when, when)).lastrowid


def edit_conversation(cid, **fields):
    fields = {k: v for k, v in fields.items() if k in ("title", "provider", "model") and v is not None}
    if fields:
        with conn() as c:
            c.execute(f"UPDATE coach_conversations SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                      (*fields.values(), cid))


def delete_conversation(cid):
    with conn() as c:
        c.execute("DELETE FROM coach_messages WHERE conversation_id=?", (cid,))
        c.execute("DELETE FROM coach_conversations WHERE id=?", (cid,))


def coach_messages(cid):
    with conn() as c:
        rows = c.execute("SELECT * FROM coach_messages WHERE conversation_id=? ORDER BY id", (cid,)).fetchall()
    return [{"id": r["id"], "role": r["role"], "content": r["content"], "files": json.loads(r["files"] or "[]"),
             "images": json.loads(r["images"] or "[]"), "provider": r["provider"] or "", "model": r["model"] or "",
             "actions": json.loads(r["actions"] or "[]")} for r in rows]


def add_coach_message(cid, role, content, files=(), images=(), provider="", model="", actions=()):
    when = now_iso()
    with conn() as c:
        c.execute("INSERT INTO coach_messages(conversation_id, role, content, files, images, provider, model, actions, at) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (cid, role, content, json.dumps(list(files)), json.dumps(list(images)),
                                                provider, model, json.dumps(list(actions)), when))
        c.execute("UPDATE coach_conversations SET updated=? WHERE id=?", (when, cid))
        # photos are heavy: keep them on the latest messages only
        old = c.execute("SELECT id FROM coach_messages WHERE conversation_id=? AND images != '[]' ORDER BY id DESC LIMIT -1 OFFSET 6",
                        (cid,)).fetchall()
        for r in old:
            c.execute("UPDATE coach_messages SET images='[]' WHERE id=?", (r["id"],))


# ---------------------------------------------------------------- cold email contacts

CONTACT_FIELDS = {"name": 200, "email": 200, "title": 200, "company": 200, "job_role": 200, "location": 200, "notes": 1000,
                  "added": 40, "sent_at": 40, "job_id": 20, "draft_subject": 300, "draft_body": 20000, "status": 20, "error": 300}


def _save_contact(c, row):
    vals = {k: str(row.get(k) or "").strip()[:n] for k, n in CONTACT_FIELDS.items()}
    if row.get("draft") and not vals["draft_body"]:         # the old JSON field
        vals["draft_body"] = str(row["draft"])[:20000]
    rid = row.get("resume_id")
    c.execute(f"INSERT INTO outreach_contacts(id, {', '.join(vals)}, resume_id) VALUES(?, {', '.join('?' * len(vals))}, ?) "
              f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in vals)}, resume_id=excluded.resume_id",
              (str(row["id"])[:40], *vals.values(), int(rid) if str(rid or "").isdigit() else None))


def contacts():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM outreach_contacts ORDER BY added DESC, rowid DESC LIMIT 500")]


def contact(cid):
    with conn() as c:
        r = c.execute("SELECT * FROM outreach_contacts WHERE id=?", (cid,)).fetchone()
    return dict(r) if r else None


def save_contacts(rows):
    with conn() as c:
        for r in rows:
            if r.get("id"):
                _save_contact(c, r)


def replace_contacts(rows):
    """The Cold email table saves the whole list: rows left out were deleted."""
    with conn() as c:
        keep = [str(r["id"])[:40] for r in rows if r.get("id")]
        c.execute(f"DELETE FROM outreach_contacts WHERE id NOT IN ({', '.join('?' * len(keep)) or chr(39) * 2})", keep)
        for r in rows:
            if r.get("id"):
                old = c.execute("SELECT * FROM outreach_contacts WHERE id=?", (str(r["id"])[:40],)).fetchone()
                _save_contact(c, (dict(old) if old else {}) | r)


def update_contact(cid, **fields):
    fields = {k: v for k, v in fields.items() if k in CONTACT_FIELDS or k == "resume_id"}
    if fields:
        with conn() as c:
            c.execute(f"UPDATE outreach_contacts SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), cid))


def add_manual_spend(tokens_in, tokens_out, usd):
    """Usage from one-off 'Ask AI' checks, which happen outside a refresh run."""
    m = get_setting("manual_ai", {"in": 0, "out": 0, "usd": 0.0, "checks": 0})
    m.update({"in": m["in"] + tokens_in, "out": m["out"] + tokens_out, "usd": m["usd"] + usd, "checks": m["checks"] + 1})
    set_setting("manual_ai", m)


def ai_spend():
    with conn() as c:
        r = c.execute("SELECT COALESCE(SUM(ai_cost),0) cost, COALESCE(SUM(ai_checked),0) n FROM runs").fetchone()
    m = get_setting("manual_ai", {"usd": 0.0, "checks": 0})
    return {"usd": round(r["cost"] + m["usd"], 4), "checks": r["n"] + m["checks"]}
