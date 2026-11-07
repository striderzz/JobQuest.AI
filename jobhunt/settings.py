"""Paths, config.toml and the .env file.

Code lives in the project folder (on the OneDrive Desktop). Everything that changes at runtime - the database,
uploaded resumes, the API keys - lives in ~/.jobhuntai instead: OneDrive syncing a live SQLite file corrupts
it, and a secret has no business being synced to the cloud. Keys themselves are encrypted there (vault.py).
"""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("JOBHUNT_HOME", Path.home() / ".jobhuntai"))
DATA = HOME / "data"
RESUMES = DATA / "resumes"
# your role resumes, changeable in Profile; a test or demo home (JOBHUNT_HOME) never reads the real folder
DEFAULT_RESUMES_DIR = (HOME / "resume-folder") if "JOBHUNT_HOME" in os.environ else Path.home() / "OneDrive" / "Desktop" / "RESUMES_26"
DB_PATH = DATA / "jobhunt.db"
ENV_PATH = HOME / ".env"

MAX_YEARS_CHOICE = 5        # the most you can pick in Profile: this stays an early-career job finder
KEY_ENV = {"adzuna_app_id": "ADZUNA_APP_ID", "adzuna_app_key": "ADZUNA_APP_KEY",
           "jsearch_rapidapi_key": "JSEARCH_RAPIDAPI_KEY", "jooble_key": "JOOBLE_KEY"}


def ensure_dirs():
    RESUMES.mkdir(parents=True, exist_ok=True)


def _read_file():
    """KEY=VALUE pairs in the plain .env file."""
    vals = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def _rewrite(updates):
    """Set or remove (value None) keys in the .env file, keeping everything else. Written atomically."""
    HOME.mkdir(parents=True, exist_ok=True)
    lines, seen = [], set()
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
            if k in updates:
                seen.add(k)
                if updates[k] is not None:
                    lines.append(f"{k}={updates[k]}")
            else:
                lines.append(line)
    for k, v in updates.items():
        if k not in seen and v is not None:
            lines.append(f"{k}={v}")
    tmp = ENV_PATH.with_name(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_PATH)


def _move_secrets(vals):
    """First run after an update: keys found in the plain file move into the encrypted vault, and leave the file
    only once the vault has read them back."""
    from . import vault
    plain = {k: v for k, v in vals.items() if vault.is_secret(k)}
    if plain and vault.available():
        vault.update(plain)
        _rewrite({k: None for k in plain})


def read_env():
    """Settings from .env plus the encrypted secrets (process environment wins)."""
    from . import vault
    vals = _read_file()
    _move_secrets(vals)
    vals.update(vault.load())
    for k in list(vals) + ["OPENAI_API_KEY", "OPENAI_MODEL", *KEY_ENV.values()]:
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


def write_env(updates):
    """Set or remove (value None) keys. Secrets go to the encrypted vault, everything else to .env."""
    from . import vault
    if vault.available():
        secret = {k: v for k, v in updates.items() if vault.is_secret(k)}
        if secret:
            vault.update(secret)
        updates = {k: v for k, v in updates.items() if k not in secret}
    if updates:
        _rewrite(updates)


def load_config():
    cfg = {"search": {}, "ai": {}, "server": {}, "sources": {}, "companies": {}, "keys": {}}
    for p in (ROOT / "config.toml", HOME / "config.local.toml"):
        if p.exists():
            with open(p, "rb") as fh:
                _merge(cfg, tomllib.load(fh))
    s = cfg["search"]
    s.setdefault("days", 14)
    s.setdefault("max_years", 2)
    s.setdefault("roles", ["SDE", "ML"])
    s.setdefault("include_internships", True)
    s["max_years"] = min(int(s["max_years"]), 2)          # the default; Profile can change it (MAX_YEARS_CHOICE)
    s["roles"] = [r.upper() for r in s["roles"]]
    a = cfg["ai"]
    a.setdefault("model", "gpt-6-luna")
    a.setdefault("reasoning_effort", "low")
    a.setdefault("max_jobs_per_refresh", 150)
    a.setdefault("compare_jobs", 30)
    a.setdefault("concurrency", 4)
    a.setdefault("prices", {})
    cfg["server"].setdefault("port", 8000)
    env = read_env()
    if env.get("OPENAI_MODEL"):
        a["model"] = env["OPENAI_MODEL"]
    for k, name in KEY_ENV.items():
        if env.get(name):
            cfg["keys"][k] = env[name]
    return cfg


def _merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
