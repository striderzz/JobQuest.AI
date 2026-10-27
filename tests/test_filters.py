import unittest
from datetime import datetime, timezone

from jobhunt import filters as F
from jobhunt.models import Job


def job(title, desc="", location="Bengaluru, India", hints=()):
    return Job("Greenhouse", "Acme", title, location, "https://x", datetime.now(timezone.utc), desc, set(hints))


class Location(unittest.TestCase):
    def test_india_onsite(self):
        self.assertEqual(F.classify_location("Bengaluru, Karnataka, IND"), "")
        self.assertEqual(F.classify_location("New York; Pune, India"), "")

    def test_indiana_is_not_india(self):
        self.assertIsNone(F.classify_location("Indianapolis, Indiana"))

    def test_remote_only_within_india(self):
        self.assertEqual(F.classify_location("Remote - India"), "India")
        self.assertEqual(F.classify_location("Remote - Australia, Canada, India"), "India")
        for loc in ("Remote, APAC", "Remote - Worldwide", "Remote", "Remote - US"):
            self.assertIsNone(F.classify_location(loc), loc)

    def test_city_names(self):
        self.assertEqual(F.india_cities("Gurgaon, Haryana; Bangalore"), ["Delhi NCR", "Bengaluru"])
        self.assertEqual(F.india_cities("Hyderabad, Telangana, IND"), ["Hyderabad"])
        self.assertEqual(F.india_cities("Pune, India"), ["Pune"])           # regressed once: \b written as a backspace
        self.assertEqual(F.india_cities("New Delhi"), ["Delhi NCR"])


class Roles(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(F.role_kind("Software Dev Engineer I, L4"), "SDE")
        self.assertEqual(F.role_kind("Data Scientist I"), "ML")
        self.assertEqual(F.role_kind("Machine Learning Engineer, Fraud"), "ML")
        self.assertEqual(F.role_kind("Software Engineer, Risk"), "SDE")

    def test_not_engineering(self):
        for t in ("Solutions Engineer", "Business Analyst", "Financial Analyst Intern", "AI Data Rater - Dutch",
                  "Technical Support Engineer", "Engineering Expression of Interest Form"):
            self.assertIsNone(F.role_kind(t), t)

    def test_seniority(self):
        for t in ("Senior ML Engineer", "Software Engineer II", "SDE-2", "Staff Engineer", "Tech Lead"):
            self.assertTrue(F.title_is_senior(t), t)
        for t in ("Software Dev Engineer I, L4", "Associate Software Engineer", "SDE I"):
            self.assertFalse(F.title_is_senior(t), t)


class Years(unittest.TestCase):
    def test_largest_lower_bound(self):
        self.assertEqual(F.years_required("3+ years of software development experience - 1+ years of SQL experience"), 3)
        self.assertEqual(F.years_required("0-2 years of relevant work experience"), 0)
        self.assertEqual(F.years_required("Experience of 1 to 3 years as an SDE"), 1)
        self.assertEqual(F.years_required("Minimum two years of experience in Python"), 2)

    def test_ignores_non_requirements(self):
        self.assertIsNone(F.years_required("We are 25 years old. Freshers welcome"))
        self.assertEqual(F.years_required("Basic qualifications: 1+ years of experience. "
                                          "Preferred qualifications: 4+ years of experience"), 1)


class Level(unittest.TestCase):
    def test_labels_and_drops(self):
        self.assertEqual(F.level_of(job("Software Engineer Intern"), 2)[0], "Intern")
        self.assertEqual(F.level_of(job("Software Engineer, New Grad"), 2)[0], "New grad")
        self.assertEqual(F.level_of(job("Software Engineer", "0-2 years of experience"), 2)[0], "New grad")
        self.assertEqual(F.level_of(job("Backend Developer", "1+ years of experience required"), 2)[0], "1+ yrs")
        self.assertIsNone(F.level_of(job("Software Engineer", "3+ years of experience"), 2))
        self.assertIsNone(F.level_of(job("Software Engineer", "Build great things."), 2))   # level unstated
        self.assertIsNone(F.level_of(job("PhD Research Intern"), 2, profile={"degree_level": 3}))

    def test_source_hints(self):
        self.assertEqual(F.level_of(job("SDE", hints={"university"}), 2)[0], "New grad")


class Dedupe(unittest.TestCase):
    def test_same_job_two_sources(self):
        a = job("Associate Security Engineer - 11925", location="Bangalore, India")
        b = job("Associate Security Engineer", location="Bengaluru")
        self.assertEqual(F.dedupe_key(a), F.dedupe_key(b))


if __name__ == "__main__":
    unittest.main()
