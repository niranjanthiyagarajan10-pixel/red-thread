import json
import threading
import time
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from redthread import press
from redthread.model import ModelError, Reply
from redthread.network import Network

NOW = datetime(2026, 11, 3, 11, 42)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
ARREST = {"kind": "call", "caller": "Inspector Sharma CBI +91 98765 43210", "chat_with": "",
          "visible_text": "Inspector Sharma CBI. Arrest warrant. Money laundering case. Do not disconnect.", "their_words": "", "signals": []}
FAMILY = {"kind": "call", "caller": "Amma", "chat_with": "", "visible_text": "Amma. Video call. 04:12", "their_words": "", "signals": []}
HOME = {"kind": "other", "caller": "", "chat_with": "", "visible_text": "11:58 Tuesday 3 November", "their_words": "", "signals": []}
MOVE = {"kind": "chat", "caller": "", "chat_with": "Aisha", "visible_text": "Aisha\nThis app is glitchy, let us talk on another app\nSure",
        "their_words": "This app is glitchy, let us talk on another app",
        "signals": [{"signal": "moves_off_platform", "quote": "let us talk on another app"}]}
MONEY = {"kind": "chat", "caller": "", "chat_with": "Aisha", "visible_text": "Aisha\nCan you send 20,000 for my visa fee today",
         "their_words": "Can you send 20,000 for my visa fee today",
         "signals": [{"signal": "asks_money", "quote": "send 20,000 for my visa fee"}]}
MADE_UP = {**MOVE, "signals": [{"signal": "asks_money", "quote": "send me money now"}]}
PAY = {"kind": "payment", "caller": "", "chat_with": "", "visible_text": "Pay Rs 38,450 to rahul.k@ybl", "their_words": "", "signals": []}


class Fake:
    def __init__(self, answer, delay=0):
        self.answer = answer
        self.delay = delay
        self.calls = 0

    def chat(self, messages, schema=None, **options):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if isinstance(self.answer, Exception):
            raise self.answer
        return Reply(text=json.dumps(self.answer))


def presses(answer, network=None, delay=0, wait=5):
    model = Fake(answer, delay)
    return press.Presses(model, network=network, wait=wait), model


