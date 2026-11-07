"""FastAPI app: the JSON API plus the single-page UI in ./static."""
import hmac
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, db, letters, links, llm, profile, refresh, salary, settings, stats, tiers
from .resume import ResumeError

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 5 * 1024 * 1024

app = FastAPI(title="JobQuest.AI", version="1.0", docs_url=None, redoc_url=None, openapi_url=None)
app.state.allowed_hosts = None          # set by `serve`; None in tests
app.state.token = None                  # this launch's session key (vault.new_session); None in tests
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Everything the page needs ships with the app (static/vendor), so it works offline and loads nothing from elsewhere.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Answer only to this machine's own page: blocks DNS-rebinding hosts and cross-site writes."""
    hosts = request.app.state.allowed_hosts
    if hosts:
        if request.headers.get("host", "") not in hosts:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin.split("://", 1)[-1] not in hosts:
            return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
    token = request.app.state.token
    if token:
        path = request.url.path
        cookie = f"jq_session_{request.url.port or 80}"
        if path == "/" and hmac.compare_digest(request.query_params.get("k", ""), token):
            resp = RedirectResponse("/", status_code=303)     # the key leaves the address bar straight away
            resp.set_cookie(cookie, token, httponly=True, samesite="strict")
            return resp
        if not path.startswith("/static/") and path != "/api/health" and \
                not hmac.compare_digest(request.cookies.get(cookie, ""), token):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Open JobQuest.AI from its own shortcut."}, status_code=401)
            return HTMLResponse("<h3>Open JobQuest.AI from its own shortcut.</h3>", status_code=401)
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"           # job sites don't need to see localhost
    if request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"           # revalidate (304 via ETag) so updates show at once
    return resp


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    return {"app": "JobQuest.AI"}


def _ai_state(cfg):
    active = llm.configured()
    label = llm.PROVIDERS[active[0]][0] if active else ""
    return {"configured": bool(active), "screening": any(p != "ollama" for p in active), "provider": label, "active": active,
            "model": llm.model_for(active[0]) if active else cfg["ai"]["model"],
            "key_hint": ai.key_hint(), "spend": db.ai_spend()}


def _mail_state():
    from . import mail
    addr, _ = mail.account()
    return {"connected": mail.connected(), "address": addr, "last_sync": db.get_setting("mail_last_sync"),
            "pending": len(db.get_setting("mail_proposals") or [])}


def _cloud_pulled():
    from . import cloud
    return cloud.state()["pulled"]


@app.get("/api/state")
def state():
    cfg = settings.load_config()
    prefs = profile.preferences()
    return {
        "profile": profile.summary(),
        "ai": _ai_state(cfg),
        "search": {"days": cfg["search"]["days"], "max_years": cfg["search"]["max_years"]},
        "last_run": db.last_run(),
        "last_attempt": db.last_run(ok_only=False),
        "refresh": refresh.PROGRESS.snapshot(),
        "links": links.search_links(prefs["roles"]),
        "optional_keys": {k: bool(v) for k, v in cfg["keys"].items()},
        "target": {"count": stats.today_count(), "target": stats.target()},
        "mail": _mail_state(),
        "cloud_pulled": _cloud_pulled(),
    }


@app.get("/api/jobs")
def jobs():
    now = datetime.now(timezone.utc)
    verdicts = db.verdicts_by_job()
    return {"jobs": [db.public_job(r, now, verdicts=verdicts.get(r["id"])) for r in db.list_jobs()]}


@app.get("/api/jobs/{job_id}")
def job(job_id: int):
    r = db.get_job(job_id)
    if not r:
        raise HTTPException(404, "No such job")
    return db.public_job(r, detail=True, verdicts=db.verdicts_by_job().get(job_id))


class JobUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    applied_on: Optional[str] = None
    job_type: Optional[str] = None
    app_source: Optional[str] = None
    last_update: Optional[str] = None
    next_step: Optional[str] = None
    contact: Optional[str] = None
    salary: Optional[str] = None              # "12-18 LPA"; empty clears it


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: int, body: JobUpdate):
    from .tracker_import import _date as parse_date
    if not db.get_job(job_id):
        raise HTTPException(404, "No such job")
    fields = body.model_dump(exclude={"status", "salary"}, exclude_none=True)
    for k in ("applied_on", "last_update"):
        if fields.get(k):
            d = parse_date(fields[k])
            if d is None:
                raise HTTPException(400, f"Couldn't read the date '{fields[k]}'.")
            fields[k] = d.isoformat()
    try:
        if body.status is not None:
            db.set_status(job_id, body.status, None)
        db.edit_entry(job_id, fields)
        if body.salary is not None:
            db.set_salary(job_id, salary.from_input(body.salary))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return db.public_job(db.get_job(job_id), detail=True)


class DeleteRequest(BaseModel):
    ids: list[int]


@app.post("/api/tracker/delete")
def delete_entries(body: DeleteRequest):
    snaps = db.delete_entries(body.ids[:5000])
    return {"deleted": len(snaps), "undo": snaps}


class UndoRequest(BaseModel):
    rows: list[dict]


@app.post("/api/tracker/undo")
def undo_delete(body: UndoRequest):
    return {"restored": db.undelete(body.rows[:5000])}


@app.post("/api/jobs/{job_id}/assess")
def assess(job_id: int):
    try:
        return db.public_job(refresh.assess_one(job_id), detail=True)
    except KeyError:
        raise HTTPException(404, "No such job")
    except ai.AIError as e:
        raise HTTPException(400, str(e))


@app.post("/api/resumes")
async def upload_resume(file: UploadFile = File(...), label: str = Form("")):
    name = file.filename or "resume"
    if Path(name).suffix.lower() not in (".pdf", ".docx", ".txt"):
        raise HTTPException(400, "Upload a PDF, DOCX or TXT resume.")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, "That file is over 5 MB.")
    try:
        _, ai_error = profile.add_resume(data, name, label, ai.client_from_settings())
    except ResumeError as e:
        raise HTTPException(400, str(e))
    return {"ai_error": ai_error, "profile": profile.summary()}


@app.post("/api/resumes/scan")
def scan_resumes():
    return profile.scan_folder(ai.client_from_settings()) | {"profile": profile.summary()}


class ResumeFolder(BaseModel):
    path: str


@app.put("/api/resumes/folder")
def set_resume_folder(body: ResumeFolder):
    p = Path(body.path.strip().strip('"'))
    if not p.is_dir():
        raise HTTPException(400, "That folder doesn't exist.")
    db.set_setting("resumes_dir", str(p))
    return scan_resumes()


@app.post("/api/resumes/open-folder")
def open_resume_folder():
    import os
    p = Path(profile.resumes_dir())
    if not p.is_dir():
        raise HTTPException(400, "That folder doesn't exist.")
    os.startfile(str(p))                                # Windows: opens Explorer
    return {"ok": True}


class ResumeRole(BaseModel):
    role: str


@app.put("/api/resumes/{rid}/role")
def set_resume_role(rid: int, body: ResumeRole):
    if body.role not in profile.TARGET_ROLES:
        raise HTTPException(400, "Unknown role")
    db.set_resume_role(rid, body.role)
    return {"profile": profile.summary()}


@app.delete("/api/resumes/{rid}")
def delete_resume(rid: int):
    path = db.delete_resume(rid)
    if path is None:
        raise HTTPException(404, "No such resume")
    p = Path(path)
    if p.parent == settings.RESUMES:
        p.unlink(missing_ok=True)
    return {"profile": profile.summary()}


class ManualEntry(BaseModel):
    company: str
    role: str
    job_type: str = ""
    app_source: str = ""
    next_step: str = ""
    contact: str = ""
    salary: str = ""
    date: Optional[str] = None
    location: str = ""
    url: str = ""
    status: str = "applied"
    notes: str = ""


