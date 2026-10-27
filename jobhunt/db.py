"""SQLite storage: jobs (with your tracking status and the AI's verdict), refresh runs, resumes, settings."""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import settings, tiers

STATUSES = ("", "saved", "applied", "screening", "oa", "interviewing", "offer", "rejected", "ghosted", "withdrawn", "hidden")
# Tracker-only columns, added to older databases on first connect.
EXTRA_COLUMNS = {"applied_on": "TEXT", "job_type": "TEXT DEFAULT ''", "app_source": "TEXT DEFAULT ''",
                 "last_update": "TEXT", "next_step": "TEXT DEFAULT ''", "contact": "TEXT DEFAULT ''"}
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
        have = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        for col, typ in EXTRA_COLUMNS.items():
            if col not in have:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
        c.commit()
        _migrated = True
    try:
        with c:
            yield c
    finally:
        c.close()


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

def add_resume(label, filename, path, parsed, ai):
    with conn() as c:
        cur = c.execute("INSERT INTO resumes(label, filename, path, parsed, ai, added) VALUES(?,?,?,?,?,?)",
                        (label, filename, str(path), json.dumps(parsed, default=str), json.dumps(ai), now_iso()))
        return cur.lastrowid


def update_resume_ai(rid, ai):
    with conn() as c:
        c.execute("UPDATE resumes SET ai=? WHERE id=?", (json.dumps(ai), rid))


def resumes():
    with conn() as c:
        rows = c.execute("SELECT * FROM resumes ORDER BY id").fetchall()
    return [{"id": r["id"], "label": r["label"], "filename": r["filename"], "path": r["path"],
             "parsed": json.loads(r["parsed"]), "ai": json.loads(r["ai"]) if r["ai"] else None, "added": r["added"]}
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
            row = c.execute("SELECT id FROM jobs WHERE key=?", (j.key,)).fetchone()
            if row:
                sets = ", ".join(f"{k}=?" for k in vals)
                c.execute(f"UPDATE jobs SET {sets}, active=1 WHERE id=?", (*vals.values(), row["id"]))
            else:
                new += 1
                cols = ", ".join(["key", *vals, "first_seen", "active", "score"])
                c.execute(f"INSERT INTO jobs({cols}) VALUES({', '.join('?' * (len(vals) + 4))})",
                          (j.key, *vals.values(), run_at, 1, j.score))
        c.execute("UPDATE jobs SET active=0 WHERE last_seen != ? OR last_seen IS NULL", (run_at,))
        _rescore(c)
    return new


def _rescore(c):
    """Final score: 70% the AI's fit score, 30% the keyword match. Keyword match alone without AI."""
    c.execute("""UPDATE jobs SET score = CASE WHEN ai_score IS NOT NULL
                 THEN ROUND(0.7 * ai_score + 0.3 * h_score, 1) ELSE h_score END""")


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
        _rescore(c)


def set_status(job_id, status=None, notes=None):
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with conn() as c:
        if status is not None:
            c.execute("UPDATE jobs SET status=?, status_at=?, last_update=? WHERE id=?",
                      (status, now_iso(), now_iso()[:10], job_id))
            if status == "applied":           # the first time you mark it applied is the applied date
                c.execute("UPDATE jobs SET applied_on=? WHERE id=? AND applied_on IS NULL", (now_iso()[:10], job_id))
        if notes is not None:
            c.execute("UPDATE jobs SET notes=? WHERE id=?", (notes[:5000], job_id))
        return c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone() is not None


def set_fields(job_id, **fields):
    fields = {k: v[:500] for k, v in fields.items() if v is not None and k in EXTRA_COLUMNS}
    if fields:
        with conn() as c:
            c.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in fields)}, last_update=? WHERE id=?",
                      (*fields.values(), now_iso()[:10], job_id))


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
        c.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), job_id))


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
            else:
                c.execute("UPDATE jobs SET status='', status_at=? WHERE id=?", (now_iso(), jid))
    return snaps


def undelete(snaps):
    """Put deleted entries back exactly as they were (same id, so links and order survive)."""
    with conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        for snap in snaps:
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


def public_job(r, now=None, detail=False):
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
