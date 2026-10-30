import csv
import json
import tempfile
import unittest
from pathlib import Path

from redthread import benchmark as bm
from redthread.conversation import Conversation, Turn
from redthread.model import BudgetExceeded, ModelError, Reply


def write_chat(root, name, stem, lines):
    folder = Path(root) / "data" / name
    folder.mkdir(parents=True, exist_ok=True)
    conversation = [{"Role": role, "Statement": text} for role, text in lines]
    (folder / f"{stem}.json").write_text(json.dumps({"conversation": conversation}), encoding="utf-8")


SCAM = Conversation("romance_baiting/1", [
    Turn(0, "assistant", "You are my soulmate."),
    Turn(1, "user", "Thanks. Is my OTP safe?"),
    Turn(2, "assistant", "My crypto platform made me rich."),
    Turn(3, "assistant", "Invest on this platform today."),
], label="romance_baiting")
NORMAL = Conversation("neutral/1", [
    Turn(0, "assistant", "Good day. How may I help?"),
    Turn(1, "user", "Tell me about social norms."),
    Turn(2, "assistant", "They are unwritten rules."),
], label="neutral")


class ScriptedModel:
    """Flags any message holding the word platform. Quotes it for the pipeline."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, schema=None, effort="low", max_output=0):
        self.calls += 1
        content = messages[-1]["content"]
        if "findings" in (schema or {}).get("properties", {}):
            findings = []
            if "platform" in content:
                findings.append({"signal": "investment_pitch", "turn": 3, "quote": "Invest on this platform today"})
                findings.append({"signal": "fast_intimacy", "turn": 0, "quote": "You are my soulmate"})
            return Reply(text=json.dumps({"findings": findings, "contradictions": []}), cost=0.01)
        return Reply(text=json.dumps({"scam": "platform" in content}), cost=0.01)


class FailingModel:
    def chat(self, *args, **kwargs):
        raise ModelError("down")


class BudgetModel:
    def chat(self, *args, **kwargs):
        raise BudgetExceeded("cap")


class LoadTests(unittest.TestCase):
    def test_loads_turns_in_numeric_order_with_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_chat(tmp, "neutral", "10", [("assistant", "ten")])
            write_chat(tmp, "neutral", "2", [("assistant", "two"), ("user", "ok")])
            chats = bm.load_set(tmp, "neutral")
        self.assertEqual([c.id for c in chats], ["neutral/2", "neutral/10"])
        self.assertEqual(chats[0].label, "neutral")
        self.assertEqual(chats[0].turns[1].speaker, "user")
        self.assertEqual(chats[0].turns[1].index, 1)

    def test_split_is_stable_and_about_thirty_percent_dev(self):
        ids = [f"romance_baiting/{i}" for i in range(1000)]
        dev = [i for i in ids if bm.split_of(i) == "dev"]
        self.assertTrue(250 <= len(dev) <= 350, len(dev))
        self.assertEqual(bm.split_of(ids[0]), bm.split_of(ids[0]))


class ArmTests(unittest.TestCase):
    def test_keyword_arm_needs_no_model(self):
        self.assertTrue(bm.arm_keyword(SCAM).flagged)
        self.assertFalse(bm.arm_keyword(NORMAL).flagged)

    def test_message_arm_reads_only_the_other_person_and_stops_at_the_first_flag(self):
        model = ScriptedModel()
        result = bm.arm_message(SCAM, model)
        self.assertTrue(result.flagged)
        self.assertEqual(result.first_turn, 2)
        self.assertEqual(model.calls, 2)

    def test_message_arm_on_a_normal_chat(self):
        model = ScriptedModel()
        result = bm.arm_message(NORMAL, model)
        self.assertFalse(result.flagged)
        self.assertEqual(model.calls, 2)

    def test_whole_arm(self):
        self.assertTrue(bm.arm_whole(SCAM, ScriptedModel()).flagged)
        self.assertFalse(bm.arm_whole(NORMAL, ScriptedModel()).flagged)

    def test_pipeline_arm_reports_the_first_verified_turn(self):
        result = bm.arm_pipeline(SCAM, ScriptedModel())
        self.assertTrue(result.flagged)
        self.assertEqual(result.first_turn, 0)

    def test_model_failures_are_errors_not_clean_results(self):
        for arm in (bm.arm_message, bm.arm_whole, bm.arm_pipeline):
            result = arm(SCAM, FailingModel())
            self.assertTrue(result.error, arm.__name__)
            self.assertFalse(result.flagged, arm.__name__)

    def test_budget_stop_is_not_hidden(self):
        for arm in (bm.arm_message, bm.arm_whole, bm.arm_pipeline):
            with self.assertRaises(BudgetExceeded):
                arm(SCAM, BudgetModel())


class ScoreTests(unittest.TestCase):
    def test_wilson_interval(self):
        self.assertEqual(bm.wilson(0, 0), (0.0, 0.0))
        low, high = bm.wilson(5, 10)
        self.assertAlmostEqual(low, 0.2366, places=3)
        self.assertAlmostEqual(high, 0.7634, places=3)
        self.assertEqual(bm.wilson(0, 10)[0], 0.0)
        self.assertAlmostEqual(bm.wilson(10, 10)[1], 1.0, places=6)

    def test_errors_are_left_out_of_the_rate_and_counted(self):
        results = [
            bm.ArmResult(flagged=True, first_turn=4),
            bm.ArmResult(flagged=True, first_turn=8),
            bm.ArmResult(flagged=False),
            bm.ArmResult(flagged=False, error=True),
        ]
        scored = bm.score_results(results)
        self.assertEqual(scored["n"], 3)
        self.assertEqual(scored["flagged"], 2)
        self.assertEqual(scored["errors"], 1)
        self.assertAlmostEqual(scored["rate"], 2 / 3)
        self.assertEqual(scored["median_first_turn"], 6)


class PassMarkTests(unittest.TestCase):
    @staticmethod
    def scores(romance, neutral, message=None, dating=None):
        scores = {"pipeline": {"romance_baiting": {"rate": romance, "n": 100}, "neutral": {"rate": neutral, "n": 100}}}
        if dating is not None:
            scores["pipeline"]["normal_dating"] = {"rate": dating, "n": 100}
        if message is not None:
            scores["message"] = {"romance_baiting": {"rate": message, "n": 100}}
        return scores

    def test_all_pass(self):
        marks = bm.pass_marks(self.scores(0.95, 0.02, message=0.2, dating=0.08))
        self.assertTrue(all(mark["passed"] for mark in marks))
        self.assertEqual(len(marks), 4)

    def test_each_mark_can_fail(self):
        marks = {m["name"]: m["passed"] for m in bm.pass_marks(self.scores(0.85, 0.06, message=0.5, dating=0.2))}
        self.assertFalse(marks["romance_caught"])
        self.assertFalse(marks["benchmark_false_alarms"])
        self.assertFalse(marks["beats_message_by_message"])
        self.assertFalse(marks["normal_dating_false_alarms"])

    def test_missing_arms_are_not_counted_as_passes(self):
        marks = {m["name"]: m["passed"] for m in bm.pass_marks(self.scores(0.95, 0.02))}
        self.assertIsNone(marks["beats_message_by_message"])
        self.assertIsNone(marks["normal_dating_false_alarms"])


class PublishedResultsTests(unittest.TestCase):
    def test_false_positives_are_not_counted_as_catches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "filter.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, ["topic", "filename", "any_flagged", "verdict"])
                writer.writeheader()
                writer.writerow({"topic": "romance_baiting", "filename": "1.json", "any_flagged": "True", "verdict": "TP"})
                writer.writerow({"topic": "romance_baiting", "filename": "2.json", "any_flagged": "True", "verdict": "FP"})
                writer.writerow({"topic": "romance_baiting", "filename": "3.json", "any_flagged": "False", "verdict": ""})
            result = bm.published_results(path)
        self.assertEqual(result["romance_baiting"], {"n": 3, "flagged": 2, "real_catches": 1})


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        for i in range(12):
            write_chat(self.root, "romance_baiting", str(i), [("assistant", "You are my soulmate. My crypto gains are great.")])
            write_chat(self.root, "neutral", str(i), [("assistant", "Good day to you.")])

    def run_main(self, *args):
        out = Path(self.root) / "out.json"
        code = bm.main(["--root", self.root, "--out", str(out), *args])
        return code, json.loads(out.read_text(encoding="utf-8"))

    def test_keyword_arm_runs_with_no_key(self):
        code, report = self.run_main("--arms", "keyword", "--split", "all")
        self.assertEqual(code, 0)
        self.assertEqual(report["scores"]["keyword"]["romance_baiting"]["rate"], 1.0)
        self.assertEqual(report["scores"]["keyword"]["neutral"]["rate"], 0.0)

    def test_the_held_out_split_needs_a_deliberate_flag(self):
        with self.assertRaises(SystemExit):
            bm.main(["--root", self.root, "--arms", "keyword", "--split", "test"])
        code, report = self.run_main("--arms", "keyword", "--split", "test", "--final")
        self.assertEqual(code, 0)
        self.assertEqual(report["split"], "test")

    def test_model_arms_without_a_key_stop_with_a_plain_message(self):
        with self.assertRaises(SystemExit) as caught:
            bm.main(["--root", self.root, "--arms", "whole", "--split", "all", "--home", self.root, "--no-env-key"])
        self.assertIn("MODEL_API_KEY", str(caught.exception))

    def test_report_credits_the_benchmark_authors_and_has_no_em_dash(self):
        text = bm.render_markdown({"split": "dev", "scores": {}, "marks": [], "published": {}, "stopped": False, "cost": 0})
        self.assertIn("Gressel", text)
        self.assertNotIn("—", text)

    def test_budget_stop_keeps_partial_results_and_says_so(self):
        chats = bm.load_set(self.root, "neutral")
        results, stopped = bm.run_arm(bm.arm_whole, chats, BudgetModel(), workers=2)
        self.assertTrue(stopped)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