@app.post("/api/tracker")
def add_application(body: ManualEntry):
    from datetime import date as _date
    from .tracker_import import _date as parse_date
    if not body.company.strip() or not body.role.strip():
        raise HTTPException(400, "Company and role are both needed.")
    d = parse_date(body.date) if body.date else _date.today()
    if d is None:
        raise HTTPException(400, f"Couldn't read the date '{body.date}'.")
    try:
        pay = salary.from_input(body.salary) if body.salary.strip() else None
    except ValueError as e:
        raise HTTPException(400, str(e))
    dup = db.find_duplicate(body.company, body.role, body.url)
    jid = db.add_manual(body.company.strip(), body.role.strip(), d.isoformat(), body.location.strip(),
                        body.url.strip(), body.status, body.notes.strip(),
                        {"job_type": body.job_type.strip(), "app_source": body.app_source.strip(),
                         "next_step": body.next_step.strip(), "contact": body.contact.strip()})
    if pay:
        db.set_salary(jid, pay)
    out = db.public_job(db.get_job(jid))
    out["possible_duplicate"] = db.describe(dup) if dup else None
    from . import normalize
    out["name_hint"] = {"company": normalize.company(body.company)[0], "title": normalize.role(body.role)[0]}
    return out


# ---------------------------------------------------------------- cleanup (suggest, tick, apply)

@app.get("/api/cleanup/suggest")
def cleanup_suggest():
    from . import normalize
    rows = [r for r in db.list_jobs() if r.get("status") and r["status"] != "hidden"]
    return {"suggestions": normalize.suggest(rows), "checked": len(rows)}


class CleanupApply(BaseModel):
    changes: list[dict]


@app.post("/api/cleanup/apply")
def cleanup_apply(body: CleanupApply):
    """Apply only the ticked changes. Returns the rows as they were, so the UI's Undo can put them back."""
    snaps, done = {}, 0
    for ch in body.changes[:5000]:
        field, new = ch.get("field"), str(ch.get("new") or "").strip()
        try:
            jid = int(ch.get("id"))
        except (TypeError, ValueError):
            continue
        if field not in ("company", "title") or not new:
            continue
        row = db.get_job(jid)
        if not row:
            continue
        snaps.setdefault(jid, dict(row))
        db.edit_entry(jid, {field: new})
        done += 1
    return {"applied": done, "undo": list(snaps.values())}


class CleanupAI(BaseModel):
    companies: list[str] = []
    roles: list[str] = []


@app.post("/api/cleanup/ai")
def cleanup_ai(body: CleanupAI):
    """Names only (no notes, contacts or dates) go to the LLM; its answers come back as suggestions to tick."""
    comps = sorted({c.strip() for c in body.companies if c.strip()})[:150]
    roles = sorted({r.strip() for r in body.roles if r.strip()})[:150]
    if not comps and not roles:
        return {"companies": {}, "roles": {}}
    system = ("You tidy a job tracker. For each company give its official, commonly used name (fix typos, expand "
              "well-known short forms, drop 'Pvt Ltd'-style suffixes). For each job title give the full, real-world "
              "title (e.g. 'mle eng' -> 'Machine Learning Engineer', 'ai engineer' -> 'AI Engineer'), keeping level "
              "numbers and Intern. If a name is already right or you are not sure, return it unchanged. Reply with "
              'JSON only: {"companies": {"old": "new"}, "roles": {"old": "new"}}')
    try:
        text, used = llm.chat([{"role": "system", "content": system},
                               {"role": "user", "content": json.dumps({"companies": comps, "roles": roles})}], 2500,
                              feature="cleanup")
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    d = _parse_json_obj(text)
    clean = lambda m, keys: {k: str(v).strip()[:200] for k, v in (m or {}).items() if k in keys and str(v).strip() and str(v).strip() != k}
    return {"companies": clean(d.get("companies"), set(comps)), "roles": clean(d.get("roles"), set(roles)), "provider": used}


@app.post("/api/tracker/import")
async def import_tracker(file: UploadFile = File(...)):
    from . import tracker_import
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, "That file is over 5 MB.")
    try:
        entries, problems = tracker_import.parse(file.filename or "", data)
    except tracker_import.ImportError_ as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Couldn't read that file: {e}")
    possible = 0
    for e in entries:
        # every row is kept, even repeats; possible duplicates are only counted so you can review them
        possible += db.find_duplicate(e["company"], e["role"], e["url"]) is not None
        jid = db.add_manual(e["company"], e["role"], e["date"], e["location"], e["url"], e["status"], e["notes"], e["extra"])
        if e.get("salary"):
            try:
                db.set_salary(jid, salary.from_input(e["salary"]))
            except ValueError:
                problems.append(f"{e['company']}: couldn't read the salary '{e['salary']}'")
    return {"added": len(entries), "possible_duplicates": possible, "problems": problems}


@app.get("/api/tracker/template.csv", include_in_schema=False)
def tracker_template():
    from fastapi.responses import Response
    from .tracker_import import TEMPLATE_CSV
    return Response(TEMPLATE_CSV, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="jobhuntai-tracker-template.csv"'})


@app.get("/api/backups")
def backups():
    from . import backup
    return {"folder": str(backup.BACKUPS), "backups": backup.listing()}


class BackupRequest(BaseModel):
    password: str = ""


@app.post("/api/backups")
def make_backup(body: Optional[BackupRequest] = None):
    from . import backup
    return backup.create("manual", (body.password if body else "") or None)


@app.get("/api/backups/{name}", include_in_schema=False)
def download_backup(name: str):
    from . import backup
    try:
        p = backup.path_of(name)
    except backup.BackupError as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, media_type="application/zip", filename=p.name)


def _restore(path, password=None):
    from . import backup
    if refresh.PROGRESS.snapshot()["running"]:
        raise HTTPException(409, "Wait for the current search to finish, then restore.")
    try:
        return {"restored": backup.restore(path, password)}
    except backup.BackupError as e:
        raise HTTPException(400, str(e))


@app.post("/api/backups/{name}/restore")
def restore_backup(name: str, body: Optional[BackupRequest] = None):
    from . import backup
    try:
        p = backup.path_of(name)
    except backup.BackupError as e:
        raise HTTPException(404, str(e))
    return _restore(p, (body.password if body else "") or None)


@app.post("/api/backups/restore-upload")
async def restore_upload(file: UploadFile = File(...), password: str = Form("")):
    import tempfile
    data = await file.read(200 * 1024 * 1024 + 1)
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(400, "That file is too large to be a JobQuest.AI backup.")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(data)
    try:
        return _restore(Path(tmp.name), password or None)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


# ---------------------------------------------------------------- MongoDB Atlas backups

class CloudAccount(BaseModel):
    uri: str
    passphrase: str


class CloudAuto(BaseModel):
    auto: bool


