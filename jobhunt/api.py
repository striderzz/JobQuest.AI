"""FastAPI app: the JSON API plus the single-page UI in ./static."""
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, db, letters, links, llm, profile, refresh, settings, stats
from .resume import ResumeError

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 5 * 1024 * 1024

app = FastAPI(title="JobQuest.AI", version="1.0", docs_url="/api/docs", redoc_url=None)
app.state.allowed_hosts = None          # set by `serve`; None in tests
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Bootstrap and its icons load from jsDelivr (pinned + SRI-checked in index.html), Montserrat (logo) from Google Fonts.
CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
       "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
       "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
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
    }


@app.get("/api/jobs")
def jobs():
    now = datetime.now(timezone.utc)
    return {"jobs": [db.public_job(r, now) for r in db.list_jobs()]}


@app.get("/api/jobs/{job_id}")
def job(job_id: int):
    r = db.get_job(job_id)
    if not r:
        raise HTTPException(404, "No such job")
    return db.public_job(r, detail=True)


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


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: int, body: JobUpdate):
    from .tracker_import import _date as parse_date
    if not db.get_job(job_id):
        raise HTTPException(404, "No such job")
    fields = body.model_dump(exclude={"status"}, exclude_none=True)
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
    dup = db.find_duplicate(body.company, body.role, body.url)
    jid = db.add_manual(body.company.strip(), body.role.strip(), d.isoformat(), body.location.strip(),
                        body.url.strip(), body.status, body.notes.strip(),
                        {"job_type": body.job_type.strip(), "app_source": body.app_source.strip(),
                         "next_step": body.next_step.strip(), "contact": body.contact.strip()})
    out = db.public_job(db.get_job(jid))
    out["possible_duplicate"] = db.describe(dup) if dup else None
    return out


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
        db.add_manual(e["company"], e["role"], e["date"], e["location"], e["url"], e["status"], e["notes"], e["extra"])
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


@app.post("/api/backups")
def make_backup():
    from . import backup
    return backup.create("manual")


@app.get("/api/backups/{name}", include_in_schema=False)
def download_backup(name: str):
    from . import backup
    try:
        p = backup.path_of(name)
    except backup.BackupError as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, media_type="application/zip", filename=p.name)


def _restore(path):
    from . import backup
    if refresh.PROGRESS.snapshot()["running"]:
        raise HTTPException(409, "Wait for the current search to finish, then restore.")
    try:
        return {"restored": backup.restore(path)}
    except backup.BackupError as e:
        raise HTTPException(400, str(e))


@app.post("/api/backups/{name}/restore")
def restore_backup(name: str):
    from . import backup
    try:
        p = backup.path_of(name)
    except backup.BackupError as e:
        raise HTTPException(404, str(e))
    return _restore(p)


@app.post("/api/backups/restore-upload")
async def restore_upload(file: UploadFile = File(...)):
    import tempfile
    data = await file.read(200 * 1024 * 1024 + 1)
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(400, "That file is too large to be a JobQuest.AI backup.")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(data)
    try:
        return _restore(Path(tmp.name))
    finally:
        Path(tmp.name).unlink(missing_ok=True)


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
    about: Optional[str] = None


@app.put("/api/profile")
def save_profile(body: Preferences):
    profile.save_preferences({k: v for k, v in body.model_dump().items() if v is not None})
    return {"profile": profile.summary()}


@app.post("/api/refresh", status_code=202)
def start_refresh():
    if refresh.PROGRESS.snapshot()["running"]:
        raise HTTPException(409, "A refresh is already running.")
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


class ProviderKey(BaseModel):
    api_key: Optional[str] = None
    model: Optional[str] = None


@app.put("/api/ai/providers/{pid}")
def ai_provider_save(pid: str, body: ProviderKey):
    if pid not in llm.PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    try:
        model = llm.test(pid, (body.api_key or "").strip() or None, (body.model or "").strip() or None)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "model": model, **llm.status()}


@app.delete("/api/ai/providers/{pid}")
def ai_provider_remove(pid: str):
    if pid not in llm.PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    llm.remove(pid)
    return llm.status()


class ProviderOrder(BaseModel):
    order: list[str]


@app.put("/api/ai/order")
def ai_order(body: ProviderOrder):
    db.set_setting("llm_order", [p for p in body.order if p in llm.PROVIDERS])
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
        text, used = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], 1400,
                              prefer="ollama")
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
                "user's message; read them carefully.\n\n")


def _tracker_digest():
    rows = [j for j in db.list_jobs() if j["status"] and j["status"] != "hidden"]
    by = {}
    for j in rows:
        by[j["status"]] = by.get(j["status"], 0) + 1
    week = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    recent = sum(1 for j in rows if (j["applied_on"] or (j["status_at"] or "")[:10]) >= week and j["status"] != "saved")
    return f"TRACKER: {len(rows)} entries; by status {by}; applied in the last 7 days: {recent}."


class ChatMessage(BaseModel):
    message: str
    files: list[dict] = []          # [{"name", "text"}] from /api/ai/context-file
    images: list[dict] = []         # [{"name", "data_url"}], resized in the browser


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


