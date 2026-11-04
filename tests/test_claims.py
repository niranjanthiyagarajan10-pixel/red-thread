import threading
import time
import unittest
from datetime import datetime, timedelta

from redthread import claims
from redthread.model import Found, ModelError

NOW = datetime(2026, 11, 4, 16, 47)
TEXT = ("Checking that claim against official sources. "
        "The Press Information Bureau says no government agency investigates by phone or video call. "
        "A blog says these calls are common.")


def found():
    first = TEXT.index("The Press")
    second = TEXT.index("A blog")
    return Found(TEXT, [(first, second - 1, "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1"),
                        (second, len(TEXT), "https://someblog.example.com/post")], searches=3)


class FakeWonder:
    def __init__(self, answer=None, delay=0):
        self.answer = answer or found()
        self.delay = delay
        self.calls = 0
        self.lock = threading.Lock()

    def search(self, instructions, text, **options):
        with self.lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class FamilyTests(unittest.TestCase):
    def test_each_scam_script_gets_its_own_question(self):
        self.assertEqual(claims.family_for("fake_authority", "Inspector Sharma CBI. Arrest warrant."), "digital_arrest")
        self.assertEqual(claims.family_for("fake_authority", "TRAI: your number will be blocked in 2 hours"), "telecom")
        self.assertEqual(claims.family_for("fake_authority", "Customs. Your parcel holds illegal items."), "parcel")
        self.assertEqual(claims.family_for("safe_account"), "safe_account")
        self.assertEqual(claims.family_for("pay_to_withdraw"), "investment")

    def test_a_romance_money_ask_has_no_official_claim_to_check(self):
        self.assertIsNone(claims.family_for("asks_money"))
        self.assertIsNone(claims.family_for(""))

    def test_no_question_holds_anything_from_the_screen(self):
        for question in claims.QUESTIONS.values():
            self.assertNotIn("{", question)


class SourceTests(unittest.TestCase):
    def test_only_cited_lines_from_official_sites_are_kept(self):
        lines = claims.official_lines(found())
        self.assertEqual(len(lines), 1)
        self.assertIn("no government agency", lines[0].text)
        self.assertTrue(all("gov.in" in url for url in lines[0].sources))

    def test_official_sites(self):
        self.assertTrue(claims.official("https://cybercrime.gov.in/Webform/Crime_AuthoLogin.aspx"))
        self.assertTrue(claims.official("https://www.rbi.org.in/x"))
        self.assertFalse(claims.official("https://gov.in.example.com/x"))
        self.assertFalse(claims.official("https://news.example.com/gov.in"))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.wonder = FakeWonder()
        self.book = claims.Claims(self.wonder)
        self.addCleanup(self.book.close)

    def test_a_check_runs_once_and_is_kept_for_everyone(self):
        self.book.start("digital_arrest", NOW)
        self.book.start("digital_arrest", NOW)
        checked = self.book.result("digital_arrest", NOW, wait=2)
        self.book.start("digital_arrest", NOW + timedelta(hours=1))
        self.assertEqual(self.wonder.calls, 1)
        self.assertEqual(len(checked.lines), 1)

    def test_a_kept_check_is_redone_after_a_day(self):
        self.book.start("digital_arrest", NOW)
        self.book.result("digital_arrest", NOW, wait=2)
        self.book.start("digital_arrest", NOW + timedelta(hours=25))
        self.book.result("digital_arrest", NOW + timedelta(hours=25), wait=2)
        self.assertEqual(self.wonder.calls, 2)

    def test_a_check_that_found_nothing_official_is_tried_again_after_an_hour(self):
        wonder = FakeWonder(Found("Nothing official found.", []))
        book = claims.Claims(wonder)
        self.addCleanup(book.close)
        book.start("digital_arrest", NOW)
        book.result("digital_arrest", NOW, wait=2)
        book.start("digital_arrest", NOW + timedelta(minutes=30))
        self.assertEqual(wonder.calls, 1)
        book.start("digital_arrest", NOW + timedelta(minutes=70))
        book.result("digital_arrest", NOW + timedelta(minutes=70), wait=2)
        self.assertEqual(wonder.calls, 2)

    def test_a_failed_check_gives_no_notice_and_no_error(self):
        book = claims.Claims(FakeWonder(ModelError("down")))
        self.addCleanup(book.close)
        book.start("digital_arrest", NOW)
        self.assertEqual(claims.notice(book.result("digital_arrest", NOW, wait=2)), "")

    def test_waiting_gives_up_in_time(self):
        book = claims.Claims(FakeWonder(delay=1.0))
        self.addCleanup(book.close)
        book.start("digital_arrest", NOW)
        started = time.monotonic()
        self.assertIsNone(book.result("digital_arrest", NOW, wait=0.2))
        self.assertLess(time.monotonic() - started, 0.8)

    def test_no_model_means_no_check(self):
        book = claims.Claims(None)
        self.addCleanup(book.close)
        self.assertIsNone(book.start("digital_arrest", NOW))
        self.assertIsNone(book.result("digital_arrest", NOW, wait=0.1))


class NoticeTests(unittest.TestCase):
    def test_the_notice_names_the_official_source_and_fits(self):
        book = claims.Claims(FakeWonder())
        self.addCleanup(book.close)
        book.start("digital_arrest", NOW)
        text = claims.notice(book.result("digital_arrest", NOW, wait=2))
        title, body = text.split("\n", 1)
        self.assertEqual(title, "Checked with official sources.")
        self.assertIn("pib.gov.in", body)
        self.assertLessEqual(len(body), claims.NOTICE_LIMIT)
        self.assertNotIn("—", text)


if __name__ == "__main__":
    unittest.main()