def _cloud(fn, *args):
    from . import backup, cloud
    try:
        return fn(*args)
    except (cloud.CloudError, backup.BackupError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/cloud")
def cloud_state():
    from . import cloud
    return cloud.state()


@app.put("/api/cloud")
def cloud_connect(body: CloudAccount):
    from . import cloud
    _cloud(cloud.connect, body.uri, body.passphrase)
    return cloud.state()


@app.delete("/api/cloud")
def cloud_disconnect():
    from . import cloud
    cloud.disconnect()
    return cloud.state()


@app.put("/api/cloud/auto")
def cloud_auto(body: CloudAuto):
    from . import cloud
    if body.auto and not cloud.connected():
        raise HTTPException(400, "Connect MongoDB Atlas first.")
    cloud.set_auto(body.auto)
    return cloud.state()


@app.post("/api/cloud/sync")
def cloud_sync():
    from . import cloud
    if refresh.PROGRESS.snapshot()["running"]:
        raise HTTPException(409, "Wait for the current search to finish, then sync.")
    return _cloud(cloud.sync) | cloud.state()


@app.get("/api/cloud/backups")
def cloud_backups():
    from . import cloud
    return {"backups": _cloud(cloud.listing)}


@app.post("/api/cloud/backups")
def cloud_backup_now():
    from . import cloud
    return _cloud(cloud.backup_now) | cloud.state()


@app.post("/api/cloud/backups/{file_id}/restore")
def cloud_restore(file_id: str):
    from . import cloud
    if refresh.PROGRESS.snapshot()["running"]:
        raise HTTPException(409, "Wait for the current search to finish, then restore.")
    return {"restored": _cloud(cloud.restore, file_id)}


class SourceKeys(BaseModel):
    jsearch_rapidapi_key: Optional[str] = None
    adzuna_app_id: Optional[str] = None
    adzuna_app_key: Optional[str] = None
    jooble_key: Optional[str] = None


@app.put("/api/settings/keys")
def set_source_keys(body: SourceKeys):
    """Optional free job-source keys, saved to ~/.jobhuntai/.env. An empty string removes a key."""
    updates = {settings.KEY_ENV[k]: (v.strip() or None) for k, v in body.model_dump().items() if v is not None}
    settings.write_env(updates)
    return {k: bool(v) for k, v in settings.load_config()["keys"].items()}


class Preferences(BaseModel):
    current_city: Optional[str] = None
    phone: Optional[str] = None
    preferred_cities: Optional[list[str]] = None
    roles: Optional[list[str]] = None
    include_internships: Optional[bool] = None
    max_years: Optional[int] = None
    about: Optional[str] = None


@app.put("/api/profile")
def save_profile(body: Preferences):
    if body.max_years is not None and not 0 <= body.max_years <= settings.MAX_YEARS_CHOICE:
        raise HTTPException(400, f"Pick between 0 and {settings.MAX_YEARS_CHOICE} years.")
    profile.save_preferences({k: v for k, v in body.model_dump().items() if v is not None})
    return {"profile": profile.summary()}


@app.post("/api/refresh", status_code=202)
def start_refresh():
    if refresh.PROGRESS.snapshot()["running"]:
        raise HTTPException(409, "A refresh is already running.")
    try:
        profile.scan_folder(ai.client_from_settings())     # pick up resumes added to the folder since last time
    except Exception:
        pass
    if not db.resumes():
        raise HTTPException(400, "Add your resume on the Profile page first.")
    _start_refresh_thread()
    return {"started": True}


@app.get("/api/refresh")
def refresh_status():
    return refresh.PROGRESS.snapshot()


def _start_refresh_thread():
    def work():
        try:
            refresh.run()
        except Exception:
            pass                      # recorded in PROGRESS and the runs table
    refresh.PROGRESS.update(running=True, phase="Starting", error=None, result=None)
    threading.Thread(target=work, daemon=True, name="refresh").start()


def maybe_auto_refresh(hours):
    """On startup: if the last good refresh is older than `hours`, start one so the list is current."""
    if not hours or not db.resumes() or refresh.PROGRESS.snapshot()["running"]:
        return
    last = db.last_run()
    if last and last.get("finished"):
        if datetime.now(timezone.utc) - datetime.fromisoformat(last["finished"]) < timedelta(hours=hours):
            return
    try:
        _start_refresh_thread()
    except Exception:
        pass


# ---------------------------------------------------------------- AI providers, cover letters, coach

@app.get("/api/ai/providers")
def ai_providers():
    return llm.status()


@app.get("/api/ai/status")
def ai_status(fresh: bool = False):
    """Which models are live right now, how fast they answer, and which one each job uses."""
    return llm.live_status(fresh)


class ActiveModel(BaseModel):
    provider: str
    model: str = ""
    screening: bool = False       # True: also use it to screen jobs, which needs `confirmed`
    confirmed: bool = False


def _recheck_count(pid, model):
    """How many open jobs a new screening model would read on the next search (capped per search)."""
    sig = profile.signature(model or llm.model_for(pid))
    return len(db.jobs_for_ai(sig, 100000))


@app.put("/api/ai/active")
def ai_active(body: ActiveModel):
    if not llm.known(body.provider):
        raise HTTPException(404, "Unknown provider")
    if body.provider not in llm.configured():
        raise HTTPException(400, f"{llm.PROVIDERS[body.provider][0]} isn't set up yet.")
    pick = {"provider": body.provider, "model": body.model.strip()}
    if body.screening:
        n = _recheck_count(body.provider, pick["model"])
        if not body.confirmed:
            return {"needs_confirm": True, "rechecks": n,
                    "limit": settings.load_config()["ai"]["max_jobs_per_refresh"]}
        db.set_setting("llm_screen", pick)
    else:
        db.set_setting("llm_active", pick)
    return llm.live_status()


class TaskModel(BaseModel):
    task: str
    provider: str = ""           # empty: back to the default
    model: str = ""


@app.put("/api/ai/task")
def ai_task(body: TaskModel):
    """Which model one area of the app uses: the Dashboard's analysis, or Cold email."""
    if body.task not in ("dashboard", "email"):
        raise HTTPException(404, "Unknown task")
    tasks = db.get_setting("llm_tasks") or {}
    if body.provider:
        if body.provider not in llm.configured():
            raise HTTPException(400, "That model isn't available.")
        tasks[body.task] = {"provider": body.provider, "model": body.model.strip()}
    else:
        tasks.pop(body.task, None)
    db.set_setting("llm_tasks", tasks)
    return llm.live_status()


class ForceModel(BaseModel):
    provider: str = ""           # empty: turn it off
    model: str = ""


@app.put("/api/ai/force")
def ai_force(body: ForceModel):
    """One model for every task. It's tested first, so a non-chat or broken model can't be set."""
    if not body.provider:
        db.set_setting("llm_force", None)
        return llm.live_status()
    if body.provider not in llm.configured():
        raise HTTPException(400, "That model isn't available.")
    try:
        used = llm.check_chat_model(body.provider, body.model.strip())
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    db.set_setting("llm_force", {"provider": body.provider, "model": used})
    return llm.live_status()


class ProviderEnabled(BaseModel):
    enabled: bool


@app.put("/api/ai/providers/{pid}/enabled")
def ai_provider_enabled(pid: str, body: ProviderEnabled):
    """Switch a provider off without removing its key. Nothing uses it, not even as a fallback."""
    if not llm.known(pid):
        raise HTTPException(404, "Unknown provider")
    off = llm.disabled()
    (off.discard if body.enabled else off.add)(pid)
    db.set_setting("llm_disabled", sorted(off))
    if not body.enabled and (llm.force_choice() or {}).get("provider") == pid:
        db.set_setting("llm_force", None)
    return llm.status()


class CompareModels(BaseModel):
    models: list[dict]
    jobs: Optional[int] = None


@app.put("/api/ai/compare")
def ai_compare(body: CompareModels):
    ready = llm.configured()
    picks = [{"provider": m["provider"], "model": str(m.get("model") or "")[:120]} for m in body.models[:5]
             if m.get("provider") in ready]
    db.set_setting("llm_compare", picks)
    if body.jobs is not None:
        if not 1 <= body.jobs <= 150:
            raise HTTPException(400, "Pick between 1 and 150 jobs per model.")
        db.set_setting("llm_compare_jobs", body.jobs)
    return llm.live_status()


class ProviderKey(BaseModel):
    api_key: Optional[str] = None
    model: Optional[str] = None


@app.put("/api/ai/providers/{pid}")
def ai_provider_save(pid: str, body: ProviderKey):
    if not llm.known(pid):
        raise HTTPException(404, "Unknown provider")
    try:
        model = llm.test(pid, (body.api_key or "").strip() or None, (body.model or "").strip() or None)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "model": model, **llm.status()}


@app.delete("/api/ai/providers/{pid}")
def ai_provider_remove(pid: str):
    if not llm.known(pid):
        raise HTTPException(404, "Unknown provider")
    llm.remove(pid)
    return llm.status()


class CustomProvider(BaseModel):
    label: str
    base_url: str
    model: str
    api_key: str = ""


@app.post("/api/ai/custom")
def ai_custom(body: CustomProvider):
    """Add a model that isn't listed. It's tested first, and only kept if it answers."""
    try:
        pid, model = llm.add_custom(body.label, body.base_url, body.api_key, body.model)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": pid, "model": model, **llm.status()}


class ProviderOrder(BaseModel):
    order: list[str]


@app.put("/api/ai/order")
def ai_order(body: ProviderOrder):
    db.set_setting("llm_order", [p for p in body.order if llm.known(p)])
    return llm.status()


