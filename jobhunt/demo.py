"""Fill a throwaway JOBHUNT_HOME with fictional data, for screenshots and trying the app without a resume.

    set JOBHUNT_HOME=%TEMP%\\jobquest-demo
    python -m jobhunt demo
    python -m jobhunt serve --port 8010

Every person, company and email here is invented (example.com / .example domains).
"""
import json
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import db, profile, settings

COMPANIES = {  # fictional company -> tier, for the [tiers] override written into the demo home
    "Northwind Labs": "big_tech", "Contoso AI": "big_tech", "Fabrikam Cloud": "big_tech",
    "Tailspin Data": "us_unicorn", "Wingtip Systems": "us_unicorn", "Adatum Robotics": "us_unicorn",
    "Litware Fintech": "startup", "Proseware Health": "startup", "Lucerne Logistics": "startup", "Fourth Pixel": "startup",
    "Woodgrove Bank Tech": "mid", "Alpine Analytics": "mid", "Blue Yonder Retail": "mid", "Coho Mobility": "mid",
    "Graphic Ocean": "mid", "Humongous Insurance": "mid", "Margie Travel Tech": "mid", "Trey Research": "mid",
}
ROLES = [("Software Engineer I", "SDE"), ("Machine Learning Engineer", "ML"), ("Backend Developer (Python)", "SDE"),
         ("Data Scientist - NLP", "ML"), ("SDE - New Grad 2026", "SDE"), ("AI Engineer, LLM Apps", "ML"),
         ("Full Stack Engineer", "SDE"), ("ML Engineer Intern", "ML"), ("Applied Scientist I", "ML"),
         ("Platform Engineer (Go)", "SDE"), ("Computer Vision Engineer", "ML"), ("Data Engineer", "SDE")]
CITIES = ["Bengaluru", "Hyderabad", "Pune", "Gurugram", "Chennai", "Mumbai", "Noida"]
SKILLS = ["Python", "PyTorch", "SQL", "AWS", "Docker", "FastAPI", "LLMs", "RAG", "Kubernetes", "Java", "React", "Spark"]

RESUME_TEXT = ("Asha Verma - Software / ML Engineer. asha.verma@example.com. MS Computer Science, State University (2026). "
               "B.Tech Computer Science, Pune (2023). Experience: ML Intern at Trey Research (6 months) - built a RAG "
               "search service in Python and FastAPI that cut support lookup time by 40%. Software Engineer Intern at "
               "Alpine Analytics (3 months) - shipped data pipelines in Spark and SQL. Projects: real-time object "
               "detection with PyTorch; LLM evaluation harness. Skills: Python, PyTorch, SQL, AWS, Docker, FastAPI, "
               "LLMs, RAG, React, Git.")


