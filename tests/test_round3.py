"""Round 3: model switcher, comparison, salary, Coach conversations, secrets, batch email, analysis, Atlas backups."""
import io
import json
import sqlite3
import tempfile
import time
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from jobhunt import ai, api, backup, cloud, db, llm, mail, outbox, refresh, salary, settings, stats, vault


def wipe():
    with db.conn() as c:
        for t in ("jobs", "job_verdicts", "llm_usage", "status_history", "coach_conversations", "coach_messages", "outreach_contacts"):
            c.execute(f"DELETE FROM {t}")
    for k in ("llm_active", "llm_screen", "llm_compare", "llm_compare_jobs", "mail_sent", "cloud_daily", "cloud_last_backup"):
        db.set_setting(k, None)


def add_job(company="Acme", title="ML Engineer", score=80, **kw):
    jid = db.add_manual(company, title, kw.pop("applied_on", date.today().isoformat()), status=kw.pop("status", "applied"))
    if kw or score:
        with db.conn() as c:
            c.execute("UPDATE jobs SET active=1, score=?, description=? WHERE id=?", (score, kw.get("description", "desc"), jid))
    return jid


class Keys(unittest.TestCase):
    def test_keys_leave_the_plain_file_and_still_read(self):
        settings.ENV_PATH.write_text("OPENAI_API_KEY=sk-test-123456\nOPENAI_MODEL=gpt-x\n", encoding="utf-8")
        env = settings.read_env()
        self.assertEqual((env["OPENAI_API_KEY"], env["OPENAI_MODEL"]), ("sk-test-123456", "gpt-x"))
        text = settings.ENV_PATH.read_text(encoding="utf-8")
        self.assertNotIn("sk-test", text)
        self.assertIn("OPENAI_MODEL", text)
        self.assertNotIn(b"sk-test", vault.path().read_bytes())
        settings.write_env({"OPENAI_API_KEY": None, "OPENAI_MODEL": None})
        self.assertNotIn("OPENAI_API_KEY", settings.read_env())

    def test_connection_string_counts_as_a_secret(self):
        self.assertTrue(vault.is_secret("MONGODB_URI"))
        self.assertFalse(vault.is_secret("GMAIL_ADDRESS"))

    def test_session_key_guards_the_api(self):
        c = TestClient(api.app, follow_redirects=False)
        api.app.state.token = "secret-session-key"
        try:
            self.assertEqual(c.get("/api/state").status_code, 401)
            self.assertEqual(c.get("/").status_code, 401)
            self.assertEqual(c.get("/api/health").status_code, 200)
            self.assertEqual(c.get("/?k=wrong").status_code, 401)
            r = c.get("/?k=secret-session-key")
            self.assertEqual(r.status_code, 303)
            self.assertIn("httponly", r.headers["set-cookie"].lower())
            self.assertEqual(c.get("/api/state").status_code, 200)
        finally:
            api.app.state.token = None


class Switcher(unittest.TestCase):
    def setUp(self):
        wipe()
        settings.write_env({k: None for k in llm.KEY_VARS})
        settings.write_env({"GROQ_API_KEY": "k" * 12, "GEMINI_API_KEY": "g" * 12})
        self.c = TestClient(api.app)

    def tearDown(self):
        settings.write_env({k: None for k in llm.KEY_VARS})
        wipe()

    def test_chosen_model_answers_first_and_fallback_is_reported(self):
        db.set_setting("llm_active", {"provider": "gemini", "model": "gemini-x"})
        seen = []

        def fake(pid, messages, max_tokens, env=None, model=None, feature="chat"):
            seen.append((pid, model))
            if pid == "gemini":
                raise llm.LLMError("down")
            return "hi"
        with mock.patch.object(llm, "_complete", side_effect=fake):
            r = llm.chat([{"role": "user", "content": "x"}])
        self.assertEqual(seen[0], ("gemini", "gemini-x"))
        self.assertEqual((r.provider, r.fell_back_from), ("groq", "gemini"))
        self.assertEqual(tuple(r)[1], llm.PROVIDERS["groq"][0])

    def test_ping_writes_nothing_and_status_reports_latency(self):
        before = settings.ENV_PATH.read_text(encoding="utf-8") if settings.ENV_PATH.exists() else ""
        with mock.patch.object(llm, "ping", return_value={"ok": True, "ms": 42, "error": ""}):
            s = self.c.get("/api/ai/status").json()
        row = next(p for p in s["providers"] if p["id"] == "groq")
        self.assertEqual((row["ok"], row["ms"]), (True, 42))
        self.assertTrue(s["chat"]["provider"])
        self.assertEqual(before, settings.ENV_PATH.read_text(encoding="utf-8") if settings.ENV_PATH.exists() else "")

    def test_screening_change_needs_confirmation(self):
        add_job()
        with mock.patch.object(llm, "ping", return_value={"ok": True, "ms": 1, "error": ""}):
            r = self.c.put("/api/ai/active", json={"provider": "gemini", "screening": True}).json()
            self.assertTrue(r["needs_confirm"])
            self.assertEqual(r["rechecks"], 1)
            self.assertIsNone(llm.screen_choice())
            self.c.put("/api/ai/active", json={"provider": "gemini", "screening": True, "confirmed": True})
        self.assertEqual(llm.screen_choice()["provider"], "gemini")

    def test_usage_is_recorded_with_unknown_prices_marked(self):
        llm.record("groq", "llama-3.3-70b-versatile", "coach", 1000, 100, time.monotonic())
        llm.record("kimi", "some-new-model", "coach", 1000, 100, time.monotonic())
        u = stats.usage()
        self.assertEqual((u["calls"], u["unpriced"]), (2, 1))
        self.assertGreater(u["cost"], 0)
        self.assertEqual(u["by_feature"][0]["label"], "coach")


