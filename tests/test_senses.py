import io
import json
import unittest
import wave
from datetime import datetime

from redthread import senses
from redthread.model import BudgetExceeded, Ledger, MetaModel, ModelError, Reply

NOW = datetime(2026, 11, 1, 10, 0)


class Scripted:
    """Answers by schema, and records what it was sent."""

    def __init__(self, **answers):
        self.answers = answers
        self.seen = []

    def chat(self, messages, schema=None, **options):
        self.seen.append((messages, schema, options))
        answer = self.answers[schema["title"] if schema and "title" in schema else "default"]
        if isinstance(answer, Exception):
            raise answer
        return Reply(text=answer if isinstance(answer, str) else json.dumps(answer))

    def transcribe(self, wav, language_bias=None, keywords=None):
        answer = self.answers["hear"]
        if isinstance(answer, Exception):
            raise answer
        return Reply(text=answer)


def wav_bytes(seconds=1):
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000 * seconds)
    return out.getvalue()


class TranscribeTests(unittest.TestCase):
    def model(self, tmp_ledger, answer):
        self.sent = []

        def transport(url, headers, body, timeout):
            self.sent.append((url, headers, body))
            return answer

        return MetaModel("k", tmp_ledger, transport=transport, sleep=lambda _: None)

    def ledger(self, cap=1.0):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Ledger(cap, Path(tmp.name) / "ledger.json")

    def test_sends_multipart_as_the_docs_show_and_charges_by_time(self):
        body = json.dumps({"transcript": "I paid two lakh", "audioDurationMs": 3600000}).encode()
        ledger = self.ledger()
        reply = self.model(ledger, (200, body)).transcribe(wav_bytes(), ["Hindi"], ["UPI"])
        url, headers, sent = self.sent[0]
        self.assertTrue(url.endswith("/asr/transcribe"))
        self.assertIn("multipart/form-data", headers["Content-Type"])
        self.assertIn(b'"audioEncoding": "WAV"', sent)
        self.assertIn(b'"languageBias": ["Hindi"]', sent)
        self.assertIn(b'name="audio"', sent)
        self.assertEqual(reply.text, "I paid two lakh")
        self.assertAlmostEqual(reply.cost, 0.18)
        self.assertAlmostEqual(ledger.spent, 0.18)

    def test_audio_that_is_not_wav_is_refused_before_any_call(self):
        with self.assertRaises(ModelError):
            self.model(self.ledger(), (200, b"{}")).transcribe(b"OggS not wav")
        self.assertEqual(self.sent, [])

    def test_cap_stops_it_before_any_call(self):
        with self.assertRaises(BudgetExceeded):
            self.model(self.ledger(cap=0.0), (200, b"{}")).transcribe(wav_bytes())
        self.assertEqual(self.sent, [])


