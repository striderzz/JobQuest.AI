"""Big companies' own career sites, read through the public JSON their pages use (checked with plain HTTP, no login,
no bot checks, robots.txt respected by net.py).

Each adapter returns Jobs; pipeline.py then applies the usual India, role, level and date rules. Descriptions that
need a second request are fetched lazily (Job.detail), only for jobs whose title passed the first filters.
Sites that need a login or a browser, or whose robots.txt says no, are never fetched: see CAREERS_ONLY.
"""
import re
import urllib.error
import urllib.parse
from datetime import datetime, timezone

from . import filters
from .models import Job
from .net import fetch_json
from .sources import ats_queries, clean_text

QUERIES = 6            # search phrases per portal (each costs a page or two of requests)

# Shown as "Open careers page" links; never fetched.
CAREERS_ONLY = {
    "Google": "https://www.google.com/about/careers/applications/jobs/results/?location=India&q={q}",
    "Meta": "https://www.metacareers.com/jobs?offices[0]=Bangalore%2C%20India&q={q}",
    "Apple": "https://jobs.apple.com/en-in/search?location=india-INDC&search={q}",
    "Uber": "https://www.uber.com/in/en/careers/list/?location=IND&query={q}",
    "Infosys": "https://career.infosys.com/jobs?searchText={q}",
    "TCS": "https://ibegin.tcs.com/iBegin/jobs/search",
    "Flipkart": "https://www.flipkartcareers.com/#!/joblist",
    "Zomato (Eternal)": "https://www.eternal.com/careers",
    "Walmart Global Tech": "https://careers.walmart.com/results?q={q}&page=1&sort=date&jobCountry=IND",
    "ServiceNow": "https://careers.servicenow.com/jobs/?search={q}&location=India",
    "Swiggy": "https://careers.swiggy.com/#/careers",
}


def careers_links(q="software engineer"):
    return [{"company": c, "url": u.replace("{q}", urllib.parse.quote(q))} for c, u in CAREERS_ONLY.items()]


def _date(value):
    """Epoch seconds or ms, ISO dates, '/Date(ms)/' and '2026-11-09 12:21 PM' -> aware datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
    s = str(value).strip()
    m = re.match(r"/Date\((\d+)\)/", s)
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, timezone.utc)
    for parse in (lambda x: datetime.fromisoformat(x.replace("Z", "+00:00")),
                  lambda x: datetime.strptime(x, "%Y-%m-%d %I:%M %p")):
        try:
            d = parse(s)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _wanted(title):
    """Cheap first look, so pages of sales or legal roles don't cost detail requests."""
    return bool(filters.role_kind(title)) and not filters.title_is_senior(title)


def _old(posted, ctx):
    return posted is not None and (ctx["now"] - posted).days > ctx["days"]


# ---------------------------------------------------------------- Eightfold (Microsoft, Qualcomm; Netflix on v2)

def eightfold_pcsx(host, domain, company, ctx):
    seen, out = set(), []
    for q in ats_queries(ctx)[:4]:
        for start in range(0, 50, 10):
            try:
                d = fetch_json(f"https://{host}/api/pcsx/search?" + urllib.parse.urlencode(
                    {"domain": domain, "query": q, "location": "India", "start": start, "sort_by": "timestamp"}), retries=2)
            except urllib.error.HTTPError as e:
                if e.code == 429:               # the site wants a break: keep what we have and try again next search
                    return out
                raise
            items = (d.get("data") or {}).get("positions") or []
            for p in items:
                if p["id"] in seen or not _wanted(p.get("name", "")):
                    continue
                seen.add(p["id"])
                url = p.get("positionUrl") or ""
                job = Job(company, company, p["name"], "; ".join(p.get("locations") or p.get("standardizedLocations") or []),
                          url if url.startswith("http") else f"https://{host}{url}", _date(p.get("postedTs") or p.get("creationTs")))

                def detail(j, pid=p["id"]):
                    info = fetch_json(f"https://{host}/api/pcsx/position_details?" +
                                      urllib.parse.urlencode({"position_id": pid, "domain": domain, "hl": "en"}))
                    j.description = clean_text((info.get("data") or info).get("jobDescription"))
                job.detail = detail
                out.append(job)
            if len(items) < 10 or (items and _old(_date(items[-1].get("postedTs")), ctx)):
                break
    return out