def _resume_text(resume_id=None):
    rs = db.resumes()
    r = next((x for x in rs if x["id"] == resume_id), rs[0] if rs else None)
    return (r["parsed"].get("text") or "")[:9000] if r else ""


class CoverLetterRequest(BaseModel):
    job_id: Optional[int] = None
    jd: str = ""
    company: str = ""
    role: str = ""
    resume_id: Optional[int] = None
    tone: str = "warm and professional"
    extra: list[dict] = []          # [{"name": "LinkedIn.pdf", "text": "..."}] from /api/ai/context-file


@app.post("/api/ai/cover-letter")
def cover_letter(body: CoverLetterRequest):
    jd, company, role = body.jd.strip(), body.company.strip(), body.role.strip()
    if body.job_id:
        j = db.get_job(body.job_id)
        if j:
            jd = jd or (j["description"] or "")
            company, role = company or j["company"], role or j["title"]
    if len(jd) < 40 and not role:
        raise HTTPException(400, "Paste the job description (or at least the role and company).")
    resume = _resume_text(body.resume_id)
    if not resume:
        raise HTTPException(400, "Add your resume on the Profile page first.")
    prefs = profile.preferences()
    system = ("You write tailored, honest cover letters for an entry-level software / ML candidate applying in India. "
              "Extra material (e.g. a LinkedIn profile export) may add facts; treat it like the resume. "
              "Use only facts from these materials; never invent employers, numbers or skills. 250-330 words, 3-4 short "
              f"paragraphs, tone: {body.tone[:40]}. Open with the specific role and why this company; connect 2-3 "
              "concrete resume achievements to the job's needs; close with a confident call to action. Plain text "
              "with a greeting and sign-off; no placeholders like [Your Name] - use the name from the resume.")
    extra = "".join(f"\n\nEXTRA MATERIAL ({str(x.get('name', 'file'))[:80]}):\n{str(x.get('text', ''))[:letters.MAX_CONTEXT_CHARS]}"
                    for x in body.extra[:4])
    user = (f"ROLE: {role}\nCOMPANY: {company}\nCANDIDATE NOTE: {prefs.get('about') or ''}\n\n"
            f"JOB DESCRIPTION:\n{jd[:7000]}\n\nRESUME:\n{resume}{extra}")
    try:
        # a free local model suits letters, unless you've picked a model in the switcher
        text, used = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], 1400,
                              prefer=(llm.chat_choice() or {}).get("provider") or "ollama", feature="cover_letter")
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    return {"text": text, "provider": used, "company": company, "role": role}


@app.post("/api/ai/context-file")
async def context_file(file: UploadFile = File(...)):
    """Text from a file that helps the cover letter (LinkedIn PDF, project notes). Nothing is stored."""
    try:
        return {"name": file.filename, "text": letters.context_text(await file.read(), file.filename or "file.txt")}
    except letters.LetterError as e:
        raise HTTPException(400, str(e))


class LetterPdf(BaseModel):
    text: str
    company: str = ""
    role: str = ""


@app.post("/api/ai/cover-letter.pdf")
def cover_letter_pdf(body: LetterPdf):
    from fastapi.responses import Response
    if not body.text.strip():
        raise HTTPException(400, "Write the letter first.")
    me = profile.summary()
    contact = "  ·  ".join(x for x in (me.get("city"), me.get("email"), me.get("phone"), me.get("linkedin")) if x)
    pdf = letters.letter_pdf(body.text[:8000], me.get("name", ""), contact, body.company[:120], body.role[:160])
    slug = "".join(ch if ch.isalnum() else "-" for ch in (body.company or "job").lower()).strip("-")[:40] or "job"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="cover-letter-{slug}.pdf"'})


COACH_SYSTEM = ("You are JobQuest Coach, a friendly, practical career coach for an entry-level SDE / ML / AI engineer "
                "job seeker in India who recently finished an MS in the US. Give specific, actionable advice on "
                "resumes, projects, applications, cold outreach, interviews and daily habits. Be concise (use short "
                "lists), honest, and ground advice in the candidate profile and tracker numbers below. If asked to "
                "write something, write it in full:\n"
                "- CV / resume: clean Markdown, '# Full Name' first, a contact line, then '## Section' headings "
                "(Summary, Education, Experience, Projects, Skills) and '- ' bullets that start with strong verbs and "
                "show impact. One page for entry level. Use only facts from the profile, resume and attachments.\n"
                "- Email: first line 'Subject: ...', then the body. Recruiter names and addresses come only from what the "
                "user attached or typed - never guess an email address.\n"
                "- Cover letter: 250-330 words, plain paragraphs.\n"
                "Attached files and photos (CVs, job descriptions, recruiter screenshots) are included with the "
                "user's message; read them carefully.\n"
                "End every reply with one last line in exactly this form, giving up to 3 short follow-ups the user could "
                "ask you next, written in the user's voice: NEXT: first | second | third\n\n")
PREP_SYSTEM = ("You are an interview coach running mock interviews for one specific job. When the user asks for a mock "
               "interview, ask ONE question at a time and wait. After each answer give a score out of 10, one thing done "
               "well, one thing to fix and a stronger sample answer built only from the candidate's real experience, then "
               "ask the next question. Never invent facts about the company; if the posting doesn't say, say so.\n"
               "End every reply with one last line in exactly this form, giving up to 3 short follow-ups in the user's "
               "voice: NEXT: first | second | third\n\n")
PREP_SHEET = ("Write an interview prep sheet in Markdown for this job, using only the posting and the candidate's resume. "
              "Sections: '## What they care about' (3-5 bullets from the posting), '## Likely technical questions' (6-8, "
              "each with a one-line pointer to the candidate's relevant experience), '## Likely behavioural questions' "
              "(4, each with which project or job to use as the story), '## Gaps to prepare for' (skills the posting wants "
              "that the resume doesn't show, with an honest way to answer), '## Questions to ask them' (3). If the posting "
              "text is missing, base it on the role title and say so at the top.")
NEXT_LINE = re.compile(r"\n?\s*\**NEXT:\**\s*(.+?)\s*$", re.I)
ROUTES = (("tracker", "/tracker"), ("cold email", "/outreach"), ("recruiter", "/outreach"), ("dashboard", "/dashboard"),
          ("profile", "/profile"), ("resume", "/profile"))


def _split_actions(text):
    """The reply, and its NEXT: line as buttons. Buttons only fill the message box or open a page; nothing runs itself."""
    m = NEXT_LINE.search(text)
    if not m:
        return text, []
    steps = [x.strip(" -*.") for x in m.group(1).split("|")]
    return text[:m.start()].rstrip(), [{"label": x[:90], "prompt": x[:300]} for x in steps if x][:3]


def _tracker_digest():
    rows = [j for j in db.list_jobs() if j["status"] and j["status"] != "hidden"]
    by = {}
    for j in rows:
        by[j["status"]] = by.get(j["status"], 0) + 1
    week = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    recent = sum(1 for j in rows if (j["applied_on"] or (j["status_at"] or "")[:10]) >= week and j["status"] != "saved")
    f = stats.tracker_facts(rows, companies=25, titles=15)
    top = ", ".join(f"{c[0]} ({c[1]})" for c in f["companies"])
    return (f"TRACKER: {len(rows)} entries; by status {by}; applied in the last 7 days: {recent}. "
            f"Applications: {f['entries']} to {f['unique_companies']} unique companies, {f['unique_role_titles']} unique role "
            f"titles, {f['unique_company_role_pairs']} unique company + role pairs ({f['possible_repeats']} possible repeats). "
            f"Most applied: {top}.")


class ChatMessage(BaseModel):
    message: str
    files: list[dict] = []          # [{"name", "text"}] from /api/ai/context-file
    images: list[dict] = []         # [{"name", "data_url"}], resized in the browser
    conversation_id: Optional[int] = None


def _user_content(m):
    """A stored user message -> chat content (text, plus image parts when it carried photos)."""
    text = m["content"] + "".join(f"\n\nATTACHED FILE ({f['name']}):\n{f['text']}" for f in m.get("files", []))
    if not m.get("images"):
        return text
    return [{"type": "text", "text": text}] + [{"type": "image_url", "image_url": {"url": i["data_url"]}} for i in m["images"]]


