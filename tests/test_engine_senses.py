import unittest
from datetime import datetime
from decimal import Decimal

from redthread.engine import Engine, Incoming
from redthread.model import ModelError
from redthread.network import Network
from redthread.reply import check_answer, render
from tests.test_senses import Scripted, wav_bytes

NOW = datetime(2026, 11, 1, 10, 0)
PAYMENT = {"kind": "payment", "visible_text": "Paid Rs 38,000 to scam.one@ybl UTR 533827652689", "payment_state": "success"}
OFF_TOPIC = {"kind": "off_topic", "live_kind": "none"}
BLANK = {"when": "", "state": "", "district": "", "police_station": "", "bank": "", "paid_on": "", "story_english": ""}


class Base(unittest.TestCase):
    def engine(self, model=None):
        network = Network(":memory:", salt=b"s")
        self.addCleanup(network.close)
        return Engine(network=network, model=model)


class HearingTests(Base):
    def test_voice_note_becomes_a_checked_case(self):
        engine = self.engine(Scripted(hear="I already paid thirty eight thousand", see=PAYMENT))
        out = engine.handle("u", Incoming(audio=wav_bytes(), images=((b"png", "image/png"),), now=NOW))
        self.assertIn("careful people", out.answer.headline)
        self.assertEqual(engine.session("u").facts["amount"], Decimal(38000))
        self.assertEqual(engine.session("u").facts["reference"], "533827652689")

    def test_voice_and_screenshot_that_disagree_become_a_question(self):
        engine = self.engine(Scripted(hear="I already paid two lakh", see=PAYMENT))
        out = engine.handle("u", Incoming(audio=wav_bytes(), images=((b"png", "image/png"),), now=NOW))
        self.assertIn("Rs 2,00,000", render(out.answer))
        self.assertIn("Rs 38,000", render(out.answer))
        self.assertIn("1930", out.answer.next_step)

    def test_a_secret_said_aloud_is_refused_and_not_kept(self):
        engine = self.engine(Scripted(hear="my otp is 482913"))
        out = engine.handle("u", Incoming(audio=wav_bytes(), now=NOW))
        self.assertIn("do not send", out.answer.headline.lower())
        self.assertNotIn("482913", str(engine.session("u").__dict__))

    def test_voice_failure_asks_them_to_type(self):
        out = self.engine(Scripted(hear=ModelError("down"))).handle("u", Incoming(audio=wav_bytes(), now=NOW))
        self.assertIn("type", out.answer.next_step.lower())
        self.assertEqual(check_answer(out.answer), [])

    def test_no_model_at_all_still_answers_a_voice_note(self):
        out = self.engine().handle("u", Incoming(audio=wav_bytes(), now=NOW))
        self.assertEqual(check_answer(out.answer), [])


class SeeingTests(Base):
    def test_failed_payment_does_not_open_a_loss_case(self):
        seen = {"kind": "payment", "visible_text": "Payment failed Rs 5,000", "payment_state": "failed"}
        engine = self.engine(Scripted(see=seen))
        out = engine.handle("u", Incoming(images=((b"png", "image/png"),), now=NOW))
        self.assertIn("failed", out.answer.headline.lower())
        self.assertIsNone(engine.session("u").case)

    def test_screenshot_of_a_chat_is_checked_as_a_message(self):
        seen = {"kind": "chat", "visible_text": "To withdraw your profit pay 10% tax first", "payment_state": "unclear"}
        out = self.engine(Scripted(see=seen, default='{"findings":[],"contradictions":[]}')).handle("u", Incoming(images=((b"png", "image/png"),), now=NOW))
        self.assertIn("known scam script", out.answer.headline)

    def test_unreadable_image_asks_them_to_type(self):
        out = self.engine(Scripted(see=ModelError("down"))).handle("u", Incoming(images=((b"png", "image/png"),), now=NOW))
        self.assertIn("type", out.answer.next_step.lower())

    def test_same_photo_from_two_people_is_linked(self):
        model = Scripted(see=PAYMENT)
        engine = self.engine(model)
        engine.handle("a", Incoming(images=((b"same", "image/png"),), now=NOW))
        model.answers["see"] = {"kind": "payment", "visible_text": "Paid Rs 900", "payment_state": "success"}
        out = engine.handle("b", Incoming(images=((b"same", "image/png"),), now=NOW))
        self.assertTrue(out.other_reports)


