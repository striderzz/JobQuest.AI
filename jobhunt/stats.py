"""Job-hunt statistics, computed in plain Python from the tracker (the AI only ever sees these numbers)."""
import re
import statistics
from collections import Counter
from datetime import date, datetime, timedelta, timezone

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


def _names(items, key=lambda j: j.get("company")):
    """Count by company, treating "Acme" and "Acme Pvt Ltd" as one. Shown under the spelling you used most."""
    groups = {}
    for j in items:
        name = (key(j) or "").strip()
        if name:
            groups.setdefault(db.norm_company(name), Counter())[name] += 1
    return Counter({c.most_common(1)[0][0]: sum(c.values()) for c in groups.values()})


def _reached(j, hist):
    """How far an application really got: a rejection after an interview still reached the interview."""
    seen = [s for s, _ in hist.get(j.get("id"), ())] + [j.get("status")]
    return max(STAGE.get(s, 1) for s in seen)


def _when(at):
    try:
        d = datetime.fromisoformat(at)
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone().date()
    except (TypeError, ValueError):
        return None


def _waits(apps, hist):
    """Days from applying to the first reply. Only replies recorded since status history began (older rows kept just
    their latest change, which isn't the first reply)."""
    out = []
    for j in apps:
        a = _day(j)
        first = next((_when(at) for s, at in hist.get(j.get("id"), ()) if s in RESPONDED), None)
        if a and first:
            out.append(max(0, (first - a).days))
    return out


