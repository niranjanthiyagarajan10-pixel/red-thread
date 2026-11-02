import unittest
from datetime import datetime
from decimal import Decimal

from redthread.conversation import Conversation, Turn
from redthread.facts import Reconciled
from redthread.reply import Answer, check_answer, render
from redthread.stages import Finding, Verdict
from redthread import templates


class CheckAnswerTests(unittest.TestCase):
    def test_good_answer_passes(self):
        answer = Answer("This follows a known scam script.", ["Day 3: moved you off the app."], "Hold off on paying.", ["Show the quotes", "I already paid"])
        self.assertEqual(check_answer(answer), [])

    def test_whatsapp_limits(self):
        self.assertIn("too_many_buttons", check_answer(Answer("a", [], "b", ["1", "2", "3", "4"])))
        self.assertIn("button_too_long", check_answer(Answer("a", [], "b", ["x" * 21])))
        self.assertIn("duplicate_buttons", check_answer(Answer("a", [], "b", ["x", "X"])))
        self.assertIn("too_long", check_answer(Answer("a", ["x" * 1100], "b")))

    def test_three_short_lines_means_little_proof(self):
        self.assertIn("too_much_proof", check_answer(Answer("a", ["1", "2", "3", "4"], "b")))

    def test_needs_a_headline_and_one_next_step(self):
        self.assertIn("empty", check_answer(Answer("", [], "b")))
        self.assertIn("empty", check_answer(Answer("a", [], "")))

    def test_banned_claims(self):
        for line in ["She is fake.", "He is a scammer.", "You will get your money back.", "We guarantee a refund.", "Risk score: 90", "This has a — in it."]:
            self.assertIn("banned_claim", check_answer(Answer(line, [], "b")), line)

    def test_render_is_plain_lines(self):
        text = render(Answer("Head", ["one", "two"], "Next"))
        self.assertEqual(text, "Head\none\ntwo\nNext")


class TemplateTests(unittest.TestCase):
    def assert_clean(self, answer):
        self.assertEqual(check_answer(answer), [], answer)

    def test_every_template_in_every_language_passes(self):
        findings = [Finding("moves_off_platform", 1, "move to Telegram"), Finding("investment_pitch", 3, "invest here today")]
        chat = Conversation("x", [Turn(i, "R", "t", when=datetime(2026, 3, 1 + i)) for i in range(4)])
        for language in templates.LANGUAGES:
            self.assert_clean(templates.welcome(language))
            self.assert_clean(templates.decline(language))
            self.assert_clean(templates.secret_refused(["otp"], language))
            self.assert_clean(templates.relay_blocked("recovery_offer", language))
            for kind in ("fake_authority", "code_request", "remote_app", "safe_account"):
                self.assert_clean(templates.during_call(kind, language))
            self.assert_clean(templates.after_loss(Reconciled(amount=Decimal(180000)), language))
            self.assert_clean(templates.after_loss(Reconciled(questions=["Which is right?"]), language))
            self.assert_clean(templates.script_report(Verdict("known_script", ["x"]), findings, chat, language))
            self.assert_clean(templates.script_report(Verdict("early_signs", ["x"]), findings, chat, language))
            self.assert_clean(templates.script_report(Verdict("nothing_found"), [], chat, language))

    def test_report_uses_days_when_the_chat_has_dates(self):
        chat = Conversation("x", [Turn(i, "R", "t", when=datetime(2026, 3, 1 + i)) for i in range(4)])
        answer = templates.script_report(Verdict("known_script", ["x"]), [Finding("investment_pitch", 3, "invest here today")], chat)
        self.assertIn("Day 4", answer.proof[0])
        self.assertIn("invest here today", answer.proof[0])

    def test_nothing_found_says_what_it_cannot_tell(self):
        answer = templates.script_report(Verdict("nothing_found"), [], Conversation("x", []))
        self.assertIn("does not prove", " ".join(answer.proof).lower())

    def test_during_call_answer_is_short_and_tells_them_to_hang_up(self):
        self.assertIn("hang up", templates.during_call("fake_authority").next_step.lower())

    def test_after_loss_never_blames_and_shows_the_amount(self):
        answer = templates.after_loss(Reconciled(amount=Decimal(180000)))
        self.assertIn("careful people", answer.headline)
        self.assertIn("Rs 1,80,000", " ".join(answer.proof))
        self.assertIn("1930", answer.next_step)

    def test_after_loss_asks_the_open_question_first(self):
        answer = templates.after_loss(Reconciled(questions=["Which is right?"]))
        self.assertIn("Which is right?", " ".join(answer.proof))

    def test_welcome_says_what_it_is_and_never_asks_for_secrets(self):
        text = render(templates.welcome()).lower()
        self.assertIn("ai guide", text)
        self.assertIn("otp", text)
        self.assertIn("fee", text)


class QuotedWordsTests(unittest.TestCase):
    def test_a_banned_word_inside_a_quote_does_not_lose_the_warning(self):
        answer = Answer("This follows a known scam script.", ['Day 3: "this is not fake, I am no scammer"'], "Hold off on paying.")
        self.assertEqual(check_answer(answer), [])

    def test_our_own_words_are_still_checked(self):
        answer = Answer("This person is a scammer.", ['Day 3: "hello"'], "Hold off on paying.")
        self.assertEqual(check_answer(answer), ["banned_claim"])


if __name__ == "__main__":
    unittest.main()
