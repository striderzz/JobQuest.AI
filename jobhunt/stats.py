"""Job-hunt statistics, computed in plain Python from the tracker (the AI only ever sees these numbers)."""
import re
from collections import Counter
from datetime import date, timedelta

from . import db, tiers

DEFAULT_TARGET = 10
STAGE = {"applied": 1, "screening": 2, "oa": 3, "interviewing": 4, "offer": 5}      # how far an application got
RESPONDED = {"screening", "oa", "interviewing", "offer", "rejected"}                 # the company answered
ROLE_FAMILIES = [("ML / AI", r"\b(ml|machine learning|ai|artificial intelligence|deep learning|nlp|computer vision|llm|genai|generative)\b"),
                 ("Data science", r"\bdata scien|\bresearch scien|\banalytics\b|\bdata analyst"),
                 ("Data engineering", r"\bdata engineer|\betl\b|\bbig data|\bspark\b"),
                 ("Frontend / full-stack", r"\bfront.?end|\bfull.?stack|\breact\b|\bui engineer|\bweb\b"),
                 ("DevOps / cloud", r"\bdevops|\bsre\b|\bsite reliability|\bcloud|\bplatform engineer|\binfra"),
                 ("Mobile", r"\bandroid|\bios\b|\bmobile|\bflutter"),
                 ("QA / test", r"\bqa\b|\bquality|\btest engineer|\bsdet\b"),
                 ("Software engineering", r"\bsoftware|\bsde\b|\bdeveloper|\bengineer|\bprogrammer|\bbackend|\bback.?end")]


def target():
    return int(db.get_setting("daily_target") or DEFAULT_TARGET)


def role_family(title):
    t = (title or "").lower()
    return next((name for name, rx in ROLE_FAMILIES if re.search(rx, t)), "Other")


def _day(j):
    d = j.get("applied_on") or (j.get("status_at") or "")[:10]
    try:
        return date.fromisoformat(d[:10]) if d else None
    except ValueError:
        return None


def _city(j):
    loc = (j.get("location") or "").strip()
    if not loc and j.get("cities"):
        import json
        try:
            loc = ", ".join(json.loads(j["cities"]) if isinstance(j["cities"], str) else j["cities"])
        except ValueError:
            loc = ""
    low = loc.lower()
    for rx, name in CITY_ALIASES:                      # catches spellings like "Banglore" or "Bangaluru Karnataka India"
        if re.search(rx, low):
            return name
    first = re.split(r"[,/;|(]", loc)[0].strip() if loc else ""
    return first.title() if first else "Not given"


CITY_ALIASES = [(r"\bb[ae]ng", "Bengaluru"), (r"\bgur[gu]", "Gurugram"), (r"\b(mumb|bomb)", "Mumbai"),
                (r"\bhyd", "Hyderabad"), (r"\b(chenn|madras)", "Chennai"), (r"\bpune\b", "Pune"), (r"\bnoida", "Noida"),
                (r"\b(new delhi|delhi)", "Delhi NCR"), (r"\b(kolk|calcut)", "Kolkata"), (r"\bremote\b", "Remote")]


def _top(counter, n):
    items = counter.most_common()
    head, rest = items[:n], sum(v for _, v in items[n:])
    return [{"label": k, "n": v} for k, v in head] + ([{"label": "Others", "n": rest}] if rest else [])


def applications(rows=None):
    rows = rows if rows is not None else db.list_jobs()
    return [j for j in rows if j.get("status") and j["status"] not in ("hidden", "saved")]


def today_count(today=None):
    today = today or date.today()
    return sum(1 for j in applications() if _day(j) == today)