def summary(values):
    """count, mean, median, min, max, spread and quartiles of a list of numbers."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None, "p25": None, "p75": None}
    q = statistics.quantiles(v, n=4, method="inclusive") if len(v) > 1 else [v[0], v[0], v[0]]
    r = lambda x: round(x, 2)
    return {"n": len(v), "mean": r(statistics.fmean(v)), "median": r(statistics.median(v)), "min": r(v[0]), "max": r(v[-1]),
            "stdev": r(statistics.stdev(v)) if len(v) > 1 else 0, "p25": r(q[0]), "p75": r(q[2])}


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
    return {"companies": [c for c, _ in _names(apps).most_common(80)],
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

    hist = db.status_history()
    waits = _waits(apps, hist)
    wb = [("0-3 days", 0, 3), ("4-7 days", 4, 7), ("1-2 weeks", 8, 14), ("2-4 weeks", 15, 30), ("30+ days", 31, 10 ** 6)]

    top = [c for c, _ in _names(apps).most_common(10)]
    sts = ("applied", "screening", "oa", "interviewing", "offer", "rejected", "ghosted")
    source = lambda j: (j.get("app_source") or ("JobQuest.AI" if j.get("source") != "Added by you" else "")).strip()
    return {
        "range": rng, "grain": grain, "start": start.isoformat(), "end": today.isoformat(), "applications": len(apps),
        "series": [{"label": _label(k, grain), "date": k.isoformat(), "applied": apc.get(k, 0), "responded": rsp.get(k, 0),
                    "found": fnd.get(k, 0)} for k in keys],
        "found": {"total": len(found), "companies": _top(_names(found), MAX_ITEMS),
                  "roles": _top(Counter(role_family(j.get("title")) for j in found), 8),
                  "tiers": [{"label": tiers.LABELS[t], "n": n} for t, n in Counter(tiers.tier_of(j.get("company")) for j in found).most_common()]},
        "rates": {"tier": [x | {"label": tiers.LABELS[x["label"]]} for x in _rate_by(apps, lambda j: tiers.tier_of(j.get("company")))],
                  "source": _rate_by(apps, source), "role": _rate_by(apps, lambda j: role_family(j.get("title"))),
                  "weekday": _rate_by(apps, lambda j: _day(j).strftime("%a") if _day(j) else "", 2)},
        "wait": [{"label": lbl, "n": sum(1 for w in waits if a <= w <= b)} for lbl, a, b in wb],
        "ghost_by_source": sorted(({"label": k, "n": len(v), "rate": round(100 * sum(1 for j in v if j["status"] == "ghosted") / len(v), 1)}
                                   for k, v in _group(apps, source).items() if k and len(v) >= 3), key=lambda x: -x["rate"]),
        "wait_tracked": len(waits),
        "company_status": {"companies": top, "statuses": list(sts),
                           "data": [[sum(1 for j in apps if db.norm_company(j.get("company")) == db.norm_company(c) and j["status"] == s)
                                     for c in top] for s in sts]},
        "roles_applied": _top(Counter(role_family(j.get("title")) for j in apps), 8),
        "oracle": oracle(compute(today, [j for j in rows if j in apps or j.get("status") == "saved"], base=rows, span=(today - start).days + 1),
                         apps, today),
        "analysis": analysis(rows, apps, start, today, hist),
    }


def _table(apps, key, label, hist):
    """One row per group: the spreadsheet behind the Companies and Roles tabs."""
    rows = []
    for k, grp in _group(apps, key).items():
        if not k:
            continue
        names = Counter((label(j) or "").strip() for j in grp)
        days = [d for d in (_day(j) for j in grp) if d]
        replies = sum(1 for j in grp if j["status"] in RESPONDED)
        w = _waits(grp, hist)
        rows.append({"name": names.most_common(1)[0][0], "applied": len(grp),
                     "active": sum(1 for j in grp if j["status"] in ("applied", "screening", "oa", "interviewing")),
                     "replies": replies, "interviews": sum(1 for j in grp if _reached(j, hist) >= 2),
                     "offers": sum(1 for j in grp if j["status"] == "offer"),
                     "rejected": sum(1 for j in grp if j["status"] == "rejected"),
                     "ghosted": sum(1 for j in grp if j["status"] == "ghosted"),
                     "reply_rate": round(100 * replies / len(grp), 1),
                     "first": min(days).isoformat() if days else "", "last": max(days).isoformat() if days else "",
                     "median_wait": statistics.median(w) if w else None})
    return sorted(rows, key=lambda r: (-r["applied"], r["name"].lower()))


def _pie(table, total, n=10):
    head = table[:n]
    return {"slices": [{"label": r["name"], "n": r["applied"], "share": round(100 * r["applied"] / total, 1)} for r in head],
            "covered": round(100 * sum(r["applied"] for r in head) / total, 1) if total else 0, "groups": len(table)}


def analysis(rows, apps, start, today, hist=None):
    """The dashboard's data sheet: spread of every number, and tables by company, title and role family."""
    hist = hist if hist is not None else db.status_history()
    n = len(apps)
    days = Counter(d for d in (_day(j) for j in apps) if d)
    span = [start + timedelta(days=i) for i in range((today - start).days + 1)]
    per_day = [days.get(d, 0) for d in span]
    weeks = Counter(_bucket(d, "week") for d in days.elements())
    per_week = [weeks.get(w, 0) for w in _buckets(start, today, "week")]
    companies = _table(apps, lambda j: db.norm_company(j.get("company")), lambda j: j.get("company"), hist)
    titles = _table(apps, lambda j: db.norm_role(j.get("title")), lambda j: j.get("title"), hist)
    families = _table(apps, lambda j: role_family(j.get("title")), lambda j: role_family(j.get("title")), hist)
    ages = [(today - _day(j)).days for j in apps if _day(j) and j["status"] == "applied"]
    pay = [((j["salary_min"] or j["salary_max"]) + j["salary_max"]) / 2 / 100_000 for j in rows
           if j.get("salary_max") and j.get("salary_currency") == "INR" and j.get("status") != "hidden"]
    open_jobs = sum(1 for j in rows if j.get("status") != "hidden")
    waits = _waits(apps, hist)
    return {
        "metrics": [
            {"key": "per_day", "label": "Applications per day", "unit": "", **summary(per_day)},
            {"key": "per_week", "label": "Applications per week", "unit": "", **summary(per_week)},
            {"key": "per_company", "label": "Applications per company", "unit": "", **summary([r["applied"] for r in companies])},
            {"key": "wait", "label": "Days to first reply", "unit": "days", **summary(waits)},
            {"key": "age", "label": "Days waiting, no reply yet", "unit": "days", **summary(ages)},
            {"key": "salary", "label": "Salary shown in postings", "unit": "LPA", **summary(pay)},
        ],
        "salary_coverage": {"with": len(pay), "total": open_jobs,
                            "percent": round(100 * len(pay) / open_jobs, 1) if open_jobs else 0},
        "wait_tracked": len(waits), "replies": sum(1 for j in apps if j["status"] in RESPONDED),
        "companies": companies[:300], "titles": titles[:300], "families": families,
        "company_pie": _pie(companies, n), "title_pie": _pie(titles, n),
        "applications": n, "distinct_companies": len(companies), "distinct_titles": len(titles),
        "active_days": sum(1 for x in per_day if x), "days_in_range": len(span),
    }


