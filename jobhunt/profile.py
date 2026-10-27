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


def add_resume(data, filename, label=None, ai_client=None):
    """Store an uploaded resume, parse it, and (with a key) let the AI read it too."""
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
    rid = db.add_resume(label, filename, path, parsed, insights)
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
        "preferences": prefs, "resumes": items,
    }