def compute(today=None, rows=None):
    today = today or date.today()
    apps = applications(rows)
    saved = sum(1 for j in (rows if rows is not None else db.list_jobs()) if j.get("status") == "saved")
    days = Counter(d for d in (_day(j) for j in apps) if d)
    n = len(apps)
    status = Counter(j["status"] for j in apps)
    responded = sum(1 for j in apps if j["status"] in RESPONDED)
    interviews = sum(1 for j in apps if j["status"] in ("screening", "oa", "interviewing", "offer"))

    streak, d = 0, today if days.get(today) else today - timedelta(days=1)
    while days.get(d):
        streak, d = streak + 1, d - timedelta(days=1)
    last30 = [today - timedelta(days=i) for i in range(29, -1, -1)]
    monday = today - timedelta(days=today.weekday())
    weeks = [monday - timedelta(weeks=i) for i in range(11, -1, -1)]
    best = days.most_common(1)[0] if days else None
    tgt = target()
    days_met = sum(1 for x in last30 if days.get(x, 0) >= tgt)

    # funnel: applications that reached at least each stage (rejected/ghosted/withdrawn count as applied only)
    funnel = [{"label": label, "n": sum(1 for j in apps if STAGE.get(j["status"], 1) >= k)}
              for k, label in ((1, "Applied"), (2, "Screening call"), (3, "OA / test"), (4, "Interview"), (5, "Offer"))]
    return {
        "today": {"date": today.isoformat(), "count": days.get(today, 0), "target": tgt, "met": days.get(today, 0) >= tgt},
        "kpis": {"applications": n, "saved": saved, "this_week": sum(v for k, v in days.items() if k >= monday),
                 "in_progress": sum(status[s] for s in ("screening", "oa", "interviewing")), "offers": status["offer"],
                 "response_rate": round(100 * responded / n, 1) if n else 0,
                 "interview_rate": round(100 * interviews / n, 1) if n else 0,
                 "ghosted_rate": round(100 * status["ghosted"] / n, 1) if n else 0,
                 "streak": streak, "avg_per_day_30": round(sum(days.get(x, 0) for x in last30) / 30, 1),
                 "best_day": {"date": best[0].isoformat(), "n": best[1]} if best else None,
                 "target_days_met_30": days_met},
        "per_day": [{"date": x.isoformat(), "n": days.get(x, 0)} for x in last30],
        "per_week": [{"week": w.isoformat(), "n": sum(days.get(w + timedelta(days=i), 0) for i in range(7))} for w in weeks],
        "weekday": [{"label": lbl, "n": sum(v for k, v in days.items() if k.weekday() == i)}
                    for i, lbl in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))],
        "status": [{"label": s, "n": status[s]} for s in ("applied", "screening", "oa", "interviewing", "offer", "rejected", "ghosted", "withdrawn") if status[s]],
        "funnel": funnel,
        "companies": _top(Counter(j["company"].strip() for j in apps if j.get("company")), 12),
        "roles": _top(Counter(role_family(j.get("title")) for j in apps), 8),
        "sources": _top(Counter((j.get("app_source") or ("JobQuest.AI" if j.get("source") != "Added by you" else "Not given")).strip()
                                for j in apps), 7),
        "job_types": _top(Counter((j.get("job_type") or "").strip().title() or "Not given" for j in apps), 6),
        "locations": _top(Counter(_city(j) for j in apps), 8),
        "tiers": [{"label": tiers.LABELS[t], "n": n} for t, n in
                  sorted(Counter(tiers.tier_of(j.get("company")) for j in apps).items(), key=lambda x: (tiers.ORDER + ("mid",)).index(x[0]))],
    }


def digest(s=None):
    """Numbers only - what the AI insights card sends (no names, emails or notes)."""
    s = s or compute()
    k = s["kpis"]
    return {"kpis": k, "today": s["today"], "per_week": [w["n"] for w in s["per_week"]],
            "funnel": {f["label"]: f["n"] for f in s["funnel"]}, "status": {x["label"]: x["n"] for x in s["status"]},
            "roles": {x["label"]: x["n"] for x in s["roles"]}, "company_tiers": {x["label"]: x["n"] for x in s["tiers"]}, "sources": {x["label"]: x["n"] for x in s["sources"]},
            "top_companies_by_count": [x["n"] for x in s["companies"][:5]]}
