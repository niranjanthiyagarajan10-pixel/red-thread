import json
import tempfile
import unittest
from pathlib import Path

from redthread.model import BudgetExceeded, Ledger, MetaModel, ModelError, load_api_key

KEY = "LLM|123|very-secret-value"


def reply(text='{"scam": true}', prompt=100, completion=50, reasoning=20, status=200):
    body = {
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    }
    return status, json.dumps(body).encode("utf-8")


class FakeTransport:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body)))
        return self.replies.pop(0)


def make_model(transport, cap=1.0, cache=None, tmp=None):
    ledger = Ledger(cap, Path(tmp) / "ledger.json")
    return MetaModel(KEY, ledger, cache_dir=cache, transport=transport, sleep=lambda _: None), ledger


class MetaModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.messages = [{"role": "user", "content": "hello"}]

    def test_contributor_models_are_refused(self):
        ledger = Ledger(1.0, Path(self.tmp.name) / "l.json")
        with self.assertRaises(ValueError):
            MetaModel(KEY, ledger, model="muse-spark-1.3-contributor")

    def test_request_shape(self):
        transport = FakeTransport(reply())
        model, _ = make_model(transport, tmp=self.tmp.name)
        schema = {"type": "object"}
        model.chat(self.messages, schema=schema, effort="minimal", max_output=500)
        url, headers, body = transport.calls[0]
        self.assertEqual(url, "https://api.meta.ai/v1/chat/completions")
        self.assertEqual(headers["Authorization"], f"Bearer {KEY}")
        self.assertEqual(body["model"], "muse-spark-1.3")
        self.assertEqual(body["reasoning_effort"], "minimal")
        self.assertEqual(body["max_completion_tokens"], 500)
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertEqual(body["response_format"]["json_schema"]["schema"], schema)

    def test_reply_usage_and_cost(self):
        model, ledger = make_model(FakeTransport(reply(prompt=1_000_000, completion=1_000_000)), cap=100, tmp=self.tmp.name)
        result = model.chat(self.messages)
        self.assertEqual(result.text, '{"scam": true}')
        self.assertEqual(result.prompt_tokens, 1_000_000)
        self.assertAlmostEqual(result.cost, 1.25 + 4.25)
        self.assertAlmostEqual(ledger.spent, 5.5)

    def test_identical_calls_use_the_cache(self):
        transport = FakeTransport(reply())
        cache = Path(self.tmp.name) / "cache"
        model, ledger = make_model(transport, cache=cache, tmp=self.tmp.name)
        first = model.chat(self.messages)
        spent = ledger.spent
        second = model.chat(self.messages)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(second.text, first.text)
        self.assertEqual(second.cost, 0)
        self.assertTrue(second.cached)
        self.assertEqual(ledger.spent, spent)

    def test_cache_never_holds_the_key(self):
        cache = Path(self.tmp.name) / "cache"
        model, _ = make_model(FakeTransport(reply()), cache=cache, tmp=self.tmp.name)
        model.chat(self.messages)
        for path in cache.iterdir():
            self.assertNotIn("very-secret-value", path.read_text(encoding="utf-8"))

    def test_rate_limits_and_server_errors_retry(self):
        transport = FakeTransport((429, b"{}"), (503, b"{}"), reply())
        model, _ = make_model(transport, tmp=self.tmp.name)
        self.assertEqual(model.chat(self.messages).text, '{"scam": true}')
        self.assertEqual(len(transport.calls), 3)

    def test_bad_requests_do_not_retry(self):
        transport = FakeTransport((400, b'{"message": "bad"}'))
        model, _ = make_model(transport, tmp=self.tmp.name)
        with self.assertRaises(ModelError):
            model.chat(self.messages)
        self.assertEqual(len(transport.calls), 1)

    def test_gives_up_after_too_many_failures(self):
        transport = FakeTransport(*[(500, b"{}")] * 5)
        model, _ = make_model(transport, tmp=self.tmp.name)
        with self.assertRaises(ModelError):
            model.chat(self.messages)

    def test_errors_never_show_the_key(self):
        transport = FakeTransport((401, f"bad key {KEY}".encode("utf-8")))
        model, _ = make_model(transport, tmp=self.tmp.name)
        with self.assertRaises(ModelError) as caught:
            model.chat(self.messages)
        self.assertNotIn("very-secret-value", str(caught.exception))

    def test_failed_calls_release_their_reservation(self):
        model, ledger = make_model(FakeTransport((400, b"{}")), tmp=self.tmp.name)
        with self.assertRaises(ModelError):
            model.chat(self.messages)
        self.assertEqual(ledger.spent, 0)
        self.assertEqual(ledger.reserved, 0)

    def test_budget_stops_the_call_before_it_is_sent(self):
        transport = FakeTransport(reply())
        model, _ = make_model(transport, cap=0.000001, tmp=self.tmp.name)
        with self.assertRaises(BudgetExceeded):
            model.chat(self.messages, max_output=1000)
        self.assertEqual(transport.calls, [])


class LedgerTests(unittest.TestCase):
    def test_spending_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            first = Ledger(1.0, path)
            ticket = first.reserve(0.5)
            first.settle(ticket, 0.25)
            self.assertAlmostEqual(Ledger(1.0, path).spent, 0.25)

    def test_cap_counts_open_reservations(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(1.0, Path(tmp) / "ledger.json")
            ledger.reserve(0.75)
            with self.assertRaises(BudgetExceeded):
                ledger.reserve(0.5)


class ApiKeyTests(unittest.TestCase):
    def test_environment_comes_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text("MODEL_API_KEY=from-file\n", encoding="utf-8")
            self.assertEqual(load_api_key({"MODEL_API_KEY": "from-env"}, Path(tmp)), "from-env")

    def test_reads_the_file_and_strips_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text('OTHER=1\nMODEL_API_KEY="from-file"\n', encoding="utf-8")
            self.assertEqual(load_api_key({}, Path(tmp)), "from-file")

    def test_missing_key_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_api_key({}, Path(tmp)))


if __name__ == "__main__":
    unittest.main()
