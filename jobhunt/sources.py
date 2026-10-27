"""Job sources. Every fetcher returns a list of Job and raises on failure.

Keyless, official public endpoints:
  Greenhouse  boards-api.greenhouse.io/v1/boards/{token}/jobs   (Job Board API)
  Lever       api.lever.co/v0/postings/{token}                   (Postings API)
  Ashby       api.ashbyhq.com/posting-api/job-board/{token}      (Job Postings API)
  SmartRecr.  api.smartrecruiters.com/v1/companies/{id}/postings (Posting API)
  Himalayas   himalayas.app/jobs/api/search                       (public API; terms: link back + credit)
Keyless, the JSON endpoints behind public career sites (robots.txt allows them):
  Workday     {tenant}.wdN.myworkdayjobs.com/wday/cxs/...         (POST search, as the careers page does)
  Amazon      www.amazon.jobs/en/search.json
Optional, need a free key (see README):
  Adzuna, JSearch (RapidAPI, wraps Google for Jobs), Jooble
"""
import html
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import filters
from .models import Job
from .net import fetch_json


def clean_text(s):
    s = html.unescape(html.unescape(s or ""))
    s = re.sub(r"<li[^>]*>", "\n• ", s, flags=re.I)                     # keep bullet lists readable
    s = re.sub(r"<(?:br|/p|/li|/div|/h\d|/ul|/ol)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def parse_time(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def split_name(entry):
    """'token|Display Name' -> (token, name). Without a name, the token is title-cased."""
    token, _, name = entry.partition("|")
    return token.strip(), (name.strip() or token.strip().replace("-", " ").title())


def tidy_company(name):
    return re.sub(r"\s+(?:job board|careers?|jobs|hiring)$", "", name.strip(), flags=re.I)


# ---------------------------------------------------------------- ATS boards

def greenhouse(entry, ctx):
    token, name = split_name(entry)
    d = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        offices = "; ".join(o.get("location") or o.get("name") or "" for o in j.get("offices", []))
        loc = "; ".join(x for x in [(j.get("location") or {}).get("name", ""), offices] if x)
        out.append(Job("Greenhouse", tidy_company(j.get("company_name") or name), j["title"], loc, j["absolute_url"],
                       parse_time(j.get("first_published") or j.get("updated_at")), clean_text(j.get("content"))))
    return out


def lever(entry, ctx):
    token, name = split_name(entry)
    d = fetch_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    out = []
    for j in d:
        c = j.get("categories") or {}
        loc = "; ".join(c.get("allLocations") or [c.get("location") or ""])
        if j.get("workplaceType") == "remote":
            loc += " (remote)"
        desc = " ".join([j.get("descriptionPlain") or "", j.get("additionalPlain") or ""] +
                        [l.get("text", "") + " " + clean_text(l.get("content")) for l in j.get("lists", [])])
        posted = datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc) if j.get("createdAt") else None
        hints = {"intern"} if "intern" in (c.get("commitment") or "").lower() else set()
        out.append(Job("Lever", name, j["text"], loc, j["hostedUrl"], posted, desc, hints))
    return out


def ashby(entry, ctx):
    token, name = split_name(entry)
    d = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    out = []
    for j in d.get("jobs", []):
        if not j.get("isListed", True):
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        locs.append(addr.get("addressCountry", ""))
        loc = "; ".join(x for x in locs if x) + (" (remote)" if j.get("isRemote") else "")
        hints = {"intern"} if j.get("employmentType") == "Intern" else set()
        out.append(Job("Ashby", name, j["title"], loc, j["jobUrl"], parse_time(j.get("publishedAt")),
                       j.get("descriptionPlain") or clean_text(j.get("descriptionHtml")), hints))
    return out


