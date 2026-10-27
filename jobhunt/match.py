"""Rank jobs against one or more resumes and explain each match.

score (0-100) = 40% skill coverage + 15% text similarity + 15% role fit + 20% level fit + 10% recency

- Skill coverage: of the skills the posting names (same taxonomy as the resume), how many you have,
  each weighted by rarity across today's postings (IDF), so "Python" counts less than "LangGraph".
  Postings naming few skills lean on text similarity instead.
- Text similarity: TF-IDF cosine between resume text and posting, scaled to today's best matches.
- Role fit: how SDE- or ML-leaning the resume is, against the posting's family.
- Level fit: new-grad roles fit best; internships score lower once you've graduated (most need enrollment).
- Recency: newer is better, linear over the search window.
With several resumes, every job is scored against each and the best one is named as the one to send.
"""
import math
import re
from collections import Counter

from .skills import find_skills

WEIGHTS = {"skills": 0.40, "text": 0.15, "role": 0.15, "level": 0.20, "recency": 0.10}

STOP = set("""a an and are as at be by for from has have in is it its of on or our that the their this to we will with
you your they them us who what which how all any can may more most other some such into than then there these those
about across after also both each few he her his i if me my no nor not only own same so too very s t just don
should now job role team work working company opportunity candidate candidates experience years year strong
ability skills skill knowledge required requirements preferred qualifications responsibilities including
etc using use used new build building help well across within per""".split())
TOKEN = re.compile(r"[a-z][a-z0-9+#.]{1,30}")


def _tokens(text):
    return [t.rstrip(".") for t in TOKEN.findall(text.lower()) if t.rstrip(".") not in STOP]


def _tfidf(counter, idf):
    v = {t: (1 + math.log(c)) * idf.get(t, 0.0) for t, c in counter.items()}
    n = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / n for t, x in v.items()}


def _cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def _age_text(days):
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"


def score_all(jobs, profiles, now, days):
    if not jobs:
        return
    n = len(jobs)
    job_skills, job_title_skills, job_tokens = [], [], []
    for j in jobs:
        job_skills.append(find_skills(f"{j.title}\n{j.title}\n{j.description}"))
        job_title_skills.append(set(find_skills(j.title)))
        job_tokens.append(Counter(_tokens(f"{j.title} {j.title} {j.description}")))

    skill_df = Counter(s for js in job_skills for s in js)
    skill_idf = {s: math.log((n + 1) / (df + 1)) + 1 for s, df in skill_df.items()}
    tok_df = Counter(t for c in job_tokens for t in c)
    tok_idf = {t: math.log((n + 1) / (df + 1)) + 1 for t, df in tok_df.items()}
    job_vecs = [_tfidf(c, tok_idf) for c in job_tokens]

    per_profile = []
    for p in profiles:
        vec = _tfidf(Counter(_tokens(p["text"])), tok_idf)
        sims = [_cos(vec, jv) for jv in job_vecs]
        ref = sorted(sims)[int(0.9 * (n - 1))] or max(sims) or 1.0     # 90th percentile = "a very good match"
        per_profile.append((p, set(p["skills"]), [min(1.0, s / ref) for s in sims]))

    for i, j in enumerate(jobs):
        best = None
        for p, have, sims in per_profile:
            r = _score_one(j, p, have, job_skills[i], job_title_skills[i], skill_idf, sims[i], now, days)
            if best is None or r["score"] > best["score"]:
                best = r
        j.score = best["score"]
        j.parts = best["parts"]
        j.matched, j.missing, j.reasons = best["matched"], best["missing"], best["reasons"]
        j.resume = best["resume"]
    jobs.sort(key=lambda j: (-j.score, j.age_days(now) or 0))


def _score_one(job, prof, have, jskills, title_skills, idf, text_sim, now, days):
    w = {s: idf.get(s, 1.0) * (1.6 if s in title_skills else 1.0) for s in jskills}
    total = sum(w.values())
    matched = sorted((s for s in jskills if s in have), key=lambda s: -w[s])
    missing = sorted((s for s in jskills if s not in have), key=lambda s: -w[s])
    coverage = sum(w[s] for s in matched) / total if total else 0.0
    confidence = min(1.0, len(jskills) / 5)          # 1-2 named skills is thin evidence
    skill_part = confidence * coverage + (1 - confidence) * text_sim

    role_part = prof["role_scores"].get(job.kind, 0.5)

    ft = prof.get("fulltime_months", 0)
    if job.level == "Intern":
        level_part = 0.4 if prof.get("graduated") else 1.0
    elif job.level in ("New grad", "0 yrs"):
        level_part = 1.0
    elif job.min_years == 1:
        level_part = 1.0 if ft >= 12 else 0.75
    elif job.min_years == 2:
        level_part = 1.0 if ft >= 24 else 0.25
    else:
        level_part = 0.6

    age = job.age_days(now) or 0
    recency_part = max(0.0, 1 - age / max(days, 1))

    parts = {"skills": skill_part, "text": text_sim, "role": role_part, "level": level_part, "recency": recency_part}
    score = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()), 1)

    reasons = []
    if jskills:
        top = ", ".join(matched[:6]) if matched else "none of them"
        reasons.append(f"You have {len(matched)} of the {len(jskills)} skills it names: {top}")
    else:
        reasons.append("The posting names few specific skills, so this leans on overall text similarity")
    lean = max(prof["role_scores"], key=prof["role_scores"].get)
    if role_part >= 0.8:
        reasons.append(f"{job.kind} role, which matches your resume")
    else:
        reasons.append(f"{job.kind} role, but this resume leans {lean}")
    if job.level == "Intern":
        reasons.append("Internship: most expect you to still be enrolled" if prof.get("graduated")
                       else "Internship, which fits a current student")
    elif job.level in ("New grad", "0 yrs"):
        reasons.append("Entry level / new-grad role")
    elif job.min_years is not None:
        reasons.append(f"Asks for {job.min_years}+ years of experience")
    else:
        reasons.append("Level not stated in the posting")
    reasons.append(f"Posted {_age_text(age)}")
    if job.also_on:
        reasons.append("Also listed on " + ", ".join(job.also_on))

    return {"score": score, "parts": {k: round(v, 2) for k, v in parts.items()}, "matched": matched,
            "missing": missing[:6], "reasons": reasons, "resume": prof["label"]}