def usage(days=None, today=None):
    """Tokens and cost by model, by feature and by day. Unknown prices are counted, never shown as free."""
    today = today or date.today()
    since = (today - timedelta(days=days - 1)).isoformat() if days else None
    rows = db.usage_rows(since)

    def roll(key):
        out = {}
        for r in rows:
            g = out.setdefault(key(r), {"label": key(r), "calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
                                        "unpriced": 0, "ms": []})
            g["calls"] += 1
            g["tokens_in"] += r["tokens_in"] or 0
            g["tokens_out"] += r["tokens_out"] or 0
            if r["cost"] is None:
                g["unpriced"] += 1
            else:
                g["cost"] += r["cost"]
            if r["latency_ms"]:
                g["ms"].append(r["latency_ms"])
        for g in out.values():
            ms = g.pop("ms")
            g["cost"] = round(g["cost"], 4)
            g["median_ms"] = round(statistics.median(ms)) if ms else None
        return sorted(out.values(), key=lambda g: -(g["tokens_in"] + g["tokens_out"]))

    return {"calls": len(rows), "tokens_in": sum(r["tokens_in"] or 0 for r in rows), "tokens_out": sum(r["tokens_out"] or 0 for r in rows),
            "cost": round(sum(r["cost"] or 0 for r in rows), 4), "unpriced": sum(1 for r in rows if r["cost"] is None),
            "by_model": roll(lambda r: f"{r['provider']} / {r['model']}"), "by_feature": roll(lambda r: r["feature"] or "other"),
            "by_day": sorted(roll(lambda r: r["at"][:10]), key=lambda g: g["label"])}


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
    replies = sum(1 for j in apps if j["status"] in RESPONDED)
    weak = f" Early signal: based on {replies} repl{'y' if replies == 1 else 'ies'}." if replies < 20 else ""
    by_tier = _group(apps, lambda j: tiers.tier_of(j.get("company")))
    rated = sorted(((t, resp(v), len(v)) for t, v in by_tier.items() if len(v) >= 5), key=lambda x: -x[1])
    if len(rated) >= 2 and rated[0][1] > 0:
        best, worst = rated[0], rated[-1]
        ratio = best[1] / worst[1] if worst[1] else None
        add("good", f"{tiers.LABELS[best[0]]} replies most: {best[1]:.0f}% vs {worst[1]:.0f}% for {tiers.LABELS[worst[0]]}" +
            (f" ({ratio:.1f}x)" if ratio else ""), f"Shift more of your daily target toward {tiers.LABELS[best[0]]} roles." + weak)
    fam = _group(apps, lambda j: role_family(j.get("title")))
    intv = [j for j in apps if j["status"] in ("screening", "oa", "interviewing", "offer")]
    if len(intv) >= 3:
        for f, grp in sorted(fam.items(), key=lambda x: -len(x[1])):
            share_a = len(grp) / n
            share_i = sum(1 for j in intv if role_family(j.get("title")) == f) / len(intv)
            if share_i - share_a >= 0.15:
                add("good", f"{f} is {share_a:.0%} of applications but {share_i:.0%} of interviews",
                    f"Your profile lands best here - lean into {f} roles and tailor that resume." + weak)
                break
    wd = _group(apps, lambda j: _day(j).weekday() if _day(j) else None)
    wr = sorted(((d, resp(v)) for d, v in wd.items() if d is not None and len(v) >= 5), key=lambda x: -x[1])
    if len(wr) >= 3 and wr[0][1] >= 5 and wr[0][1] > wr[-1][1]:
        names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        add("info", f"{names[wr[0][0]]} applications get the most replies ({wr[0][1]:.0f}%)",
            f"Batch your best-fit applications on {names[wr[0][0]]}s; {names[wr[-1][0]]} is your weakest day ({wr[-1][1]:.0f}%)." + weak)
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
    fam_rated = sorted(((f, resp(v), len(v)) for f, v in fam.items() if len(v) >= 10), key=lambda x: -x[1])
    if len(fam_rated) >= 2 and fam_rated[0][1] > fam_rated[-1][1]:
        f, r, m = fam_rated[0]
        add("info", f"{f} has your best reply rate: {r:.0f}% across {m} applications",
            f"{fam_rated[-1][0]} is lowest at {fam_rated[-1][1]:.0f}%." + weak)
    names = _names(apps)
    if len(names) >= 20:
        top10 = sum(v for _, v in names.most_common(10))
        add("info", f"Your top 10 companies are {100 * top10 / n:.0f}% of {n} applications, across {len(names)} companies",
            "A wide spread is good for volume. Pick five you care about most and try a referral or cold email there.")
    silent = [c for c, grp in _group(apps, lambda j: db.norm_company(j.get("company"))).items()
              if c and len(grp) >= 3 and not any(j["status"] in RESPONDED for j in grp)]
    if len(silent) >= 3:
        add("warn", f"{len(silent)} companies have had 3+ applications from you and no reply",
            "More applications there are unlikely to help. Try a person at the company instead of another form.")
    active = sorted({d for d in (_day(j) for j in apps) if d})
    gaps = [(b - a).days - 1 for a, b in zip(active, active[1:])]
    if gaps and max(gaps) >= 5:
        add("info", f"Your longest break was {max(gaps)} days with no applications", "Short daily batches keep you in fresh postings.")
    if k["ghosted_rate"] >= 40:
        add("warn", f"{k['ghosted_rate']}% of applications were ghosted", "Favour companies where you can reach a person; big job boards ghost the most.")
    return out[:10]


