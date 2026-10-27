"""Backups: one zip per snapshot in ~/.jobhuntai/backups (outside OneDrive).

Each zip holds jobhunt.db (copied with SQLite's online backup API, so it is consistent even while the app runs),
your uploaded resumes, and a manifest. The .env file with API keys is never included.
"""
import json
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from . import settings

BACKUPS = settings.HOME / "backups"
KEEP_AUTO = 7
NAME = re.compile(r"^jobhuntai-(manual|auto|pre-restore)-\d{8}-\d{6}\.zip$")


class BackupError(Exception):
    pass


def _counts(db_file):
    c = sqlite3.connect(db_file)
    try:
        return {"tracked": c.execute("SELECT COUNT(*) FROM jobs WHERE status NOT IN ('', 'hidden')").fetchone()[0],
                "jobs": c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
                "resumes": c.execute("SELECT COUNT(*) FROM resumes").fetchone()[0]}
    finally:
        c.close()


def create(kind="manual"):
    """Write a new backup zip and return its info."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    name = f"jobhuntai-{kind}-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with tempfile.TemporaryDirectory(dir=BACKUPS) as tmp:
        snap = Path(tmp) / "jobhunt.db"
        src = sqlite3.connect(settings.DB_PATH)
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        manifest = {"app": "JobQuest.AI", "created": datetime.now().isoformat(timespec="seconds"), "kind": kind,
                    "counts": _counts(snap)}
        with zipfile.ZipFile(BACKUPS / name, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "jobhunt.db")
            for f in sorted(settings.RESUMES.glob("*")) if settings.RESUMES.exists() else []:
                if f.is_file():
                    z.write(f, f"resumes/{f.name}")
            z.writestr("manifest.json", json.dumps(manifest, indent=1))
    if kind == "auto":
        _prune()
    return info(BACKUPS / name)


def info(path):
    try:
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("manifest.json"))
    except Exception:
        manifest = {}
    return {"name": path.name, "size_kb": round(path.stat().st_size / 1024), "created": manifest.get("created"),
            "kind": manifest.get("kind", ""), "counts": manifest.get("counts", {})}


def listing():
    if not BACKUPS.exists():
        return []
    return [info(p) for p in sorted(BACKUPS.glob("jobhuntai-*.zip"), key=lambda p: p.name.split("-", 2)[2], reverse=True)
            if NAME.match(p.name)]


def path_of(name):
    if not NAME.match(name or "") or not (BACKUPS / name).exists():
        raise BackupError("No such backup.")
    return BACKUPS / name


def _prune():
    autos = sorted(BACKUPS.glob("jobhuntai-auto-*.zip"), reverse=True)
    for old in autos[KEEP_AUTO:]:
        old.unlink(missing_ok=True)
    safety = sorted(BACKUPS.glob("jobhuntai-pre-restore-*.zip"), reverse=True)
    for old in safety[3:]:
        old.unlink(missing_ok=True)


def auto_daily():
    """Make the daily automatic backup if the newest one is older than 20 hours."""
    newest = max(BACKUPS.glob("jobhuntai-auto-*.zip"), default=None, key=lambda p: p.name) if BACKUPS.exists() else None
    if newest:
        stamp = datetime.strptime(newest.name[len("jobhuntai-auto-"):-4], "%Y%m%d-%H%M%S")
        if datetime.now() - stamp < timedelta(hours=20):
            return None
    if not settings.DB_PATH.exists():
        return None
    return create("auto")


def restore(zip_path):
    """Replace the current data with a backup. A safety backup of the current state is made first."""
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise BackupError("That isn't a JobQuest.AI backup (not a zip file).")
    with z, tempfile.TemporaryDirectory(dir=BACKUPS) as tmp:
        if "jobhunt.db" not in z.namelist():
            raise BackupError("That isn't a JobQuest.AI backup (no jobhunt.db inside).")
        snap = Path(tmp) / "jobhunt.db"
        snap.write_bytes(z.read("jobhunt.db"))
        check = sqlite3.connect(snap)
        try:
            ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError:
            ok, tables = False, set()
        finally:
            check.close()
        if not ok or "jobs" not in tables:
            raise BackupError("That backup's database is damaged or not from JobQuest.AI.")
        create("pre-restore")
        src = sqlite3.connect(snap)
        dst = sqlite3.connect(settings.DB_PATH, timeout=30)
        try:
            src.backup(dst)                 # copies page by page into the live database, safely
        finally:
            dst.close()
            src.close()
        settings.RESUMES.mkdir(parents=True, exist_ok=True)
        for member in z.namelist():
            if member.startswith("resumes/") and not member.endswith("/"):
                fname = Path(member).name                      # never trust paths inside the zip
                if fname:
                    (settings.RESUMES / fname).write_bytes(z.read(member))
        return _counts(settings.DB_PATH)
