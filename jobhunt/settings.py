"""Paths, config.toml and the .env file.

Code lives in the project folder (on the OneDrive Desktop). Everything that changes at runtime - the database,
uploaded resumes, the API key - lives in ~/.jobhuntai instead: OneDrive syncing a live SQLite file corrupts
it, and a secret has no business being synced to the cloud.
"""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("JOBHUNT_HOME", Path.home() / ".jobhuntai"))
DATA = HOME / "data"
RESUMES = DATA / "resumes"
DB_PATH = DATA / "jobhunt.db"
ENV_PATH = HOME / ".env"

KEY_ENV = {"adzuna_app_id": "ADZUNA_APP_ID", "adzuna_app_key": "ADZUNA_APP_KEY",
           "jsearch_rapidapi_key": "JSEARCH_RAPIDAPI_KEY", "jooble_key": "JOOBLE_KEY"}


def ensure_dirs():
    RESUMES.mkdir(parents=True, exist_ok=True)


def read_env():
    """KEY=VALUE pairs from ~/.jobhuntai/.env (process environment wins)."""
    vals = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    for k in list(vals) + ["OPENAI_API_KEY", "OPENAI_MODEL", *KEY_ENV.values()]:
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


def write_env(updates):
    """Set or remove (value None) keys in ~/.jobhuntai/.env, keeping everything else."""
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
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    s["max_years"] = min(int(s["max_years"]), 2)          # the app is for <3 years of experience, by design
    s["roles"] = [r.upper() for r in s["roles"]]
    a = cfg["ai"]
    a.setdefault("model", "gpt-6-luna")
    a.setdefault("reasoning_effort", "low")
    a.setdefault("max_jobs_per_refresh", 150)
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
