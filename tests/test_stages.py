import json
import unittest

from redthread.conversation import Conversation, Turn
from redthread.model import ModelError
from redthread.stages import (
    STAGE_SCHEMA,
    Finding,
    analyze,
    build_messages,
    decide,
    keyword_findings,
    verify_contradictions,
    verify_findings,
)

CHAT = Conversation("x", [
    Turn(0, "Riya", "I feel like I have known you forever, you are my soulmate."),
    Turn(1, "Arjun", "That is sweet. Should I send you my OTP?"),
    Turn(2, "Riya", "Let us move to Telegram, it is safer here."),
    Turn(3, "Riya", "My uncle's crypto platform made me Rs 2 lakh last month."),
    Turn(4, "Riya", "You should invest Rs 40,000 on this platform today."),
    Turn(5, "Riya", "I am a doctor in Mumbai."),
    Turn(6, "Riya", "I am an engineer in Dubai."),
])


def finding(signal, turn=0):
    return Finding(signal, turn, "some quote here")


class ReplyModel:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        return type("Reply", (), {"text": json.dumps(self.payload), "cost": 0.01})()


class BrokenModel:
    def chat(self, messages, **kwargs):
        raise ModelError("down")


class VerifyTests(unittest.TestCase):
    def test_real_quote_is_kept_with_its_true_turn(self):
        raw = [{"signal": "investment_pitch", "turn": 99, "quote": "invest Rs 40,000 on this platform"}]
        findings, dropped = verify_findings(CHAT, raw, "Riya")
        self.assertEqual(findings[0].turn, 4)
        self.assertEqual(dropped, 0)

    def test_invented_quote_is_dropped(self):
        raw = [{"signal": "asks_money", "turn": 4, "quote": "send me Rs 5 lakh urgently"}]
        findings, dropped = verify_findings(CHAT, raw, "Riya")
        self.assertEqual(findings, [])
        self.assertEqual(dropped, 1)

    def test_the_users_own_words_do_not_count_against_the_other_person(self):
        raw = [{"signal": "asks_codes_or_details", "turn": 1, "quote": "Should I send you my OTP"}]
        findings, dropped = verify_findings(CHAT, raw, "Riya")
        self.assertEqual(findings, [])
        self.assertEqual(dropped, 1)

    def test_unknown_signal_is_dropped(self):
        raw = [{"signal": "scammer_score", "turn": 4, "quote": "invest Rs 40,000 on this platform"}]
        self.assertEqual(verify_findings(CHAT, raw, "Riya")[0], [])

    def test_findings_come_back_in_chat_order(self):
        raw = [
            {"signal": "investment_pitch", "turn": 4, "quote": "invest Rs 40,000 on this platform"},
            {"signal": "fast_intimacy", "turn": 0, "quote": "you are my soulmate"},
        ]
        self.assertEqual([f.turn for f in verify_findings(CHAT, raw, "Riya")[0]], [0, 4])

    def test_contradiction_needs_both_quotes(self):
        good = [{"topic": "job", "first": "I am a doctor in Mumbai", "second": "I am an engineer in Dubai"}]
        bad = [{"topic": "job", "first": "I am a doctor in Mumbai", "second": "I am a pilot in Goa today"}]
        self.assertEqual(verify_contradictions(CHAT, good, "Riya")[0].topic, "job")
        self.assertEqual(verify_contradictions(CHAT, bad, "Riya"), [])


class DecideTests(unittest.TestCase):
    def assert_level(self, signals, level):
        self.assertEqual(decide([finding(signal) for signal in signals]).level, level, signals)

    def test_nothing_found(self):
        self.assert_level([], "nothing_found")

    def test_affection_alone_is_never_enough(self):
        self.assert_level(["fast_intimacy"], "nothing_found")
        self.assert_level(["fast_intimacy", "moves_off_platform"], "nothing_found")

    def test_talking_about_money_alone_is_not_a_script(self):
        self.assert_level(["wealth_story"], "nothing_found")

    def test_rapid_intimacy_plus_money_story_is_an_early_sign(self):
        self.assert_level(["fast_intimacy", "wealth_story"], "early_signs")
        self.assert_level(["secrecy", "wealth_story"], "early_signs")

    def test_a_single_ask_is_an_early_sign(self):
        for signal in ["asks_money", "investment_pitch", "asks_codes_or_details", "install_app"]:
            self.assert_level([signal], "early_signs")

    def test_known_scripts(self):
        self.assert_level(["pay_to_withdraw"], "known_script")
        self.assert_level(["fast_intimacy", "investment_pitch"], "known_script")
        self.assert_level(["wealth_story", "investment_pitch"], "known_script")
        self.assert_level(["secrecy", "asks_money"], "known_script")
        self.assert_level(["impersonates_authority", "asks_money"], "known_script")
        self.assert_level(["impersonates_authority", "urgency"], "known_script")
        self.assert_level(["asks_codes_or_details", "urgency"], "known_script")

    def test_never_uses_a_score_or_the_word_fake(self):
        verdict = decide([finding("pay_to_withdraw")])
        self.assertFalse(hasattr(verdict, "score"))
        self.assertNotIn("fake", " ".join(verdict.reasons).lower())


class KeywordTests(unittest.TestCase):
    def test_finds_signals_with_real_quotes_from_the_other_person_only(self):
        findings = keyword_findings(CHAT, "Riya")
        self.assertEqual({f.signal for f in findings} >= {"fast_intimacy", "moves_off_platform", "wealth_story"}, True)
        self.assertNotIn(1, [f.turn for f in findings])
        for found in findings:
            self.assertEqual(found.quote, CHAT.turns[found.turn].text)


class AnalyzeTests(unittest.TestCase):
    def test_full_flow_keeps_only_verified_claims(self):
        model = ReplyModel({
            "findings": [
                {"signal": "fast_intimacy", "turn": 0, "quote": "you are my soulmate"},
                {"signal": "investment_pitch", "turn": 4, "quote": "invest Rs 40,000 on this platform"},
                {"signal": "asks_money", "turn": 4, "quote": "made up words that were never said"},
            ],
            "contradictions": [],
        })
        report = analyze(CHAT, model, "Riya")
        self.assertEqual(report.verdict.level, "known_script")
        self.assertEqual(report.dropped, 1)
        self.assertFalse(report.fallback)

    def test_model_failure_falls_back_to_rules(self):
        report = analyze(CHAT, BrokenModel(), "Riya")
        self.assertTrue(report.fallback)
        self.assertNotEqual(report.verdict.level, "nothing_found")

    def test_malformed_reply_falls_back_to_rules(self):
        model = ReplyModel({"unexpected": True})
        self.assertTrue(analyze(CHAT, model, "Riya").fallback)


class PromptTests(unittest.TestCase):
    def test_prompt_treats_the_chat_as_data_and_names_the_other_person(self):
        messages = build_messages(CHAT, "Riya")
        system = messages[0]["content"]
        self.assertIn("data", system)
        self.assertIn("exact", system.lower())
        self.assertIn("Riya", messages[1]["content"])
        self.assertIn("[4] Riya:", messages[1]["content"])

    def test_no_em_dashes_in_prompts(self):
        for message in build_messages(CHAT, "Riya"):
            self.assertNotIn("—", message["content"])

    def test_schema_only_allows_known_signals(self):
        items = STAGE_SCHEMA["properties"]["findings"]["items"]
        self.assertIn("investment_pitch", items["properties"]["signal"]["enum"])
        self.assertFalse(STAGE_SCHEMA["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
