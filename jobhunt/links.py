"""Pre-filtered search links for job sites that can't be queried automatically (their terms forbid scraping).

Each opens in your own browser, already filtered to your role, India, entry level and recent postings, so you
search them by hand in one click. URL formats checked 2026-10-16.
"""
from urllib.parse import quote, quote_plus

ROLES = {
    "SDE": {"label": "Software engineer", "query": "software engineer", "slug": "software-engineer",
            "internshala": "software-development", "cutshort": "software-engineer-jobs"},
    "ML": {"label": "ML / AI engineer", "query": "machine learning engineer", "slug": "machine-learning-engineer",
           "internshala": "machine-learning", "cutshort": "machine-learning-jobs"},
}


def search_links(roles):
    out = []
    for role in roles:
        r = ROLES.get(role)
        if not r:
            continue
        q = r["query"]
        out.append({"role": r["label"], "links": [
            {"site": "LinkedIn", "note": "entry level + internships, last 24 h",
             "url": f"https://www.linkedin.com/jobs/search/?keywords={quote(q)}&location=India&geoId=102713980"
                    f"&f_E=1%2C2&f_TPR=r86400"},
            {"site": "Naukri", "note": "0 years experience, last day",
             "url": f"https://www.naukri.com/{r['slug']}-jobs?k={quote(q)}&experience=0&jobAge=1"},
            {"site": "Indeed India", "note": "fresher, last 24 h",
             "url": f"https://in.indeed.com/jobs?q={quote_plus(q + ' fresher')}&l=India&fromage=1"},
            {"site": "Internshala", "note": "fresher jobs",
             "url": f"https://internshala.com/jobs/{r['internshala']}-jobs/"},
            {"site": "Internshala", "note": "internships",
             "url": f"https://internshala.com/internships/{r['internshala']}-internship/"},
            {"site": "Wellfound", "note": "startups in India",
             "url": f"https://wellfound.com/role/l/{r['slug']}/india"},
            {"site": "Cutshort", "note": "India",
             "url": f"https://cutshort.io/jobs/{r['cutshort']}"},
            {"site": "Foundit", "note": "0-2 years",
             "url": f"https://www.foundit.in/srp/results?query={quote(q)}&locations=India&experienceRanges=0~2"},
            {"site": "Google Jobs", "note": "all sites at once",
             "url": f"https://www.google.com/search?q={quote_plus(q + ' fresher jobs in India')}&udm=8"},
        ]})
    return out