def smartrecruiters(entry, ctx):
    company, name = split_name(entry)
    out, offset = [], 0
    while True:
        d = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{company}/postings?country=in&limit=100&offset={offset}")
        for j in d.get("content", []):
            loc = (j.get("location") or {})
            where = ", ".join(x for x in [loc.get("city"), loc.get("region"), "India"] if x)
            if loc.get("remote"):
                where += " (remote)"
            lvl = (j.get("experienceLevel") or {}).get("id", "")
            hints = {"intern"} if lvl == "internship" else {"entry"} if lvl == "entry_level" else set()
            out.append(Job("SmartRecruiters", (j.get("company") or {}).get("name") or name, j["name"], where,
                           f"https://jobs.smartrecruiters.com/{company}/{j['id']}", parse_time(j.get("releasedDate")),
                           "", hints, detail=_sr_detail(company, j["id"])))
        offset += 100
        if offset >= min(d.get("totalFound") or 0, 1000):
            return out


def _sr_detail(company, pid):
    def fill(job):
        d = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{pid}")
        secs = ((d.get("jobAd") or {}).get("sections") or {})
        job.description = clean_text(" ".join(s.get("text", "") for s in secs.values()))
        job.url = d.get("postingUrl") or job.url
    return fill


_WD_POSTED = re.compile(r"posted (today|yesterday|(\d+)\+? days? ago)", re.I)


def _wd_posted(s, now):
    m = _WD_POSTED.search(s or "")
    if not m:
        return None
    days = 0 if m.group(1).lower() == "today" else 1 if m.group(1).lower() == "yesterday" else int(m.group(2))
    if "+" in (s or ""):
        days += 1          # "30+ Days Ago" is older than the window anyway
    return now - timedelta(days=days)


