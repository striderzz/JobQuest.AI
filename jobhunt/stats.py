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
    return first.title() if first else "India"


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


RANGES = {"7d": 7, "30d": 30, "90d": 90, "12m": 365, "all": None}


def _bucket(d, grain):
    if grain == "month":
        return date(d.year, d.month, 1)
    if grain == "week":
        return d - timedelta(days=d.weekday())
    return d


def _buckets(start, end, grain):
    out, d = [], _bucket(start, grain)
    while d <= end:
        out.append(d)
        d = (date(d.year + d.month // 12, d.month % 12 + 1, 1) if grain == "month"
             else d + timedelta(days=7 if grain == "week" else 1))
    return out


def _label(d, grain):
    return d.strftime("%b %Y") if grain == "month" else d.strftime("%d %b").lstrip("0")


def _rate_by(apps, key, min_n=3):
    """Response rate per group, only for groups with enough applications to mean something."""
    groups = {}
    for j in apps:
        groups.setdefault(key(j), []).append(j)
    out = [{"label": k, "n": len(v), "rate": round(100 * sum(1 for j in v if j["status"] in RESPONDED) / len(v), 1)}
           for k, v in groups.items() if k and len(v) >= min_n]
    return sorted(out, key=lambda x: -x["rate"])


def filter_rows(rows, rng="all", company="", family="", today=None):
    """Rows for one company / role family, and (for tracker entries) inside the date range."""
    today = today or date.today()
    days = RANGES.get(rng)
    start = today - timedelta(days=days - 1) if days else None
    key = db.norm_company(company) if company else ""
    out = []
    for j in rows:
        if key and db.norm_company(j.get("company")) != key:
            continue
        if family and role_family(j.get("title")) != family:
            continue
        if start and j.get("status") and j["status"] not in ("hidden", "saved") and (_day(j) or date.min) < start:
            continue
        out.append(j)
    return out


def options(rows):
    """What the dashboard's company and role pickers offer (companies you applied to, most first)."""
    apps = applications(rows)
    return {"companies": [c for c, _ in Counter(j["company"].strip() for j in apps if j.get("company")).most_common(80)],
            "families": [name for name, _ in ROLE_FAMILIES] + ["Other"]}


def explore(today=None, rows=None, rng="30d", grain="day"):
    """The dashboard's filtered view: a date range, a grain (day/week/month) and deeper breakdowns."""
    today = today or date.today()
    rows = rows if rows is not None else db.list_jobs()
    days = RANGES.get(rng, 30)
    apps_all = applications(rows)
    first = min((d for d in (_day(j) for j in apps_all) if d), default=today)
    start = today - timedelta(days=days - 1) if days else first
    apps = [j for j in apps_all if (_day(j) or date.min) >= start] if days else apps_all
    if grain not in ("day", "week", "month"):
        grain = "day" if (today - start).days <= 45 else "week" if (today - start).days <= 200 else "month"
    keys = _buckets(start, today, grain)
    apc = Counter(_bucket(d, grain) for d in (_day(j) for j in apps) if d)
    rsp = Counter(_bucket(d, grain) for d in (_day(j) for j in apps if j["status"] in RESPONDED) if d)

    found = [j for j in rows if j.get("source") != "Added by you" and j.get("first_seen")]
    def seen(j):
        try:
            return date.fromisoformat(j["first_seen"][:10])
        except ValueError:
            return None
    found = [j for j in found if (seen(j) or date.min) >= start]
    fnd = Counter(_bucket(seen(j), grain) for j in found if seen(j))

    waits = []
    for j in apps:
        a, s = _day(j), (j.get("status_at") or "")[:10]
        if j["status"] in RESPONDED and a and s and j.get("applied_on"):
            try:
                waits.append(max(0, (date.fromisoformat(s) - a).days))
            except ValueError:
                pass
    wb = [("0-3 days", 0, 3), ("4-7 days", 4, 7), ("1-2 weeks", 8, 14), ("2-4 weeks", 15, 30), ("30+ days", 31, 10 ** 6)]

    top = [c for c, _ in Counter(j["company"].strip() for j in apps if j.get("company")).most_common(10)]
    sts = ("applied", "screening", "oa", "interviewing", "offer", "rejected", "ghosted")
    source = lambda j: (j.get("app_source") or ("JobQuest.AI" if j.get("source") != "Added by you" else "")).strip()
    return {
        "range": rng, "grain": grain, "start": start.isoformat(), "end": today.isoformat(), "applications": len(apps),
        "series": [{"label": _label(k, grain), "date": k.isoformat(), "applied": apc.get(k, 0), "responded": rsp.get(k, 0),
                    "found": fnd.get(k, 0)} for k in keys],
        "found": {"total": len(found), "companies": _top(Counter(j["company"].strip() for j in found if j.get("company")), 12),
                  "roles": _top(Counter(role_family(j.get("title")) for j in found), 8),
                  "tiers": [{"label": tiers.LABELS[t], "n": n} for t, n in Counter(tiers.tier_of(j.get("company")) for j in found).most_common()]},
        "rates": {"tier": [x | {"label": tiers.LABELS[x["label"]]} for x in _rate_by(apps, lambda j: tiers.tier_of(j.get("company")))],
                  "source": _rate_by(apps, source), "role": _rate_by(apps, lambda j: role_family(j.get("title"))),
                  "weekday": _rate_by(apps, lambda j: _day(j).strftime("%a") if _day(j) else "", 2)},
        "wait": [{"label": lbl, "n": sum(1 for w in waits if a <= w <= b)} for lbl, a, b in wb],
        "ghost_by_source": sorted(({"label": k, "n": len(v), "rate": round(100 * sum(1 for j in v if j["status"] == "ghosted") / len(v), 1)}
                                   for k, v in _group(apps, source).items() if k and len(v) >= 3), key=lambda x: -x["rate"]),
        "company_status": {"companies": top, "statuses": list(sts),
                           "data": [[sum(1 for j in apps if j.get("company", "").strip() == c and j["status"] == s) for c in top] for s in sts]},
        "roles_applied": _top(Counter(role_family(j.get("title")) for j in apps), 8),
        "oracle": oracle(compute(today, rows), apps, today),
    }


def _group(items, key):
    g = {}
    for j in items:
        g.setdefault(key(j), []).append(j)
    return g


def oracle(s, apps, today=None):
    """Plain-Python findings with the numbers behind them: [{tone, title, tip}]. No AI involved."""
    today = today or date.today()
    out, k = [], s["kpis"]
    add = lambda tone, title, tip: out.append({"tone": tone, "title": title, "tip": tip})
    n = len(apps)
    if not n:
        return [{"tone": "info", "title": "No applications in this range yet", "tip": "Apply to a few roles and the oracle starts reading your patterns."}]
    resp = lambda grp: (100 * sum(1 for j in grp if j["status"] in RESPONDED) / len(grp)) if grp else 0
    by_tier = _group(apps, lambda j: tiers.tier_of(j.get("company")))
    rated = sorted(((t, resp(v), len(v)) for t, v in by_tier.items() if len(v) >= 5), key=lambda x: -x[1])
    if len(rated) >= 2 and rated[0][1] > 0:
        best, worst = rated[0], rated[-1]
        ratio = best[1] / worst[1] if worst[1] else None
        add("good", f"{tiers.LABELS[best[0]]} replies most: {best[1]:.0f}% vs {worst[1]:.0f}% for {tiers.LABELS[worst[0]]}" +
            (f" ({ratio:.1f}x)" if ratio else ""), f"Shift more of your daily target toward {tiers.LABELS[best[0]]} roles.")
    fam = _group(apps, lambda j: role_family(j.get("title")))
    intv = [j for j in apps if j["status"] in ("screening", "oa", "interviewing", "offer")]
    if len(intv) >= 3:
        for f, grp in sorted(fam.items(), key=lambda x: -len(x[1])):
            share_a = len(grp) / n
            share_i = sum(1 for j in intv if role_family(j.get("title")) == f) / len(intv)
            if share_i - share_a >= 0.15:
                add("good", f"{f} is {share_a:.0%} of applications but {share_i:.0%} of interviews",
                    f"Your profile lands best here - lean into {f} roles and tailor that resume.")
                break
    wd = _group(apps, lambda j: _day(j).weekday() if _day(j) else None)
    wr = sorted(((d, resp(v)) for d, v in wd.items() if d is not None and len(v) >= 5), key=lambda x: -x[1])
    if len(wr) >= 3 and wr[0][1] >= 5 and wr[0][1] > wr[-1][1]:
        names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        add("info", f"{names[wr[0][0]]} applications get the most replies ({wr[0][1]:.0f}%)",
            f"Batch your best-fit applications on {names[wr[0][0]]}s; {names[wr[-1][0]]} is your weakest day ({wr[-1][1]:.0f}%).")
    stale = [j for j in apps if j["status"] == "applied" and _day(j) and (today - _day(j)).days > 21]
    if len(stale) >= 3:
        add("warn", f"{len(stale)} applications have had no answer for 3+ weeks",
            "Mark them Ghosted to keep your numbers honest, or send one short follow-up to the best ones.")
    tgt, week = s["today"]["target"], s["kpis"]["this_week"]
    want = tgt * (today.weekday() + 1)
    if week < want:
        add("warn", f"{want - week} applications behind this week's pace ({week} of {want})",
            f"{s['today']['target'] - s['today']['count']} more today gets you back on track." if not s["today"]["met"] else "Today's target is met - keep the streak going tomorrow.")
    elif week:
        add("good", f"Ahead of pace: {week} applications this week (target pace {want})", "Use the spare time to prepare for screening calls.")
    if k["streak"] >= 3:
        add("good", f"{k['streak']}-day application streak", "Streaks beat bursts - small daily batches keep you visible to recruiters.")
    if k["response_rate"] < 8 and n >= 20:
        add("warn", f"Response rate is low ({k['response_rate']}%)",
            "Apply earlier (postings under 3 days old), tailor the top third of your resume to the role, and add referrals or cold emails.")
    elif k["response_rate"] >= 15:
        add("good", f"Healthy response rate: {k['response_rate']}%", "Your resume is working; volume is the lever now.")
    src = _group(apps, lambda j: (j.get("app_source") or "").strip())
    cold = src.get("Cold email") or []
    rest = [j for j in apps if (j.get("app_source") or "").strip() != "Cold email"]
    if len(cold) >= 3 and rest and resp(cold) > resp(rest):
        add("good", f"Cold emails reply {resp(cold):.0f}% vs {resp(rest):.0f}% for regular applications",
            "Pair every strong-fit application with a cold email to a contact printed in the post.")
    if k["ghosted_rate"] >= 40:
        add("warn", f"{k['ghosted_rate']}% of applications were ghosted", "Favour companies where you can reach a person; big job boards ghost the most.")
    return out[:10]


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