class FakeAI:
    def __init__(self, pid, model, score):
        self.provider, self.model, self.label, self.score = pid, model, pid, score
        self.tokens_in = self.tokens_out = 0
        self.calls = 0

    def assess(self, block, row, cache_key=None):
        self.calls += 1
        return {"fit_score": self.score, "verdict": "good", "reasons": ["r"], "years_required": 0, "in_india": True,
                "role_match": True, "entry_level_ok": True}

    def cost(self):
        return 0


class Compare(unittest.TestCase):
    def setUp(self):
        wipe()
        settings.write_env({"GROQ_API_KEY": "k" * 12, "GEMINI_API_KEY": "g" * 12})

    def tearDown(self):
        settings.write_env({k: None for k in llm.KEY_VARS})
        wipe()

    def test_extra_models_read_only_the_top_jobs_and_never_move_the_score(self):
        ids = [add_job(f"Co{i}", score=50 + i) for i in range(5)]
        db.set_setting("llm_compare", [{"provider": "groq", "model": "m2"}])
        db.set_setting("llm_compare_jobs", 2)
        extra = FakeAI("groq", "m2", 10)
        before = {j["id"]: j["score"] for j in db.list_jobs()}
        main = SimpleNamespace(provider="gemini", model="m1")
        with mock.patch.object(ai, "client_for", return_value=extra):
            out = refresh._compare_pass(main, settings.load_config(), 2, refresh.Progress())
        self.assertEqual((out["checked"], extra.calls), (2, 2))
        self.assertEqual(sorted(db.verdicts_by_job()), sorted(ids[-2:]))
        self.assertEqual(before, {j["id"]: j["score"] for j in db.list_jobs()})
        self.assertEqual(db.public_job(db.get_job(ids[-1]), verdicts=db.verdicts_by_job()[ids[-1]])["models"][0]["score"], 10)

    def test_one_failing_model_does_not_stop_the_search(self):
        add_job()
        db.set_setting("llm_compare", [{"provider": "groq", "model": "m2"}])
        bad = FakeAI("groq", "m2", 10)
        bad.assess = mock.Mock(side_effect=ai.AIError("quota"))
        p = refresh.Progress()
        with mock.patch.object(ai, "client_for", return_value=bad):
            refresh._compare_pass(SimpleNamespace(provider="gemini", model="m1"), settings.load_config(), 2, p)
        self.assertEqual(p.snapshot()["models"][0]["state"], "failed")


class Salary(unittest.TestCase):
    def test_reads_real_figures(self):
        self.assertEqual(salary.parse("CTC 12-18 LPA")["salary_max"], 1_800_000)
        self.assertEqual(salary.parse("stipend of ₹50,000 per month")["salary_min"], 600_000)
        self.assertEqual(salary.parse("₹12,00,000 - ₹18,00,000 per annum")["salary_min"], 1_200_000)
        self.assertEqual(salary.parse("salary $80,000 - $120,000")["salary_currency"], "USD")

    def test_never_invents_one(self):
        for t in ("", "raised $50,000,000 in funding", "10 lakh customers trust us", "Python 3.10, 5 years", "2 lakh users, great pay"):
            self.assertIsNone(salary.parse(t), t)

    def test_your_figure_beats_a_parsed_one(self):
        wipe()
        jid = add_job()
        db.set_salary(jid, salary.from_input("20 LPA"))
        with db.conn() as c:
            db._set_salary(c, jid, salary.parse("CTC 5 LPA"))
        self.assertEqual(db.get_job(jid)["salary_max"], 2_000_000)
        self.assertEqual(salary.label(db.get_job(jid)), "20 LPA")

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            salary.from_input("a lot")


