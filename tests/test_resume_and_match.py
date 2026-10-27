import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import docx

from jobhunt import match, resume
from jobhunt.models import Job
from jobhunt.skills import find_skills

LINES = [
    "Priya Sharma",
    "Software Engineer | Backend & Cloud",
    "Pune, India | +91 98765 43210 | priya.sharma@example.com | github.com/priya-example",
    "EDUCATION",
    "Indian Institute of Technology Example",
    "B.Tech in Computer Science   Aug 2022 – May 2026",
    "SKILLS",
    "Languages: Java, Python, Go, SQL",
    "Frameworks: Spring Boot, React, Node.js, Docker, Kubernetes, AWS",
    "EXPERIENCE",
    "Example Payments",
    "Software Engineering Intern   May 2025 - Jul 2025",
    "• Built REST APIs in Spring Boot serving 2M requests a day",
    "• Moved CI/CD to GitHub Actions",
    "PROJECTS",
    "Distributed Key-Value Store",
    "• Implemented Raft consensus in Go with gRPC",
]


class ResumeParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "Priya Resume.docx"
        d = docx.Document()
        for line in LINES:
            d.add_paragraph(line)
        d.save(path)
        cls.p = resume.parse(path, today=date(2026, 10, 15))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_contact(self):
        self.assertEqual(self.p["name"], "Priya Sharma")
        self.assertEqual(self.p["email"], "priya.sharma@example.com")
        self.assertIn("98765", self.p["phone"])
        self.assertEqual(self.p["location"], "Pune, India")
        self.assertTrue(self.p["github"].endswith("github.com/priya-example"))

    def test_education(self):
        self.assertEqual(self.p["highest_degree"], "Bachelor's")
        self.assertEqual(self.p["graduation"], "May 2026")
        self.assertTrue(self.p["graduated"])

    def test_skills_and_role(self):
        for s in ("Java", "Python", "Go", "Spring Boot", "React", "Docker", "Kubernetes", "AWS", "REST APIs", "gRPC"):
            self.assertIn(s, self.p["skills"], s)
        self.assertEqual(self.p["roles"][0], "SDE")
        self.assertEqual(self.p["internship_months"], 3)

    def test_entries(self):
        names = [e["name"] for e in self.p["entries"]]
        self.assertIn("Software Engineering Intern at Example Payments", names)
        self.assertIn("Distributed Key-Value Store", names)


class SkillMatching(unittest.TestCase):
    def test_short_names_need_list_context(self):
        self.assertIn("Go", find_skills("Languages: Python, Go, Rust"))
        self.assertNotIn("Go", find_skills("Let's go to market. Go ahead."))
        self.assertNotIn("C", find_skills("Schedule C, P&L documents"))
        self.assertNotIn("React", find_skills("ReAct agents with a critic loop"))


class Ranking(unittest.TestCase):
    def test_relevant_job_outranks_unrelated(self):
        now = datetime.now(timezone.utc)
        prof = resume.build_profile("\n".join(LINES), label="priya", today=date(2026, 10, 15))
        good = Job("Lever", "A", "Backend Engineer, New Grad", "Pune, India", "u1", now - timedelta(days=1),
                   "Java, Spring Boot, Docker, Kubernetes, AWS, REST APIs, microservices")
        bad = Job("Lever", "B", "Frontend Developer, New Grad", "Pune, India", "u2", now - timedelta(days=1),
                  "Angular, Vue, Redux, Tailwind, iOS, Swift")
        for j in (good, bad):
            j.kind, j.level = "SDE", "New grad"
        match.score_all([bad, good], [prof], now, 14)
        self.assertGreater(good.score, bad.score)
        self.assertIn("Spring Boot", good.matched)


if __name__ == "__main__":
    unittest.main()