class ScreenTests(unittest.TestCase):
    def test_arrest_call_says_hang_up_at_once(self):
        book, _ = presses(ARREST)
        out = book.press("k" * 22, PNG, NOW)
        self.assertEqual(out.level, "danger")
        self.assertIn("hang up", out.title.lower())
        self.assertIn("police", out.body.lower())

    def test_second_press_after_the_call_gives_the_1930_words(self):
        book, model = presses(ARREST)
        book.press("k" * 22, PNG, NOW)
        model.answer = HOME
        out = book.press("k" * 22, PNG, NOW + timedelta(minutes=16))
        self.assertIn("1930", out.title)
        self.assertIn("9876543210", out.body)
        self.assertIn("11:42", out.body)

    def test_the_follow_up_ends_after_three_hours(self):
        book, model = presses(ARREST)
        book.press("k" * 22, PNG, NOW)
        model.answer = HOME
        self.assertEqual(book.press("k" * 22, PNG, NOW + timedelta(hours=4)).level, "calm")

    def test_each_key_has_its_own_history(self):
        book, model = presses(ARREST)
        book.press("a" * 22, PNG, NOW)
        model.answer = HOME
        self.assertNotIn("1930", book.press("b" * 22, PNG, NOW).title)

    def test_a_family_video_call_raises_nothing(self):
        book, _ = presses(FAMILY)
        out = book.press("k" * 22, PNG, NOW)
        self.assertEqual(out.level, "calm")
        self.assertNotIn("hang up", out.title.lower())

    def test_moving_apps_in_a_dating_chat_suggests_a_proof_call(self):
        book, _ = presses(MOVE)
        out = book.press("k" * 22, PNG, NOW)
        self.assertEqual(out.level, "caution")
        self.assertIn("Aisha", out.body)
        self.assertIn("Proof Call", out.body)

    def test_a_money_ask_in_a_chat_is_a_stop(self):
        book, _ = presses(MONEY)
        out = book.press("k" * 22, PNG, NOW)
        self.assertEqual(out.level, "danger")
        self.assertIn("money", out.title.lower())

    def test_a_signal_whose_quote_is_not_on_screen_is_dropped(self):
        book, _ = presses(MADE_UP)
        out = book.press("k" * 22, PNG, NOW)
        self.assertNotEqual(out.level, "danger")

    def test_a_reported_upi_id_stops_the_payment(self):
        network = Network(":memory:", salt=b"s")
        self.addCleanup(network.close)
        network.add_case("earlier", {"upi": ["rahul.k@ybl"]})
        book, _ = presses(PAY, network)
        out = book.press("k" * 22, PNG, NOW)
        self.assertEqual(out.level, "danger")
        self.assertIn("rahul.k@ybl", out.body)

    def test_a_fresh_upi_id_still_gets_a_check_before_paying(self):
        book, _ = presses(PAY, Network(":memory:", salt=b"s"))
        out = book.press("k" * 22, PNG, NOW)
        self.assertEqual(out.level, "caution")
        self.assertIn("rahul.k@ybl", out.body)


    def test_a_message_threatening_arrest_is_a_stop_even_without_a_caller(self):
        sms = {**HOME, "kind": "chat", "chat_with": "VK-CUSTMS",
               "visible_text": "Your parcel contains illegal items. A case is registered in your name. Call the officer now or face arrest."}
        out = presses(sms)[0].press("k" * 22, PNG, NOW)
        self.assertEqual(out.level, "danger")
        self.assertIn("do not reply", out.title.lower())

    def test_someone_claiming_to_be_an_officer_and_asking_for_money_is_a_known_scam(self):
        for said in ("I'm a CBI officer and you need to pay 1000", "This is Mumbai police. Pay the fine of Rs 5000 today",
                     "We are calling from customs, transfer the clearance amount now"):
            chat = {**MONEY, "chat_with": "Chandru", "visible_text": f"Chandru\n{said}", "their_words": said,
                    "signals": [{"signal": "asks_money", "quote": said}]}
            out = presses(chat)[0].press("k" * 22, PNG, NOW)
            self.assertEqual(out.level, "danger", said)
            self.assertIn("known scam", out.title.lower(), said)
            self.assertNotIn("romance", out.body.lower(), said)

    def test_a_relative_in_the_police_is_not_a_fake_officer(self):
        for said in ("My brother is in the police. Can you pay me back for dinner?",
                     "My dad is a police officer, he will pay the bill"):
            chat = {**MOVE, "visible_text": f"Aisha\n{said}", "their_words": said, "signals": []}
            out = presses(chat)[0].press("k" * 22, PNG, NOW)
            self.assertNotIn("known scam", out.title.lower(), said)

    def test_ordinary_chats_that_sound_like_call_rules_are_not_a_scam(self):
        for name, said in (("Aisha", "Don't tell anyone, but I really like you"), ("Rahul", "Stay on the call, I'm joining in 2 min"),
                           ("Amma", "Did you see the news about digital arrest scams? Be careful")):
            chat = {**MOVE, "chat_with": name, "visible_text": f"{name}\n{said}", "their_words": said, "signals": []}
            out = presses(chat)[0].press("k" * 22, PNG, NOW)
            self.assertNotEqual(out.level, "danger", said)

    def test_a_chat_saying_you_are_under_digital_arrest_is_a_stop(self):
        said = "You are under digital arrest. Stay on the video call."
        chat = {**MOVE, "chat_with": "Officer", "visible_text": f"Officer\n{said}", "their_words": said, "signals": []}
        out = presses(chat)[0].press("k" * 22, PNG, NOW)
        self.assertEqual(out.level, "danger")
        self.assertIn("known scam", out.title.lower())

    def test_a_bank_warning_not_to_share_the_code_is_not_an_alarm(self):
        sms = {**HOME, "kind": "message", "visible_text": "HDFC: OTP for txn is 482913. Do not share it with anyone."}
        self.assertEqual(presses(sms)[0].press("k" * 22, PNG, NOW).level, "calm")

    def test_a_message_asking_for_the_code_is_still_a_stop(self):
        sms = {**HOME, "kind": "chat", "chat_with": "Bank Support", "visible_text": "Bank Support\nPlease share the OTP you just got to stop the block",
               "their_words": "Please share the OTP you just got to stop the block"}
        self.assertEqual(presses(sms)[0].press("k" * 22, PNG, NOW).level, "danger")

    def test_a_bank_code_message_is_not_an_alarm(self):
        sms = {**HOME, "kind": "message", "visible_text": "[removed] is your login code. Valid for 5 minutes."}
        self.assertEqual(presses(sms)[0].press("k" * 22, PNG, NOW).level, "calm")