def workday(entry, ctx):
    """entry: 'host/tenant/site|Name', e.g. 'nvidia.wd5/nvidia/NVIDIAExternalCareerSite|NVIDIA'."""
    spec, name = split_name(entry)
    host, tenant, site = spec.split("/")
    base = f"https://{host}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    queries = ["intern", "graduate", "university", "associate"]
    if "SDE" in ctx["roles"]:
        queries += ["software engineer", "software developer"]
    if "ML" in ctx["roles"]:
        queries += ["machine learning", "data scientist", "AI engineer"]
    seen, out = set(), []
    for q in queries:
        for offset in (0, 20, 40):
            d = fetch_json(f"{api}/jobs", data={"appliedFacets": {}, "limit": 20, "offset": offset,
                                                 "searchText": f"India {q}"})
            posts = d.get("jobPostings", [])
            for p in posts:
                path = p.get("externalPath")
                title = p.get("title", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                if not filters.role_kind(title) or filters.title_is_senior(title):
                    continue
                posted = _wd_posted(p.get("postedOn"), ctx["now"])
                if posted is None or (ctx["now"] - posted).days > ctx["days"]:
                    continue
                info = fetch_json(f"{api}{path}").get("jobPostingInfo", {})
                loc = "; ".join([info.get("location", "")] + (info.get("additionalLocations") or []))
                if info.get("remoteType"):
                    loc += f" ({info['remoteType']})"
                out.append(Job("Workday", name, title, loc, info.get("externalUrl") or f"{base}/en-US/{site}{path}",
                               posted, clean_text(info.get("jobDescription"))))
            if len(posts) < 20:
                break
            time.sleep(0.3)
    return out


def amazon(ctx):
    # The search is literal keyword matching: "software development engineer" misses "Software Dev Engineer I",
    # so the short spellings Amazon uses in titles are queried too.
    queries = ["intern", "university", "SDE"]
    if "SDE" in ctx["roles"]:
        queries += ["software development engineer", "software dev engineer", "software engineer"]
    if "ML" in ctx["roles"]:
        queries += ["machine learning", "applied scientist", "data scientist", "data engineer"]
    seen, out = set(), []
    for q in queries:
        for offset in (0, 100, 200):
            url = ("https://www.amazon.jobs/en/search.json?normalized_country_code%5B%5D=IND&result_limit=100"
                   f"&sort=recent&offset={offset}&base_query=" + urllib.parse.quote(q))
            page = fetch_json(url).get("jobs", [])
            out += _amazon_jobs(page, seen)
            # Sorted newest first: stop paging once this page reaches past the search window.
            if len(page) < 100 or (out and out[-1].posted and (ctx["now"] - out[-1].posted).days > ctx["days"]):
                break
    return out


def _amazon_jobs(page, seen):
    out = []
    for j in page:
        if j["id"] in seen:
            continue
        seen.add(j["id"])
        try:
            posted = datetime.strptime(j["posted_date"], "%B %d, %Y").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            posted = None
        hints = ({"intern"} if j.get("is_intern") else set()) | ({"university"} if j.get("university_job") else set())
        desc = clean_text(" ".join([j.get("description") or "", "Basic qualifications: " +
                                    (j.get("basic_qualifications") or ""), "Preferred qualifications: " +
                                    (j.get("preferred_qualifications") or "")]))
        out.append(Job("amazon.jobs", "Amazon", j["title"], j.get("normalized_location") or j.get("location", ""),
                       "https://www.amazon.jobs" + j["job_path"], posted, desc, hints))
    return out


def himalayas(ctx):
    """Remote jobs that name India as a hiring location. Terms: link to the Himalayas page and credit Himalayas."""
    queries = []
    if "SDE" in ctx["roles"]:
        queries += ["software engineer", "developer"]
    if "ML" in ctx["roles"]:
        queries += ["machine learning", "AI engineer", "data scientist"]
    seen, out = set(), []
    for q in queries:
        for page in (1, 2, 3):
            url = (f"https://himalayas.app/jobs/api/search?country=India&seniority=Entry-level&page={page}&q="
                   + urllib.parse.quote(q))
            jobs = fetch_json(url).get("jobs", [])
            for j in jobs:
                if j["guid"] in seen:
                    continue
                seen.add(j["guid"])
                locs = j.get("locationRestrictions") or []
                if not any("india" in l.lower() for l in locs):
                    continue            # India only: skip worldwide/other-country remote roles
                where = "Remote - " + ", ".join(locs)
                out.append(Job("Himalayas", j.get("companyName", ""), j["title"], where, j["guid"],
                               datetime.fromtimestamp(j["pubDate"], timezone.utc) if j.get("pubDate") else None,
                               clean_text(j.get("description")), {"entry"}))
            if len(jobs) < 20:
                break
    return out


# ---------------------------------------------------------------- optional, keyed

def _aggregator_queries(ctx):
    q = []
    if "SDE" in ctx["roles"]:
        q += ["software engineer fresher", "graduate software engineer", "software engineer intern"]
    if "ML" in ctx["roles"]:
        q += ["machine learning engineer fresher", "data scientist fresher", "AI engineer entry level"]
    return q


def adzuna(ctx):
    app_id, key = ctx["keys"].get("adzuna_app_id"), ctx["keys"].get("adzuna_app_key")
    out = []
    for q in _aggregator_queries(ctx):
        params = urllib.parse.urlencode({"app_id": app_id, "app_key": key, "what": q, "results_per_page": 50,
                                         "max_days_old": ctx["days"], "sort_by": "date",
                                         "content-type": "application/json"})
        d = fetch_json(f"https://api.adzuna.com/v1/api/jobs/in/search/1?{params}")
        for j in d.get("results", []):
            out.append(Job("Adzuna", (j.get("company") or {}).get("display_name", ""), clean_text(j.get("title")),
                           (j.get("location") or {}).get("display_name", "") + ", India", j.get("redirect_url", ""),
                           parse_time(j.get("created")), clean_text(j.get("description"))))
    return out


def jsearch(ctx):
    key = ctx["keys"].get("jsearch_rapidapi_key")
    window = "today" if ctx["days"] <= 1 else "3days" if ctx["days"] <= 3 else "week" if ctx["days"] <= 7 else "month"
    out = []
    for q in _aggregator_queries(ctx)[:4]:          # free tier is ~200 requests/month: 4 a day fits
        params = urllib.parse.urlencode({"query": f"{q} in India", "page": 1, "num_pages": 1, "country": "in",
                                         "date_posted": window})
        # JSearch moved to /search-v2 (Oct 2026); its jobs now sit under data.jobs instead of data
        d = fetch_json(f"https://jsearch.p.rapidapi.com/search-v2?{params}",
                       headers={"x-rapidapi-key": key, "x-rapidapi-host": "jsearch.p.rapidapi.com"})
        data = d.get("data") or []
        for j in (data.get("jobs") or []) if isinstance(data, dict) else data:
            country = "India" if (j.get("job_country") or "").upper() in ("IN", "IND", "INDIA") else j.get("job_country")
            loc = ", ".join(x for x in [j.get("job_city"), j.get("job_state"), country] if x)
            if j.get("job_is_remote"):
                loc += " (remote)"
            quals = " ".join(sum(((j.get("job_highlights") or {}).get(k, []) for k in ("Qualifications",)), []))
            out.append(Job("JSearch", j.get("employer_name", ""), j.get("job_title", ""), loc,
                           j.get("job_apply_link") or j.get("job_google_link", ""),
                           parse_time(j.get("job_posted_at_datetime_utc")),
                           clean_text((j.get("job_description") or "") + " " + quals)))
    return out


def jooble(ctx):
    key = ctx["keys"].get("jooble_key")
    out = []
    for q in _aggregator_queries(ctx):
        d = fetch_json(f"https://jooble.org/api/{key}", data={"keywords": q, "location": "India", "page": "1"})
        for j in d.get("jobs", []):
            out.append(Job("Jooble", j.get("company", ""), clean_text(j.get("title")),
                           (j.get("location") or "") + ", India", j.get("link", ""), parse_time(j.get("updated")),
                           clean_text(j.get("snippet"))))
    return out


# Lower number wins when the same job turns up on two sources: a direct company link beats an aggregator's.
PRIORITY = {"Greenhouse": 0, "Lever": 0, "Ashby": 0, "SmartRecruiters": 0, "Workday": 0, "amazon.jobs": 0,
            "Himalayas": 1, "JSearch": 2, "Adzuna": 3, "Jooble": 4}


def plan(cfg, ctx):
    """[(label, callable)] for everything enabled in the config."""
    tasks = []
    boards = cfg["companies"]
    on = cfg["sources"]
    for kind, fn in (("greenhouse", greenhouse), ("lever", lever), ("ashby", ashby),
                     ("smartrecruiters", smartrecruiters), ("workday", workday)):
        if on.get(kind, True):
            # Workday is labelled by tenant: site names like "External_Career_Site" repeat across companies.
            tasks += [(f"{kind}:{split_name(e)[0].split('/')[1] if kind == 'workday' else split_name(e)[0]}",
                       (lambda e=e, fn=fn: fn(e, ctx))) for e in boards.get(kind, [])]
    if on.get("amazon", True):
        tasks.append(("amazon.jobs", lambda: amazon(ctx)))
    if on.get("himalayas", True):
        tasks.append(("himalayas", lambda: himalayas(ctx)))
    keys = ctx["keys"]
    if on.get("adzuna", True) and keys.get("adzuna_app_id") and keys.get("adzuna_app_key"):
        tasks.append(("adzuna", lambda: adzuna(ctx)))
    if on.get("jsearch", True) and keys.get("jsearch_rapidapi_key"):
        tasks.append(("jsearch", lambda: jsearch(ctx)))
    if on.get("jooble", True) and keys.get("jooble_key"):
        tasks.append(("jooble", lambda: jooble(ctx)))
    return tasks