def seed(days=30, seed_value=7):
    if settings.HOME.resolve() == (Path.home() / ".jobhuntai").resolve():
        raise SystemExit("Refusing to fill your real data folder. Set JOBHUNT_HOME to an empty folder first.")
    rnd = random.Random(seed_value)
    settings.ensure_dirs()
    tiers = {t: [c.lower() for c, k in COMPANIES.items() if k == t] for t in ("big_tech", "us_unicorn", "startup")}
    (settings.HOME / "config.local.toml").write_text(
        "[tiers]\n" + "".join(f"{t} = {json.dumps(v)}\n" for t, v in tiers.items()), encoding="utf-8")

    parsed = {"name": "Asha Verma", "email": "asha.verma@example.com", "phone": "+91 90000 00000", "location": "Pune",
              "linkedin": "linkedin.com/in/example", "github": "github.com/example", "headline": "Software / ML Engineer",
              "education": [{"degree": "MS", "field": "Computer Science", "school": "State University"}],
              "entries": [{"name": "ML Intern - Trey Research"}, {"name": "SWE Intern - Alpine Analytics"}],
              "skills": SKILLS, "role_scores": {"ML": 0.8, "SDE": 0.7}, "fulltime_months": 0, "internship_months": 9,
              "graduation": "2026-05", "roles": ["ML", "SDE"], "text": RESUME_TEXT}
    ai = {"headline": "MS CS graduate building ML and backend systems", "skills": SKILLS,
          "education": [{"degree": "MS", "field": "Computer Science", "school": "State University", "end": "2026"}],
          "strengths": ["RAG systems in production", "Python + FastAPI services", "PyTorch computer vision"],
          "target_roles": ["ML Engineer", "Software Engineer"], "fulltime_experience_months": 0, "internship_months": 9}
    db.add_resume("ML resume", "asha_ml.docx", str(settings.RESUMES / "asha_ml.docx"), parsed, ai)
    profile.save_preferences({"current_city": "Pune", "preferred_cities": ["Bengaluru", "Hyderabad", "Pune"],
                              "roles": ["SDE", "ML"], "about": "Back in India after an MS in the US; open to relocation."})

    now = datetime.now(timezone.utc)
    with db.conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}

        def insert(**row):
            row = {k: v for k, v in {**base, **row}.items() if k in cols}
            c.execute(f"INSERT INTO jobs({', '.join(row)}) VALUES({', '.join('?' * len(row))})", tuple(row.values()))

        base = dict(cities="[]", remote="", also_on="[]", matched="[]", missing="[]", reasons="[]", best_resume="",
                    description="", first_seen=now.isoformat(), last_seen=now.isoformat(), notes="", status="")
        # open jobs on the Jobs page
        for i in range(34):
            company = rnd.choice(list(COMPANIES))
            title, kind = rnd.choice(ROLES)
            city = rnd.choice(CITIES)
            fit = rnd.randint(52, 96)
            verdict = "strong" if fit >= 85 else "good" if fit >= 70 else "stretch"
            matched = rnd.sample(SKILLS, 5)
            missing = rnd.sample([s for s in ["Kafka", "Go", "Terraform", "Airflow", "CUDA", "GraphQL"]], 2)
            assessment = {"years_required": rnd.choice([None, 0, 1, 2]), "entry_level_ok": True, "in_india": True,
                          "work_mode": rnd.choice(["onsite", "hybrid", "remote"]), "fit_score": fit, "verdict": verdict,
                          "reasons": [f"Hands-on {matched[0]} and {matched[1]} from the RAG internship",
                                      f"Projects show {matched[2]} at the level the team asks for"],
                          "missing_skills": missing, "must_haves": matched[:3] + [f"0-2 years ({kind})"],
                          "best_resume": "ML resume", "role_match": True,
                          "contacts": [{"name": "Priya Nair", "role": "Talent Partner", "email": "priya.nair@" +
                                        company.split()[0].lower() + ".example", "phone": ""}] if i % 5 == 0 else []}
            posted = now - timedelta(hours=rnd.randint(2, 24 * 9))
            insert(key=f"demo|{uuid.uuid4().hex}", source=rnd.choice(["Greenhouse", "Lever", "Ashby", "Workday"]),
                   company=company, title=title, location=f"{city}, India", cities=json.dumps([city]),
                   url=f"https://careers.example.com/{company.split()[0].lower()}/{i}", posted=posted.isoformat(),
                   kind=kind, level="Intern" if "Intern" in title else "New grad" if "New Grad" in title else "Entry",
                   min_years=assessment["years_required"], h_score=fit - rnd.randint(0, 12), score=fit,
                   matched=json.dumps(matched), missing=json.dumps(missing), reasons=json.dumps(assessment["reasons"]),
                   best_resume="ML resume", ai_json=json.dumps(assessment), ai_rejected=0, active=1,
                   first_seen=(now - timedelta(hours=rnd.randint(1, 60))).isoformat(),
                   description=f"{company} is hiring a {title} in {city}. You will build {kind} systems with "
                               f"{', '.join(matched[:3])}. 0-2 years of experience." +
                               (f" Questions? Write to priya.nair@{company.split()[0].lower()}.example" if i % 5 == 0 else ""))
        # tracker history: a realistic month of applications
        statuses = ["applied"] * 70 + ["screening"] * 6 + ["oa"] * 8 + ["interviewing"] * 5 + ["offer"] * 1 + \
                   ["rejected"] * 18 + ["ghosted"] * 9 + ["saved"] * 6
        sources = ["Company site", "LinkedIn", "Referral", "Naukri", "JobQuest.AI", "Cold email"]
        for i, st in enumerate(statuses):
            d = date.today() - timedelta(days=min(1 + int(rnd.expovariate(1 / 9)), days - 1))
            company = rnd.choice(list(COMPANIES))
            title, _ = rnd.choice(ROLES)
            insert(key=f"manual|{uuid.uuid4().hex}", source="Added by you", company=company, title=title,
                   location=rnd.choice(CITIES), url="", posted=None, kind="", level="", h_score=0, score=0, active=0,
                   status=st, status_at=f"{d.isoformat()}T10:00:00+00:00", applied_on=d.isoformat(),
                   job_type=rnd.choice(["Full-time", "Full-time", "Full-time", "Internship"]),
                   app_source=rnd.choice(sources), last_update=(d + timedelta(days=rnd.randint(0, 6))).isoformat(),
                   next_step={"oa": "Finish the online test", "interviewing": "Prepare system design",
                              "screening": "Recruiter call Thursday"}.get(st, ""),
                   contact="Priya Nair" if st in ("screening", "interviewing") else "")
        for _ in range(7):                                      # today's progress toward the target
            company = rnd.choice(list(COMPANIES))
            insert(key=f"manual|{uuid.uuid4().hex}", source="Added by you", company=company,
                   title=rnd.choice(ROLES)[0], location=rnd.choice(CITIES), url="", posted=None, kind="", level="",
                   h_score=0, score=0, active=0, status="applied", status_at=now.isoformat(),
                   applied_on=date.today().isoformat(), job_type="Full-time", app_source="Company site")

    rid = db.start_run()
    db.finish_run(rid, ok=1, raw=12840, kept=34, new=9, ai_checked=34,
                  sources={"Greenhouse": 4210, "Lever": 860, "Ashby": 1290, "SmartRecruiters": 2100, "Workday": 3980, "amazon.jobs": 400})
    db.set_setting("daily_target", 10)
    db.set_setting("coach_history", [
        {"role": "user", "content": "How do I get more interview calls for ML roles?"},
        {"role": "assistant", "provider": "OpenAI", "content":
            "## What your numbers say\n- 125 applications, **11% reach a screening or test** - solid for new grads.\n"
            "- Referrals convert about 3x better than cold applies in your tracker.\n\n## This week\n"
            "- Ask for **2 referrals a day** at Big Tech and US scale-ups.\n- Lead your resume with the RAG project "
            "and its 40% impact.\n- Apply within 48 hours of posting - use the New today filter."}])
    db.set_setting("ai_insights", {"provider": "OpenAI", "at": now.isoformat(timespec="seconds"), "text":
        "## Summary\nSteady volume (about 4 a day) with an 11% screen rate; offers are starting to come through.\n\n"
        "## Insights\n- Referral and cold-email applications respond about 3x more than job-board applies.\n"
        "- Tuesday and Wednesday applications get the most replies.\n- ML roles make up most of the pipeline; SDE "
        "roles respond faster.\n\n## Next 7 days\n- Hit 10 a day with 2 referrals or cold emails in each batch.\n"
        "- Follow up on applications older than 14 days without a reply.\n- Practise system design for the 5 "
        "interviews in progress."})
    db.set_setting("outreach_contacts", [
        {"id": uuid.uuid4().hex[:10], "name": "Priya Nair", "email": "priya.nair@tailspin.example", "title": "Talent Partner",
         "company": "Tailspin Data", "job_role": "ML Engineer (0-2 yrs)", "location": "Bengaluru",
         "notes": "Subject line: ML Engineer - <Your Name>", "added": now.isoformat()},
        {"id": uuid.uuid4().hex[:10], "name": "Karan Mehta", "email": "karan.m@litware.example", "title": "Engineering Manager",
         "company": "Litware Fintech", "job_role": "Backend SDE", "location": "Pune", "notes": "", "added": now.isoformat(),
         "sent_at": now.isoformat()}])
    return settings.HOME