class Coach(unittest.TestCase):
    def setUp(self):
        wipe()
        settings.write_env({"GROQ_API_KEY": "k" * 12, "GEMINI_API_KEY": "g" * 12})
        self.c = TestClient(api.app)

    def tearDown(self):
        settings.write_env({k: None for k in llm.KEY_VARS})
        wipe()

    def test_old_history_moves_into_a_conversation(self):
        db.set_setting("coach_history", [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi", "provider": "OpenAI"}])
        db.set_setting("moved_v3", [x for x in (db.get_setting("moved_v3") or []) if x != "coach"])
        db.reset_migration()
        v = self.c.get("/api/ai/chat").json()
        self.assertEqual([m["content"] for m in v["messages"]], ["hello", "hi"])
        db.set_setting("coach_history", [])

    def test_each_conversation_uses_its_own_model_and_gets_next_steps(self):
        a = self.c.post("/api/ai/conversations", json={"provider": "gemini", "model": "gem-1"}).json()["conversation"]["id"]
        seen = []

        def fake(pid, messages, max_tokens, env=None, model=None, feature="chat"):
            seen.append((pid, model, feature))
            return "Do this.\nNEXT: Draft my email | Review my resume"
        with mock.patch.object(llm, "_complete", side_effect=fake):
            r = self.c.post("/api/ai/chat", json={"message": "help me", "conversation_id": a}).json()
        self.assertEqual(seen[0], ("gemini", "gem-1", "coach"))
        self.assertEqual(r["reply"], "Do this.")
        self.assertEqual([x["label"] for x in r["actions"]], ["Draft my email", "Review my resume"])
        self.assertEqual(r["conversation"]["title"], "help me")

    def test_interview_prep_opens_its_own_conversation(self):
        jid = add_job("Tailspin", "ML Engineer")
        with mock.patch.object(llm, "_complete", side_effect=lambda pid, m, *a, **k: "## What they care about\n- Python"):
            v = self.c.post("/api/ai/interview-prep", json={"job_id": jid}).json()
        self.assertEqual((v["conversation"]["kind"], v["conversation"]["job_id"]), ("prep", jid))
        self.assertIn("Tailspin", v["conversation"]["title"])
        self.assertTrue(v["messages"][0]["actions"])


class Password(unittest.TestCase):
    def test_any_length_reaches_google(self):
        fake = mock.Mock()
        with mock.patch.object(mail, "_imap", return_value=fake) as login:
            mail.connect("me@example.com", "abcd efgh ijkl mnop qrst")
        self.assertEqual(login.call_args[0][1], "abcdefghijklmnopqrst")
        self.assertEqual(settings.read_env()[mail.PASS_VAR], "abcdefghijklmnopqrst")
        mail.disconnect()

    def test_empty_is_refused(self):
        with self.assertRaises(mail.MailError):
            mail.connect("me@example.com", "   ")


class Batch(unittest.TestCase):
    def setUp(self):
        wipe()
        settings.write_env({mail.ADDR_VAR: "me@example.com", mail.PASS_VAR: "x" * 16})
        self.c = TestClient(api.app)
        db.save_contacts([{"id": f"c{i}", "name": "Mira Rao", "email": f"m{i}@acme.example", "company": "Acme", "job_role": "SDE",
                           "added": db.now_iso()} for i in range(3)])

    def tearDown(self):
        settings.write_env({mail.ADDR_VAR: None, mail.PASS_VAR: None})
        wipe()

    def drafts(self, body="Hi {first_name}, about {role} at {company}."):
        return self.c.post("/api/outreach/drafts", json={"ids": ["c0", "c1", "c2"], "subject": "{role} at {company}", "body": body}).json()

    def wait(self):
        for _ in range(200):
            if not outbox.snapshot()["running"]:
                return
            time.sleep(0.01)

    def test_template_fills_each_contact_and_keeps_long_drafts(self):
        long = "Hi {first_name}. " + "word " * 200
        self.drafts(long)
        c = db.contact("c1")
        self.assertTrue(c["draft_body"].startswith("Hi Mira."))
        self.assertGreater(len(c["draft_body"]), 900)

    def test_missing_field_blocks_nothing_silently(self):
        db.update_contact("c2", company="")
        r = self.drafts()
        self.assertEqual(r["missing"]["c2"], ["company"])
        c = db.contact("c2")
        self.assertEqual(c["status"], "needs_edit")
        self.assertIn("[company]", c["draft_body"])

    def test_count_must_match_what_you_agreed_to(self):
        self.drafts()
        r = self.c.post("/api/outbox/start", json={"ids": ["c0", "c1", "c2"], "confirm_count": 2})
        self.assertEqual(r.status_code, 400)

    def test_sends_one_at_a_time_and_files_them(self):
        self.drafts()
        sent = []
        with mock.patch.object(mail, "send", side_effect=lambda to, *a, **k: sent.append(to)), \
                mock.patch.object(outbox, "GAP_SECONDS", 0):
            self.c.post("/api/outbox/start", json={"ids": ["c0", "c1", "c2"], "confirm_count": 3})
            self.wait()
        self.assertEqual(sent, ["m0@acme.example", "m1@acme.example", "m2@acme.example"])
        self.assertTrue(all(db.contact(f"c{i}")["sent_at"] for i in range(3)))
        self.assertEqual(self.c.post("/api/outbox/start", json={"ids": ["c0"], "confirm_count": 1}).status_code, 400)   # never twice

    def test_stop_ends_the_batch(self):
        self.drafts()
        sent = []
        with mock.patch.object(mail, "send", side_effect=lambda to, *a, **k: sent.append(to)):
            outbox.start(["c0", "c1", "c2"], api._deliver, gap=5)
            for _ in range(200):
                if sent:
                    break
                time.sleep(0.01)
            outbox.stop()
            self.wait()
        self.assertEqual(len(sent), 1)
        self.assertTrue(outbox.snapshot()["stopped"])

    def test_daily_cap(self):
        self.drafts()
        db.set_setting("mail_sent", [{"to": "x", "subject": "s", "at": db.now_iso()}] * (mail.DAILY_CAP - 1))
        r = self.c.post("/api/outbox/start", json={"ids": ["c0", "c1", "c2"], "confirm_count": 3})
        self.assertIn("limit", r.json()["detail"])
        db.set_setting("mail_sent", [{"to": "x", "subject": "s", "at": db.now_iso()}] * mail.DAILY_CAP)
        with self.assertRaises(mail.MailError):
            mail.send("a@b.example", "s", "b")

    def test_a_refused_login_stops_the_rest(self):
        self.drafts()
        with mock.patch.object(mail, "send", side_effect=mail.MailError("Gmail refused the login.")) as m:
            outbox.start(["c0", "c1", "c2"], api._deliver, gap=0)
            self.wait()
        self.assertEqual(m.call_count, 1)
        self.assertEqual(db.contact("c0")["status"], "failed")
        self.assertFalse(db.contact("c1")["sent_at"])


class Analysis(unittest.TestCase):
    def setUp(self):
        wipe()

    def tearDown(self):
        wipe()

    def test_summary_matches_hand_calculation(self):
        s = stats.summary([1, 2, 3, 4, 10])
        self.assertEqual((s["n"], s["mean"], s["median"], s["min"], s["max"], s["p25"], s["p75"]), (5, 4.0, 3, 1, 10, 2, 4))
        self.assertEqual(stats.summary([])["n"], 0)
        self.assertEqual(stats.summary([7])["median"], 7)

    def test_rejected_after_interview_still_counts_as_interview(self):
        jid = add_job()
        db.set_status(jid, "interviewing")
        db.set_status(jid, "rejected")
        funnel = {f["label"]: f["n"] for f in stats.compute()["funnel"]}
        self.assertEqual(funnel["Interview"], 1)

    def test_reply_time_uses_the_first_reply_not_the_last_change(self):
        d = date.today() - timedelta(days=10)
        jid = add_job(applied_on=d.isoformat())
        db.set_status(jid, "screening", at=f"{(d + timedelta(days=3)).isoformat()}T12:00:00+05:30")
        db.set_status(jid, "rejected")
        a = stats.explore(rng="all")["analysis"]
        wait = next(m for m in a["metrics"] if m["key"] == "wait")
        self.assertEqual((wait["n"], wait["median"]), (1, 3))

    def test_old_rows_stay_out_of_timing(self):
        add_job(status="rejected")
        self.assertEqual(stats.explore(rng="all")["wait_tracked"], 0)

    def test_company_spellings_group_and_top_ten_covers_all(self):
        for i in range(3):
            add_job("Acme Pvt Ltd")
        add_job("Acme")
        for i in range(12):
            add_job(f"Other{chr(65 + i)}")
        a = stats.explore(rng="all")["analysis"]
        self.assertEqual((a["companies"][0]["name"], a["companies"][0]["applied"]), ("Acme Pvt Ltd", 4))
        pie = a["company_pie"]
        self.assertEqual(len(pie["slices"]), 10)
        self.assertEqual(pie["slices"][0]["share"], 25.0)
        self.assertEqual(pie["covered"], 81.2)
        self.assertEqual(pie["groups"], 13)

    def test_filter_keeps_todays_count_true(self):
        add_job("Acme")
        add_job("Zeta")
        c = TestClient(api.app)
        s = c.get("/api/stats", params={"range": "30d", "company": "Acme"}).json()
        self.assertEqual((s["kpis"]["applications"], s["today"]["count"]), (1, 2))

    def test_small_samples_say_so(self):
        for i in range(6):
            add_job("Google", "ML Engineer", status="rejected" if i == 0 else "applied")
        for i in range(6):
            add_job("Tiny Startup Co", "Backend Engineer")
        found = " ".join(f["tip"] for f in stats.explore(rng="all")["oracle"])
        self.assertIn("Early signal", found)

    def test_excel_export_has_every_sheet_and_defuses_formulas(self):
        from openpyxl import load_workbook
        from jobhunt import workbook
        add_job("=HYPERLINK(1)", "ML Engineer")
        wb = load_workbook(io.BytesIO(workbook.build()))
        self.assertEqual(wb.sheetnames, ["Applications", "Companies", "Roles", "Role Families", "Statistics"])
        self.assertTrue(wb["Applications"]["B2"].value.startswith("'="))

    def test_digest_stays_free_of_names(self):
        add_job("SecretCompanyName")
        self.assertNotIn("SecretCompanyName", json.dumps(stats.digest(), default=str))


class ModelChoices(unittest.TestCase):
    def setUp(self):
        wipe()
        for k in ("llm_force", "llm_tasks", "llm_disabled"):
            db.set_setting(k, None)
        settings.write_env({"GROQ_API_KEY": "k" * 12, "GEMINI_API_KEY": "g" * 12})
        self.c = TestClient(api.app)
        self.seen = []

    def tearDown(self):
        settings.write_env({k: None for k in llm.KEY_VARS})
        for k in ("llm_force", "llm_tasks", "llm_disabled"):
            db.set_setting(k, None)
        wipe()

    def fake(self, pid, messages, max_tokens, env=None, model=None, feature="chat"):
        self.seen.append((pid, model, feature))
        return "OK"

    def ask(self, **kw):
        with mock.patch.object(llm, "_complete", side_effect=self.fake):
            return llm.chat([{"role": "user", "content": "x"}], **kw)

    def test_a_disabled_provider_keeps_its_key_but_is_never_used(self):
        self.c.put("/api/ai/providers/groq/enabled", json={"enabled": False})
        self.assertEqual(llm.configured(), ["gemini"])
        self.assertTrue(settings.read_env().get("GROQ_API_KEY"))
        row = next(p for p in llm.status()["providers"] if p["id"] == "groq")
        self.assertEqual((row["ready"], row["disabled"], row["usable"]), (False, True, True))
        self.c.put("/api/ai/providers/groq/enabled", json={"enabled": True})
        self.assertIn("groq", llm.configured())

    def test_each_task_uses_its_own_model(self):
        self.assertEqual(self.c.put("/api/ai/task", json={"task": "other", "provider": "gemini"}).status_code, 404)
        db.set_setting("llm_tasks", {"email": {"provider": "gemini", "model": "gem-mail"}})
        self.ask(feature="email")
        self.ask(feature="dashboard")
        self.assertEqual(self.seen[0][:2], ("gemini", "gem-mail"))
        self.assertNotEqual(self.seen[1][1], "gem-mail")

    def test_one_model_beats_everything_and_never_swaps(self):
        db.set_setting("llm_tasks", {"email": {"provider": "gemini", "model": "gem-mail"}})
        db.set_setting("llm_force", {"provider": "groq", "model": "big"})
        self.ask(feature="email", prefer="gemini", model="other")
        self.assertEqual(self.seen[0][:2], ("groq", "big"))
        with mock.patch.object(llm, "_complete", side_effect=llm.LLMError("down")) as m:
            with self.assertRaises(llm.LLMError):
                llm.chat([{"role": "user", "content": "x"}])
        self.assertEqual(m.call_count, 1)
        self.assertEqual(llm.compare_choices(), [])
        self.assertEqual(ai.client_from_settings().provider, "groq")

    def test_only_a_working_chat_model_can_do_every_task(self):
        with mock.patch.object(llm, "ping", return_value={"ok": True, "ms": 1, "error": ""}):
            self.assertEqual(self.c.put("/api/ai/force", json={"provider": "groq", "model": "text-embedding-3"}).status_code, 400)
            with mock.patch.object(llm, "_complete", side_effect=llm.LLMError("no such model")):
                self.assertEqual(self.c.put("/api/ai/force", json={"provider": "groq", "model": "broken"}).status_code, 400)
            self.assertIsNone(llm.force_choice())
            with mock.patch.object(llm, "_complete", side_effect=self.fake):
                r = self.c.put("/api/ai/force", json={"provider": "groq", "model": "llama-x"}).json()
            self.assertEqual(r["force"]["model"], "llama-x")
            self.assertEqual(r["chat"]["model"], "llama-x")
            self.assertEqual(self.c.put("/api/ai/force", json={}).json()["force"], None)

    def test_disabling_the_forced_provider_turns_the_setting_off(self):
        db.set_setting("llm_force", {"provider": "groq", "model": "big"})
        self.c.put("/api/ai/providers/groq/enabled", json={"enabled": False})
        self.assertIsNone(llm.force_choice())


class OracleData(unittest.TestCase):
    def setUp(self):
        wipe()
        db.set_setting("oracle_history", [])
        settings.write_env({"GROQ_API_KEY": "k" * 12})
        add_job("Acme Pvt Ltd", "ML Engineer")
        add_job("Acme", "ML Engineer")
        add_job("Zeta Labs", "Data Engineer", status="interviewing")
        with db.conn() as c:
            c.execute("UPDATE jobs SET notes='private note', contact='hr@secret.example'")

    def tearDown(self):
        settings.write_env({k: None for k in llm.KEY_VARS})
        db.set_setting("oracle_history", [])
        wipe()

    def test_the_oracle_sees_companies_but_not_notes_or_contacts(self):
        prompts = []
        with mock.patch.object(llm, "_complete", side_effect=lambda pid, m, *a, **k: prompts.append(m[0]["content"]) or "ok"):
            TestClient(api.app).post("/api/ai/oracle-chat", json={"message": "unique companies"})
        self.assertIn("Zeta Labs", prompts[0])
        self.assertIn('"unique_companies":2', prompts[0])
        self.assertNotIn("private note", prompts[0])
        self.assertNotIn("secret.example", prompts[0])

    def test_the_coach_knows_unique_counts(self):
        text = api._tracker_digest()
        self.assertIn("2 unique companies", text)
        self.assertIn("2 unique company + role pairs (1 possible repeats)", text)


class OracleCharts(unittest.TestCase):
    def setUp(self):
        wipe()
        db.set_setting("oracle_history", [])
        for i in range(3):
            add_job("Acme Pvt Ltd", "ML Engineer")
        add_job("Acme", "Data Engineer", status="interviewing")
        add_job("Zeta Labs", "Software Engineer", status="rejected")
        settings.write_env({"GROQ_API_KEY": "k" * 12})

    def tearDown(self):
        settings.write_env({k: None for k in llm.KEY_VARS})
        db.set_setting("oracle_history", [])
        wipe()

    def test_counts_come_from_the_tracker(self):
        c = stats.query({"type": "bar", "title": "T", "group_by": "company", "metric": "applications"})
        self.assertEqual((c["labels"], c["values"]), (["Acme Pvt Ltd", "Zeta Labs"], [4, 1]))
        p = stats.query({"type": "pie", "group_by": "status", "where": {"company": "Acme"}})
        self.assertEqual(dict(zip(p["labels"], p["values"])), {"applied": 3, "interviewing": 1})
        r = stats.query({"type": "bar", "group_by": "company", "metric": "reply_rate"})
        self.assertEqual(r["values"], [100.0, 25.0])

    def test_table_has_every_metric(self):
        t = stats.query({"type": "table", "group_by": "company"})
        self.assertEqual(t["columns"][:3], ["company", "applications", "replies"])
        self.assertEqual(t["rows"][0][:3], ["Acme Pvt Ltd", 4, 1])

    def test_histogram_counts_every_value_once(self):
        h = stats.query({"type": "histogram", "measure": "applications_per_company", "bins": 4})
        self.assertEqual((sum(h["values"]), h["n"]), (2, 2))
        self.assertEqual(stats.query({"type": "histogram", "measure": "salary_lpa"})["values"], [])

    def test_timelines_stay_in_order(self):
        w = stats.query({"type": "line", "group_by": "weekday"})
        self.assertEqual(w["labels"], sorted(w["labels"], key=stats.WEEKDAYS.index))

    def test_unknown_requests_are_refused(self):
        for spec in ({"type": "radar"}, {"type": "bar", "group_by": "salary"}, {"type": "bar", "group_by": "company", "metric": "x"},
                     {"type": "histogram", "measure": "x"}):
            with self.assertRaises(ValueError):
                stats.query(spec)

    def test_numbers_in_a_reply_never_reach_the_chart(self):
        reply = 'Here you go.\nCHART: {"type":"bar","title":"By company","group_by":"company","values":[999]}'
        with mock.patch.object(llm, "_complete", side_effect=lambda *a, **k: reply):
            r = TestClient(api.app).post("/api/ai/oracle-chat", json={"message": "chart"}).json()
        m = r["messages"][-1]
        self.assertEqual(m["content"], "Here you go.")
        self.assertEqual(m["charts"][0]["values"], [4, 1])

    def test_a_bad_chart_line_is_reported_and_history_stays_clean(self):
        with mock.patch.object(llm, "_complete", side_effect=lambda *a, **k: 'Sure.\nCHART: {"type":"radar"}'):
            c = TestClient(api.app)
            m = c.post("/api/ai/oracle-chat", json={"message": "chart"}).json()["messages"][-1]
        self.assertIn("couldn't draw 1", m["content"])
        seen = []
        with mock.patch.object(llm, "_complete", side_effect=lambda pid, msgs, *a, **k: seen.append(msgs) or "ok"):
            c.post("/api/ai/oracle-chat", json={"message": "again"})
        self.assertTrue(all(set(x) == {"role", "content"} for x in seen[0]))


class RoleFamilies(unittest.TestCase):
    def test_each_job_carries_its_role_family(self):
        wipe()
        jid = add_job("Acme", "Data Engineer")
        self.assertEqual(db.public_job(db.get_job(jid))["family"], "Data engineering")
        wipe()


class CustomModels(unittest.TestCase):
    BODY = {"label": "NewAI", "base_url": "https://api.newai.example/v1/", "model": "newai-1-chat", "api_key": "secret-custom-key-1"}

    def setUp(self):
        wipe()
        for k in ("llm_custom", "llm_force", "llm_active", "llm_disabled", "llm_order"):
            db.set_setting(k, None)
        settings.write_env({k: None for k in llm.KEY_VARS})
        llm.load_custom()
        self.c = TestClient(api.app)

    def tearDown(self):
        for c in list(db.get_setting("llm_custom") or []):
            llm.remove(c["id"])
        for k in ("llm_custom", "llm_force", "llm_active"):
            db.set_setting(k, None)
        llm.load_custom()

    def add(self, **kw):
        with mock.patch.object(llm, "_complete", side_effect=lambda pid, m, *a, **k: "OK"):
            return self.c.post("/api/ai/custom", json=self.BODY | kw)

    def test_a_working_model_is_added_and_used(self):
        r = self.add()
        self.assertEqual(r.status_code, 200)
        pid = r.json()["id"]
        self.assertIn(pid, llm.configured())
        row = next(p for p in llm.status()["providers"] if p["id"] == pid)
        self.assertEqual((row["label"], row["custom"], row["ready"], row["model"]), ("NewAI", True, True, "newai-1-chat"))
        self.assertEqual(llm.PROVIDERS[pid][1], "https://api.newai.example/v1")
        self.assertEqual(ai.client_for(pid).model, "newai-1-chat")
        seen = []
        with mock.patch.object(llm, "_complete", side_effect=lambda p, m, mt, env=None, model=None, feature="chat": seen.append((p, model)) or "hi"):
            llm.chat([{"role": "user", "content": "x"}])
        self.assertEqual(seen[0], (pid, "newai-1-chat"))

    def test_the_key_is_encrypted_and_never_returned(self):
        r = self.add().json()
        self.assertNotIn("secret-custom-key-1", json.dumps(r))
        self.assertNotIn(b"secret-custom-key-1", vault.path().read_bytes())
        self.assertEqual(settings.read_env()[llm.PROVIDERS[r["id"]][3]], "secret-custom-key-1")

    def test_a_model_that_fails_is_not_kept(self):
        with mock.patch.object(llm, "_complete", side_effect=llm.LLMError("no such model")):
            r = self.c.post("/api/ai/custom", json=self.BODY)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(db.get_setting("llm_custom"), [])

    def test_bad_details_are_refused(self):
        for kw in ({"base_url": "http://api.newai.example/v1"}, {"base_url": "not a url"}, {"model": "text-embedding-3"}, {"label": ""}, {"label": "OpenAI"}):
            self.assertEqual(self.add(**kw).status_code, 400, kw)

    def test_a_local_server_needs_no_key_or_https(self):
        r = self.add(base_url="http://127.0.0.1:1234/v1", api_key="")
        self.assertEqual(r.status_code, 200)
        self.assertIn(r.json()["id"], llm.configured())

    def test_removing_it_clears_every_choice_that_used_it(self):
        pid = self.add().json()["id"]
        db.set_setting("llm_force", {"provider": pid, "model": "newai-1-chat"})
        self.c.delete(f"/api/ai/providers/{pid}")
        self.assertFalse(llm.known(pid))
        self.assertIsNone(llm.force_choice())
        self.assertNotIn(pid, llm.configured())


class AppWindow(unittest.TestCase):
    def test_a_launcher_that_exits_at_once_does_not_stop_the_app(self):
        from jobhunt import desktop
        # free just after launch (Edge is still starting), then locked for a while, then really closed
        states = iter([False, False, True, True, False, False, False, False, False])
        with mock.patch.object(desktop, "_profile_in_use", side_effect=lambda: next(states)):
            desktop._wait_until_closed(mock.Mock(), checks=5, gap=0)
        self.assertEqual(list(states), [])                  # it kept watching until five free checks in a row


class Upgrade(unittest.TestCase):
    def test_existing_data_is_backed_up_before_the_first_migration(self):
        wipe()
        add_job("KeepMe")
        db.set_setting("moved_v3", None)
        with db.conn() as c:
            c.execute("DELETE FROM settings WHERE key='moved_v3'")
        db.reset_migration()
        with mock.patch.object(backup, "create") as made:
            db.get_setting("x")
        made.assert_called_once_with("manual")
        self.assertIsNotNone(db.get_setting("moved_v3"))
        wipe()


class Experience(unittest.TestCase):
    def tearDown(self):
        db.set_setting("profile", {})

    def test_profile_saves_years_within_limits(self):
        c = TestClient(api.app)
        self.assertEqual(c.put("/api/profile", json={"max_years": 0}).status_code, 200)
        self.assertEqual(api.profile.preferences()["max_years"], 0)
        self.assertEqual(c.put("/api/profile", json={"max_years": 9}).status_code, 400)

    def test_default_years_do_not_trigger_a_paid_recheck(self):
        db.set_setting("profile", {})
        before = api.profile.signature("m")
        api.profile.save_preferences({"max_years": 2})
        self.assertEqual(before, api.profile.signature("m"))
        api.profile.save_preferences({"max_years": 4})
        self.assertNotEqual(before, api.profile.signature("m"))
        self.assertIn("up to 4 years", api.profile.candidate_block())


class FakeFile:
    def __init__(self, oid, name, data, metadata):
        from datetime import datetime
        self._id, self.filename, self.data, self.metadata = oid, name, data, metadata
        self.length, self.upload_date = len(data), datetime.utcnow()


class FakeBucket:
    def __init__(self):
        self.files = []

    def find(self, _):
        return list(self.files)

    def upload_from_stream(self, name, fh, metadata=None):
        from bson import ObjectId
        self.files.append(FakeFile(ObjectId(), name, fh.read(), metadata))

    def download_to_stream(self, oid, fh):
        fh.write(next(f for f in self.files if f._id == oid).data)

    def delete(self, oid):
        self.files = [f for f in self.files if f._id != oid]


class Atlas(unittest.TestCase):
    URI = "mongodb+srv://user:hunter2hunter@cluster0.abc.mongodb.net/?appName=x"

    def setUp(self):
        wipe()
        self.bucket = FakeBucket()
        for p in (mock.patch.object(cloud, "_client", return_value=mock.MagicMock()),
                  mock.patch.object(cloud, "_bucket", return_value=self.bucket)):
            p.start()
            self.addCleanup(p.stop)
        self.c = TestClient(api.app)

    def tearDown(self):
        cloud.disconnect()
        wipe()

    def connect(self):
        return self.c.put("/api/cloud", json={"uri": self.URI, "passphrase": "correct horse battery"})

    def test_bad_input_is_refused(self):
        for uri, pw in (("http://x", "long enough pass"), ("mongodb+srv://u:<db_password>@x.mongodb.net", "long enough pass"), (self.URI, "short")):
            self.assertEqual(self.c.put("/api/cloud", json={"uri": uri, "passphrase": pw}).status_code, 400)
        self.assertFalse(cloud.connected())

    def test_string_is_encrypted_and_never_shown(self):
        r = self.connect().json()
        self.assertEqual((r["connected"], r["host"]), (True, "cluster0.abc.mongodb.net"))
        self.assertNotIn("hunter2", json.dumps(r))
        env = settings.ENV_PATH.read_text(encoding="utf-8") if settings.ENV_PATH.exists() else ""
        self.assertNotIn("hunter2", env)
        self.assertNotIn(b"hunter2", vault.path().read_bytes())

    def test_errors_never_carry_the_password(self):
        text = cloud._clean_error(Exception(f"bad auth for {self.URI} and hunter2hunter"), self.URI)
        self.assertNotIn("hunter2", text)

    def test_upload_is_encrypted_and_restores(self):
        self.connect()
        add_job("BackedUpCo")
        self.c.post("/api/cloud/backups")
        data = self.bucket.files[0].data
        self.assertNotIn(b"SQLite format", data)
        self.assertNotIn(b"BackedUpCo", data)
        fid = self.c.get("/api/cloud/backups").json()["backups"][0]["id"]
        wipe()
        self.assertEqual(self.c.post(f"/api/cloud/backups/{fid}/restore").status_code, 200)
        self.assertTrue(any(j["company"] == "BackedUpCo" for j in db.list_jobs()))

    def test_wrong_passphrase_cannot_open_it(self):
        self.connect()
        made = backup.create("manual", "correct horse battery")
        with self.assertRaises(backup.BackupError):
            backup.restore(backup.BACKUPS / made["name"], "wrong passphrase")

    def test_keeps_only_the_newest(self):
        self.connect()
        with mock.patch.object(backup, "create", wraps=backup.create):
            for _ in range(cloud.KEEP + 2):
                cloud.backup_now()
                time.sleep(1.01)                                  # backups are named to the second
        self.assertEqual(len(self.bucket.files), cloud.KEEP)

    def test_auto_sync_runs_on_schedule_only_when_on(self):
        self.connect()
        self.assertIsNotNone(cloud.sync_if_due())
        self.assertIsNone(cloud.sync_if_due())                       # just synced
        cloud._save_sync(last_sync="2020-01-01T00:00:00+00:00")
        self.assertIsNotNone(cloud.sync_if_due())
        self.c.put("/api/cloud/auto", json={"auto": False})
        cloud._save_sync(last_sync="2020-01-01T00:00:00+00:00")
        self.assertIsNone(cloud.sync_if_due())


class Sync(unittest.TestCase):
    """Two PCs, played by snapshotting this database, changing it, and merging the snapshot back."""

    def setUp(self):
        wipe()
        with db.conn() as c:
            c.execute("DELETE FROM deleted_keys")
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        wipe()
        with db.conn() as c:
            c.execute("DELETE FROM deleted_keys")

    def snapshot(self):
        path = f"{self.dir}/other-{time.monotonic_ns()}.db"
        src, dst = sqlite3.connect(settings.DB_PATH), sqlite3.connect(path)
        src.backup(dst)
        src.close()
        dst.close()
        return path

    def test_rows_from_the_other_pc_arrive_with_their_history(self):
        jid = add_job("OtherPC Co")
        db.set_status(jid, "interviewing")
        snap = self.snapshot()
        wipe()
        add_job("ThisPC Co")
        r = cloud.merge(snap)
        self.assertEqual(r["added"], 1)
        names = {j["company"] for j in db.list_jobs()}
        self.assertEqual(names, {"OtherPC Co", "ThisPC Co"})
        self.assertEqual(sum(len(v) for v in db.status_history().values()), 1)

    def test_the_newest_edit_wins_and_older_ones_do_not(self):
        jid = add_job("Acme")
        with db.conn() as c:
            c.execute("UPDATE jobs SET notes='old', edited_at='2026-01-01T00:00:00+00:00' WHERE id=?", (jid,))
        older = self.snapshot()
        with db.conn() as c:
            c.execute("UPDATE jobs SET notes='newer', edited_at='2026-02-01T00:00:00+00:00' WHERE id=?", (jid,))
        newer = self.snapshot()
        self.assertEqual(cloud.merge(older)["updated"], 0)
        self.assertEqual(db.get_job(jid)["notes"], "newer")
        with db.conn() as c:
            c.execute("UPDATE jobs SET notes='old', edited_at='2026-01-01T00:00:00+00:00' WHERE id=?", (jid,))
        self.assertEqual(cloud.merge(newer)["updated"], 1)
        self.assertEqual(db.get_job(jid)["notes"], "newer")

    def test_a_deletion_carries_over_and_does_not_come_back(self):
        keep, drop = add_job("Keep"), add_job("Drop")
        before = self.snapshot()
        db.delete_entries([drop])
        cloud.merge(before)                                  # the other PC still has it: it must stay deleted
        self.assertIsNone(db.get_job(drop))
        after = self.snapshot()
        wipe()
        with db.conn() as c:
            c.execute("DELETE FROM deleted_keys")
        cloud.merge(before)
        self.assertEqual({j["company"] for j in db.list_jobs()}, {"Keep", "Drop"})
        self.assertEqual(cloud.merge(after)["removed"], 1)
        self.assertEqual({j["company"] for j in db.list_jobs()}, {"Keep"})
        self.assertIsNotNone(keep)

    def test_merging_twice_changes_nothing(self):
        add_job("Once")
        snap = self.snapshot()
        cloud.merge(snap)
        before = cloud.fingerprint()
        cloud.merge(snap)
        self.assertEqual(before, cloud.fingerprint())
        self.assertEqual(len([j for j in db.list_jobs() if j["company"] == "Once"]), 1)

    def test_sent_status_and_chats_carry_over(self):
        db.save_contacts([{"id": "c9", "email": "a@b.example", "added": db.now_iso()}])
        cid = db.new_conversation("Plan")
        db.add_coach_message(cid, "user", "hello")
        db.update_contact("c9", sent_at=db.now_iso())
        snap = self.snapshot()
        db.update_contact("c9", sent_at="")
        db.delete_conversation(cid)
        cloud.merge(snap)
        self.assertTrue(db.contact("c9")["sent_at"])
        self.assertEqual([c["title"] for c in db.conversations()], ["Plan"])
        self.assertEqual(len(db.coach_messages(db.conversations()[0]["id"])), 1)


class TwoPCs(unittest.TestCase):
    URI = Atlas.URI

    def setUp(self):
        wipe()
        self.bucket = FakeBucket()
        for p in (mock.patch.object(cloud, "_client", return_value=mock.MagicMock()),
                  mock.patch.object(cloud, "_bucket", return_value=self.bucket)):
            p.start()
            self.addCleanup(p.stop)
        cloud.connect(self.URI, "correct horse battery")

    def tearDown(self):
        cloud.disconnect()
        wipe()

    def test_nothing_is_uploaded_when_nothing_changed(self):
        add_job("Acme")
        cloud.sync()
        time.sleep(1.01)
        cloud.sync()
        self.assertEqual(len(self.bucket.files), 1)

    def test_a_snapshot_from_another_pc_is_merged_then_saved_back(self):
        add_job("FromOther")
        cloud.sync()
        self.bucket.files[0].metadata["device"] = "other-pc"
        cloud._save_sync(seen="", pushed="")
        wipe()
        add_job("FromHere")
        time.sleep(1.01)
        r = cloud.sync()
        self.assertTrue(r["pulled"])
        self.assertEqual({j["company"] for j in db.list_jobs()}, {"FromOther", "FromHere"})
        self.assertEqual(len(self.bucket.files), 2)

    def test_an_empty_new_pc_restores_everything(self):
        add_job("OnlyInCloud")
        cloud.sync()
        self.bucket.files[0].metadata["device"] = "other-pc"
        cloud._save_sync(seen="", pushed="")
        wipe()
        with db.conn() as c:
            c.execute("DELETE FROM resumes")
        self.assertTrue(cloud.sync()["pulled"])
        self.assertTrue(any(j["company"] == "OnlyInCloud" for j in db.list_jobs()))

    def test_a_different_passphrase_is_explained(self):
        add_job("Locked")
        cloud.sync()
        self.bucket.files[0].metadata["device"] = "other-pc"
        cloud._save_sync(seen="", pushed="")
        settings.write_env({cloud.PASS_VAR: "a totally different one"})
        with self.assertRaises(cloud.CloudError):
            cloud.sync()


class CompanyList(unittest.TestCase):
    def test_each_company_once_with_links_and_outcome(self):
        wipe()
        add_job("Acme Pvt Ltd", "ML Engineer")
        add_job("Acme", "Data Engineer", status="interviewing")
        add_job("Zeta & Co", "SDE")
        rows = TestClient(api.app).get("/api/companies/applied").json()["companies"]
        self.assertEqual([r["name"] for r in rows][1:], ["Zeta & Co"])
        a = rows[0]
        self.assertEqual((a["applied"], a["best"], a["replied"]), (2, "interviewing", True))
        self.assertIn("careers+India", a["careers"])
        self.assertIn("Zeta+%26+Co", rows[1]["website"])
        wipe()



class FirstRunSetup(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)
        db.set_setting("setup_done", None)
        self.resumes = mock.patch.object(api.db, "resumes", return_value=[])
        self.resumes.start()
        self.addCleanup(self.resumes.stop)
        self.addCleanup(db.set_setting, "profile", db.get_setting("profile"))
        self.addCleanup(db.set_setting, "setup_done", None)

    def test_new_install_needs_setup_until_finished(self):
        self.assertTrue(api._setup_needed())
        self.client.post("/api/setup/done")
        self.assertFalse(api._setup_needed())

    def test_existing_user_with_a_resume_never_sees_it(self):
        api.db.resumes.return_value = [{"id": 1}]
        self.assertFalse(api._setup_needed())

    def test_typed_name_is_kept_and_does_not_recheck_jobs(self):
        from jobhunt import profile
        before = profile.signature("m")
        profile.save_preferences({"name": "Asha Rao"})
        self.assertEqual(profile.preferences()["name"], "Asha Rao")
        self.assertEqual(profile.signature("m"), before)


if __name__ == "__main__":
    unittest.main()