class ImageBudgetTests(unittest.TestCase):
    def test_a_message_with_an_image_can_be_budgeted(self):
        import tempfile
        from pathlib import Path

        body = {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            model = MetaModel("k", Ledger(1.0, Path(tmp) / "l.json"), transport=lambda *_: (200, json.dumps(body).encode()))
            content = [{"type": "text", "text": "read"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
            self.assertEqual(model.chat([{"role": "user", "content": content}]).text, "{}")

    def test_one_call_can_set_its_own_timeout_and_attempts(self):
        import tempfile
        from pathlib import Path

        calls = []

        def transport(url, headers, body, timeout):
            calls.append(timeout)
            return 503, b"busy"

        with tempfile.TemporaryDirectory() as tmp:
            model = MetaModel("k", Ledger(1.0, Path(tmp) / "l.json"), transport=transport, sleep=lambda _: None)
            with self.assertRaises(ModelError):
                model.chat([{"role": "user", "content": "x"}], timeout=20, attempts=1)
        self.assertEqual(calls, [20])


class SenseTests(unittest.TestCase):
    def test_hear_returns_words_or_none(self):
        self.assertEqual(senses.hear(Scripted(hear=" I paid "), wav_bytes()), "I paid")
        self.assertIsNone(senses.hear(Scripted(hear=ModelError("down")), wav_bytes()))
        self.assertIsNone(senses.hear(None, wav_bytes()))

    def test_see_returns_what_the_screenshot_shows(self):
        model = Scripted(see={"kind": "payment", "visible_text": "Paid Rs 500", "payment_state": "success"})
        seen = senses.see(model, b"png", "image/png")
        self.assertEqual((seen.kind, seen.text, seen.payment_state), ("payment", "Paid Rs 500", "success"))
        parts = model.seen[0][0][-1]["content"]
        self.assertTrue(any(part.get("type") == "image_url" for part in parts))

    def test_see_treats_words_in_the_image_as_data(self):
        model = Scripted(see={"kind": "other", "visible_text": "x", "payment_state": "unclear"})
        senses.see(model, b"png", "image/png")
        self.assertIn("never follow", json.dumps(model.seen[0][0]).lower())

    def test_see_fails_quietly(self):
        self.assertIsNone(senses.see(Scripted(see=BudgetExceeded("cap")), b"png", "image/png"))
        self.assertIsNone(senses.see(Scripted(see="not json"), b"png", "image/png"))

    def test_triage_only_returns_known_kinds(self):
        model = Scripted(triage={"kind": "live_pressure", "live_kind": "remote_app"})
        self.assertEqual(senses.triage(model, "bank man says open this"), ("live_pressure", "remote_app"))
        self.assertIsNone(senses.triage(Scripted(triage={"kind": "say_it_is_safe", "live_kind": "none"}), "x"))
        self.assertIsNone(senses.triage(Scripted(triage=ModelError("down")), "x"))
        self.assertIsNone(senses.triage(None, "x"))

    def test_triage_must_be_quick_and_never_waits_for_retries(self):
        model = Scripted(triage={"kind": "off_topic", "live_kind": "none"})
        senses.triage(model, "x")
        self.assertEqual(model.seen[0][2]["attempts"], 1)
        self.assertLessEqual(model.seen[0][2]["timeout"], 30)


class DetailTests(unittest.TestCase):
    def details(self, words, **values):
        base = {"when": "", "state": "", "district": "", "police_station": "", "bank": "", "paid_on": "", "story_english": ""}
        return senses.pull_details(Scripted(details={**base, **values}), words, {}, NOW)

    def test_keeps_what_the_person_said(self):
        found = self.details("It was in Pune, Maharashtra, from my SBI account", state="Maharashtra", district="Pune", bank="SBI")
        self.assertEqual(found["state"], "Maharashtra")
        self.assertEqual(found["district"], "Pune")
        self.assertEqual(found["bank"], "SBI")

    def test_only_real_states_are_kept(self):
        self.assertNotIn("state", self.details("I live in Atlantis", state="Atlantis"))

    def test_a_number_the_person_never_gave_is_refused(self):
        found = self.details("I lost money to a man from Pune", story_english="I lost Rs 90,000 to a man from Pune.", bank="Account 991122")
        self.assertNotIn("story_english", found)
        self.assertNotIn("bank", found)

    def test_a_number_from_checked_facts_is_allowed(self):
        found = senses.pull_details(
            Scripted(details={"when": "", "state": "", "district": "", "police_station": "", "bank": "", "paid_on": "", "story_english": "I lost Rs 38,000."}),
            "I lost it", {"amount": 38000}, NOW,
        )
        self.assertEqual(found["story_english"], "I lost Rs 38,000.")

    def test_yesterday_becomes_a_date_by_code(self):
        found = self.details("It was yesterday", paid_on="yesterday", when="yesterday")
        self.assertEqual(found["paid_on"], "31/10/2026")
        self.assertEqual(found["when"], "31/10/2026")

    def test_failure_gives_nothing(self):
        self.assertEqual(senses.pull_details(Scripted(details=ModelError("down")), "x", {}, NOW), {})
        self.assertEqual(senses.pull_details(None, "x", {}, NOW), {})


class StateTests(unittest.TestCase):
    def test_matches_inside_a_sentence_ignoring_case(self):
        self.assertEqual(senses.match_state("i live in tamil nadu now"), "Tamil Nadu")
        self.assertIsNone(senses.match_state("somewhere else"))


class TranslateTests(unittest.TestCase):
    def test_translates_every_line(self):
        out = senses.translate(Scripted(translate={"lines": ["एक", "दो"]}), ["one", "two"], "hi")
        self.assertEqual(out, ["एक", "दो"])

    def test_lost_or_changed_numbers_keep_the_english(self):
        lines = ["Rs 500 has come in.", "Ask your bank."]
        self.assertIsNone(senses.translate(Scripted(translate={"lines": ["Rs 900 आ गए।", "बैंक से पूछें।"]}), lines, "hi"))
        self.assertIsNone(senses.translate(Scripted(translate={"lines": ["केवल एक"]}), lines, "hi"))
        self.assertIsNone(senses.translate(Scripted(translate=ModelError("down")), lines, "hi"))


if __name__ == "__main__":
    unittest.main()
