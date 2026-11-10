"""One refresh: search every source, filter to India + entry level, rank, store, then let the AI check new jobs."""
import concurrent.futures as cf
import os
import threading
import time
from datetime import datetime, timezone

from . import ai as AI
from . import db, filters, llm, match, pipeline, profile, queries, settings


class Progress:
    """What the UI's progress bar shows. Shared between the refresh thread and the API."""

    def __init__(self):
        self._lock = threading.Lock()
        self.state = {"running": False, "phase": "", "detail": "", "done": 0, "total": 0,
                      "started": None, "finished": None, "error": None, "result": None, "models": []}

    def update(self, **kw):
        with self._lock:
            self.state.update(kw)

    def lane(self, key, **kw):
        """One model's row in the progress panel."""
        with self._lock:
            lanes = [dict(x) for x in self.state["models"]]
            for x in lanes:
                if x["key"] == key:
                    x.update(kw)
                    break
            else:
                lanes.append({"key": key} | kw)
            self.state["models"] = lanes

    def snapshot(self):
        with self._lock:
            s = dict(self.state)
        s["percent"] = overall_percent(s)
        return s


# (phase prefix, start %, end %) - one overall bar across the whole search
PHASES = [("Starting", 0, 2), ("AI is reading", 2, 5), ("Searching", 5, 70), ("Filtering", 70, 75),
          ("AI is checking", 75, 90), ("Comparing", 90, 98), ("Saving", 98, 99), ("Done", 100, 100)]


def overall_percent(s):
    if not s.get("running"):
        return 100 if s.get("phase") == "Done" else 0
    phase = s.get("phase") or ""
    for prefix, a, b in PHASES:
        if phase.startswith(prefix):
            frac = (s.get("done") or 0) / s["total"] if s.get("total") else 0
            return round(a + (b - a) * min(1, frac))
    return 75


PROGRESS = Progress()
_IN_PROCESS = threading.Lock()


class Busy(Exception):
    pass


