import json
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from redthread import aftercall
from redthread.claims import Checked
from redthread.investigate import Line
from redthread.model import Reply
from redthread.press import PressReply

NOW = datetime(2026, 11, 4, 16, 47)
CALL = PressReply("This is a scam. You can hang up.", "Real police never arrest anyone on a call.", "danger", "call", at=NOW,
                  caller="Inspector Sharma CBI +91 98765 43210", live_kind="fake_authority", about="a fake police call", claim="digital_arrest")
PAY = PressReply("Stop. Do not pay.", "At 16:47 you pressed on a fake police call.", "danger", "payment", at=NOW + timedelta(minutes=11),
                 upi="rahul.k@ybl", about="a payment after a fake police call")
CALM = PressReply("Nothing alarming on this screen.", "", "calm", "other", at=NOW)
CHECKED = Checked("digital_arrest", [Line("No government agency investigates by phone or video call.", ["https://pib.gov.in/x"])], NOW)


class Details:
    """A stand-in for Spark: restates the story and fills fields from it."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def chat(self, messages, schema=None, **options):
        self.calls += 1
        return Reply(text=json.dumps(self.answer))


def details(**fields):
    base = {name: "" for name in ("when", "state", "district", "police_station", "bank", "paid_on", "story_english")}
    return {**base, **fields}


class CaseTests(unittest.TestCase):
    def test_no_scam_press_means_no_case(self):
        self.assertIsNone(aftercall.case_view([CALM], aftercall.Story(), None, NOW))

    def test_the_case_has_the_1930_words_with_the_number_and_time(self):
        view = aftercall.case_view([CALL], aftercall.Story(), CHECKED, NOW + timedelta(minutes=20))
        self.assertIn("A fake police call", view["title"])
        self.assertIn("9876543210", view["script"])
        self.assertIn("16:47", view["script"])
        self.assertIn("I did not pay", view["script"])
        self.assertEqual(view["checked"][0]["source"], "pib.gov.in")

    def test_not_paying_gives_the_suspect_report_form_for_the_number(self):
        view = aftercall.case_view([CALL], aftercall.Story(paid=False), None, NOW)
        labels = {field["label"]: field["value"] for field in view["sheet"]}
        self.assertEqual(labels["What do you want to report"], "Phone Number")
        self.assertEqual(labels["Phone Number"], "9876543210")
        self.assertEqual(view["form"], "Report a suspect")

    def test_paying_starts_the_first_hour_timer_and_the_fraud_form(self):
        story = aftercall.Story(paid=True, paid_at=NOW + timedelta(minutes=11), amount=Decimal("50000"))
        view = aftercall.case_view([CALL, PAY], story, None, NOW + timedelta(minutes=30))
        self.assertEqual(view["timer"]["minutes_left"], 41)
        self.assertIn("50,000", view["script"])
        self.assertIn("rahul.k@ybl", view["script"])
        labels = {field["label"]: field["value"] for field in view["sheet"]}
        self.assertEqual(labels["Fraud amount"], "50000")
        self.assertEqual(labels["Suspect UPI ID"], "rahul.k@ybl")
        self.assertEqual(labels["Suspect mobile"], "9876543210")
        self.assertEqual(view["form"], "Report financial fraud")

    def test_the_timer_says_when_the_first_hour_is_over(self):
        story = aftercall.Story(paid=True, paid_at=NOW, amount=Decimal("1000"))
        view = aftercall.case_view([CALL], story, None, NOW + timedelta(hours=2))
        self.assertEqual(view["timer"]["minutes_left"], 0)


class TellTests(unittest.TestCase):
    def test_the_story_fills_the_form_and_numbers_never_given_are_dropped(self):
        model = Details(details(state="Tamil Nadu", bank="Canara Bank 99999", story_english="A man said he was from CBI and I paid."))
        story = aftercall.tell(model, aftercall.Story(), "A man said he was CBI. I paid from Canara Bank.", {}, NOW)
        self.assertEqual(story.details.get("state"), "Tamil Nadu")
        self.assertNotIn("bank", story.details)
        self.assertEqual(model.calls, 1)

    def test_codes_in_the_story_are_removed_before_anything_is_kept(self):
        story = aftercall.tell(None, aftercall.Story(), "They asked for the OTP 482913 and I read it out.", {}, NOW)
        self.assertNotIn("482913", story.words)

    def test_a_spoken_amount_is_found_by_code(self):
        story = aftercall.tell(None, aftercall.Story(), "I paid fifty thousand rupees to them", {}, NOW)
        self.assertEqual(story.amount, Decimal("50000"))

    def test_the_story_reaches_the_form_text(self):
        model = Details(details(story_english="A man said he was from CBI and kept me on video for two hours."))
        story = aftercall.tell(model, aftercall.Story(paid=False), "Aadmi ne kaha CBI se hai", {}, NOW)
        view = aftercall.case_view([CALL], story, None, NOW)
        description = {field["label"]: field["value"] for field in view["sheet"]}["Description"]
        self.assertIn("kept me on video for two hours", description)
        self.assertIn("9876543210", description)
        self.assertNotIn("—", json.dumps(view))


if __name__ == "__main__":
    unittest.main()
