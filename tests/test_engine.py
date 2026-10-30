import unittest
from datetime import datetime
from decimal import Decimal

from redthread.engine import Engine, Incoming
from redthread.network import Network
from redthread.reply import check_answer, render
from redthread.world import demo_events, run_world

NOW = datetime(2026, 11, 1, 10, 0)
CHAT_EXPORT = """01/03/2026, 21:00 - Riya: Hi, you seem so genuine.
01/03/2026, 21:05 - Arjun: Thanks, you too.
02/03/2026, 21:00 - Riya: You are my soulmate, I feel it already.
04/03/2026, 21:00 - Riya: Let us move to Telegram, the app is glitchy.
09/03/2026, 21:00 - Riya: My uncle's crypto desk doubled my savings.
20/03/2026, 21:00 - Riya: You should invest on this platform today, pay scam.one@ybl
"""
NORMAL_EXPORT = """01/03/2026, 21:00 - Meera: Dinner on Friday?
01/03/2026, 21:05 - Arjun: Yes, the usual place.
05/03/2026, 21:00 - Meera: Lovely evening. I will send you the photos.
"""


def engine():
    network = Network(":memory:", salt=b"s")
    return Engine(network=network, model=None)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine()
        self.addCleanup(self.engine.network.close)

    def send(self, user="u1", **fields):
        return self.engine.handle(user, Incoming(now=NOW, **fields))

    def test_first_empty_message_gets_the_welcome(self):
        self.assertIn("AI guide", self.send(text="").answer.headline)

    def test_secrets_are_refused_and_never_kept(self):
        out = self.send(text="my OTP is 482913")
        self.assertIn("do not send", out.answer.headline.lower())
        self.assertNotIn("482913", str(self.engine.session("u1").__dict__))

    def test_complaint_number_that_passes_the_card_check_is_not_refused(self):
        out = self.send(text="Acknowledgement No 30000000000012")
        self.assertNotIn("do not send", out.answer.headline.lower())
        self.assertEqual(self.engine.session("u1").case.ack_id, "30000000000012")

    def test_same_digits_with_a_card_word_are_still_refused(self):
        out = self.send(text="my diners card is 30000000000012")
        self.assertIn("do not send", out.answer.headline.lower())

    def test_live_call_answers_at_once_even_with_no_model(self):
        out = self.send(text="CBI officer on video call says digital arrest")
        self.assertIn("hang up", out.answer.next_step.lower())

    def test_off_topic_is_politely_declined(self):
        self.assertIn("only help", self.send(text="write me a poem about the sea").answer.headline)

    def test_single_scam_message_gets_an_answer(self):
        out = self.send(text="Is this real? I am a SEBI registered broker, pay rahul@ybl. To withdraw profit pay 10% tax first.")
        self.assertIn("known scam script", out.answer.headline)
        self.assertTrue(any("valid" in line or "SEBI" in line for line in out.answer.proof))

    def test_every_answer_passes_the_format_check(self):
        for text in ["", "hi", "my OTP is 482913", "I already paid 40k", "digital arrest on call", "is this a scam? send otp now"]:
            out = self.engine.handle("u2", Incoming(text=text, now=NOW))
            self.assertEqual(check_answer(out.answer), [], text)


class WholeChatTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine()
        self.addCleanup(self.engine.network.close)

    def test_asks_which_name_is_yours_first(self):
        out = self.engine.handle("u1", Incoming(chat_export=CHAT_EXPORT, now=NOW))
        self.assertEqual(sorted(out.answer.buttons), ["Arjun", "Riya"])

    def test_then_reports_the_script_with_days_and_real_quotes(self):
        self.engine.handle("u1", Incoming(chat_export=CHAT_EXPORT, now=NOW))
        out = self.engine.handle("u1", Incoming(button="Arjun", now=NOW))
        self.assertIn("known scam script", out.answer.headline)
        text = render(out.answer)
        self.assertIn("Day ", text)
        self.assertIn("invest on this platform", text)

    def test_normal_chat_finds_nothing_and_says_what_that_means(self):
        self.engine.handle("u1", Incoming(chat_export=NORMAL_EXPORT, now=NOW))
        out = self.engine.handle("u1", Incoming(button="Arjun", now=NOW))
        self.assertIn("no known scam script", out.answer.headline)

    def test_report_button_gives_a_passing_sheet(self):
        self.engine.handle("u1", Incoming(chat_export=CHAT_EXPORT, now=NOW))
        self.engine.handle("u1", Incoming(button="Arjun", now=NOW))
        out = self.engine.handle("u1", Incoming(button="report", now=NOW))
        self.assertIsNotNone(out.sheet)
        description = next(item for item in out.sheet.fields if item.label == "Description")
        self.assertEqual(description.problems, [])


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine()
        self.addCleanup(self.engine.network.close)

    def test_loss_opens_a_case_with_checked_facts(self):
        out = self.engine.handle("u1", Incoming(voice_text="I already paid dhai lakh", screenshot_text="Paid Rs 2,50,000 to scam@ybl UTR 533827652689", now=NOW))
        self.assertIn("careful people", out.answer.headline)
        self.assertIn("Rs 2,50,000", render(out.answer))
        self.assertIn('Say: "I lost Rs 2,50,000', render(out.answer))
        self.assertIn("533827652689", render(out.answer))
        session = self.engine.session("u1")
        self.assertEqual(session.facts["amount"], Decimal(250000))
        self.assertEqual(session.facts["reference"], "533827652689")

    def test_mismatch_asks_but_still_says_call_first(self):
        out = self.engine.handle("u1", Incoming(voice_text="I paid two lakh", screenshot_text="Paid Rs 2,50,000", now=NOW))
        self.assertIn("1930", out.answer.next_step)
        self.assertIn("2,50,000", render(out.answer))

    def test_forwarded_ack_moves_the_case_and_offers_the_filing_sheet(self):
        self.engine.handle("u1", Incoming(voice_text="I paid Rs 500", screenshot_text="Paid Rs 500 UTR 533827652689", now=NOW))
        out = self.engine.handle("u1", Incoming(text="Acknowledgement No 30000000000012", now=NOW))
        self.assertEqual(self.engine.session("u1").case.state, "helpline_ack")
        self.assertIsNotNone(out.sheet)
        self.assertIn("24 hours", render(out.answer))
        self.assertIn("Still needed before you file", render(out.answer))
        self.assertIn("State", render(out.answer))

    def test_second_victim_is_offered_a_link_only_with_consent(self):
        self.engine.handle("a", Incoming(voice_text="I paid Rs 500", screenshot_text="Paid Rs 500 to scam@ybl", now=NOW))
        out = self.engine.handle("b", Incoming(voice_text="I paid Rs 900", screenshot_text="Paid Rs 900 to scam@ybl", now=NOW))
        self.assertTrue(out.link_offer)
        self.assertFalse(self.engine.network.linked("a", "b"))


class FullLoopTests(unittest.TestCase):
    def test_closed_case_warns_the_next_person_before_they_pay(self):
        eng = engine()
        self.addCleanup(eng.network.close)
        eng.handle("victim", Incoming(voice_text="I paid Rs 38,000", screenshot_text="Paid Rs 38,000 to scam.one@ybl UTR 533827652689", now=NOW))
        run_world(eng, "victim", NOW, held=Decimal(40000), restorable=Decimal(38000))
        self.assertEqual(eng.session("victim").case.state, "closed")
        self.assertEqual(eng.session("victim").case.ack_id, "20000000000012")
        self.assertEqual(eng.session("victim").case.request_id, "MR20261101000003")

        eng.handle("next", Incoming(chat_export=CHAT_EXPORT, now=NOW))
        out = eng.handle("next", Incoming(button="Arjun", now=NOW))
        self.assertIn("1 other report shares", render(out.answer))


class WorldTests(unittest.TestCase):
    def test_every_event_is_labelled_simulated(self):
        events = demo_events(Decimal(40000), Decimal(38000))
        self.assertTrue(all(event.simulated for event in events))
        self.assertEqual([event.day for event in events], sorted(event.day for event in events))


if __name__ == "__main__":
    unittest.main()