def _clean_files(files, images):
    fs = [{"name": str(f.get("name", "file"))[:80], "text": str(f.get("text", ""))[:letters.MAX_CONTEXT_CHARS]}
          for f in files[:4] if f.get("text")]
    ims = [{"name": str(i.get("name", "photo"))[:80], "data_url": i["data_url"]} for i in images[:4]
           if str(i.get("data_url", "")).startswith("data:image/") and len(i["data_url"]) < 3_000_000]
    return fs, ims


def _current_conversation(cid=None):
    """The conversation asked for, else the latest one, else a new one."""
    if cid:
        c = db.conversation(cid)
        if not c:
            raise HTTPException(404, "No such conversation")
        return c
    cs = db.conversations()
    return cs[0] if cs else db.conversation(db.new_conversation())


def _chat_view(cid):
    msgs = db.coach_messages(cid)
    return {"conversation": db.conversation(cid), "conversations": db.conversations(),
            "messages": [{k: v for k, v in m.items() if k not in ("files", "images")} |
                         {"attached": [f["name"] for f in m["files"] + m["images"]]} for m in msgs]}


@app.get("/api/ai/chat")
def chat_history(conversation_id: Optional[int] = None):
    return _chat_view(_current_conversation(conversation_id)["id"])


@app.post("/api/ai/chat")
def chat(body: ChatMessage):
    msg = body.message.strip()[:4000]
    files, images = _clean_files(body.files, body.images)
    if not msg and not files and not images:
        raise HTTPException(400, "Type a question first.")
    msg = msg or "Here are my files. What can you tell me, and how should I use them?"
    conv = _current_conversation(body.conversation_id)
    cid = conv["id"]
    history = db.coach_messages(cid)
    turn = {"role": "user", "content": msg, "files": files, "images": images}
    recent = history[-12:] + [turn]
    # photos are re-sent only with the latest few user turns; older ones stay as text notes so prompts stay small
    photo_turns = {id(m) for m in [m for m in recent if m["role"] == "user" and m.get("images")][-3:]}
    convo = []
    for m in recent:
        if m["role"] == "user":
            mm = m if id(m) in photo_turns else {**m, "images": [],
                                                 "content": m["content"] + "".join(f"\n[photo: {i['name']}]" for i in m.get("images", []))}
            convo.append({"role": "user", "content": _user_content(mm)})
        else:
            convo.append({"role": "assistant", "content": m["content"]})
    system = PREP_SYSTEM + _job_brief(conv["job_id"]) + "\n\n" if conv["kind"] == "prep" else COACH_SYSTEM
    context = system + profile.candidate_block() + "\n" + _tracker_digest()
    try:
        reply = llm.chat([{"role": "system", "content": context}] + convo, 2500, prefer=conv["provider"] or None,
                         model=conv["model"] or None, feature="interview" if conv["kind"] == "prep" else "coach")
    except llm.LLMError as e:
        hint = " (Photos need a vision model: OpenAI, Gemini or Anthropic.)" if images else ""
        raise HTTPException(400, str(e) + hint)
    text, used = reply
    text, actions = _split_actions(text)
    db.add_coach_message(cid, "user", msg, files, images)
    db.add_coach_message(cid, "assistant", text, provider=used, model=getattr(reply, "model", ""), actions=actions)
    if conv["title"] == "New chat":
        db.edit_conversation(cid, title=msg[:60])
    return _chat_view(cid) | {"reply": text, "provider": used, "actions": actions,
                              "fell_back_from": getattr(reply, "fell_back_from", "")}


@app.delete("/api/ai/chat")
def chat_clear(conversation_id: Optional[int] = None):
    """Empty one conversation (the current one if none is named)."""
    cid = _current_conversation(conversation_id)["id"]
    title = db.conversation(cid)["title"]
    db.delete_conversation(cid)
    new = db.new_conversation("New chat" if title == "New chat" or not conversation_id else title)
    return _chat_view(new)


class Conversation(BaseModel):
    title: str = "New chat"
    provider: str = ""
    model: str = ""


@app.post("/api/ai/conversations")
def conversation_new(body: Conversation):
    return _chat_view(db.new_conversation(body.title.strip() or "New chat", body.provider, body.model))


class ConversationEdit(BaseModel):
    title: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


@app.patch("/api/ai/conversations/{cid}")
def conversation_edit(cid: int, body: ConversationEdit):
    if not db.conversation(cid):
        raise HTTPException(404, "No such conversation")
    if body.provider and not llm.known(body.provider):
        raise HTTPException(400, "Unknown provider")
    db.edit_conversation(cid, title=(body.title or "").strip()[:120] or None, provider=body.provider, model=body.model)
    return _chat_view(cid)


@app.delete("/api/ai/conversations/{cid}")
def conversation_delete(cid: int):
    db.delete_conversation(cid)
    return _chat_view(_current_conversation()["id"])


def _job_brief(job_id):
    j = db.get_job(job_id) if job_id else None
    if not j:
        return "JOB: not linked."
    return (f"JOB\nRole: {j['title']}\nCompany: {j['company']}\nLocation: {j['location'] or 'not stated'}\n\n"
            f"POSTING:\n{(j['description'] or 'The posting text was not saved for this application.')[:6000]}")


class InterviewPrep(BaseModel):
    job_id: int
    provider: str = ""
    model: str = ""


@app.post("/api/ai/interview-prep")
def interview_prep(body: InterviewPrep):
    """A prep sheet for one job, opened as its own conversation so the mock interview can carry on from it."""
    j = db.get_job(body.job_id)
    if not j:
        raise HTTPException(404, "No such job")
    pick = profile.pick_resume(j["title"])
    resume = (pick["parsed"].get("text") or "")[:9000] if pick else ""
    try:
        reply = llm.chat([{"role": "system", "content": PREP_SHEET},
                          {"role": "user", "content": f"{_job_brief(body.job_id)}\n\nRESUME:\n{resume or 'none added'}"}], 2200,
                         prefer=body.provider or None, model=body.model or None, feature="interview")
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    text, used = reply
    cid = db.new_conversation(f"Prep: {j['company']} – {j['title']}"[:120], body.provider, body.model, "prep", body.job_id)
    actions = [{"label": x, "prompt": x} for x in ("Start a mock interview", "Ask me the hardest technical question",
                                                   "How do I answer the gaps?")]
    db.add_coach_message(cid, "assistant", text, provider=used, model=getattr(reply, "model", ""), actions=actions)
    return _chat_view(cid)


class DocRequest(BaseModel):
    text: str
    title: str = "Document"
    kind: str = "pdf"


@app.post("/api/ai/document")
def coach_document(body: DocRequest):
    """A Coach draft (CV, letter, email) as a PDF or Word file."""
    from fastapi.responses import Response
    if not body.text.strip():
        raise HTTPException(400, "Nothing to save.")
    title = body.title.strip()[:80] or "Document"
    slug = "".join(ch if ch.isalnum() else "-" for ch in title.lower()).strip("-")[:50] or "document"
    if body.kind == "docx":
        data, mime, ext = letters.document_docx(body.text[:20000], title), \
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
    else:
        data, mime, ext = letters.document_pdf(body.text[:20000], title), "application/pdf", "pdf"
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{slug}.{ext}"'})


# ---------------------------------------------------------------- dashboard, insights, daily target

@app.get("/api/names")
def name_suggestions():
    """Type-ahead lists: known companies and real-world role titles, plus names already in the tracker."""
    from . import companies, normalize
    rows = [r for r in db.list_jobs() if r.get("status") and r["status"] != "hidden"]
    comps = [n for n, _ in companies.parsed()] + [r["company"].strip() for r in rows if r.get("company")]
    roles = sorted({v for v in normalize.ROLE_SHORT.values()} | set(normalize.COMMON_ROLES)) + \
        [r["title"].strip() for r in rows if r.get("title")]
    uniq = lambda xs: list(dict.fromkeys(x for x in xs if x))[:1500]
    return {"companies": uniq(comps), "roles": uniq(roles)}


