"""Round 4: Claude screening (Haiku triage, Opus deep check) and combined scores. No network, no spend."""
import json
import unittest
from datetime import date
from unittest import mock

from jobhunt import ai, claude, db, llm, profile, refresh, settings

GOOD = {"years_required": 0, "entry_level_ok": True, "in_india": True, "work_mode": "onsite", "fit_score": 80,
        "verdict": "good", "reasons": ["Python"], "missing_skills": [], "must_haves": [], "best_resume": "",
        "role_match": True, "contacts": []}


def add_job(company, score=50):
    jid = db.add_manual(company, "ML Engineer", date.today().isoformat(), status="saved")
    with db.conn() as c:
        c.execute("UPDATE jobs SET active=1, status='', score=?, h_score=?, description='Python ML' WHERE id=?", (score, score, jid))
    return jid


class Schema(unittest.TestCase):
    def test_strict_schema_is_flat_and_closed(self):
        s = ai.strict_schema(ai.JobAssessment)
        text = json.dumps(s)
        self.assertNotIn("$ref", text)
        self.assertNotIn("$defs", text)
        self.assertFalse(s["additionalProperties"])
        self.assertEqual(set(s["required"]), set(ai.JobAssessment.model_fields))
        contact = s["properties"]["contacts"]["items"]
        self.assertFalse(contact["additionalProperties"])


class ClaudeScreening(unittest.TestCase):
    def client(self):
        return ai.ClaudeAI("sk-ant-test", claude.HAIKU, "screening", {claude.HAIKU: [0.10, 0.50, 0.01]})

    def test_assess_parses_and_counts_cost(self):
        usage = {"tokens_in": 3000, "tokens_out": 300, "cache_read": 2500, "cache_write": 0}
        with mock.patch.object(claude, "complete", return_value=(json.dumps(GOOD), claude.HAIKU, usage)) as comp, \
                mock.patch.object(llm, "record"):
            c = self.client()
            res = c.assess("CANDIDATE", {"title": "ML", "company": "A", "location": "Pune", "description": "x"})
        self.assertEqual(res["fit_score"], 80)
        kwargs = comp.call_args.kwargs
        self.assertEqual(comp.call_args.args[3], "screening")
        self.assertIn("properties", kwargs["schema"])
        self.assertEqual((c.tokens_in, c.tokens_out), (3000, 300))
        self.assertAlmostEqual(c.cost(), (500 * 0.10 + 2500 * 0.01 + 300 * 0.50) / 1e6, places=7)

    def test_schema_rejection_switches_to_plain_json(self):
        calls = []

        def fake(msgs, key, model, feature, max_tokens, schema=None):
            calls.append(schema)
            if schema is not None:
                raise claude.ClaudeError("Claude: output_config.format schema not supported")
            return "Here: " + json.dumps(GOOD), model, {"tokens_in": 1, "tokens_out": 1, "cache_read": 0, "cache_write": 0}
        with mock.patch.object(claude, "complete", side_effect=fake), mock.patch.object(llm, "record"):
            c = self.client()
            res = c.assess("C", {"title": "ML", "company": "A", "location": "Pune"})
        self.assertEqual((res["verdict"], c.json_mode), ("good", True))
        self.assertIsNotNone(calls[0])
        self.assertIsNone(calls[1])

    def test_key_errors_stop_the_search(self):
        with mock.patch.object(claude, "complete", side_effect=claude.ClaudeError("Claude rejected the key.")):
            with self.assertRaisesRegex(ai.AIError, "API key"):
                self.client().assess("C", {"title": "x", "company": "y", "location": "z"})

    def test_all_claude_checks_share_one_signature(self):
        a = ai.ClaudeAI("k", claude.HAIKU)
        b = ai.ClaudeAI("k", claude.OPUS)
        self.assertEqual(ai.sig_for(a), ai.sig_for(b))