def compute(today=None, rows=None, base=None, span=None):
    """`rows` is what the tiles and charts cover. `base` (all rows) keeps today's count and the streak true while a
    filter is on, and `span` is the days in the range, for the daily average."""
    today = today or date.today()
    apps = applications(rows)
    saved = sum(1 for j in (rows if rows is not None else db.list_jobs()) if j.get("status") == "saved")
    days = Counter(d for d in (_day(j) for j in apps) if d)
    all_days = Counter(d for d in (_day(j) for j in applications(base)) if d) if base is not None else days
    hist = db.status_history()
    n = len(apps)
    status = Counter(j["status"] for j in apps)
    responded = sum(1 for j in apps if j["status"] in RESPONDED)
    interviews = sum(1 for j in apps if j["status"] in ("screening", "oa", "interviewing", "offer"))

    streak, d = 0, today if all_days.get(today) else today - timedelta(days=1)
    while all_days.get(d):
        streak, d = streak + 1, d - timedelta(days=1)
    last30 = [today - timedelta(days=i) for i in range(29, -1, -1)]
    monday = today - timedelta(days=today.weekday())
    weeks = [monday - timedelta(weeks=i) for i in range(11, -1, -1)]
    best = days.most_common(1)[0] if days else None
    tgt = target()
    days_met = sum(1 for x in last30 if days.get(x, 0) >= tgt)

    # funnel: applications that reached at least each stage, whatever happened to them afterwards
    funnel = [{"label": label, "n": sum(1 for j in apps if _reached(j, hist) >= k)}
              for k, label in ((1, "Applied"), (2, "Screening call"), (3, "OA / test"), (4, "Interview"), (5, "Offer"))]
    return {
        "today": {"date": today.isoformat(), "count": all_days.get(today, 0), "target": tgt, "met": all_days.get(today, 0) >= tgt},
        "kpis": {"applications": n, "saved": saved, "this_week": sum(v for k, v in days.items() if k >= monday),
                 "in_progress": sum(status[s] for s in ("screening", "oa", "interviewing")), "offers": status["offer"],
                 "response_rate": round(100 * responded / n, 1) if n else 0,
                 "interview_rate": round(100 * interviews / n, 1) if n else 0,
                 "ghosted_rate": round(100 * status["ghosted"] / n, 1) if n else 0,
                 "streak": streak, "avg_per_day_30": round(sum(days.get(x, 0) for x in last30) / 30, 1),
                 "avg_per_day": round(n / span, 1) if span else round(sum(days.get(x, 0) for x in last30) / 30, 1),
                 "best_day": {"date": best[0].isoformat(), "n": best[1]} if best else None,
                 "target_days_met_30": days_met},
        "per_day": [{"date": x.isoformat(), "n": days.get(x, 0)} for x in last30],
        "per_week": [{"week": w.isoformat(), "n": sum(days.get(w + timedelta(days=i), 0) for i in range(7))} for w in weeks],
        "weekday": [{"label": lbl, "n": sum(v for k, v in days.items() if k.weekday() == i)}
                    for i, lbl in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))],
        "status": [{"label": s, "n": status[s]} for s in ("applied", "screening", "oa", "interviewing", "offer", "rejected", "ghosted", "withdrawn") if status[s]],
        "funnel": funnel,
        "companies": _top(_names(apps), MAX_ITEMS),
        "roles": _top(Counter(role_family(j.get("title")) for j in apps), 8),
        "sources": _top(Counter((j.get("app_source") or ("JobQuest.AI" if j.get("source") != "Added by you" else "Not given")).strip()
                                for j in apps), 7),
        "job_types": _top(Counter((j.get("job_type") or "").strip().title() or "Not given" for j in apps), 6),
        "locations": _top(Counter(_city(j) for j in apps), 8),
        "tiers": [{"label": tiers.LABELS[t], "n": n} for t, n in
                  sorted(Counter(tiers.tier_of(j.get("company")) for j in apps).items(), key=lambda x: (tiers.ORDER + ("mid",)).index(x[0]))],
    }


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
GROUPS = {
    "company": lambda j: (j.get("company") or "").strip(), "role": lambda j: (j.get("title") or "").strip(),
    "family": lambda j: role_family(j.get("title")), "status": lambda j: j.get("status") or "",
    "source": lambda j: (j.get("app_source") or "").strip() or "Not given", "location": _city,
    "tier": lambda j: tiers.LABELS[tiers.tier_of(j.get("company"))], "job_type": lambda j: (j.get("job_type") or "").strip().title() or "Not given",
    "weekday": lambda j: _day(j).strftime("%a") if _day(j) else "", "month": lambda j: _day(j).strftime("%Y-%m") if _day(j) else "",
    "week": lambda j: _bucket(_day(j), "week").isoformat() if _day(j) else "", "day": lambda j: _day(j).isoformat() if _day(j) else "",
    "year": lambda j: str(_day(j).year) if _day(j) else "",
    "quarter": lambda j: f"{_day(j).year}-Q{(_day(j).month - 1) // 3 + 1}" if _day(j) else "",
}
TIMELINE = ("year", "quarter", "month", "week", "day", "weekday")
METRICS = ("applications", "replies", "reached_screening", "interviews", "offers", "rejected", "ghosted", "active", "reply_rate",
           "median_days_to_reply")