@app.get("/api/companies")
def company_directory():
    from .sources import split_name
    comp = settings.load_config().get("companies", {})
    boards = [split_name(e.split("|")[0].split("/")[-1] + ("|" + e.split("|", 1)[1] if "|" in e else ""))[1]
              for lst in comp.values() for e in lst]
    if settings.load_config().get("sources", {}).get("amazon"):
        boards += ["Amazon", "Amazon Web Services"]             # amazon.jobs is searched directly
    return {"companies": links.company_directory(boards)}


@app.get("/api/companies/applied")
def companies_applied():
    return {"companies": [c | links.company_links(c["name"]) | {"tier_label": tiers.LABELS[c["tier"]]} for c in stats.applied_companies()]}


@app.get("/api/stats")
def get_stats(range: str = "", grain: str = "", company: str = "", family: str = ""):
    if not (range or company or family):
        return stats.compute()
    rows = db.list_jobs()
    picked = stats.filter_rows(rows, "all", company, family)            # one company / role, every date
    days = stats.RANGES.get(range or "all")
    if not days:                                                        # "all": from your first application
        first = min((d for d in (stats._day(j) for j in stats.applications(picked)) if d), default=None)
        days = (datetime.now().date() - first).days + 1 if first else None
    s = stats.compute(rows=stats.filter_rows(picked, range or "all"), base=rows, span=days)   # tiles and charts: the range too
    s["explore"] = stats.explore(rows=picked, rng=range or "all", grain=grain or "auto")
    s["usage"] = stats.usage(stats.RANGES.get(range or "all"))
    s["options"] = stats.options(rows)
    s["filter"] = {"range": range, "company": company, "family": family}
    return s


@app.get("/api/tracker/export.xlsx", include_in_schema=False)
def tracker_export():
    """Your tracker and its analysis as an Excel workbook."""
    from fastapi.responses import Response
    from . import workbook
    name = f"jobquest-tracker-{datetime.now():%Y-%m-%d}.xlsx"
    return Response(workbook.build(), headers={"Content-Disposition": f'attachment; filename="{name}"'},
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


ORACLE_CHART_TOOL = (
    "CHART TOOL: when a chart, table or histogram would help, or the user asks for one, add a line of exactly this form, one "
    "per chart (at most 3), and the app draws it from the real data: CHART: {json}\n"
    "Never write numbers in the JSON; the app counts them. Fields:\n"
    '- bar, pie, line or table: {"type":"bar","title":"...","group_by":G,"metric":M,"top":10,"where":{...}}\n'
    '  G: company, role, family, status, source, location, tier, job_type, weekday, month, week, day\n'
    "  M: applications, replies, reached_screening, offers, rejected, ghosted, reply_rate\n"
    '- histogram: {"type":"histogram","title":"...","measure":X,"bins":8,"where":{...}}\n'
    "  X: days_waiting, days_to_reply, applications_per_company, applications_per_day, salary_lpa\n"
    '- where (optional): status, family, tier, source, location, company, role (text in the title), from, to (YYYY-MM-DD)\n'
    "A table shows every metric, with up to 400 rows. Use line for months, weeks or days. Keep any text short when you draw a chart.")
CHART_LINE = re.compile(r"^[ \t*`]*CHART:\s*(\{.*\})[ \t*`]*$", re.M)


def _oracle_charts(text):
    """Turn each CHART: line into real data. A line that can't be built becomes a note, never a made-up chart."""
    charts, failed = [], 0
    for m in CHART_LINE.finditer(text):
        try:
            charts.append(stats.query(json.loads(m.group(1))))
        except (ValueError, TypeError, KeyError):
            failed += 1
    text = CHART_LINE.sub("", text).strip()
    if failed:
        text += f"\n\n(I couldn't draw {failed} of those charts. Try asking in a different way.)"
    return text.strip(), charts[:3]


class OracleChat(BaseModel):
    message: str
    reset: bool = False


@app.post("/api/ai/oracle-chat")
def oracle_chat(body: OracleChat):
    """The dashboard chatbot: answers from your numbers and the oracle's findings (no names, notes or contacts)."""
    if body.reset:
        db.set_setting("oracle_history", [])
        return {"messages": []}
    msg = body.message.strip()[:2000]
    if not msg:
        raise HTTPException(400, "Ask something first.")
    ex = stats.explore(rng="all", grain="auto")
    facts = {"tracker": stats.tracker_facts(), "summary": stats.digest(), "findings": ex["oracle"], "response_rate_by": ex["rates"],
             "days_to_reply": ex["wait"], "jobs_found_by_family": ex["found"]["roles"]}
    system = ("You are the JobQuest.AI oracle: a sharp, encouraging job-search analyst for an entry-level SDE / ML "
              "candidate in India. You have the user's real tracker below, including every company and role title with its "
              "counts, so answer from it directly and name companies. Quote the numbers. If asked for a list, give the full "
              "list; \"companies_listed\" says how many of \"unique_companies\" are included. Otherwise be brief (under 150 "
              "words, short bullets) with concrete, prioritised tips. Ignore any earlier reply in this chat that said it "
              "couldn't see company names. If the data can't answer, say what to track.\n\n" + ORACLE_CHART_TOOL + "\n\nTRACKER DATA (JSON):\n" +
              json.dumps(facts, default=str, separators=(",", ":")))
    history = (db.get_setting("oracle_history") or [])[-10:]
    past = [{"role": m["role"], "content": m["content"]} for m in history]
    try:
        text, used = llm.chat([{"role": "system", "content": system}] + past + [{"role": "user", "content": msg}], 2000,
                              feature="dashboard")
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    text, charts = _oracle_charts(text)
    history = (history + [{"role": "user", "content": msg}, {"role": "assistant", "content": text, "charts": charts}])[-20:]
    db.set_setting("oracle_history", history)
    return {"messages": history, "provider": used}


class Target(BaseModel):
    target: int


@app.put("/api/settings/target")
def set_target(body: Target):
    if not 1 <= body.target <= 100:
        raise HTTPException(400, "Pick a daily target between 1 and 100.")
    db.set_setting("daily_target", body.target)
    return {"count": stats.today_count(), "target": body.target}


@app.post("/api/notify/test")
def notify_test():
    from . import notify
    if not notify.toast("JobQuest.AI", f"Reminders are on: {stats.today_count()} of {stats.target()} applications today."):
        raise HTTPException(400, "Windows didn't accept the notification. Check Settings > System > Notifications.")
    return {"ok": True}


INSIGHTS_SYSTEM = ("You are a sharp, encouraging job-search analyst. You get ONLY aggregate numbers from an entry-level "
                   "SDE / ML job seeker's application tracker in India (no names). Write: 1) a 2-sentence summary, "
                   "2) 3-5 insights that cite the numbers, 3) 3-5 concrete actions for the next 7 days. Use short "
                   "Markdown with '## ' headings and '- ' bullets. Never invent numbers.")


@app.get("/api/ai/insights")
def insights_cached():
    return db.get_setting("ai_insights") or {}


@app.post("/api/ai/insights")
def insights(force: bool = False):
    import json
    cached = db.get_setting("ai_insights") or {}
    if cached.get("at") and not force and \
            datetime.now(timezone.utc) - datetime.fromisoformat(cached["at"]) < timedelta(hours=12):
        return cached
    numbers = json.dumps(stats.digest(), default=str)
    try:
        text, used = llm.chat([{"role": "system", "content": INSIGHTS_SYSTEM},
                               {"role": "user", "content": f"TRACKER NUMBERS (JSON):\n{numbers}"}], 900, feature="dashboard")
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    out = {"text": text, "provider": used, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    db.set_setting("ai_insights", out)
    return out


# ---------------------------------------------------------------- cold email (only to contacts printed in the posting)

class ColdEmail(BaseModel):
    job_id: int
    email: str = ""
    name: str = ""
    role: str = ""


@app.post("/api/ai/cold-email")
def cold_email(body: ColdEmail):
    row = db.get_job(body.job_id)
    if not row:
        raise HTTPException(404, "No such job")
    job = db.public_job(row, detail=True)
    listed = {c["email"].lower() for c in job["contacts"] if c.get("email")}
    to = body.email.strip()
    if to and to.lower() not in listed:
        raise HTTPException(400, "JobQuest.AI only writes to contacts printed in the job posting.")
    pick = profile.pick_resume(job["title"])
    resume = (pick["parsed"].get("text") or "")[:9000] if pick else ""
    if not resume:
        raise HTTPException(400, "Add your resume on the Profile page first.")
    system = ("Write a short cold email (90-150 words) from an entry-level SDE / ML candidate in India to a recruiter or "
              "hiring contact about a specific opening. First line 'Subject: ...' (specific, under 9 words). Greet the "
              "contact by first name if known. One line on why this role and company, two concrete achievements from "
              "the resume that match the job, a clear ask (a quick call or a referral to the hiring manager), and a "
              "polite sign-off with the candidate's name. Use only facts from the resume. No placeholders, no emojis.")
    user = (f"CONTACT: {body.name or 'unknown name'} ({body.role or 'recruiting'}) <{to or 'unknown'}>\n"
            f"ROLE: {job['title']}\nCOMPANY: {job['company']}\nJOB LINK: {job['url']}\n\n"
            f"JOB DESCRIPTION:\n{job['description'][:5000]}\n\nRESUME:\n{resume}")
    try:
        text, used = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], 700, feature="email")
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    return {"text": text, "provider": used, "to": to, "company": job["company"], "role": job["title"],
            "resume_id": pick["id"], "resume": pick["filename"]}