def eightfold_v2(host, domain, company, cities, ctx):
    seen, out = set(), []
    for city in cities:
        for start in range(0, 100, 10):
            d = fetch_json(f"https://{host}/api/apply/v2/jobs?" + urllib.parse.urlencode(
                {"domain": domain, "query": "", "location": city, "start": start, "num": 10, "sort_by": "relevance"}))
            items = d.get("positions") or []
            for p in items:
                if p["id"] in seen:
                    continue
                seen.add(p["id"])
                out.append(Job(company, company, p.get("name", ""), "; ".join(p.get("locations") or [p.get("location", "")]),
                               p.get("canonicalPositionUrl") or f"https://{host}/careers/job/{p['id']}",
                               _date(p.get("t_create") or p.get("t_update")), clean_text(p.get("job_description"))))
            if len(items) < 10:
                break
    return out


# ---------------------------------------------------------------- Oracle Recruiting Cloud (Oracle, JPMorgan Chase)

def oracle_cloud(host, site, location_id, company, ctx):
    api = f"https://{host}/hcmRestApi/resources/latest"
    seen, out = set(), []
    for q in ats_queries(ctx)[:QUERIES]:
        for offset in (0, 25, 50):
            finder = (f'findReqs;siteNumber={site},limit=25,offset={offset},keyword="{q}",locationId={location_id},'
                      "sortBy=POSTING_DATES_DESC")
            d = fetch_json(f"{api}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder="
                           + urllib.parse.quote(finder, safe=';=,"'))
            items = ((d.get("items") or [{}])[0].get("requisitionList")) or []
            for r in items:
                if r["Id"] in seen or not _wanted(r.get("Title", "")):
                    continue
                seen.add(r["Id"])
                job = Job(company, company, r["Title"], r.get("PrimaryLocation") or "India",
                          f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}", _date(r.get("PostedDate")),
                          clean_text(r.get("ShortDescriptionStr")))

                def detail(j, rid=r["Id"]):
                    f = urllib.parse.quote(f'ById;Id="{rid}",siteNumber={site}', safe=';=,"')
                    it = (fetch_json(f"{api}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder={f}").get("items") or [{}])[0]
                    j.description = clean_text(" ".join(it.get(k) or "" for k in ("ExternalDescriptionStr", "ExternalQualificationsStr",
                                                                                  "ExternalResponsibilitiesStr")))
                job.detail = detail
                out.append(job)
            if len(items) < 25:
                break
    return out


# ---------------------------------------------------------------- Phenom (Cisco)

def phenom(host, company, ctx):
    seen, out = set(), []
    for q in ats_queries(ctx)[:QUERIES]:
        for start in (0, 50):
            d = fetch_json(f"https://{host}/widgets", data={
                "lang": "en_global", "country": "global", "pageName": "search-results", "ddoKey": "refineSearch",
                "from": start, "size": 50, "jobs": True, "counts": True, "siteType": "external", "keywords": q,
                "global": True, "selected_fields": {"country": ["India"]}})
            items = ((d.get("refineSearch") or {}).get("data") or {}).get("jobs") or []
            for j in items:
                jid = j.get("jobSeqNo") or j.get("jobId")
                if jid in seen:
                    continue
                seen.add(jid)
                out.append(Job(company, company, j.get("title", ""), j.get("cityStateCountry") or j.get("location") or "India",
                               j.get("applyUrl") or f"https://{host}/global/en/job/{j.get('jobId')}", _date(j.get("postedDate") or j.get("dateCreated")),
                               clean_text(" ".join([j.get("descriptionTeaser") or "", ", ".join(j.get("ml_skills") or [])]))))
            if len(items) < 50:
                break
    return out


# ---------------------------------------------------------------- one-off sites

def ibm(ctx):
    seen, out = set(), []
    for q in ats_queries(ctx)[:QUERIES]:
        d = fetch_json("https://www-api.ibm.com/search/api/v2", data={
            "appId": "careers", "scopes": ["careers2"], "size": 50, "from": 0, "lang": "zz",
            "_source": ["title", "url", "description", "field_keyword_19", "field_keyword_05", "dcdate"],
            "query": {"bool": {"must": [{"simple_query_string": {"query": q, "fields": ["title^3", "description^2", "body"]}}],
                               "filter": [{"term": {"field_keyword_05": "India"}}]}}})
        for h in (d.get("hits") or {}).get("hits") or []:
            s = h.get("_source") or {}
            if not s.get("url") or s["url"] in seen:
                continue
            seen.add(s["url"])
            out.append(Job("IBM", "IBM", s.get("title", ""), f"{s.get('field_keyword_19') or ''}, India".strip(", "), s["url"],
                           _date(s.get("dcdate")), clean_text(s.get("description"))))
    return out