@app.get("/api/ai/chat")
def chat_history():
    msgs = db.get_setting("coach_history") or []
    return {"messages": [{k: v for k, v in m.items() if k not in ("files", "images")} |
                         {"attached": [f["name"] for f in m.get("files", []) + m.get("images", [])]} for m in msgs]}


@app.post("/api/ai/chat")
def chat(body: ChatMessage):
    msg = body.message.strip()[:4000]
    files, images = _clean_files(body.files, body.images)
    if not msg and not files and not images:
        raise HTTPException(400, "Type a question first.")
    msg = msg or "Here are my files. What can you tell me, and how should I use them?"
    history = db.get_setting("coach_history") or []
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
    context = COACH_SYSTEM + profile.candidate_block() + "\n" + _tracker_digest()
    try:
        text, used = llm.chat([{"role": "system", "content": context}] + convo, 2500)
    except llm.LLMError as e:
        hint = " (Photos need a vision model: OpenAI, Gemini or Anthropic.)" if images else ""
        raise HTTPException(400, str(e) + hint)
    history += [turn, {"role": "assistant", "content": text, "provider": used}]
    history = history[-30:]
    for m in history[:-6]:                                      # keep the stored history light
        m.pop("images", None)
    db.set_setting("coach_history", history)
    return chat_history() | {"reply": text, "provider": used}


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


@app.delete("/api/ai/chat")
def chat_clear():
    db.set_setting("coach_history", [])
    return {"messages": []}


# ---------------------------------------------------------------- dashboard, insights, daily target

@app.get("/api/stats")
def get_stats():
    return stats.compute()


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
                               {"role": "user", "content": f"TRACKER NUMBERS (JSON):\n{numbers}"}], 900)
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
    resume = _resume_text()
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
        text, used = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], 700)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    return {"text": text, "provider": used, "to": to, "company": job["company"], "role": job["title"]}


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


def count_cold_email(job_id=None, contact_id=None, to=""):
    """A sent cold email is an application: file it in the tracker as Applied (source "Cold email") so it counts
    toward today's target. Upgrades an existing Saved entry instead of adding a duplicate. Returns the job id."""
    from datetime import date
    today = date.today().isoformat()
    contact = None
    if contact_id:
        contacts = db.get_setting("outreach_contacts") or []
        contact = next((c for c in contacts if c.get("id") == contact_id), None)
        if contact:
            contact["sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.set_setting("outreach_contacts", contacts)
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
        mail.send(body.to, body.subject, body.body, body.job_id)
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
    return {"tracked_job_id": jid, "contacts": db.get_setting("outreach_contacts") or []}


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
                              {"role": "user", "content": content if images else content[0]["text"]}], 1500)
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
    saved = db.get_setting("outreach_contacts") or []
    have = {(r.get("email", "").lower(), r.get("company", "").lower()) for r in saved}
    import uuid
    for r in rows:
        if (r["email"].lower(), r["company"].lower()) not in have or not r["email"]:
            saved.insert(0, r | {"id": uuid.uuid4().hex[:10], "added": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    db.set_setting("outreach_contacts", saved[:500])
    return {"found": len(rows), "provider": used, "contacts": saved[:500]}


@app.get("/api/outreach")
def outreach_list():
    return {"contacts": db.get_setting("outreach_contacts") or []}


class OutreachEdit(BaseModel):
    contacts: list[dict]


@app.put("/api/outreach")
def outreach_save(body: OutreachEdit):
    keep = []
    for c in body.contacts[:500]:
        row = {k: str(c.get(k) or "").strip()[:300] for k in ("id", "name", "email", "title", "company", "job_role",
                                                              "location", "notes", "added", "sent_at", "draft", "job_id")}
        if row["id"]:
            keep.append(row)
    db.set_setting("outreach_contacts", keep)
    return {"contacts": keep}


class OutreachDraft(BaseModel):
    contact: dict
    tone: str = "warm and professional"
    extra: str = ""


@app.post("/api/ai/outreach-email")
def outreach_email(body: OutreachDraft):
    c = {k: str(body.contact.get(k) or "")[:300] for k in ("name", "email", "title", "company", "job_role", "location", "notes")}
    if not (c["company"] or c["job_role"]):
        raise HTTPException(400, "Add at least the company or the role you're writing about.")
    resume = _resume_text()
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
        text, used = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], 700)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    return {"text": text, "provider": used}


@app.post("/api/outreach/from-postings")
def outreach_from_postings():
    """Collect every hiring email printed in the job postings JobQuest.AI has found (never guessed addresses)."""
    import uuid
    saved = db.get_setting("outreach_contacts") or []
    have = {(c.get("email") or "").lower() for c in saved if c.get("email")}
    added = 0
    for r in db.list_jobs():
        if r["status"] == "hidden":
            continue
        job = db.public_job(r)
        for c in job["contacts"]:
            email = (c.get("email") or "").strip()
            if not email or email.lower() in have:
                continue
            have.add(email.lower())
            saved.insert(0, {"id": uuid.uuid4().hex[:10], "name": c.get("name") or "", "email": email,
                             "title": c.get("role") or "", "company": job["company"], "job_role": job["title"],
                             "location": job["location"] or "", "notes": f"From the posting: {job['url']}"[:300],
                             "job_id": str(job["id"]), "added": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            added += 1
    db.set_setting("outreach_contacts", saved[:500])
    return {"added": added, "contacts": saved[:500]}