# ---------------------------------------------------------------- Gmail (app password): review-first sync, send on click

class MailAccount(BaseModel):
    address: str
    app_password: str


@app.put("/api/mail/settings")
def mail_connect(body: MailAccount):
    from . import mail
    try:
        mail.connect(body.address, body.app_password)
    except mail.MailError as e:
        raise HTTPException(400, str(e))
    return _mail_state()


@app.delete("/api/mail/settings")
def mail_disconnect():
    from . import mail
    mail.disconnect()
    return _mail_state()


@app.get("/api/mail")
def mail_pending():
    return _mail_state() | {"proposals": db.get_setting("mail_proposals") or []}


@app.post("/api/mail/sync")
def mail_sync():
    from . import mail
    try:
        mail.sync()
    except mail.MailError as e:
        raise HTTPException(400, str(e))
    return mail_pending()


class MailReview(BaseModel):
    ids: list[str]
    apply: bool = True


@app.post("/api/mail/review")
def mail_review(body: MailReview):
    from . import mail
    n = mail.review(set(body.ids), body.apply)
    return mail_pending() | {"applied": n}


class MailSend(BaseModel):
    to: str
    subject: str
    body: str
    job_id: Optional[int] = None
    contact_id: Optional[str] = None
    resume_id: Optional[int] = None          # attach this resume


def _resume_file(rid):
    """The file to attach: the original in your resumes folder if it's still there, else the app's copy."""
    r = next((x for x in db.resumes() if x["id"] == rid), None)
    if not r:
        return None
    src = Path(r["source"]) if r["source"] else None
    return str(src) if src and src.is_file() else r["path"]


def count_cold_email(job_id=None, contact_id=None, to=""):
    """A sent cold email is an application: file it in the tracker as Applied (source "Cold email") so it counts
    toward today's target. Upgrades an existing Saved entry instead of adding a duplicate. Returns the job id."""
    from datetime import date
    today = date.today().isoformat()
    contact = None
    if contact_id:
        contact = db.contact(contact_id)
        if contact:
            db.update_contact(contact_id, sent_at=db.now_iso(), status="sent")
            job_id = job_id or (int(contact["job_id"]) if str(contact.get("job_id") or "").isdigit() else None)
    if job_id and db.get_job(job_id):
        j = db.get_job(job_id)
        if (j["status"] or "") in ("", "saved"):
            db.set_status(job_id, "applied", None)
            db.edit_entry(job_id, {"applied_on": today, "app_source": j.get("app_source") or "Cold email"})
        return job_id
    if not contact or not (contact.get("company") or "").strip():
        return None
    company, role = contact["company"].strip(), (contact.get("job_role") or "").strip() or "Cold outreach"
    who = " ".join(x for x in (contact.get("name"), to or contact.get("email")) if x)
    dup = db.find_duplicate(company, role)
    if dup:
        if dup["status"] in ("", "saved"):
            db.set_status(dup["id"], "applied", None)
            db.edit_entry(dup["id"], {"applied_on": today, "app_source": "Cold email", "contact": who})
        return dup["id"]
    return db.add_manual(company, role, today, contact.get("location") or "", "", "applied",
                         f"Cold email to {who}", {"app_source": "Cold email", "contact": who, "last_update": today})


@app.post("/api/mail/send")
def mail_send(body: MailSend):
    from . import mail
    try:
        att = [_resume_file(body.resume_id)] if body.resume_id else []
        mail.send(body.to, body.subject, body.body, body.job_id, [a for a in att if a])
    except mail.MailError as e:
        raise HTTPException(400, str(e))
    jid = count_cold_email(body.job_id, body.contact_id, body.to)
    return {"sent": True, "to": body.to, "tracked_job_id": jid}


class MarkSent(BaseModel):
    contact_id: Optional[str] = None
    job_id: Optional[int] = None


@app.post("/api/outreach/mark-sent")
def outreach_mark_sent(body: MarkSent):
    """For emails sent from your own mail app: count them like ones sent from here."""
    jid = count_cold_email(body.job_id, body.contact_id)
    if jid is None:
        raise HTTPException(400, "Add the company for this contact first, so it can go in the tracker.")
    return {"tracked_job_id": jid, "contacts": db.contacts()}


# ---------------------------------------------------------------- Cold email desk: contacts from screenshots / text

OUTREACH_EXTRACT = ("You extract hiring contacts from screenshots and pasted text (LinkedIn posts, job ads, recruiter "
                    "profiles, emails). Return JSON only: {\"contacts\": [{\"name\": str, \"email\": str, "
                    "\"title\": str, \"company\": str, \"job_role\": str, \"location\": str, \"notes\": str}]}. "
                    "title = the person's own job title; job_role = the position they are hiring for. "
                    "Copy email addresses EXACTLY as they appear; if no email is visible for a person use \"\" - never "
                    "guess or construct an address. Use \"\" for anything not shown. notes = key requirements or "
                    "instructions in the post (e.g. 'send resume with subject X'), max 200 characters. One entry per "
                    "person or per distinct email.")


class ExtractRequest(BaseModel):
    text: str = ""
    images: list[dict] = []


def _parse_json_obj(text):
    import json
    t = text.strip()
    t = t[t.find("{"):t.rfind("}") + 1] if "{" in t else "{}"
    try:
        return json.loads(t)
    except ValueError:
        return {}


@app.post("/api/ai/extract-contacts")
def extract_contacts(body: ExtractRequest):
    from . import mail
    _, images = _clean_files([], body.images)
    text = body.text.strip()[:12000]
    if not text and not images:
        raise HTTPException(400, "Add screenshots or paste some text first.")
    content = [{"type": "text", "text": "Extract the contacts." + (f"\n\nPASTED TEXT:\n{text}" if text else "")}] + \
              [{"type": "image_url", "image_url": {"url": i["data_url"]}} for i in images]
    try:
        out, used = llm.chat([{"role": "system", "content": OUTREACH_EXTRACT},
                              {"role": "user", "content": content if images else content[0]["text"]}], 1500, feature="email")
    except llm.LLMError as e:
        raise HTTPException(400, str(e) + (" (Screenshots need a vision model: OpenAI, Gemini or Anthropic.)" if images else ""))
    rows, seen = [], set()
    for c in (_parse_json_obj(out).get("contacts") or [])[:40]:
        if not isinstance(c, dict):
            continue
        row = {k: str(c.get(k) or "").strip()[:200] for k in ("name", "email", "title", "company", "job_role", "location", "notes")}
        if row["email"] and not mail.EMAIL_RX.match(row["email"]):
            row["email"] = ""
        # an address must appear in the pasted text or come from a screenshot; drop anything that looks invented
        if row["email"] and text and not images and row["email"].lower() not in text.lower():
            row["email"] = ""
        key = (row["email"].lower() or row["name"].lower(), row["company"].lower())
        if key in seen or not (row["email"] or row["name"]):
            continue
        seen.add(key)
        rows.append(row)
    have = {(r["email"].lower(), r["company"].lower()) for r in db.contacts()}
    import uuid
    db.save_contacts([r | {"id": uuid.uuid4().hex[:10], "added": db.now_iso()} for r in rows
                      if (r["email"].lower(), r["company"].lower()) not in have or not r["email"]])
    return {"found": len(rows), "provider": used, "contacts": db.contacts()}


