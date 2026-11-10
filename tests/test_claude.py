"""Claude through the native SDK, with a fake client: no network and no spend."""
import unittest
from types import SimpleNamespace
from unittest import mock

from jobhunt import claude, db, llm, settings


def reply(text="Hello", stop="end_turn", model=claude.OPUS, cache_read=0, cache_write=0):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, model=model,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=40, cache_read_input_tokens=cache_read,
                                                 cache_creation_input_tokens=cache_write))


class FakeClient:
    def __init__(self, result):
        self.calls = []
        self.result = result
        make = self._create
        self.messages = SimpleNamespace(create=lambda **kw: make("messages", kw))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: make("beta", kw)))

    def _create(self, path, kw):
        self.calls.append((path, kw))
        return self.result


class Convert(unittest.TestCase):
    def test_system_images_and_alternation(self):
        system, msgs = claude.convert([
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": [{"type": "text", "text": "Read this"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
            {"role": "user", "content": "and this"},
            {"role": "assistant", "content": "ok"}])
        self.assertEqual(system, "Be brief.")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[0]["content"][1], {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}})
        self.assertEqual(msgs[0]["content"][2]["text"], "and this")


class Complete(unittest.TestCase):
    def run_one(self, model, feature, result=None):
        fake = FakeClient(result or reply(model=model))
        with mock.patch.object(claude, "client", return_value=fake):
            out = claude.complete([{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}], "k", model, feature, 300)
        return out, fake.calls[0]

    def test_opus_uses_refusal_fallback_effort_and_caching(self):
        (text, used, usage), (path, kw) = self.run_one(claude.OPUS, "cover_letter", reply(cache_read=60, cache_write=20))
        self.assertEqual((text, used), ("Hello", claude.OPUS))
        self.assertEqual(path, "beta")
        self.assertEqual((kw["fallbacks"], kw["betas"]), ("default", [claude.FALLBACK_BETA]))
        self.assertEqual(kw["output_config"], {"effort": "high"})
        self.assertEqual(kw["cache_control"], {"type": "ephemeral"})
        self.assertEqual(kw["system"], "S")
        self.assertGreaterEqual(kw["max_tokens"], claude.MIN_TOKENS)       # thinking counts toward the limit
        self.assertEqual(usage, {"tokens_in": 180, "tokens_out": 40, "cache_read": 60, "cache_write": 20})

    def test_haiku_for_bulk_without_beta(self):
        _, (path, kw) = self.run_one(claude.HAIKU, "screening")
        self.assertEqual(path, "messages")
        self.assertNotIn("fallbacks", kw)
        self.assertEqual(kw["output_config"], {"effort": "low"})

    def test_structured_output_schema(self):
        fake = FakeClient(reply('{"a": 1}', model=claude.HAIKU))
        with mock.patch.object(claude, "client", return_value=fake):
            claude.complete([{"role": "user", "content": "x"}], "k", claude.HAIKU, "screening", schema={"type": "object"})
        self.assertEqual(fake.calls[0][1]["output_config"]["format"], {"type": "json_schema", "schema": {"type": "object"}})

    def test_refusal_and_empty_answers_raise(self):
        for bad in (reply(stop="refusal"), reply(text="")):
            with self.assertRaises(claude.ClaudeError):
                self.run_one(claude.OPUS, "chat", bad)

    def test_task_models(self):
        with mock.patch.object(claude, "list_models", side_effect=RuntimeError("offline")):
            claude._latest.update(at=0.0, ids={})
            self.assertEqual(claude.task_model("screening", "k"), claude.HAIKU)
            self.assertEqual(claude.task_model("coach", "k"), claude.OPUS)
        with mock.patch.object(claude, "list_models", return_value=["claude-opus-6", "claude-opus-5-5", "claude-haiku-5-5"]):
            claude._latest.update(at=0.0, ids={})
            self.assertEqual(claude.latest("opus", "k"), "claude-opus-6")         # newest first
        claude._latest.update(at=0.0, ids={})


class CostAndLimit(unittest.TestCase):
    def setUp(self):
        self.saved = {k: settings.read_env().get(k) for k in llm.KEY_VARS}
        settings.write_env({k: None for k in llm.KEY_VARS})
        db.set_setting("ai_monthly_limit", None)
        self.addCleanup(lambda: settings.write_env(self.saved))
        self.addCleanup(db.set_setting, "ai_monthly_limit", None)
        self.addCleanup(db.set_setting, "llm_order", None)
        self.addCleanup(db.set_setting, "llm_order_v4", None)

    def test_cache_reads_are_cheap(self):
        price = (4.0, 20.0, 0.20)
        full = llm.cost_of(price, 10_000, 0)
        cached = llm.cost_of(price, 10_000, 0, cache_read=9_000)
        self.assertAlmostEqual(full, 0.04)
        self.assertAlmostEqual(cached, (1_000 * 4 + 9_000 * 0.2) / 1e6)
        self.assertIsNone(llm.cost_of(None, 10, 10))

    def test_claude_is_first_and_old_orders_are_moved_once(self):
        self.assertEqual(list(llm.PROVIDERS)[:3], ["anthropic", "openai", "gemini"])
        db.set_setting("llm_order", ["groq", "openai"])
        db.set_setting("llm_order_v4", None)
        self.assertEqual(llm.order()[:4], ["anthropic", "openai", "gemini", "groq"])

    def test_over_limit_only_free_providers_answer(self):
        settings.write_env({"ANTHROPIC_API_KEY": "sk-ant-test-1234567890", "GROQ_API_KEY": "gsk_test_1234567890"})
        db.set_setting("ai_monthly_limit", 1)
        tried = []

        def fake(pid, messages, max_tokens, env=None, model=None, feature="chat"):
            tried.append(pid)
            return "ok"
        with mock.patch.object(db, "month_spend", return_value=5.0), mock.patch.object(llm, "_complete", side_effect=fake), \
                mock.patch.object(llm, "ollama_models", return_value=[]), mock.patch.object(llm, "default_model", return_value="m"):
            r = llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual((tried, r.provider), (["groq"], "groq"))

    def test_claude_refusal_falls_back_to_next_provider(self):
        settings.write_env({"ANTHROPIC_API_KEY": "sk-ant-test-1234567890", "GROQ_API_KEY": "gsk_test_1234567890"})
        with mock.patch.object(claude, "complete", side_effect=claude.ClaudeError("Claude declined this request.")), \
                mock.patch.object(claude, "task_model", return_value=claude.OPUS), \
                mock.patch.object(llm, "ollama_models", return_value=[]), \
                mock.patch.object(llm, "_complete", wraps=llm._complete) as comp:
            with mock.patch.object(llm, "client") as oa:
                oa.return_value.chat.completions.create.return_value = SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="from groq"))], usage=None)
                r = llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual((r[0], r.provider, r.fell_back_from), ("from groq", "groq", ""))
        self.assertEqual([c.args[0] for c in comp.call_args_list], ["anthropic", "groq"])


if __name__ == "__main__":
    unittest.main()