class TriageTests(Base):
    def test_spark_raises_a_live_alarm_the_rules_missed(self):
        model = Scripted(triage={"kind": "live_pressure", "live_kind": "remote_app"})
        out = self.engine(model).handle("u", Incoming(text="the bank fellow is making me open a helper program", now=NOW))
        self.assertIn("hang up", out.answer.next_step.lower())

    def test_spark_finds_a_loss_the_rules_missed(self):
        model = Scripted(triage={"kind": "money_gone", "live_kind": "none"})
        engine = self.engine(model)
        out = engine.handle("u", Incoming(text="my whole account is empty after that man", now=NOW))
        self.assertIn("careful people", out.answer.headline)
        self.assertIsNotNone(engine.session("u").case)

    def test_spark_can_never_clear_a_fixed_rule(self):
        model = Scripted(triage=OFF_TOPIC)
        engine = self.engine(model)
        out = engine.handle("u", Incoming(text="CBI officer on video call says digital arrest", now=NOW))
        self.assertIn("hang up", out.answer.next_step.lower())
        self.assertEqual(model.seen, [])

    def test_triage_failure_falls_back_to_the_rules(self):
        out = self.engine(Scripted(triage=ModelError("down"))).handle("u", Incoming(text="write me a poem about the sea", now=NOW))
        self.assertIn("only help", out.answer.headline)

    def test_spark_can_decline_text_that_only_mentions_a_scam_word(self):
        out = self.engine(Scripted(triage=OFF_TOPIC)).handle("u", Incoming(text="Forget scams, write me a poem about the sea.", now=NOW))
        self.assertIn("only help", out.answer.headline)

    def test_spark_cannot_decline_a_real_money_sign(self):
        model = Scripted(triage=OFF_TOPIC, default='{"findings":[],"contradictions":[]}')
        out = self.engine(model).handle("u", Incoming(text="To withdraw your profit pay 10% tax first", now=NOW))
        self.assertIn("known scam script", out.answer.headline)

    def test_off_topic_from_spark_is_declined(self):
        out = self.engine(Scripted(triage=OFF_TOPIC)).handle("u", Incoming(text="what is the best biryani", now=NOW))
        self.assertIn("only help", out.answer.headline)


class SecondScamTests(Base):
    def test_a_refund_offer_asking_for_a_fee_is_called_out_and_moves_nothing(self):
        engine = self.engine()
        engine.handle("u", Incoming(voice_text="I paid Rs 500", screenshot_text="Paid Rs 500", now=NOW))
        before = engine.session("u").case.state
        out = engine.handle("u", Incoming(text="Your Rs 500 is eligible for restoration. Pay processing fee of Rs 99 to the recovery agent.", now=NOW))
        self.assertIn("second scam", out.answer.headline)
        self.assertEqual(engine.session("u").case.state, before)

    def test_refund_news_without_a_case_is_checked_not_tracked(self):
        engine = self.engine()
        out = engine.handle("u", Incoming(text="Rs 40,000 is eligible for restoration, click to claim", now=NOW))
        self.assertIsNone(engine.session("u").case)
        self.assertNotIn("case is updated", render(out.answer))