JOB_METRICS = ("jobs", "avg_score", "median_salary_lpa")
MEASURES = ("days_waiting", "days_to_reply", "applications_per_company", "applications_per_day", "salary_lpa", "match_score")
MAX_ITEMS = 10          # a chart of names shows the top 10 at most, so it stays readable
CHART_TYPES = ("bar", "horizontal_bar", "stacked_bar", "grouped_bar", "line", "area", "pie", "doughnut", "polar", "radar", "scatter",
               "histogram", "table", "list", "stat")
DATASETS = ("applications", "jobs")
LIST_COLUMNS = {
    "company": lambda j: (j.get("company") or "").strip(), "role": lambda j: (j.get("title") or "").strip(),
    "status": lambda j: j.get("status") or "", "applied": lambda j: _day(j).isoformat() if _day(j) else "",
    "days_since": lambda j: (date.today() - _day(j)).days if _day(j) else "", "location": _city,
    "source": lambda j: (j.get("app_source") or "").strip(), "job_type": lambda j: (j.get("job_type") or "").strip(),
    "score": lambda j: j.get("score") if j.get("score") is not None else "", "salary_lpa": lambda j: _lpa(j) or "",
}
SPEC_KEYS = ("type", "title", "dataset", "group_by", "split_by", "metric", "metrics", "top", "sort", "where", "measure", "bins", "x", "y",
             "columns")


def _lpa(j):
    if not j.get("salary_max") or j.get("salary_currency") != "INR":
        return None
    return round(((j.get("salary_min") or j["salary_max"]) + j["salary_max"]) / 2 / 100_000, 1)


def _found(rows):
    """Jobs the search found, shaped so the same groups work: dated by when they were found, sourced by their job board."""
    return [{**j, "applied_on": (j.get("first_seen") or "")[:10], "app_source": j.get("source") or ""}
            for j in rows if j.get("active") and j.get("status") != "hidden"]


def _metric(name, grp, hist):
    def median(values):
        return round(statistics.median(values), 1) if values else 0
    replies = lambda: sum(1 for j in grp if j["status"] in RESPONDED)                # noqa: E731
    return {"applications": lambda: len(grp), "jobs": lambda: len(grp), "replies": replies,
            "reached_screening": lambda: sum(1 for j in grp if _reached(j, hist) >= 2),
            "interviews": lambda: sum(1 for j in grp if _reached(j, hist) >= 4),
            "offers": lambda: sum(1 for j in grp if j["status"] == "offer"),
            "rejected": lambda: sum(1 for j in grp if j["status"] == "rejected"),
            "ghosted": lambda: sum(1 for j in grp if j["status"] == "ghosted"),
            "active": lambda: sum(1 for j in grp if j["status"] in ("applied", "screening", "oa", "interviewing")),
            "reply_rate": lambda: round(100 * replies() / len(grp), 1) if grp else 0,
            "median_days_to_reply": lambda: median(_waits(grp, hist)),
            "avg_score": lambda: round(statistics.mean(s), 1) if (s := [j["score"] for j in grp if j.get("score") is not None]) else 0,
            "median_salary_lpa": lambda: median([x for x in map(_lpa, grp) if x])}[name]()


