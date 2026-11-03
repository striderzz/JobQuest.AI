"""Autocorrect for company and role names. Only ever *suggests*; the tracker changes when you tick and apply."""
import difflib
import re
from functools import lru_cache

from . import companies
from .db import norm_company


# ---------------------------------------------------------------- companies

@lru_cache(maxsize=1)
def _company_index():
    exact, fuzzy = {}, {}
    for name, aliases in companies.parsed():
        for a in [name] + aliases:
            key = norm_company(a)
            if key:
                exact.setdefault(key, name)
                if len(key) >= 5:                    # short keys ("ea", "tcs") only ever match exactly
                    fuzzy.setdefault(key, name)
    return exact, fuzzy


def company(name):
    """(suggested name, reason) or (None, None) when it already looks right."""
    raw = (name or "").strip()
    if not raw:
        return None, None
    exact, fuzzy = _company_index()
    key = norm_company(raw)
    hit, reason = exact.get(key), "known company"
    if not hit:                                       # "Razorpay Software Solutions" -> Razorpay
        base = re.sub(r"\b(?:software|solutions|services|labs|systems|global|digital|tech|it|consulting|group)\b", " ", key)
        hit = exact.get(re.sub(r"\s+", " ", base).strip())
    if not hit:
        close = difflib.get_close_matches(key, fuzzy.keys(), n=1, cutoff=0.88)
        if close:
            hit, reason = fuzzy[close[0]], "looks like a typo"
    if not hit and len(key) > 3 and (raw.islower() or raw.isupper()):
        hit, reason = " ".join(w.capitalize() for w in raw.split()), "capitalisation"
    clean = re.sub(r"\s+", " ", raw)
    if hit and hit != clean:
        return hit, reason
    if clean != raw:
        return clean, "extra spaces"
    return None, None


# ---------------------------------------------------------------- roles

# whole-title shorthands people type in sheets
ROLE_SHORT = {
    "mle": "Machine Learning Engineer", "ml": "Machine Learning Engineer", "ai": "AI Engineer", "ai/ml": "AI/ML Engineer", "ml/ai": "AI/ML Engineer",
    "sde": "Software Development Engineer", "swe": "Software Engineer", "se": "Software Engineer",
    "ds": "Data Scientist", "de": "Data Engineer", "da": "Data Analyst", "fe": "Frontend Engineer",
    "be": "Backend Engineer", "fs": "Full Stack Engineer", "sre": "Site Reliability Engineer",
    "sdet": "Software Development Engineer in Test", "qa": "QA Engineer", "pm": "Product Manager",
    "ba": "Business Analyst", "ios": "iOS Developer", "android": "Android Developer", "devops": "DevOps Engineer",
    "mlops": "MLOps Engineer", "genai": "Generative AI Engineer", "gen ai": "Generative AI Engineer",
    "nlp": "NLP Engineer", "cv": "Computer Vision Engineer", "llm": "LLM Engineer", "dl": "Deep Learning Engineer",
}
COMMON_ROLES = ["Software Engineer", "Software Development Engineer", "Software Development Engineer I",
                "Software Development Engineer II", "Associate Software Engineer", "Graduate Engineer Trainee",
                "Software Engineer Intern", "Machine Learning Engineer", "Machine Learning Engineer Intern",
                "AI Engineer", "AI/ML Engineer", "Applied Scientist", "Research Engineer", "Data Scientist",
                "Data Analyst", "Data Engineer", "Analytics Engineer", "Backend Engineer", "Backend Developer",
                "Frontend Engineer", "Frontend Developer", "Full Stack Engineer", "Full Stack Developer",
                "Python Developer", "Java Developer", "DevOps Engineer", "Cloud Engineer", "Site Reliability Engineer",
                "Platform Engineer", "MLOps Engineer", "Generative AI Engineer", "LLM Engineer", "NLP Engineer",
                "Computer Vision Engineer", "Deep Learning Engineer", "QA Engineer", "SDET", "Embedded Software Engineer",
                "Firmware Engineer", "Mobile Developer", "Android Developer", "iOS Developer", "Product Engineer",
                "Member of Technical Staff", "Solutions Engineer", "Security Engineer", "Game Developer"]