class SaverBatch(unittest.TestCase):
    def test_batch_results_by_id_at_half_price(self):
        c = ai.ClaudeAI("k", claude.HAIKU, "screening", {claude.HAIKU: [0.10, 0.50, 0.01]})
        jobs = [{"id": 1, "title": "a", "company": "A", "location": "Pune"}, {"id": 2, "title": "b", "company": "B", "location": "Pune"},
                {"id": 3, "title": "c", "company": "C", "location": "Pune"}]
        u = {"tokens_in": 1000, "tokens_out": 100, "cache_read": 0, "cache_write": 0}
        raw = {"job-2": (json.dumps(GOOD), u), "job-1": (json.dumps(dict(GOOD, fit_score=55)), u), "job-3": (None, None)}
        with mock.patch.object(claude, "batch", return_value=raw) as b, mock.patch.object(db, "record_usage") as rec:
            got = c.assess_many("C", jobs)
        self.assertEqual({k: v["fit_score"] for k, v in got.items()}, {1: 55, 2: 80})       # job 3 failed: checked normally later
        self.assertEqual([i for i, _ in b.call_args.args[0]], ["job-1", "job-2", "job-3"])
        full = 2 * (1000 * 0.10 + 100 * 0.50) / 1e6
        self.assertAlmostEqual(c.cost(), full / 2, places=8)
        self.assertEqual(rec.call_count, 1)


class Combined(unittest.TestCase):
    def setUp(self):
        with db.conn() as c:
            c.execute("DELETE FROM jobs")
            c.execute("DELETE FROM job_verdicts")

    def test_weighted_mean_latest_per_provider_and_spread(self):
        vs = [{"provider": "anthropic", "score": 60, "at": "1"}, {"provider": "anthropic", "score": 90, "at": "2"},
              {"provider": "openai", "score": 50, "at": "1"}]
        score, spread = db.combined(vs)
        self.assertEqual(score, round((90 * 1.0 + 50 * 0.8) / 1.8, 1))
        self.assertEqual(spread, 40)
        self.assertEqual(db.combined([]), (None, None))
        self.assertEqual(db.combined([{"provider": "openai", "score": 70, "at": "1"}]), (70.0, None))

    def test_combine_scores_updates_the_ranking(self):
        jid = add_job("Acme")
        db.save_ai(jid, "s", dict(GOOD, fit_score=90), False)
        db.save_verdict(jid, "anthropic", claude.OPUS, "s", dict(GOOD, fit_score=90), False)
        db.save_verdict(jid, "openai", "gpt", "s", dict(GOOD, fit_score=40), False)
        db.combine_scores("s")
        job = db.get_job(jid)
        self.assertAlmostEqual(job["ai_score"], round((90 + 40 * 0.8) / 1.8, 1))
        public = db.public_job(job, verdicts=db.verdicts_by_job()[jid])
        self.assertTrue(public["disagree"])


class DeepPass(unittest.TestCase):
    def setUp(self):
        with db.conn() as c:
            c.execute("DELETE FROM jobs")
            c.execute("DELETE FROM job_verdicts")
        self.saved = settings.read_env().get("ANTHROPIC_API_KEY")
        settings.write_env({"ANTHROPIC_API_KEY": "sk-ant-test-123456"})
        self.addCleanup(settings.write_env, {"ANTHROPIC_API_KEY": self.saved})

    def test_opus_rechecks_the_best_and_replaces_the_verdict(self):
        best, worst = add_job("Best", 90), add_job("Worst", 10)
        haiku = ai.ClaudeAI("k", claude.HAIKU)
        sig = ai.sig_for(haiku)
        for jid in (best, worst):
            db.save_ai(jid, sig, dict(GOOD, fit_score=60), False)
        opus = ai.ClaudeAI("k", claude.OPUS, "deep_check")
        progress = mock.Mock()
        cfg = settings.load_config()
        cfg["ai"]["deep_check_jobs"] = 1
        with mock.patch.object(ai, "client_for", return_value=opus), \
                mock.patch.object(ai.ClaudeAI, "assess", return_value=dict(GOOD, fit_score=95)), \
                mock.patch.object(llm, "over_limit", return_value=False), \
                mock.patch.object(profile, "candidate_block", return_value="C"):
            out = refresh._deep_pass(haiku, cfg, 2, progress)
        self.assertEqual(out["checked"], 1)
        self.assertEqual(db.get_job(best)["ai_score"], 95)
        self.assertEqual(db.get_job(worst)["ai_score"], 60)

    def test_no_deep_pass_for_other_providers_or_over_limit(self):
        other = mock.Mock(provider="openai")
        self.assertEqual(refresh._deep_pass(other, settings.load_config(), 2, mock.Mock())["checked"], 0)
        with mock.patch.object(llm, "over_limit", return_value=True):
            self.assertEqual(refresh._deep_pass(ai.ClaudeAI("k", claude.HAIKU), settings.load_config(), 2, mock.Mock())["checked"], 0)