class _FileLock:
    """Stops the daily scheduled refresh and a click in the app from running at the same time."""
    path = settings.DATA / "refresh.lock"

    def __enter__(self):
        settings.ensure_dirs()
        try:
            if self.path.exists() and (time.time() - self.path.stat().st_mtime > 30 * 60 or not _owner_alive(self.path)):
                self.path.unlink()                       # left behind by a crashed or closed app
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
        except FileExistsError:
            raise Busy("A refresh is already running.") from None
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _owner_alive(lock_path):
    try:
        pid = int(lock_path.read_text().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":                 # os.kill(pid, 0) would terminate the process on Windows, so ask the kernel
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == 259                 # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run(progress=PROGRESS):
    if not _IN_PROCESS.acquire(blocking=False):
        raise Busy("A refresh is already running.")
    run_id = None
    try:
        with _FileLock():
            progress.update(running=True, phase="Starting", detail="", done=0, total=0, started=db.now_iso(),
                            finished=None, error=None, result=None, models=[])
            cfg = settings.load_config()
            prefs = profile.preferences()
            s = cfg["search"]
            s["roles"], s["include_internships"] = prefs["roles"], prefs["include_internships"]
            s["max_years"] = min(int(prefs["max_years"]), settings.MAX_YEARS_CHOICE)
            client = AI.client_from_settings(cfg)
            if client:
                progress.update(phase="AI is reading your resume")
                profile.enrich_missing(client)
            profiles = profile.scoring_profiles()
            if not profiles:
                raise RuntimeError("Add your resume on the Profile page first.")

            now = datetime.now(timezone.utc)
            run_at = now.isoformat(timespec="seconds")
            run_id = db.start_run()
            ctx = {"now": now, "days": s["days"], "roles": s["roles"], "keys": cfg["keys"],
                   "regions": profile.preferences().get("regions") or []}
            if cfg["search"].get("discover_per_search", 40):
                progress.update(phase="Looking for your tracker companies' job boards")
                try:
                    from . import discovery, stats as _stats, sources as _sources
                    known = {_sources.split_name(e)[0] for v in cfg["companies"].values() for e in v}
                    discovery.discover([c["name"] for c in _stats.applied_companies()], known,
                                       int(cfg["search"].get("discover_per_search", 40)))
                except Exception as e:                       # discovery is a bonus: it must never stop a search
                    print(f"board discovery: {e}")
            progress.update(phase="Planning searches")
            skills = list(dict.fromkeys(k for p in profiles for k in p.get("skills", [])))
            ctx["queries"] = queries.plan(s["roles"], skills[:12], use_ai=bool(client) and not llm.over_limit())

            progress.update(phase="Searching job boards")
            raw, status = pipeline.fetch_all(
                cfg, ctx, lambda d, t, n: progress.update(done=d, total=t, detail=f"{n:,} postings so far"))

            progress.update(phase="Filtering to India and entry level", done=0, total=0, detail=f"{len(raw):,} postings")
            jobs, stats = pipeline.select(raw, cfg, ctx, profiles, allow_unstated=bool(client))
            for j in jobs:
                j.cities = filters.india_cities(j.location)
            match.score_all(jobs, profiles, now, s["days"])
            for j in jobs:                    # India first: a job abroad ranks below an equal one at home
                if filters.is_abroad(j.remote):
                    j.score = max(0.0, j.score - 15)
            new = db.upsert_jobs(jobs, run_at)

            db.recheck_rejections(lambda a, remote: AI.rejected(a, s["max_years"], filters.is_abroad(remote)))  # cached verdicts follow rule changes
            checked, ai_error = 0, None
            none = {"checked": 0, "cost": 0, "in": 0, "out": 0}
            if client:
                checked, ai_error = _ai_pass(client, cfg, s["max_years"], progress)
            deep = _deep_pass(client, cfg, s["max_years"], progress) if client and not ai_error else none
            compared = _compare_pass(client, cfg, s["max_years"], progress) if client else none
            for k in compared:
                compared[k] += deep[k]
            db.combine_scores(AI.sig_for(client)) if client else None       # every model's verdict counts toward the score
            contacts_added = db.collect_posting_contacts()                  # hiring emails printed in postings join the queue

            cost = ((client.cost() or 0) + compared["cost"]) if client else None
            result = {"raw": len(raw), "candidates": stats["after_title_location_date"], "kept": len(jobs), "new": new,
                      "contacts_added": contacts_added,
                      "ai_checked": checked, "ai_compared": compared["checked"], "ai_cost_usd": cost, "ai_error": ai_error,
                      "failed_sources": sorted(k for k, v in status.items() if not isinstance(v, int))}
            db.finish_run(run_id, ok=1, raw=len(raw), candidates=result["candidates"], kept=len(jobs), new=new,
                          ai_checked=checked + compared["checked"], ai_in=(client.tokens_in if client else 0) + compared["in"],
                          ai_out=(client.tokens_out if client else 0) + compared["out"], ai_cost=cost or 0,
                          sources=status, error=ai_error)
            try:
                from . import backup
                backup.auto_daily()               # the daily task also keeps the daily backup going
            except Exception:
                pass
            progress.update(running=False, phase="Done", finished=db.now_iso(), result=result, done=0, total=0)
            return result
    except Exception as e:
        progress.update(running=False, phase="Failed", error=str(e), finished=db.now_iso())
        if run_id:
            db.finish_run(run_id, ok=0, error=str(e))
        raise
    finally:
        _IN_PROCESS.release()


def _ai_pass(client, cfg, max_years, progress):
    sig = AI.sig_for(client)
    todo = db.jobs_for_ai(sig, cfg["ai"]["max_jobs_per_refresh"])
    block = profile.candidate_block()
    progress.update(phase="AI is checking jobs", done=0, total=len(todo), detail=client.model)
    key = f"{client.provider}/{client.model}"
    progress.lane(key, label=client.label, model=client.model, done=0, total=len(todo), state="running", main=True)
    stop = threading.Event()             # a bad key fails every call: stop after the first
    errors = []

    def one(row):
        if stop.is_set():
            return False
        try:
            res = client.assess(block, row, cache_key=f"jobhunt-{sig}")
            rejected = AI.rejected(res, max_years, filters.is_abroad(row.get("remote")))
            db.save_ai(row["id"], sig, res, rejected)
            db.save_verdict(row["id"], client.provider, client.model, sig, res, rejected)
            return True
        except AI.AIError as e:
            errors.append(str(e))
            db.save_ai(row["id"], sig, None, False, error=str(e))
            if "API key" in str(e) or "isn't available" in str(e) or "quota" in str(e):
                stop.set()
            return False

    checked = 0
    if client.provider == "anthropic" and db.get_setting("screen_mode") == "saver" and len(todo) >= 20:
        # Saver: one batch at half price, then any job it couldn't do is checked the normal way below
        progress.update(phase="Claude batch (half price) is checking jobs")
        try:
            got = client.assess_many(block, todo, lambda d, t: (progress.update(done=d, total=t), progress.lane(key, done=d)))
        except AI.AIError as e:
            got, _ = {}, errors.append(str(e))
        for row in todo:
            if row["id"] in got:
                rejected = AI.rejected(got[row["id"]], max_years, filters.is_abroad(row.get("remote")))
                db.save_ai(row["id"], sig, got[row["id"]], rejected)
                db.save_verdict(row["id"], client.provider, client.model, sig, got[row["id"]], rejected)
        checked = len(got)
        todo = [row for row in todo if row["id"] not in got]
    with cf.ThreadPoolExecutor(max(1, int(cfg["ai"]["concurrency"]))) as ex:
        for i, ok in enumerate(ex.map(one, todo), 1):
            checked += ok
            progress.update(done=i)
            progress.lane(key, done=i)
    progress.lane(key, state="failed" if errors and not checked else "done", error=errors[0][:160] if errors else "")
    return checked, (errors[0] if errors else None)


def _deep_pass(main, cfg, max_years, progress):
    """When Claude screens, Opus re-reads the best jobs Haiku found and its verdict becomes the main one."""
    totals = {"checked": 0, "cost": 0.0, "in": 0, "out": 0}
    limit = int(cfg["ai"].get("deep_check_jobs", 100))
    if main.provider != "anthropic" or limit <= 0 or llm.over_limit():
        return totals
    deep = AI.client_for("anthropic", None, cfg, feature="deep_check")
    if deep.model == main.model:
        return totals
    sig, block = AI.sig_for(main), profile.candidate_block()
    todo = db.jobs_for_compare(deep.provider, deep.model, sig, limit)
    key = f"{deep.provider}/{deep.model}"
    progress.update(phase="Claude Opus is double-checking the best jobs", done=0, total=len(todo), detail=deep.model)
    progress.lane(key, label="Claude Opus", model=deep.model, done=0, total=len(todo), state="running", main=False)
    stop, errors = threading.Event(), []

    def one(row):
        if stop.is_set():
            return False
        try:
            res = deep.assess(block, row)
        except AI.AIError as e:
            errors.append(str(e))
            stop.set()                   # keep Haiku's verdicts rather than retrying a failing model
            return False
        rejected = AI.rejected(res, max_years, filters.is_abroad(row.get("remote")))
        db.save_ai(row["id"], sig, res, rejected)
        db.save_verdict(row["id"], deep.provider, deep.model, sig, res, rejected)
        return True

    with cf.ThreadPoolExecutor(max(1, int(cfg["ai"]["concurrency"]))) as ex:
        for i, ok in enumerate(ex.map(one, todo), 1):
            totals["checked"] += ok
            progress.update(done=i)
            progress.lane(key, done=i)
    progress.lane(key, state="failed" if errors and not totals["checked"] else "done", error=errors[0][:160] if errors else "")
    totals.update(cost=deep.cost() or 0, **{"in": deep.tokens_in, "out": deep.tokens_out})
    return totals


def _compare_pass(main, cfg, max_years, progress):
    """Each extra model you picked for job search reads the best jobs too. Their scores are averaged into the job's
    score afterwards (db.combine_scores), and a big disagreement is flagged on the card."""
    picks = [c for c in llm.compare_choices() if (c["provider"], c.get("model") or "") != (main.provider, main.model)]
    ready = llm.configured()
    picks = [c for c in picks if c["provider"] in ready]
    totals = {"checked": 0, "cost": 0.0, "in": 0, "out": 0}
    if not picks:
        return totals
    limit, block, lock = llm.compare_limit(), profile.candidate_block(), threading.Lock()
    progress.update(phase="Comparing models", done=0, total=0, detail=f"{len(picks)} extra model(s)")

    def lane(pick):
        client = AI.client_for(pick["provider"], pick.get("model"), cfg)
        key, sig = f"{client.provider}/{client.model}", AI.sig_for(client)
        todo = db.jobs_for_compare(client.provider, client.model, sig, limit)
        progress.lane(key, label=client.label, model=client.model, done=0, total=len(todo), state="running", main=False)
        n = 0
        for i, row in enumerate(todo, 1):
            try:
                res = client.assess(block, row, cache_key=f"jobhunt-{sig}")
            except AI.AIError as e:                      # one model failing never stops the others or the search
                progress.lane(key, state="failed", error=str(e)[:160])
                break
            db.save_verdict(row["id"], client.provider, client.model, sig, res, AI.rejected(res, max_years, filters.is_abroad(row.get("remote"))))
            n += 1
            progress.lane(key, done=i)
        else:
            progress.lane(key, state="done")
        with lock:
            totals["checked"] += n
            totals["cost"] += client.cost() or 0
            totals["in"] += client.tokens_in
            totals["out"] += client.tokens_out

    with cf.ThreadPoolExecutor(len(picks)) as ex:
        list(ex.map(lane, picks))
    return totals


def assess_one(job_id):
    """Check one job with the AI right now (the 'Ask AI' button)."""
    cfg = settings.load_config()
    client = AI.client_from_settings(cfg)
    if not client:
        raise AI.AIError(llm.NO_KEY)
    row = db.get_job(job_id)
    if not row:
        raise KeyError(job_id)
    sig = AI.sig_for(client)
    res = client.assess(profile.candidate_block(), row, cache_key=f"jobhunt-{sig}")
    rejected = AI.rejected(res, min(int(profile.preferences()["max_years"]), settings.MAX_YEARS_CHOICE), filters.is_abroad(row.get("remote")))
    db.save_ai(job_id, sig, res, rejected)
    db.save_verdict(job_id, client.provider, client.model, sig, res, rejected)
    db.add_manual_spend(client.tokens_in, client.tokens_out, client.cost() or 0)
    return db.get_job(job_id)