class MemoryTests(unittest.TestCase):
    def test_a_payment_soon_after_a_fake_police_call_is_a_stop(self):
        book, model = presses(ARREST)
        book.press("k" * 22, PNG, NOW)
        model.answer = PAY
        out = book.press("k" * 22, PNG, NOW + timedelta(minutes=11))
        self.assertEqual(out.level, "danger")
        self.assertIn("11:42", out.body)
        self.assertIn("fake police call", out.body)
        self.assertEqual(out.upi, "rahul.k@ybl")

    def test_a_payment_long_after_the_call_is_only_a_check(self):
        book, model = presses(ARREST)
        book.press("k" * 22, PNG, NOW)
        model.answer = PAY
        self.assertEqual(book.press("k" * 22, PNG, NOW + timedelta(hours=4)).level, "caution")

    def test_a_payment_after_a_money_ask_in_a_chat_is_a_stop(self):
        book, model = presses(MONEY)
        book.press("k" * 22, PNG, NOW)
        model.answer = PAY
        out = book.press("k" * 22, PNG, NOW + timedelta(minutes=5))
        self.assertEqual(out.level, "danger")
        self.assertIn("Aisha", out.body)

    def test_a_payment_after_a_calm_press_is_only_a_check(self):
        book, model = presses(FAMILY)
        book.press("k" * 22, PNG, NOW)
        model.answer = PAY
        self.assertEqual(book.press("k" * 22, PNG, NOW + timedelta(minutes=5)).level, "caution")


class AlertTests(unittest.TestCase):
    def test_a_fake_police_call_writes_a_family_alert_with_the_number(self):
        out = presses(ARREST)[0].press("k" * 22, PNG, NOW)
        self.assertIn("9876543210", out.alert)
        self.assertIn("call me", out.alert.lower())
        self.assertNotIn("laundering", out.alert.lower())
        self.assertLessEqual(len(out.alert), 160)
        self.assertNotIn("—", out.alert)

    def test_the_alert_goes_at_most_once_in_thirty_minutes(self):
        book, _ = presses(ARREST)
        self.assertTrue(book.press("k" * 22, PNG, NOW).alert)
        self.assertEqual(book.press("k" * 22, PNG, NOW + timedelta(minutes=5)).alert, "")
        self.assertTrue(book.press("k" * 22, PNG, NOW + timedelta(minutes=40)).alert)

    def test_calm_and_careful_presses_never_alert(self):
        for answer in (FAMILY, HOME, MOVE, PAY):
            self.assertEqual(presses(answer)[0].press("k" * 22, PNG, NOW).alert, "")

    def test_a_scam_payment_alert_says_so(self):
        book, model = presses(ARREST)
        book.press("k" * 22, PNG, NOW)
        model.answer = PAY
        out = book.press("k" * 22, PNG, NOW + timedelta(minutes=11))
        self.assertIn("payment", out.alert)
        self.assertEqual(book.press("k" * 22, PNG, NOW + timedelta(minutes=12)).alert, "")