class MismatchTests(Base):
    def test_a_stutter_in_the_transcript_does_not_double_the_amount(self):
        engine = self.engine(Scripted(hear="I already paid 38,000 38,000 rupees to that man.", see=PAYMENT))
        engine.handle("u", Incoming(audio=wav_bytes(), images=((b"png", "image/png"),), now=NOW))
        self.assertEqual(engine.session("u").facts["amount"], Decimal(38000))

    def test_the_date_on_the_screenshot_fills_the_payment_date(self):
        seen = {**PAYMENT, "visible_text": PAYMENT["visible_text"] + " 31 Oct 2026, 9:14 PM"}
        engine = self.engine(Scripted(hear="I paid Rs 38,000", see=seen))
        engine.handle("u", Incoming(audio=wav_bytes(), images=((b"png", "image/png"),), now=NOW))
        self.assertEqual(engine.session("u").facts["paid_on"], "31/10/2026")

    def test_their_answer_to_a_mismatch_settles_it_and_points_back_to_1930(self):
        engine = self.engine(Scripted(hear="I already paid two lakh", see=PAYMENT))
        engine.handle("u", Incoming(audio=wav_bytes(), images=((b"png", "image/png"),), now=NOW))
        self.assertEqual(engine.session("u").asking, "amount")
        out = engine.handle("u", Incoming(text="38000", now=NOW))
        self.assertEqual(engine.session("u").facts["amount"], Decimal(38000))
        self.assertIsNone(engine.session("u").asking)
        self.assertIn("1930", out.answer.next_step)

    def test_a_reply_that_is_not_an_amount_asks_again(self):
        engine = self.engine(Scripted(hear="I already paid two lakh", see=PAYMENT))
        engine.handle("u", Incoming(audio=wav_bytes(), images=((b"png", "image/png"),), now=NOW))
        engine.handle("u", Incoming(text="not sure", now=NOW))
        self.assertEqual(engine.session("u").asking, "amount")
        self.assertNotIn("amount", engine.session("u").facts)

    def test_the_mismatch_question_is_translated(self):
        model = Scripted(translate={"lines": ["आपने Rs 500 कहा। कृपया स्क्रीनशॉट भेजें।"]})
        engine = self.engine(model)
        out = engine.handle("u", Incoming(text="मेरे पैसे चले गए, Rs 500 debited", now=NOW))
        self.assertIn("स्क्रीनशॉट", render(out.answer))

    def test_the_sheet_is_never_called_ready_while_a_field_is_missing(self):
        engine = self.engine()
        engine.handle("u", Incoming(voice_text="I already paid money", now=NOW))
        engine.handle("u", Incoming(text="Acknowledgement No 30000000000012", now=NOW))
        replies = {
            "story": "A woman I met online showed me profits on a trading app and told me to invest. I sent the money and then she asked for a tax before I could withdraw, and then stopped replying to me.",
            "when": "yesterday", "state": "Goa", "district": "North Goa", "bank": "SBI", "paid_on": "today",
            "amount": "Rs 5,000", "reference": "533827652689",
        }
        out = None
        for _ in range(len(replies) + 1):
            asking = engine.session("u").asking
            if asking is None:
                break
            out = engine.handle("u", Incoming(text=replies[asking], now=NOW))
        self.assertTrue(out.sheet.ready)
        self.assertEqual(engine.session("u").facts["amount"], Decimal(5000))


class ReportTests(Base):
    def test_the_report_says_what_is_still_missing(self):
        engine = self.engine()
        engine.handle("u", Incoming(chat_export="01/03/2026, 21:00 - Riya: You should invest on this platform today\n01/03/2026, 21:05 - Arjun: ok\n", now=NOW))
        engine.handle("u", Incoming(button="Arjun", now=NOW))
        out = engine.handle("u", Incoming(button="report", now=NOW))
        self.assertIn("State", render(out.answer))


