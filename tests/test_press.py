import json
import threading
import time
import unittest
from datetime import datetime, timedelta

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

    def test_a_bank_code_message_is_not_an_alarm(self):
        sms = {**HOME, "kind": "message", "visible_text": "[removed] is your login code. Valid for 5 minutes."}
        self.assertEqual(presses(sms)[0].press("k" * 22, PNG, NOW).level, "calm")


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
