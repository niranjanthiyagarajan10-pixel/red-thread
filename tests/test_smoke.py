import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from redthread.model import load_api_key
from redthread.smoke import SMOKE_CAP, main

SECRET = "LLM_123_secretvalue"


def reply(prompt=20, completion=5):
    body = {
        "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }
    return 200, json.dumps(body).encode("utf-8")


class SmokeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.calls = []

    def run_smoke(self, environ=None, answer=reply):
        def transport(url, headers, body, timeout):
            self.calls.append((url, json.loads(body)))
            return answer() if callable(answer) else answer

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(environ={} if environ is None else environ, home=self.home, transport=transport)
        return code, out.getvalue()

    def test_no_key_says_where_to_put_it_and_spends_nothing(self):
        code, text = self.run_smoke()
        self.assertEqual(code, 1)
        self.assertIn(".env", text)
        self.assertEqual(self.calls, [])

    def test_a_file_saved_as_env_txt_is_named_as_the_problem(self):
        (self.home / ".env.txt").write_text(f"MODEL_API_KEY={SECRET}", encoding="utf-8")
        code, text = self.run_smoke()
        self.assertEqual(code, 1)
        self.assertIn(".env.txt", text)

    def test_success_makes_exactly_one_call_and_reports_the_cost(self):
        code, text = self.run_smoke({"MODEL_API_KEY": SECRET})
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("muse-spark-1.3", text)
        self.assertIn("20 in, 5 out", text)
        self.assertIn("$0.0000", text)

    def test_it_never_uses_a_contributor_model(self):
        self.run_smoke({"MODEL_API_KEY": SECRET})
        self.assertNotIn("contributor", self.calls[0][1]["model"])

    def test_the_key_is_never_printed(self):
        for answer in (reply, (401, b"bad key")):
            _, text = self.run_smoke({"MODEL_API_KEY": SECRET}, answer)
            self.assertNotIn("secretvalue", text)

    def test_rejected_key_explains_itself(self):
        code, text = self.run_smoke({"MODEL_API_KEY": SECRET}, (401, b"bad key"))
        self.assertEqual(code, 1)
        self.assertIn("401", text)
        self.assertEqual(len(self.calls), 1)

    def test_it_stops_before_calling_once_the_cap_is_used(self):
        results = self.home / "results"
        results.mkdir()
        (results / "smoke-ledger.json").write_text(json.dumps({"spent": SMOKE_CAP}), encoding="utf-8")
        code, text = self.run_smoke({"MODEL_API_KEY": SECRET})
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, [])
        self.assertIn("cap", text)


class KeyFileTests(unittest.TestCase):
    def test_key_file_saved_by_notepad_with_a_byte_order_mark_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text(f"MODEL_API_KEY={SECRET}\n", encoding="utf-8-sig")
            self.assertEqual(load_api_key({}, tmp), SECRET)


if __name__ == "__main__":
    unittest.main()