class KeepTests(unittest.TestCase):
    def test_forget_removes_everything_for_that_link(self):
        book, model = presses(ARREST)
        book.press("k" * 22, PNG, NOW)
        self.assertEqual(book.forget("k" * 22), 1)
        self.assertEqual(book.history("k" * 22), [])
        model.answer = PAY
        self.assertEqual(book.press("k" * 22, PNG, NOW + timedelta(minutes=2)).level, "caution")

    def test_forget_leaves_other_links_alone(self):
        book, _ = presses(ARREST)
        book.press("a" * 22, PNG, NOW)
        book.press("b" * 22, PNG, NOW)
        book.forget("a" * 22)
        self.assertEqual(len(book.history("b" * 22)), 1)

    def test_presses_older_than_a_day_are_dropped(self):
        book, _ = presses(ARREST)
        book.press("a" * 22, PNG, NOW)
        book.press("b" * 22, PNG, NOW + timedelta(hours=25))
        self.assertEqual(book.history("a" * 22, now=NOW + timedelta(hours=25)), [])
        self.assertEqual(len(book.history("b" * 22, now=NOW + timedelta(hours=25))), 1)


class LaterTests(unittest.TestCase):
    def checker(self, wonder=None):
        from redthread.claims import Claims
        from tests.test_claims import FakeWonder
        check = Claims(wonder or FakeWonder())
        self.addCleanup(check.close)
        return check

    def test_a_scam_call_starts_the_claim_check_and_later_gives_notice_two(self):
        book = press.Presses(Fake(ARREST), claims=self.checker(), wait=5)
        out = book.press("k" * 22, PNG, NOW)
        self.assertEqual(out.claim, "digital_arrest")
        text = book.later("k" * 22, NOW, wait=2)
        self.assertTrue(text.startswith("Checked with official sources."))
        self.assertIn("pib.gov.in", text)

    def test_a_calm_press_has_no_notice_two(self):
        book = press.Presses(Fake(FAMILY), claims=self.checker(), wait=5)
        book.press("k" * 22, PNG, NOW)
        started = time.monotonic()
        self.assertEqual(book.later("k" * 22, NOW, wait=2), "")
        self.assertLess(time.monotonic() - started, 0.5)

    def test_without_a_checker_there_is_no_notice_two(self):
        book, _ = presses(ARREST)
        book.press("k" * 22, PNG, NOW)
        self.assertEqual(book.later("k" * 22, NOW, wait=0.5), "")

    def test_notice_two_is_only_for_a_recent_press(self):
        book = press.Presses(Fake(ARREST), claims=self.checker(), wait=5)
        book.press("k" * 22, PNG, NOW)
        book.later("k" * 22, NOW, wait=2)
        self.assertEqual(book.later("k" * 22, NOW + timedelta(minutes=30), wait=0.5), "")


class CaseTests(unittest.TestCase):
    def test_a_case_opens_after_a_scam_press_and_forget_closes_it(self):
        book, _ = presses(ARREST)
        book.press("k" * 22, PNG, NOW)
        view = book.case("k" * 22, NOW + timedelta(minutes=5))
        self.assertIn("9876543210", view["script"])
        book.forget("k" * 22)
        self.assertIsNone(book.case("k" * 22, NOW + timedelta(minutes=6)))

    def test_no_case_for_calm_presses(self):
        book, _ = presses(FAMILY)
        book.press("k" * 22, PNG, NOW)
        self.assertIsNone(book.case("k" * 22, NOW))

    def test_saying_you_paid_starts_the_timer(self):
        book, _ = presses(ARREST)
        book.press("k" * 22, PNG, NOW)
        book.paid("k" * 22, True, NOW + timedelta(minutes=10), Decimal("20000"))
        view = book.case("k" * 22, NOW + timedelta(minutes=20))
        self.assertEqual(view["timer"]["minutes_left"], 50)
        self.assertIn("20,000", view["script"])

    def test_the_story_is_kept_with_codes_removed_and_goes_after_a_day(self):
        book, _ = presses(ARREST)
        book.press("k" * 22, PNG, NOW)
        view = book.tell("k" * 22, "They said CBI and asked for OTP 482913", NOW + timedelta(minutes=3))
        self.assertIn("CBI", view["heard"])
        self.assertNotIn("482913", view["heard"])
        self.assertIsNone(book.case("k" * 22, NOW + timedelta(hours=25)))