# word-level expansions inside longer titles
WORD = {
    "eng": "engineer", "sr.": "senior", "jr.": "junior", "eng.": "engineer", "dev.": "developer", "engg": "engineer", "engr": "engineer", "engineeer": "engineer", "dev": "developer",
    "devs": "developers", "sr": "senior", "snr": "senior", "jr": "junior", "mgr": "manager", "assoc": "associate",
    "sw": "software", "swe": "software engineer", "sde": "software development engineer", "mle": "machine learning engineer",
    "ml": "machine learning", "dl": "deep learning", "cv": "computer vision", "ds": "data science", "fullstack": "full stack",
    "frontend": "frontend", "front-end": "frontend", "backend": "backend", "back-end": "backend", "full-stack": "full stack",
    "intern.": "intern", "internship": "intern", "trainee": "trainee", "genai": "generative ai", "admin": "administrator",
    "sys": "systems", "infra": "infrastructure", "mgmt": "management", "analyt": "analytics", "prog": "programmer",
}
UPPER = {"ai": "AI", "ml": "ML", "nlp": "NLP", "llm": "LLM", "qa": "QA", "sdet": "SDET", "sre": "SRE", "ui": "UI",
         "ux": "UX", "api": "API", "aws": "AWS", "gcp": "GCP", "ios": "iOS", "mlops": "MLOps", "devops": "DevOps",
         "ii": "II", "iii": "III", "iv": "IV", "r&d": "R&D", "c++": "C++", ".net": ".NET", "bi": "BI", "etl": "ETL",
         "sap": "SAP", "it": "IT", "erp": "ERP", "hr": "HR", "vlsi": "VLSI", "rtl": "RTL", "fpga": "FPGA", "soc": "SoC",
         "and": "and", "of": "of", "in": "in", "for": "for", "the": "the", "to": "to", "&": "&", "/": "/", "-": "-"}
VOCAB = ["engineer", "developer", "software", "machine", "learning", "scientist", "science", "analyst", "intern",
         "data", "backend", "frontend", "full", "stack", "senior", "junior", "associate", "research", "applied",
         "platform", "infrastructure", "systems", "computer", "vision", "deep", "artificial", "intelligence",
         "generative", "cloud", "security", "embedded", "firmware", "mobile", "product", "manager", "graduate",
         "trainee", "analytics", "development", "programmer", "architect", "consultant", "specialist", "member",
         "technical", "staff", "quality", "automation", "reliability", "operations", "network", "test", "web"]


def _fix_word(w):
    if len(w) < 5 or w in VOCAB or not w.isalpha():
        return w
    close = difflib.get_close_matches(w, VOCAB, n=1, cutoff=0.84)
    return close[0] if close else w


def _case(w, first):
    if w in UPPER and not (first and w in ("and", "of", "in", "for", "the", "to")):
        return UPPER[w]
    return w[:1].upper() + w[1:]


def role(title):
    """(suggested title, reason) or (None, None)."""
    raw = re.sub(r"\s+", " ", (title or "").strip())
    if not raw:
        return None, None
    low = raw.lower()
    # "SDE 1", "MLE intern", "sde-2": keep the level / intern tag after a known shorthand
    m = re.fullmatch(r"([a-z/ ]+?)[\s\-_]*((?:[1-4]|i{1,3}|iv)?)\s*(intern|trainee)?", low)
    if m and m.group(1).strip() in ROLE_SHORT:
        new = ROLE_SHORT[m.group(1).strip()]
        lvl = m.group(2).upper()
        new += (f" {lvl}" if lvl else "") + (f" {m.group(3).title()}" if m.group(3) else "")
        return (new, "expanded shorthand") if new != raw else (None, None)
    # edit word by word inside the original, so punctuation and anything already right ("(Python)", ", LLM Apps") stays
    lower_only = raw == low
    why = set()

    def fix(m):
        w = m.group(0)
        lw = w.lower()
        slash = m.string[max(0, m.start() - 1):m.start()] == "/" or m.string[m.end():m.end() + 1] == "/"
        if lw in WORD and WORD[lw] != lw and not (slash and lw in ("ml", "dl", "cv", "ds")):   # "AI/ML" stays
            why.add("expanded shorthand")
            return " ".join(_case(x, False) for x in WORD[lw].split())
        fixed = _fix_word(lw)
        if fixed != lw:
            why.add("spelling")
            return _case(fixed, False)
        if lower_only:
            why.add("capitalisation")
            return _case(lw, m.start() == 0)
        return w

    new = re.sub(r"[A-Za-z][A-Za-z+#]*\.?", fix, raw)
    new = new[:1].upper() + new[1:]
    new = re.sub(r"\b(Engineer|Developer)( \1)+\b", r"\1", new)
    if new == raw:
        return None, None
    reason = next(r for r in ("expanded shorthand", "spelling", "capitalisation") if r in why) if why else "capitalisation"
    return new, reason


# ---------------------------------------------------------------- cleanup suggestions

def suggest(rows):
    """[{id, field, old, new, reason}] for every tracker row whose company or role could be tidied. Writes nothing."""
    out = []
    for r in rows:
        for field, fn, key in (("company", company, "company"), ("title", role, "title")):
            new, why = fn(r.get(key))
            if new:
                out.append({"id": r["id"], "field": field, "old": r.get(key) or "", "new": new, "reason": why})
    return out