def _where(apps, w):
    out = apps
    for field, fn in (("status", GROUPS["status"]), ("family", GROUPS["family"]), ("tier", GROUPS["tier"]), ("source", GROUPS["source"]), ("location", GROUPS["location"])):
        if w.get(field):
            want = {str(x).lower() for x in w[field]} if isinstance(w[field], list) else {str(w[field]).lower()}
            out = [j for j in out if fn(j).lower() in want]
    if w.get("company"):
        key = db.norm_company(str(w["company"]))
        out = [j for j in out if db.norm_company(j.get("company")) == key]
    if w.get("role"):
        out = [j for j in out if str(w["role"]).lower() in (j.get("title") or "").lower()]
    for field, keep in (("from", lambda d, x: d >= x), ("to", lambda d, x: d <= x)):
        if w.get(field):
            x = date.fromisoformat(str(w[field])[:10])
            out = [j for j in out if _day(j) and keep(_day(j), x)]
    if w.get("job_type"):
        out = [j for j in out if GROUPS["job_type"](j).lower() == str(w["job_type"]).lower()]
    if w.get("no_reply"):
        out = [j for j in out if j["status"] == "applied"]
    if w.get("min_days_ago") is not None:
        limit = date.today() - timedelta(days=int(w["min_days_ago"]))
        out = [j for j in out if _day(j) and _day(j) <= limit]
    return out