class SafetyTests(unittest.TestCase):
    def test_a_failed_read_still_answers(self):
        book, _ = presses(ModelError("down"))
        out = book.press("k" * 22, PNG, NOW)
        self.assertTrue(out.title and out.body)
        self.assertEqual(out.kind, "unread")

    def test_a_slow_read_answers_within_the_wait(self):
        book, _ = presses(ARREST, delay=2, wait=0.3)
        started = time.monotonic()
        out = book.press("k" * 22, PNG, NOW)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(out.kind, "unread")

    def test_a_slow_read_still_counts_for_the_next_press(self):
        book, model = presses(ARREST, delay=0.5, wait=0.1)
        book.press("k" * 22, PNG, NOW)
        time.sleep(0.8)
        model.answer, model.delay = HOME, 0
        self.assertIn("1930", book.press("k" * 22, PNG, NOW + timedelta(minutes=5)).title)

    def test_busy_screens_get_twelve_seconds(self):
        self.assertEqual(press.WAIT_SECONDS, 12.0)

    def test_a_failed_read_says_why_in_the_history_and_the_failure_list(self):
        book, _ = presses(ModelError("Model call failed with status 400. bad image"))
        book.press("k" * 22, PNG, NOW)
        entry = book.history("k" * 22)[0]
        self.assertIn("ModelError", entry["why"])
        self.assertIn("status 400", book.failures()[0]["why"])
        self.assertEqual(book.failures()[0]["at"], "11:42")

    def test_a_good_read_records_how_long_it_took(self):
        book, _ = presses(ARREST, delay=0.2)
        book.press("k" * 22, PNG, NOW)
        entry = book.history("k" * 22)[0]
        self.assertGreaterEqual(entry["seconds"], 0.2)
        self.assertEqual(entry["why"], "")
        self.assertEqual(book.failures(), [])

    def test_a_slow_read_is_listed_as_slow_and_keeps_the_real_answer(self):
        book, _ = presses(ARREST, delay=0.5, wait=0.1)
        book.press("k" * 22, PNG, NOW)
        time.sleep(0.8)
        entry = book.history("k" * 22)[0]
        self.assertEqual((entry["level"], entry["why"]), ("danger", "slow"))
        self.assertEqual(book.failures()[0]["why"], "slow")

    def test_the_failure_list_never_holds_screen_text(self):
        book, _ = presses({**HOME, "visible_text": "Your OTP is 482913"}, delay=0.3, wait=0.05)
        book.press("k" * 22, PNG, NOW)
        time.sleep(0.5)
        self.assertNotIn("482913", json.dumps(book.failures()))

    def test_not_an_image_never_reaches_the_model(self):
        book, model = presses(ARREST)
        out = book.press("k" * 22, b"hello", NOW)
        self.assertEqual(model.calls, 0)
        self.assertTrue(out.title)

    def test_codes_on_screen_are_never_kept(self):
        book, _ = presses({**HOME, "visible_text": "Your OTP is 482913"})
        book.press("k" * 22, PNG, NOW)
        self.assertNotIn("482913", json.dumps(book.history("k" * 22), default=str))

    def test_every_reply_fits_a_notification(self):
        for answer in (ARREST, FAMILY, HOME, MOVE, MONEY, PAY, ModelError("x")):
            out = presses(answer)[0].press("k" * 22, PNG, NOW)
            self.assertLessEqual(len(out.title), press.TITLE_LIMIT)
            self.assertLessEqual(len(out.body), press.BODY_LIMIT)
            self.assertNotIn("—", out.title + out.body)

    def test_key_shape(self):
        self.assertTrue(press.valid_key("k" * 22))
        self.assertFalse(press.valid_key("short"))
        self.assertFalse(press.valid_key("k" * 21 + "/"))

    def test_presses_from_two_threads_do_not_clash(self):
        book, _ = presses(ARREST)
        threads = [threading.Thread(target=book.press, args=("k" * 22, PNG, NOW)) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(book.history("k" * 22)), 5)


if __name__ == "__main__":
    unittest.main()