@app.get("/api/outreach")
def outreach_list():
    return {"contacts": db.contacts()}


class OutreachEdit(BaseModel):
    contacts: list[dict]


@app.put("/api/outreach")
def outreach_save(body: OutreachEdit):
    db.replace_contacts([c for c in body.contacts[:500] if c.get("id")])
    return {"contacts": db.contacts()}


class DraftEdit(BaseModel):
    draft_subject: Optional[str] = None
    draft_body: Optional[str] = None
    resume_id: Optional[int] = None


@app.patch("/api/outreach/{cid}")
def outreach_edit_draft(cid: str, body: DraftEdit):
    if not db.contact(cid):
        raise HTTPException(404, "No such contact")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if fields.get("resume_id") == 0:                       # "None" in the list: send without a resume
        fields["resume_id"] = None
    db.update_contact(cid, **fields, status="", error="")
    return db.contact(cid)


class TemplateDrafts(BaseModel):
    ids: list[str]
    subject: str
    body: str


MERGE = re.compile(r"\{(first_name|name|company|role|title)\}")


def _fill(text, c):
    """Put one contact's details into a template. Returns the text and the fields that were empty."""
    vals = {"first_name": (c["name"].split() or [""])[0], "name": c["name"], "company": c["company"],
            "role": c["job_role"], "title": c["title"]}
    missing = sorted({m for m in MERGE.findall(text) if not vals[m]})
    return MERGE.sub(lambda m: vals[m.group(1)] or ("there" if m.group(1) == "first_name" else f"[{m.group(1)}]"), text), missing


@app.post("/api/outreach/drafts")
def outreach_template_drafts(body: TemplateDrafts):
    """Fill your template for each ticked contact, with the resume tagged for that role. No AI, no sending."""
    if not body.subject.strip() or not body.body.strip():
        raise HTTPException(400, "Write a subject and a message first.")
    out = {}
    for cid in body.ids[:200]:
        c = db.contact(cid)
        if not c or c["sent_at"]:
            continue
        subject, m1 = _fill(body.subject.strip(), c)
        text, m2 = _fill(body.body.strip(), c)
        pick = profile.pick_resume(c["job_role"] or c["title"])
        # a blank company or role leaves a visible [company] that must be fixed; a missing first name just reads "Hi there"
        hard = [m for m in set(m1 + m2) if m != "first_name"]
        db.update_contact(cid, draft_subject=subject[:300], draft_body=text, resume_id=pick["id"] if pick else None,
                          status="needs_edit" if hard else "", error=f"Missing: {', '.join(sorted(hard))}" if hard else "")
        out[cid] = sorted(set(m1 + m2))
    return {"missing": out, "contacts": db.contacts()}


class OutboxStart(BaseModel):
    ids: list[str]
    confirm_count: int              # the number you agreed to in the dialog; must match what would really go


def _deliver(c):
    """Send one reviewed draft with its resume, then file it in the tracker."""
    from . import mail
    att = _resume_file(c["resume_id"]) if c["resume_id"] else None
    job_id = int(c["job_id"]) if str(c["job_id"] or "").isdigit() else None
    mail.send(c["email"], c["draft_subject"], c["draft_body"], job_id, [att] if att else [])
    count_cold_email(job_id, c["id"], c["email"])


@app.get("/api/outbox")
def outbox_status():
    from . import outbox
    return outbox.snapshot()


@app.post("/api/outbox/start")
def outbox_start(body: OutboxStart):
    from . import outbox
    try:
        rows = outbox.ready(body.ids[:200])
        if len(rows) != body.confirm_count:
            raise outbox.OutboxError(f"{len(rows)} emails are ready, not {body.confirm_count}. Check the list and try again.")
        return outbox.start([c["id"] for c in rows], _deliver)
    except outbox.OutboxError as e:
        raise HTTPException(400, str(e))


@app.post("/api/outbox/stop")
def outbox_stop():
    from . import outbox
    return outbox.stop()


class OutreachDraft(BaseModel):
    contact: dict
    tone: str = "warm and professional"
    extra: str = ""
    resume_id: Optional[int] = None          # None: pick the resume tagged for this role
    save: bool = False                       # keep it on the contact, for the batch review


@app.post("/api/ai/outreach-email")
def outreach_email(body: OutreachDraft):
    c = {k: str(body.contact.get(k) or "")[:300] for k in ("name", "email", "title", "company", "job_role", "location", "notes")}
    if not (c["company"] or c["job_role"]):
        raise HTTPException(400, "Add at least the company or the role you're writing about.")
    pick = next((r for r in db.resumes() if r["id"] == body.resume_id), None) or profile.pick_resume(c["job_role"] or c["title"])
    resume = (pick["parsed"].get("text") or "")[:9000] if pick else ""
    if not resume:
        raise HTTPException(400, "Add your resume on the Profile page first.")
    system = ("Write a short cold email (100-160 words) from an entry-level SDE / ML candidate in India (recent MS "
              "graduate) to a recruiter or hiring manager. First line 'Subject: ...' - follow any subject-line "
              "instruction in the notes exactly, otherwise make it specific (role + name + key skill), under 10 words. "
              "Greet by first name if known. One line on the role and why this company; two concrete achievements "
              "from the resume that fit the role; mention the resume is attached or linked; a clear, polite ask; "
              f"sign off with the candidate's name and phone/LinkedIn if in the resume. Tone: {body.tone[:40]}. Use only "
              "facts from the resume. No placeholders, no emojis.")
    user = (f"CONTACT: {c['name'] or 'unknown'} - {c['title'] or 'recruiting'} at {c['company'] or 'unknown company'}\n"
            f"HIRING FOR: {c['job_role'] or 'not stated'} ({c['location'] or 'location not stated'})\n"
            f"NOTES FROM THE POST: {c['notes'] or 'none'}\nEXTRA FROM ME: {body.extra[:1000] or 'none'}\n\nRESUME:\n{resume}")
    try:
        text, used = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], 700, feature="email")
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    if body.save and body.contact.get("id") and db.contact(str(body.contact["id"])):
        first, _, rest = text.partition("\n")
        subject = first[len("subject:"):].strip() if first.lower().startswith("subject:") else ""
        db.update_contact(str(body.contact["id"]), draft_subject=subject[:300], draft_body=(rest if subject else text).strip(),
                          resume_id=pick["id"], status="" if subject else "needs_edit",
                          error="" if subject else "Missing: subject")
    return {"text": text, "provider": used, "resume_id": pick["id"], "resume": pick["filename"]}


@app.post("/api/outreach/from-postings")
def outreach_from_postings():
    """Collect every hiring email printed in the job postings JobQuest.AI has found (never guessed addresses)."""
    import uuid
    have = {c["email"].lower() for c in db.contacts() if c["email"]}
    fresh = []
    for r in db.list_jobs():
        if r["status"] == "hidden":
            continue
        job = db.public_job(r)
        for c in job["contacts"]:
            email = (c.get("email") or "").strip()
            if not email or email.lower() in have:
                continue
            have.add(email.lower())
            fresh.append({"id": uuid.uuid4().hex[:10], "name": c.get("name") or "", "email": email,
                          "title": c.get("role") or "", "company": job["company"], "job_role": job["title"],
                          "location": job["location"] or "", "notes": f"From the posting: {job['url']}",
                          "job_id": str(job["id"]), "added": db.now_iso()})
    db.save_contacts(fresh)
    return {"added": len(fresh), "contacts": db.contacts()}
