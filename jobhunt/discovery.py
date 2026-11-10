"""Find the public job boards of the companies in your tracker, so every search also reads their openings.

Tries each company's likely board name on Greenhouse, Lever and Ashby (SmartRecruiters is left out: its robots.txt
allows only LinkedIn's crawler). A board counts only when it's clearly that company's: Greenhouse must report the same
company name; Lever and Ashby must match the full name exactly and be at least six characters long, so "Delta" can't
pick up some other Delta. Every company is checked once and re-checked after 60 days; hits are kept in company_boards.
"""
import concurrent.futures as cf
import re
import urllib.error
from datetime import datetime, timedelta, timezone

from . import db
from .net import RobotsBlocked, fetch_json

ATS = ("greenhouse", "lever", "ashby")
RECHECK_DAYS = 60

TABLE = """CREATE TABLE IF NOT EXISTS company_boards (
    company TEXT, ats TEXT, slug TEXT DEFAULT '', name TEXT DEFAULT '', checked_at TEXT, PRIMARY KEY(company, ats))"""


def _table():
    with db.conn() as c:
        c.execute(TABLE)


def slugs(name):
    """Likely board names: 'Pine Labs' -> pinelabs, pine-labs."""
    norm = db.norm_company(name)
    compact = re.sub(r"[^a-z0-9]", "", norm)
    dashed = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    return [s for s in dict.fromkeys([compact, dashed]) if len(s) >= 3]


def check(ats, slug, name):
    """True when this board exists, has openings and is clearly this company's."""
    try:
        if ats == "greenhouse":
            info = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}", retries=1)
            return db.norm_company(info.get("name", "")) == db.norm_company(name)
        if len(slug) < 6 or slug.replace("-", "") != re.sub(r"[^a-z0-9]", "", db.norm_company(name)):
            return False
        if ats == "lever":
            return bool(fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1", retries=1))
        return bool((fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", retries=1) or {}).get("jobs"))
    except (urllib.error.HTTPError, urllib.error.URLError, RobotsBlocked, ValueError):
        return False


def due(companies, known_slugs, now=None):
    """Tracker companies not checked lately and not already in the built-in board list."""
    now = now or datetime.now(timezone.utc)
    _table()
    with db.conn() as c:
        checked = {r["company"]: r["checked_at"] for r in c.execute("SELECT company, MIN(checked_at) checked_at FROM company_boards GROUP BY company")}
    out = []
    for name in companies:
        key = db.norm_company(name)
        if not key or any(s in known_slugs for s in slugs(name)):
            continue
        last = checked.get(key)
        if last and now - datetime.fromisoformat(last) < timedelta(days=RECHECK_DAYS):
            continue
        out.append(name)
    return out


def discover(companies, known_slugs, limit=40, on_progress=None):
    """Check up to `limit` due companies. One thread per ATS, so each site is asked at its own polite pace."""
    todo = due(companies, known_slugs)[:limit]
    found = []
    if not todo:
        return found

    def run_ats(ats):
        hits = []
        for name in todo:
            slug = next((s for s in slugs(name) if check(ats, s, name)), "")
            with db.conn() as c:
                c.execute("INSERT INTO company_boards(company, ats, slug, name, checked_at) VALUES(?,?,?,?,?) "
                          "ON CONFLICT(company, ats) DO UPDATE SET slug=excluded.slug, name=excluded.name, checked_at=excluded.checked_at",
                          (db.norm_company(name), ats, slug, name, db.now_iso()))
            if slug:
                hits.append((ats, slug, name))
        return hits

    with cf.ThreadPoolExecutor(len(ATS)) as ex:
        for hits in ex.map(run_ats, ATS):
            found += hits
    if on_progress:
        on_progress(len(todo), len(found))
    return found


def boards():
    """Boards found so far, in config.toml's 'slug|Name' form: {'greenhouse': [...], 'lever': [...], 'ashby': [...]}."""
    _table()
    with db.conn() as c:
        rows = c.execute("SELECT ats, slug, name FROM company_boards WHERE slug != ''").fetchall()
    out = {a: [] for a in ATS}
    for r in rows:
        out[r["ats"]].append(f"{r['slug']}|{r['name']}")
    return out