def _histogram(measure, apps, rows, hist, bins):
    today = date.today()
    if measure == "days_waiting":
        v = [(today - _day(j)).days for j in apps if _day(j) and j["status"] == "applied"]
    elif measure == "days_to_reply":
        v = _waits(apps, hist)
    elif measure == "applications_per_company":
        v = list(_names(apps).values())
    elif measure == "applications_per_day":
        c = Counter(d for d in (_day(j) for j in apps) if d)
        first = min(c, default=today)
        v = [c.get(first + timedelta(days=i), 0) for i in range((today - first).days + 1)]
    elif measure == "match_score":
        v = [j["score"] for j in rows if j.get("active") and j.get("score") is not None]
    else:
        v = [x for x in map(_lpa, rows) if x]
    if not v:
        return [], [], 0
    lo, hi = min(v), max(v)
    width = max(1, -(-(hi - lo + 1) // bins))
    labels, values, x = [], [], lo
    while x <= hi:
        top = x + width
        labels.append(f"{x:g}" if width == 1 else f"{x:g}–{top - 1:g}")
        values.append(sum(1 for n in v if x <= n < top))
        x = top
    return labels, values, len(v)


def _groups(items, group):
    if group == "company":
        by_key = _group(items, lambda j: db.norm_company(j.get("company")))
        return {Counter((j.get("company") or "").strip() for j in v).most_common(1)[0][0]: v for k, v in by_key.items() if k}
    return {k: v for k, v in _group(items, GROUPS[group]).items() if k}


def _pick(name, allowed, what):
    if name not in allowed:
        raise ValueError(f"unknown {what} {name}")
    return name


def _list(spec, items, title):
    cols = spec.get("columns") or ["company", "role", "status", "applied", "days_since"]
    for c in cols:
        _pick(c, LIST_COLUMNS, "column")
    table = [[LIST_COLUMNS[c](j) for c in cols] for j in items]
    by = spec.get("sort") if spec.get("sort") in cols else ("applied" if "applied" in cols else cols[0])
    i = cols.index(by)
    table.sort(key=lambda r: str(r[i]).zfill(12) if isinstance(r[i], (int, float)) else str(r[i]).lower(), reverse=by in ("applied", "days_since", "score", "salary_lpa"))
    top = max(1, min(int(spec.get("top") or 100), 400))
    return {"type": "list", "title": title, "columns": cols, "rows": table[:top], "total": len(table)}


def _build(spec, rows):
    kind = _pick(spec.get("type", "bar"), CHART_TYPES, "chart type")
    dataset = _pick(spec.get("dataset", "applications"), DATASETS, "dataset")
    allowed = JOB_METRICS if dataset == "jobs" else METRICS
    hist = db.status_history()
    items = _where(_found(rows) if dataset == "jobs" else applications(rows), spec.get("where") or {})
    title = str(spec.get("title") or "Chart")[:90]
    if kind == "list":
        return _list(spec, items, title)
    if kind == "histogram":
        measure = _pick(spec.get("measure"), MEASURES, "measure")
        labels, values, n = _histogram(measure, items, rows, hist, max(3, min(int(spec.get("bins") or 8), 20)))
        return {"type": "histogram", "title": title, "labels": labels, "values": values, "n": n, "metric": measure}
    metric = _pick(spec.get("metric", allowed[0]), allowed, "metric")
    if kind == "stat":
        return {"type": "stat", "title": title, "value": _metric(metric, items, hist), "unit": "%" if metric == "reply_rate" else "", "n": len(items), "metric": metric}
    group = _pick(spec.get("group_by"), GROUPS, "group")
    groups = _groups(items, group)
    timeline = group in TIMELINE
    is_table = kind == "table"
    top = max(1, min(int(spec.get("top") or (30 if timeline else MAX_ITEMS)), 400 if is_table else 60 if timeline else MAX_ITEMS))
    if timeline or spec.get("sort") == "label":
        keys = sorted(groups, key=(WEEKDAYS.index if group == "weekday" else str))
    else:
        keys = sorted(groups, key=lambda k: (-_metric(metric, groups[k], hist), str(k)))
    shown, total = (keys[-top:] if timeline and group != "weekday" else keys[:top]), len(keys)       # a timeline keeps its latest
    if is_table:
        cols = [_pick(m, allowed, "metric") for m in (spec.get("metrics") or allowed)]
        return {"type": "table", "title": title, "columns": [group, *cols], "total": total,
                "rows": [[k, *[_metric(m, groups[k], hist) for m in cols]] for k in shown]}
    if kind == "scatter":
        x, y = _pick(spec.get("x", allowed[0]), allowed, "metric"), _pick(spec.get("y", metric), allowed, "metric")
        return {"type": "scatter", "title": title, "group": group, "x": x, "y": y, "total": total, "n": len(items),
                "points": [{"label": str(k), "x": _metric(x, groups[k], hist), "y": _metric(y, groups[k], hist)} for k in shown]}
    out = {"type": kind, "title": title, "labels": [str(k) for k in shown], "metric": metric, "total": total, "n": len(items)}
    if spec.get("split_by"):
        split = _pick(spec["split_by"], GROUPS, "group")
        names = [k for k, _ in Counter(GROUPS[split](j) for j in items if GROUPS[split](j)).most_common(6)]
        out["series"] = [{"label": name, "values": [_metric(metric, [j for j in groups[k] if GROUPS[split](j) == name], hist) for k in shown]} for name in names]
    elif spec.get("metrics"):
        out["series"] = [{"label": m.replace("_", " "), "values": [_metric(_pick(m, allowed, "metric"), groups[k], hist) for k in shown]} for m in spec["metrics"]]
    else:
        out["values"] = [_metric(metric, groups[k], hist) for k in shown]
    return out


def query(spec, rows=None):
    """Build a chart or table from a spec. The oracle and the chart builder only name what to show; every number is
    counted here, so none can be invented. Raises ValueError on anything unknown."""
    if not isinstance(spec, dict):
        raise ValueError("a chart needs a spec")
    out = _build(spec, rows if rows is not None else db.list_jobs())
    out["spec"] = {k: spec[k] for k in SPEC_KEYS if k in spec}
    return out


DEFAULT_CHARTS = (
    {"type": "bar", "title": "Applications by month", "group_by": "month", "metric": "applications", "top": 12},
    {"type": "line", "title": "Applications by week", "group_by": "week", "metric": "applications", "top": 16},
    {"type": "stacked_bar", "title": "Status by month", "group_by": "month", "split_by": "status", "metric": "applications", "top": 12},
    {"type": "doughnut", "title": "Status now", "group_by": "status", "metric": "applications"},
    {"type": "horizontal_bar", "title": "Top 10 companies", "group_by": "company", "metric": "applications"},
    {"type": "horizontal_bar", "title": "Top 10 roles", "group_by": "role", "metric": "applications"},
    {"type": "pie", "title": "Role families", "group_by": "family", "metric": "applications"},
    {"type": "horizontal_bar", "title": "Top 10 locations", "group_by": "location", "metric": "applications"},
    {"type": "bar", "title": "Applications by weekday", "group_by": "weekday", "metric": "applications"},
    {"type": "bar", "title": "Reply rate by company tier", "group_by": "tier", "metric": "reply_rate"},
    {"type": "bar", "title": "Reply rate by source", "group_by": "source", "metric": "reply_rate"},
    {"type": "histogram", "title": "Days waiting for a reply", "measure": "days_waiting", "bins": 8},
)


def default_charts(rows=None):
    rows = rows if rows is not None else db.list_jobs()
    return [query(spec, rows) for spec in DEFAULT_CHARTS]


def chart_options():
    return {"types": list(CHART_TYPES), "datasets": list(DATASETS), "groups": list(GROUPS), "measures": list(MEASURES),
            "metrics": {"applications": list(METRICS), "jobs": list(JOB_METRICS)}}


def calendar(rows=None, days=182, today=None):
    """Applications per day, for the activity calendar. `pad` lines the first day up with its weekday."""
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    count = Counter(d for d in map(_day, applications(rows)) if d and d >= start)
    cells = [{"date": (start + timedelta(days=i)).isoformat(), "n": count.get(start + timedelta(days=i), 0)} for i in range(days)]
    return {"pad": start.weekday(), "days": cells, "max": max(count.values(), default=0), "total": sum(count.values()),
            "active_days": len(count)}


def follow_ups(rows=None, after=10, before=45, limit=10, today=None):
    """Applications with no reply yet that are old enough for a nudge but not so old it's pointless, oldest first."""
    today = today or date.today()
    due = [(j, (today - _day(j)).days) for j in applications(rows) if j["status"] == "applied" and _day(j)]
    due = sorted(((j, n) for j, n in due if after <= n <= before), key=lambda x: -x[1])
    return {"n": len(due), "after": after, "items": [{"company": j.get("company"), "title": j.get("title"), "days": n} for j, n in due[:limit]]}


def compare(rows=None, days=None, today=None):
    """This period against the one before it, by application date."""
    if not days:
        return None
    today = today or date.today()
    now, before = [], []
    for j in applications(rows):
        d = _day(j)
        if d:
            age = (today - d).days
            (now if 0 <= age < days else before if days <= age < 2 * days else []).append(j)
    replies = lambda grp: sum(1 for j in grp if j["status"] in RESPONDED)            # noqa: E731
    return {"days": days, "applications": [len(now), len(before)], "replies": [replies(now), replies(before)]}


def applied_companies(rows=None):
    """Every company you've applied to, once, with what happened there."""
    rows = rows if rows is not None else db.list_jobs()
    order = ("applied", "screening", "oa", "interviewing", "offer")
    hist = db.status_history()
    out = []
    for grp in _group(applications(rows), lambda j: db.norm_company(j.get("company"))).values():
        names = Counter((j.get("company") or "").strip() for j in grp)
        name = names.most_common(1)[0][0]
        days = [d for d in (_day(j) for j in grp) if d]
        best = max(_reached(j, hist) for j in grp)
        out.append({"name": name, "tier": tiers.tier_of(name), "applied": len(grp), "last": max(days).isoformat() if days else "",
                    "best": order[best - 1], "replied": any(j["status"] in RESPONDED for j in grp),
                    "roles": [t for t, _ in Counter((j.get("title") or "").strip() for j in grp).most_common(3)],
                    "job_url": next((j["url"] for j in grp if j.get("url")), "")})
    return sorted(out, key=lambda c: c["name"].lower())


def tracker_facts(rows=None, companies=400, titles=120):
    """Your tracker by name, for the chats you start yourself (the oracle and the Coach). Names and counts only: no
    notes, contacts, emails or links. The AI insights card still uses digest(), which has no names at all."""
    rows = rows if rows is not None else db.list_jobs()
    apps = applications(rows)
    today = date.today()
    first = min((d for d in (_day(j) for j in apps) if d), default=today)
    a = analysis(rows, apps, first, today)
    pairs = {(db.norm_company(j.get("company")), db.norm_role(j.get("title"))) for j in apps}
    short = lambda r: [r["name"], r["applied"], r["replies"], r["interviews"], r["offers"], r["rejected"]]
    return {"entries": len(apps), "unique_companies": a["distinct_companies"], "unique_role_titles": a["distinct_titles"],
            "unique_company_role_pairs": len(pairs), "possible_repeats": len(apps) - len(pairs),
            "first_application": first.isoformat(), "row_format": ["name", "applied", "replies", "reached_screening", "offers", "rejected"],
            "companies": [short(r) for r in a["companies"][:companies]], "companies_listed": min(companies, a["distinct_companies"]),
            "role_titles": [short(r) for r in a["titles"][:titles]], "role_families": [short(r) for r in a["families"]],
            "spread": a["metrics"]}


def digest(s=None):
    """Numbers only - what the AI insights card sends (no names, emails or notes)."""
    s = s or compute()
    k = s["kpis"]
    return {"kpis": k, "today": s["today"], "per_week": [w["n"] for w in s["per_week"]],
            "funnel": {f["label"]: f["n"] for f in s["funnel"]}, "status": {x["label"]: x["n"] for x in s["status"]},
            "roles": {x["label"]: x["n"] for x in s["roles"]}, "company_tiers": {x["label"]: x["n"] for x in s["tiers"]}, "sources": {x["label"]: x["n"] for x in s["sources"]},
            "top_companies_by_count": [x["n"] for x in s["companies"][:5]]}
