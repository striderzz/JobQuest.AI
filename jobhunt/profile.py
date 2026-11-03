"""Your profile: uploaded resumes (parsed by regex, enriched by the AI) plus details you set in the app."""
import hashlib
import json
import re
import time

from . import ai, db, settings
from . import resume as R
from .skills import find_skills

DEFAULTS = {"current_city": "", "phone": "", "preferred_cities": [], "roles": ["SDE", "ML"],
            "include_internships": True, "about": ""}


def preferences():
    p = dict(DEFAULTS)
    p.update(db.get_setting("profile", {}) or {})
    return p


def save_preferences(update):
    p = preferences()
    for k in DEFAULTS:
        if k in update:
            p[k] = update[k]
    p["roles"] = [r for r in p["roles"] if r in ("SDE", "ML")] or ["SDE", "ML"]
    db.set_setting("profile", p)
    return p


MAX_RESUMES = 10
TARGET_ROLES = ["Software Engineer", "Machine Learning Engineer", "AI Engineer", "Data Scientist", "Data Engineer",
                "Full Stack Engineer", "Backend Engineer", "Frontend Engineer", "DevOps / Cloud Engineer", "Other"]
# which resume role suits which job family (stats.role_family names)
FAMILY_ROLES = {"ML / AI": ["Machine Learning Engineer", "AI Engineer", "Data Scientist"],
                "Data science": ["Data Scientist", "Machine Learning Engineer"],
                "Data engineering": ["Data Engineer", "Software Engineer"],
                "Frontend / full-stack": ["Full Stack Engineer", "Frontend Engineer", "Software Engineer"],
                "DevOps / cloud": ["DevOps / Cloud Engineer", "Backend Engineer", "Software Engineer"],
                "Software engineering": ["Software Engineer", "Backend Engineer", "Full Stack Engineer"]}


def guess_role(filename, parsed=None):
    t = re.sub(r"[_\-.]+", " ", filename.lower())
    for rx, role in ((r"\b(ml|mle|machine)", "Machine Learning Engineer"), (r"\b(ai|genai|llm)\b", "AI Engineer"),
                     (r"\b(ds|data ?scien)", "Data Scientist"), (r"\bdata ?eng", "Data Engineer"),
                     (r"\b(full ?stack|mern)", "Full Stack Engineer"), (r"\b(front ?end|react)", "Frontend Engineer"),
                     (r"\b(back ?end)", "Backend Engineer"), (r"\b(devops|cloud|sre)", "DevOps / Cloud Engineer"),
                     (r"\b(sde|swe|software|dev)", "Software Engineer")):
        if re.search(rx, t):
            return role
    scores = (parsed or {}).get("role_scores") or {}
    return "Machine Learning Engineer" if scores and max(scores, key=scores.get) == "ML" else "Software Engineer"


def resumes_dir():
    return db.get_setting("resumes_dir") or str(settings.DEFAULT_RESUMES_DIR)


