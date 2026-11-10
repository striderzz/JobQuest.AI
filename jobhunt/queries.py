"""What to type into each job board's search box.

Built from your roles, your resume's top skills, the ways Indian postings word entry-level roles, and synonyms, so a
search finds "SDE-1", "Graduate Engineer Trainee" and "GenAI Engineer" as well as "software engineer". When a model is
ready, Claude (or your planning model) suggests more phrases once per profile change; the result is cached.
Spelling slips in anything you type are fixed against a vocabulary of role words, skills and company names.
"""
import difflib
import json
import re

from . import db

ROLE_TERMS = {
    "SDE": ["software engineer", "software developer", "software development engineer", "SDE", "backend developer",
            "full stack developer", "python developer", "java developer", "associate software engineer"],
    "ML": ["machine learning engineer", "AI engineer", "data scientist", "applied scientist", "ML engineer",
           "generative AI engineer", "LLM engineer", "data engineer", "NLP engineer", "computer vision engineer"],
}
# How Indian postings say "entry level"
INDIA_LEVEL = ["fresher", "freshers", "entry level", "graduate", "new grad", "0-1 years", "0-2 years", "junior",
               "trainee", "graduate engineer trainee", "GET", "SDE 1", "SDE I", "campus", "off campus", "2025 batch",
               "2026 batch", "intern", "internship", "associate"]
SYNONYMS = [
    {"machine learning", "ml", "ai", "artificial intelligence", "deep learning"},
    {"software engineer", "software developer", "sde", "swe", "software development engineer", "programmer"},
    {"data scientist", "decision scientist", "data science"},
    {"generative ai", "genai", "gen ai", "llm", "large language model"},
    {"backend", "back end", "back-end", "server side"},
    {"frontend", "front end", "front-end", "ui developer"},
    {"fresher", "entry level", "new grad", "graduate", "0-1 years", "junior", "trainee"},
    {"bengaluru", "bangalore"}, {"gurugram", "gurgaon"}, {"mumbai", "bombay"}, {"chennai", "madras"},
]
MAX_ATS = 10          # phrases per company search box (each one costs a request or three per board)
MAX_AGGREGATOR = 12   # phrases for Adzuna, JSearch and Jooble (their free tiers have monthly limits)


def synonyms(term):
    t = term.lower().strip()
    for group in SYNONYMS:
        if t in group:
            return sorted(group - {t})
    return []


def vocabulary(skills=(), companies=()):
    from .normalize import VOCAB
    words = set(VOCAB)
    for phrase in [p for terms in ROLE_TERMS.values() for p in terms] + INDIA_LEVEL + list(skills) + list(companies):
        words.update(w for w in re.findall(r"[a-z][a-z0-9+#.]+", phrase.lower()) if len(w) > 2)
    for group in SYNONYMS:
        for phrase in group:
            words.update(re.findall(r"[a-z]+", phrase))
    return sorted(words)


def fix_typos(text, vocab=None):
    """'machin lerning engneer' -> 'machine learning engineer'. Short words, numbers and known words are left alone."""
    vocab = vocab or vocabulary()
    known = set(vocab)
    out = []
    for w in re.split(r"(\s+)", text):
        lw = w.lower()
        if len(lw) < 4 or not lw.isalpha() or lw in known:
            out.append(w)
            continue
        close = difflib.get_close_matches(lw, vocab, n=1, cutoff=0.8)
        out.append(close[0] if close else w)
    return "".join(out)


def rule_queries(roles, skills=()):
    """No AI needed: role terms with entry-level words, plus 'skill developer fresher' for the top skills."""
    ats, agg = [], []
    lists = [ROLE_TERMS[r] for r in (roles or ["SDE", "ML"]) if r in ROLE_TERMS]
    for i in range(max((len(t) for t in lists), default=0)):        # take turns, so every role gets its best terms
        ats += [t[i] for t in lists if i < len(t)]
    for i in range(4):
        agg += [f"{t[i]} fresher" for t in lists]
    agg += [f"{t[0]} entry level" for t in lists] + [f"graduate {t[0]}" for t in lists]
    ats = ats[:MAX_ATS - 3] + ["graduate engineer trainee", "intern", "new grad"] + ats[MAX_ATS - 3:]
    for s in [s for s in skills if 2 < len(s) < 25][:4]:
        agg.append(f"{s} developer fresher")
    return _clean(ats, MAX_ATS * 2), _clean(agg, MAX_AGGREGATOR * 2)


def _clean(items, n):
    seen, out = set(), []
    for q in items:
        q = re.sub(r"\s+", " ", str(q)).strip()[:60]
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:n]


PLAN_PROMPT = (
    "You plan job-board searches for one entry-level tech candidate in India. Given the roles and skills below, "
    "write short search phrases (2-5 words) that real Indian job postings use for roles this person can get: "
    "include fresher/0-2 years wording, synonyms (SDE, GenAI, applied scientist), and skill-led titles. No company "
    "names, no locations, no senior roles. Reply with JSON only: {\"ats\": [up to 10 phrases for company career-site "
    "search boxes], \"aggregator\": [up to 12 phrases for job aggregators]}.")


def plan(roles, skills=(), use_ai=True):
    """The phrases for this search: {'ats': [...], 'aggregator': [...]}. AI suggestions are cached per profile."""
    ats, agg = rule_queries(roles, skills)
    key = json.dumps([sorted(roles or []), list(skills)[:12]], sort_keys=True)
    cached = db.get_setting("query_plan") or {}
    extra = cached.get("plan") if cached.get("key") == key else None
    if extra is None and use_ai:
        extra = _ai_plan(roles, skills)
        if extra is not None:
            db.set_setting("query_plan", {"key": key, "plan": extra})
    extra = extra or {}
    return {"ats": _clean(ats[:6] + list(extra.get("ats", [])) + ats[6:], MAX_ATS),
            "aggregator": _clean(agg[:6] + list(extra.get("aggregator", [])) + agg[6:], MAX_AGGREGATOR)}


def _ai_plan(roles, skills):
    from . import llm
    try:
        text, _ = llm.chat([{"role": "system", "content": PLAN_PROMPT},
                            {"role": "user", "content": f"Roles: {', '.join(roles or ['SDE', 'ML'])}\nSkills: {', '.join(list(skills)[:20])}"}],
                           800, feature="planning")
        d = json.loads(text[text.find("{"):text.rfind("}") + 1])
        return {"ats": [str(x) for x in d.get("ats", [])][:MAX_ATS], "aggregator": [str(x) for x in d.get("aggregator", [])][:MAX_AGGREGATOR]}
    except Exception:            # no model, offline, odd reply: the rule-based phrases are enough on their own
        return None