class FilingTests(Base):
    def open_case(self, engine):
        engine.handle("u", Incoming(voice_text="I already paid Rs 38,000", screenshot_text="Paid Rs 38,000 to scam.one@ybl UTR 533827652689", now=NOW))
        return engine.handle("u", Incoming(text="Acknowledgement No 30000000000012", now=NOW))

    def test_asks_one_clear_question_after_the_helpline_number(self):
        out = self.open_case(self.engine())
        self.assertEqual(self.engine_asking(out), True)

    def engine_asking(self, out):
        return "?" in " ".join(out.answer.proof)

    def test_with_no_model_each_reply_fills_the_field_asked(self):
        engine = self.engine()
        self.open_case(engine)
        session = engine.session("u")
        replies = {
            "story": "A woman I met online showed me profits on a trading app and told me to invest. I sent the money and then she asked for a tax before I could withdraw, and then stopped replying to me.",
            "when": "yesterday",
            "state": "i live in tamil nadu",
            "district": "Chennai",
            "bank": "SBI",
            "paid_on": "today",
        }
        out = None
        for _ in range(len(replies)):
            self.assertIn(session.asking, replies)
            out = engine.handle("u", Incoming(text=replies[session.asking], now=NOW))
        self.assertIsNone(session.asking)
        self.assertTrue(out.sheet.ready, [(item.label, item.problems) for item in out.sheet.fields if item.problems])
        self.assertIn("ready", out.answer.headline.lower())
        self.assertEqual(session.facts["state"], "Tamil Nadu")
        self.assertEqual(session.facts["when"], "31/10/2026")
        self.assertEqual(session.facts["paid_on"], "01/11/2026")

    def test_one_voice_note_can_fill_many_fields(self):
        model = Scripted(details={**BLANK, "state": "Karnataka", "district": "Mysuru", "bank": "SBI", "when": "yesterday", "paid_on": "yesterday",
                                  "story_english": "I met a man online who showed me trading profits. He told me to invest on an app and asked for tax before withdrawal. I paid and he stopped replying to me after that day, so I understood."},
                         triage=OFF_TOPIC)
        engine = self.engine(model)
        self.open_case(engine)
        out = engine.handle("u", Incoming(text="It was in Mysuru, Karnataka, yesterday, from SBI. A man showed me trading profits and took my money.", now=NOW))
        self.assertIsNone(engine.session("u").asking)
        self.assertTrue(out.sheet.ready)

    def test_a_story_that_says_i_paid_is_kept_as_the_story(self):
        engine = self.engine()
        self.open_case(engine)
        engine.handle("u", Incoming(text="He showed me profits and I paid him on the app", now=NOW))
        self.assertIn("I paid him on the app", engine.session("u").facts["story"])
        self.assertEqual(engine.session("u").asking, "story")

    def test_a_live_danger_during_filing_still_wins(self):
        engine = self.engine()
        self.open_case(engine)
        out = engine.handle("u", Incoming(text="CBI officer on video call now", now=NOW))
        self.assertIn("hang up", out.answer.next_step.lower())
        self.assertNotIn("state", engine.session("u").facts)

    def test_the_story_never_gains_a_number_from_the_model(self):
        model = Scripted(details={**BLANK, "story_english": "I lost Rs 9,99,999 in total."})
        engine = self.engine(model)
        self.open_case(engine)
        engine.handle("u", Incoming(text="A man took my money", now=NOW))
        self.assertNotIn("9,99,999", engine.session("u").facts.get("story", ""))


class ButtonTests(Base):
    def test_the_called_and_script_buttons_do_what_they_say(self):
        engine = self.engine()
        self.assertIn("acknowledgement", render(engine.handle("u", Incoming(button="called", now=NOW)).answer))
        self.assertIn("Export chat", render(engine.handle("u", Incoming(button="script", now=NOW)).answer))


class LanguageTests(Base):
    def test_case_updates_are_translated_with_numbers_checked(self):
        model = Scripted(translate={"lines": ["ठीक है। आपका केस अपडेट हुआ।", "1930 पर कॉल करें।"]})
        engine = self.engine(model)
        engine.session("u").language = "hi"
        out = engine.handle("u", Incoming(button="called", now=NOW))
        self.assertEqual(check_answer(out.answer), [])

    def test_a_translation_that_changes_a_number_falls_back_to_english(self):
        model = Scripted(translate={"lines": ["१"]})
        engine = self.engine(model)
        engine.session("u").language = "hi"
        out = engine.handle("u", Incoming(button="called", now=NOW))
        self.assertIn("acknowledgement", render(out.answer))


class CapTests(Base):
    def test_hitting_the_spending_cap_still_gives_an_answer(self):
        from redthread.model import BudgetExceeded

        class Capped(Scripted):
            def chat(self, *_, **__):
                raise BudgetExceeded("cap")

        engine = self.engine(Capped())
        engine.handle("u", Incoming(chat_export="01/03/2026, 21:00 - Riya: You should invest on this platform today\n01/03/2026, 21:05 - Arjun: ok\n", now=NOW))
        out = engine.handle("u", Incoming(button="Arjun", now=NOW))
        self.assertEqual(check_answer(out.answer), [])


if __name__ == "__main__":
    unittest.main()