def scan_folder(ai_client=None):
    """Mirror the resumes folder: import new or changed PDF/DOCX files (newest first, 10 in all) and drop the ones
    whose file is gone. Uploaded resumes (no source file) are left alone."""
    from pathlib import Path
    folder = Path(resumes_dir())
    if not folder.is_dir():
        return {"folder": str(folder), "added": 0, "removed": 0, "error": "Folder not found"}
    files = sorted((p for p in folder.iterdir() if p.suffix.lower() in (".pdf", ".docx") and not p.name.startswith("~$")),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    have = {r["source"]: r for r in db.resumes() if r["source"]}
    added = removed = 0
    errors = []
    for src, r in have.items():
        if not Path(src).exists() or Path(src).parent != folder:
            _drop(r)
            removed += 1
    room = MAX_RESUMES - len([r for r in db.resumes()])
    for p in files:
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()[:16]
        old = have.get(str(p))
        if old and old["sha"] == sha:
            continue
        if old:                                      # file changed: keep its role tag, re-read it
            _drop(old)
            room += 1
        if room <= 0:
            continue
        try:
            add_resume(data, p.name, label=p.stem, ai_client=ai_client, source=str(p), sha=sha,
                       target_role=old["target_role"] if old else None)
            added += 1
            room -= 1
        except R.ResumeError as e:
            errors.append(f"{p.name}: {e}")
    return {"folder": str(folder), "added": added, "removed": removed, "errors": errors,
            "skipped": max(0, len(files) - MAX_RESUMES)}


def _drop(r):
    from pathlib import Path
    db.delete_resume(r["id"])
    p = Path(r["path"])
    if p.parent == settings.RESUMES:
        p.unlink(missing_ok=True)


def pick_resume(title="", jd=""):
    """The resume to send for a role: one tagged for the job's family, else the first. Returns a db.resumes() row."""
    from .stats import role_family
    rs = db.resumes()
    if not rs:
        return None
    want = FAMILY_ROLES.get(role_family(title), [])
    for role in want:
        for r in sorted(rs, key=lambda r: r["added"], reverse=True):
            if (r["target_role"] or guess_role(r["filename"], r["parsed"])) == role:
                return r
    return rs[0]


def add_resume(data, filename, label=None, ai_client=None, source="", sha="", target_role=None):
    """Store an uploaded resume, parse it, and (with a key) let the AI read it too."""
    if len(db.resumes()) >= MAX_RESUMES:
        raise R.ResumeError(f"You already have {MAX_RESUMES} resumes. Remove one first.")
    settings.ensure_dirs()
    safe = re.sub(r"[^\w.\- ]", "_", filename)[-80:]
    path = settings.RESUMES / f"{int(time.time())}-{safe}"
    path.write_bytes(data)
    try:
        parsed = R.parse(path)
    except R.ResumeError:
        path.unlink(missing_ok=True)
        raise
    insights, ai_error = None, None
    if ai_client:
        try:
            insights = ai_client.read_resume(parsed["text"])
        except Exception as e:                       # the regex profile is still usable
            ai_error = str(e)
    label = (label or "").strip() or parsed["label"]
    rid = db.add_resume(label, filename, path, parsed, insights, source, sha or hashlib.sha256(data).hexdigest()[:16],
                        target_role or guess_role(filename, parsed))
    return rid, ai_error


def enrich_missing(ai_client):
    """Have the AI read any resume it hasn't seen yet (e.g. uploaded before a key was added)."""
    for r in db.resumes():
        if r["ai"] is None:
            try:
                db.update_resume_ai(r["id"], ai_client.read_resume(r["parsed"]["text"]))
            except Exception:
                pass


def scoring_profiles():
    """One profile dict per resume, in the shape match.py expects, with the AI's reading merged in."""
    out = []
    for r in db.resumes():
        p = dict(r["parsed"])
        p["label"] = r["label"]
        ai = r["ai"]
        if ai:
            canon = set(find_skills(", ".join(ai.get("skills", []))))
            p["skills"] = list(dict.fromkeys(p["skills"] + sorted(canon)))
            p["fulltime_months"] = ai.get("fulltime_experience_months", p.get("fulltime_months", 0))
            p["internship_months"] = ai.get("internship_months", p.get("internship_months", 0))
            rs = dict(p["role_scores"])
            for role in ai.get("target_roles", []):
                rs[role] = max(rs.get(role, 0), 0.85)
            p["role_scores"] = rs
        out.append(p)
    return out


def signature(model=""):
    """Changes whenever anything the AI's job verdicts depend on changes, so those get re-checked."""
    prefs = preferences()
    blob = json.dumps([[(r["id"], r["ai"]) for r in db.resumes()],
                       {k: prefs[k] for k in ("roles", "current_city", "preferred_cities", "about")}, model,
                       ai.SCHEMA_VERSION],
                      sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def candidate_block():
    """The candidate description sent with every job check (identical across a refresh, so it caches)."""
    prefs = preferences()
    rs = db.resumes()
    lines = ["CANDIDATE"]
    if prefs["current_city"]:
        lines.append(f"Lives in: {prefs['current_city']}, India")
    if prefs["preferred_cities"]:
        lines.append("Wants to work in: " + ", ".join(prefs["preferred_cities"]) + " (or remote within India)")
    lines.append("Target roles: " + ", ".join(prefs["roles"]))
    if prefs["about"]:
        lines.append("Note: " + prefs["about"])
    for r in rs:
        p, ai = r["parsed"], r["ai"] or {}
        edu = "; ".join(f"{e['degree']} {e['field']}, {e['school']} ({e['end']})" for e in ai.get("education", [])) or \
            "; ".join(f"{e['degree']} {e['field']}".strip() for e in p.get("education", []))
        lines += [f"\nResume \"{r['label']}\":",
                  f"  Headline: {ai.get('headline') or p.get('headline', '')}",
                  f"  Education: {edu}",
                  f"  Experience: {ai.get('fulltime_experience_months', p.get('fulltime_months', 0))} months full-time, "
                  f"{ai.get('internship_months', p.get('internship_months', 0))} months internships",
                  "  Roles/projects: " + "; ".join(e["name"] for e in p.get("entries", [])[:8]),
                  "  Skills: " + ", ".join((ai.get("skills") or p.get("skills", []))[:40])]
        if ai.get("strengths"):
            lines.append("  Strengths: " + " | ".join(ai["strengths"][:5]))
    return "\n".join(lines)


def summary():
    prefs = preferences()
    rs = db.resumes()
    first = rs[0]["parsed"] if rs else {}
    items = []
    for r in rs:
        p, ai = r["parsed"], r["ai"] or {}
        lean = max(p["role_scores"], key=p["role_scores"].get) if p.get("role_scores") else ""
        items.append({
            "id": r["id"], "label": r["label"], "filename": r["filename"], "added": r["added"],
            "target_role": r["target_role"] or guess_role(r["filename"], p), "from_folder": bool(r["source"]),
            "ai_read": bool(r["ai"]), "headline": ai.get("headline") or p.get("headline", ""),
            "education": ai.get("education") or [{"degree": e["degree"], "field": e["field"], "school": e["school"],
                                                  "end": ""} for e in p.get("education", [])],
            "graduation": p.get("graduation", ""),
            "fulltime_months": ai.get("fulltime_experience_months", p.get("fulltime_months", 0)),
            "internship_months": ai.get("internship_months", p.get("internship_months", 0)),
            "skills": (ai.get("skills") or p.get("skills", []))[:40],
            "roles": ai.get("target_roles") or p.get("roles", []), "lean": lean,
            "strengths": ai.get("strengths", []),
            "entries": [e["name"] for e in p.get("entries", [])],
        })
    return {
        "name": first.get("name", ""), "email": first.get("email", ""),
        "phone": prefs["phone"] or first.get("phone", ""),
        "city": prefs["current_city"] or "", "resume_location": first.get("location", ""),
        "linkedin": first.get("linkedin", ""), "github": first.get("github", ""),
        "resumes_dir": resumes_dir(), "target_roles": TARGET_ROLES, "max_resumes": MAX_RESUMES,
        "preferences": prefs, "resumes": items,
    }