class CompareDefaults(unittest.TestCase):
    def setUp(self):
        self.saved = {k: settings.read_env().get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
        self.addCleanup(settings.write_env, self.saved)
        db.set_setting("llm_compare", None)

    def test_openai_joins_claude_by_default_and_local_never_screens(self):
        settings.write_env({"ANTHROPIC_API_KEY": "sk-ant-test-1", "OPENAI_API_KEY": "sk-test-2"})
        self.assertEqual(llm.compare_choices(), [{"provider": "openai"}])
        db.set_setting("llm_compare", [{"provider": "ollama"}, {"provider": "gemini"}])
        self.assertEqual(llm.compare_choices(), [{"provider": "gemini"}])
        db.set_setting("llm_compare", None)



class Queries(unittest.TestCase):
    def setUp(self):
        db.set_setting("query_plan", None)
        self.addCleanup(db.set_setting, "query_plan", None)

    def test_typos_are_fixed_but_short_words_and_numbers_are_kept(self):
        from jobhunt import queries
        self.assertEqual(queries.fix_typos("machin lerning engneer in bangalor"), "machine learning engineer in bangalore")
        self.assertEqual(queries.fix_typos("ML 0-2 yrs"), "ML 0-2 yrs")
        self.assertEqual(queries.fix_typos("pytorh", queries.vocabulary(["pytorch"])), "pytorch")

    def test_rules_cover_every_role_and_indian_wording(self):
        from jobhunt import queries
        p = queries.plan(["SDE", "ML"], ["Python"], use_ai=False)
        self.assertLessEqual(len(p["ats"]), queries.MAX_ATS)
        self.assertIn("machine learning engineer", p["ats"][:3])
        self.assertIn("graduate engineer trainee", p["ats"])
        self.assertTrue(any("fresher" in q for q in p["aggregator"]))

    def test_ai_suggestions_are_added_once_and_cached(self):
        from jobhunt import queries
        reply = llm.Reply.of('{"ats": ["GenAI engineer", "SDE-1"], "aggregator": ["LLM engineer fresher"]}', "anthropic", claude.OPUS)
        with mock.patch.object(llm, "chat", return_value=reply) as chat:
            a = queries.plan(["ML"], ["Python"])
            b = queries.plan(["ML"], ["Python"])
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(chat.call_args.kwargs["feature"], "planning")
        self.assertIn("GenAI engineer", a["ats"])
        self.assertEqual(a, b)

    def test_ai_failure_falls_back_to_rules(self):
        from jobhunt import queries
        with mock.patch.object(llm, "chat", side_effect=llm.LLMError("offline")):
            p = queries.plan(["SDE"], [])
        self.assertIn("software engineer", p["ats"])
        self.assertIsNone(db.get_setting("query_plan"))           # a failure isn't cached, so it's tried next time



class Regions(unittest.TestCase):
    def test_region_and_sponsorship_rules(self):
        from jobhunt import filters
        self.assertEqual(filters.region_of("Dubai, UAE", ["gulf"]), "gulf")
        self.assertIsNone(filters.region_of("Dubai, UAE", ["europe"]))
        self.assertIsNone(filters.region_of("Pune, India", ["gulf", "europe"]))
        self.assertTrue(filters.sponsors("We offer visa sponsorship and relocation support."))
        self.assertFalse(filters.sponsors("We are unable to sponsor visas. Relocation to Berlin not covered."))
        self.assertFalse(filters.sponsors("Great team in London."))

    def test_pipeline_keeps_abroad_jobs_only_with_sponsorship(self):
        from datetime import datetime, timezone
        from jobhunt import pipeline
        from jobhunt.models import Job
        now = datetime.now(timezone.utc)
        jobs = [Job("Greenhouse", "A", "Software Engineer", "Berlin, Germany", "u1", now, "Visa sponsorship provided. New grad."),
                Job("Greenhouse", "B", "Software Engineer", "Berlin, Germany", "u2", now, "New grad role."),
                Job("Greenhouse", "C", "Software Engineer", "Dubai", "u3", now, "We sponsor visas. New grad."),
                Job("Greenhouse", "D", "Software Engineer", "Pune, India", "u4", now, "New grad.")]
        cfg = settings.load_config()
        kept, stats = pipeline.select(jobs, cfg, {"now": now, "regions": ["europe"]}, [], allow_unstated=True)
        self.assertEqual(sorted(j.company for j in kept), ["A", "D"])
        self.assertEqual(next(j for j in kept if j.company == "A").remote, "Abroad: Europe")
        self.assertEqual(stats["abroad_with_sponsorship"], 1)

    def test_ai_rule_lets_sponsored_abroad_jobs_through(self):
        abroad = dict(GOOD, in_india=False)
        self.assertTrue(ai.rejected(abroad))
        self.assertFalse(ai.rejected(abroad, abroad=True))
        self.assertTrue(ai.rejected(dict(abroad, years_required=5), abroad=True))

    def test_regions_are_saved_and_cleaned(self):
        saved = profile.preferences()
        self.addCleanup(db.set_setting, "profile", saved)
        p = profile.save_preferences({"regions": ["gulf", "mars", "europe"]})
        self.assertEqual(p["regions"], ["gulf", "europe"])


class Portals(unittest.TestCase):
    ctx = None

    def setUp(self):
        from datetime import datetime, timezone
        self.ctx = {"now": datetime.now(timezone.utc), "days": 14, "roles": ["SDE", "ML"], "keys": {},
                    "queries": {"ats": ["software engineer"], "aggregator": []}}

    def test_eightfold_lazy_description_and_429_keeps_results(self):
        import urllib.error
        from jobhunt import portals
        page = {"data": {"positions": [{"id": 1, "name": "Software Engineer", "locations": ["Hyderabad, India"],
                                        "postedTs": 1794249000, "positionUrl": "/careers/job/1"},
                                       {"id": 2, "name": "Sales Manager", "locations": ["India"], "postedTs": 1794249000}]}}
        calls = []

        def fake(url, data=None, **kw):
            calls.append(url)
            if "position_details" in url:
                return {"data": {"jobDescription": "<p>Python, 0-2 years</p>"}}
            if len(calls) > 1:
                raise urllib.error.HTTPError(url, 429, "slow", None, None)
            return page
        with mock.patch.object(portals, "fetch_json", side_effect=fake):
            jobs = portals.eightfold_pcsx("apply.example.com", "example.com", "Example", self.ctx)
            self.assertEqual([j.title for j in jobs], ["Software Engineer"])     # sales role skipped, 429 kept results
            jobs[0].detail(jobs[0])
        self.assertEqual(jobs[0].url, "https://apply.example.com/careers/job/1")
        self.assertIn("Python", jobs[0].description)

    def test_phonepe_skips_unpublished(self):
        from jobhunt import portals
        data = {"results": [{"status": "NOT_PUBLISHED", "title": "SDE 1", "location": "Bengaluru", "updatedAt": "/Date(1794249000000)/"},
                            {"status": "PUBLISHED", "title": "Software Engineer", "location": "Pune", "updatedAt": "/Date(1794249000000)/", "applyUrl": "https://x/1"}]}
        with mock.patch.object(portals, "fetch_json", return_value=data):
            jobs = portals.phonepe(self.ctx)
        self.assertEqual([(j.title, j.location, j.url) for j in jobs], [("Software Engineer", "Pune, India", "https://x/1")])

    def test_dates_in_every_shape(self):
        from jobhunt import portals
        for v in (1794249000, 1794249000000, "2026-11-06", "/Date(1794249000000)/", "2026-11-09 12:21 PM", "2026-10-01T05:00:00Z"):
            self.assertIsNotNone(portals._date(v), v)
        self.assertIsNone(portals._date("someday"))

    def test_careers_only_sites_are_links(self):
        from jobhunt import portals
        links = {l["company"]: l["url"] for l in portals.careers_links("ml engineer")}
        self.assertIn("ml%20engineer", links["Google"])
        self.assertNotIn("google", portals.PORTALS)


class Discovery(unittest.TestCase):
    def setUp(self):
        from jobhunt import discovery
        discovery._table()
        with db.conn() as c:
            c.execute("DELETE FROM company_boards")

    def test_board_must_belong_to_the_company(self):
        from jobhunt import discovery
        with mock.patch.object(discovery, "fetch_json", return_value={"name": "Pine Labs"}):
            self.assertTrue(discovery.check("greenhouse", "pinelabs", "Pine Labs Pvt Ltd"))
        with mock.patch.object(discovery, "fetch_json", return_value={"name": "Delta Air Lines"}):
            self.assertFalse(discovery.check("greenhouse", "delta", "Delta"))
        with mock.patch.object(discovery, "fetch_json", return_value=[{"id": 1}]):
            self.assertFalse(discovery.check("lever", "delta", "Delta"))              # too short to trust
            self.assertTrue(discovery.check("lever", "meesho", "Meesho"))

    def test_discover_records_hits_and_misses_once(self):
        from jobhunt import discovery
        def fake(ats, slug, name):
            return ats == "lever" and slug == "meesho"
        with mock.patch.object(discovery, "check", side_effect=fake):
            found = discovery.discover(["Meesho", "Acme Corp", "Stripe"], {"stripe"})
        self.assertEqual(found, [("lever", "meesho", "Meesho")])
        self.assertEqual(discovery.boards()["lever"], ["meesho|Meesho"])
        self.assertEqual(discovery.due(["Meesho", "Acme Corp"], set()), [])             # both checked: not due again



class ContactQueue(unittest.TestCase):
    def setUp(self):
        with db.conn() as c:
            c.execute("DELETE FROM outreach_contacts")
            c.execute("DELETE FROM jobs")

    def test_posting_contacts_join_once_with_source_and_date(self):
        jid = add_job("Acme")
        assessment = dict(GOOD, contacts=[{"name": "Priya", "role": "Recruiter", "email": "priya@acme.example", "phone": ""}])
        db.save_ai(jid, "s", assessment, False)
        with db.conn() as c:
            c.execute("UPDATE jobs SET url='https://acme.example/jobs/1', first_seen='2026-11-01T10:00:00+00:00' WHERE id=?", (jid,))
        self.assertEqual(db.collect_posting_contacts(), 1)
        self.assertEqual(db.collect_posting_contacts(), 0)                  # never added twice
        c = db.contacts()[0]
        self.assertEqual((c["email"], c["source"], c["job_url"]), ("priya@acme.example", "posting", "https://acme.example/jobs/1"))
        self.assertTrue(c["found_at"].startswith("2026-11-01"))

    def test_old_contacts_get_a_source_and_date(self):
        with db.conn() as c:
            c.execute("INSERT INTO outreach_contacts(id, email, added, job_id, source, found_at) VALUES('a', 'x@y.example', '2026-10-01', '5', '', '')")
            c.execute("INSERT INTO outreach_contacts(id, email, added, source, found_at) VALUES('b', 'z@y.example', '2026-10-02', '', '')")
        db._migrated = False
        rows = {r["id"]: r for r in db.contacts()}
        self.assertEqual((rows["a"]["source"], rows["a"]["found_at"]), ("posting", "2026-10-01"))
        self.assertEqual(rows["b"]["source"], "pasted")

    def test_saving_the_page_never_drops_contacts_beyond_the_old_500(self):
        db.save_contacts([{"id": f"c{i}", "email": f"p{i}@x.example", "added": db.now_iso()} for i in range(600)])
        everything = db.contacts()
        self.assertEqual(len(everything), 600)
        db.replace_contacts(everything)
        self.assertEqual(len(db.contacts()), 600)


if __name__ == "__main__":
    unittest.main()