GS_QUERY = """query RoleSearch($input: RoleSearchQueryInput!) { roleSearch(searchQueryInput: $input) {
  totalCount items { roleId jobTitle locations { city country } } } }"""


def goldman(ctx):
    seen, out = set(), []
    for q in ats_queries(ctx)[:3]:
        d = fetch_json("https://api-higher.gs.com/gateway/api/v1/graphql", data={"operationName": "RoleSearch", "query": GS_QUERY,
                       "variables": {"input": {"page": {"pageSize": 50, "pageNumber": 0}, "searchTerm": q,
                                               "experiences": ["EARLY_CAREER", "PROFESSIONAL"],
                                               "filters": [{"filterCategoryType": "LOCATION", "filters": [{"filter": "India", "subFilters": []}]}]}}})
        for r in (((d.get("data") or {}).get("roleSearch") or {}).get("items")) or []:
            if r["roleId"] in seen:
                continue
            seen.add(r["roleId"])
            where = "; ".join(f"{loc.get('city')}, {loc.get('country')}" for loc in r.get("locations") or [])
            out.append(Job("Goldman Sachs", "Goldman Sachs", r.get("jobTitle", ""), where or "India",
                           f"https://higher.gs.com/roles/{r['roleId']}", ctx["now"]))     # no date given: dated by this search
    return out


def atlassian(ctx):
    out = []
    for j in fetch_json("https://www.atlassian.com/endpoint/careers/listings") or []:
        where = "; ".join(j.get("locations") or [])
        if not filters.INDIA.search(where):
            continue
        out.append(Job("Atlassian", "Atlassian", j.get("title", ""), where, j.get("applyUrl") or "https://www.atlassian.com/company/careers",
                       _date((j.get("portalJobPost") or {}).get("updatedDate")),
                       clean_text(" ".join(j.get(k) or "" for k in ("overview", "responsibilities", "qualifications")))))
    return out


def phonepe(ctx):
    out = []
    d = fetch_json("https://www.phonepe.com/apollo/job-postings/latest.json") or {}
    for j in d.get("results") or []:
        if j.get("status") in ("NOT_PUBLISHED", "PRIVATE", "CLOSED"):
            continue
        out.append(Job("PhonePe", "PhonePe", j.get("title", ""), f"{j.get('location') or ''}, India".strip(", "),
                       j.get("applyUrl") or "https://www.phonepe.com/careers/", _date(j.get("updatedAt")),
                       clean_text(f"{j.get('department') or ''} {j.get('type') or ''}")))
    return out


def capgemini(ctx):
    seen, out = set(), []
    for q in ats_queries(ctx)[:QUERIES]:
        d = fetch_json("https://cg-jobstream-api.azurewebsites.net/api/job-search?" +
                       urllib.parse.urlencode({"country_code": "in-en", "size": 50, "page": 1, "search": q}))
        for j in d.get("data") or []:
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            out.append(Job("Capgemini", "Capgemini", j.get("title", ""), f"{j.get('location') or ''}, India".strip(", "),
                           j.get("apply_job_url") or "https://www.capgemini.com/in-en/careers/", _date(j.get("updated_at") or j.get("indexed_at")),
                           clean_text(j.get("description_stripped") or j.get("description"))))
    return out


# ---------------------------------------------------------------- what runs

PORTALS = {
    "microsoft": lambda ctx: eightfold_pcsx("apply.careers.microsoft.com", "microsoft.com", "Microsoft", ctx),
    "qualcomm": lambda ctx: eightfold_pcsx("careers.qualcomm.com", "qualcomm.com", "Qualcomm", ctx),
    "netflix": lambda ctx: eightfold_v2("explore.jobs.netflix.net", "netflix.com", "Netflix", ["Mumbai", "Bengaluru", "Bangalore"], ctx),
    "oracle": lambda ctx: oracle_cloud("eeho.fa.us2.oraclecloud.com", "CX_45001", "300000000106947", "Oracle", ctx),
    "jpmorgan": lambda ctx: oracle_cloud("jpmc.fa.oraclecloud.com", "CX_1001", "300000000289360", "JPMorgan Chase", ctx),
    "cisco": lambda ctx: phenom("careers.cisco.com", "Cisco", ctx),
    "ibm": ibm,
    "goldman": goldman,
    "atlassian": atlassian,
    "phonepe": phonepe,
    "capgemini": capgemini,
}


def tasks(cfg, ctx):
    on = cfg.get("portals") or {}
    return [(f"portal:{name}", (lambda fn=fn: fn(ctx))) for name, fn in PORTALS.items() if on.get(name, True)]
