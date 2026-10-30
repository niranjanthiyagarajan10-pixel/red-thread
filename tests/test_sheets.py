import unittest
from decimal import Decimal

from redthread import portal_rules as pr
from redthread.sheets import as_messages, filing_sheet, refund_sheet, suspect_report_sheet

STORY = (
    "A person I met on a dating app told me to update my KYC and then asked me to invest in a trading app. "
    "I didn't know it was a problem. I paid scammer@ybl on the same day. When I tried to take my money out, "
    "they asked me to pay more, and then they stopped replying to all of my messages."
)
FACTS = {
    "when": "12/03/2026 21:40",
    "state": "Tamil Nadu",
    "district": "Chennai",
    "story": STORY,
    "bank": "State Bank of India",
    "reference": "533827652689",
    "paid_on": "12/03/2026",
    "amount": Decimal(180000),
    "suspect_mobile": "9876543210",
}


def field(sheet, label):
    return next(item for item in sheet.fields if item.label == label)


class FilingSheetTests(unittest.TestCase):
    def test_story_is_cleaned_and_passes_the_portal(self):
        sheet = filing_sheet(FACTS)
        details = field(sheet, "Incident details").value
        self.assertEqual(pr.check_incident(details), [])
        self.assertNotIn("@", details)
        self.assertNotIn("update", details.lower())

    def test_upi_id_moves_to_the_suspect_section(self):
        self.assertEqual(field(filing_sheet(FACTS), "Suspect UPI ID").value, "scammer@ybl")

    def test_amount_and_digits_are_exact(self):
        sheet = filing_sheet(FACTS)
        self.assertEqual(field(sheet, "Fraud amount").value, "180000")
        self.assertEqual(field(sheet, "Transaction ID or UTR").value, "533827652689")

    def test_ready_when_nothing_is_wrong(self):
        self.assertTrue(filing_sheet(FACTS).ready)

    def test_short_story_is_flagged_and_never_padded(self):
        sheet = filing_sheet({**FACTS, "story": "I paid money."})
        details = field(sheet, "Incident details")
        self.assertIn("too_short", details.problems)
        self.assertEqual(details.value, "I paid money.")
        self.assertFalse(sheet.ready)

    def test_missing_amount_blocks_ready_and_is_never_invented(self):
        facts = dict(FACTS)
        del facts["amount"]
        sheet = filing_sheet(facts)
        self.assertFalse(sheet.ready)
        self.assertEqual(field(sheet, "Fraud amount").value, "")

    def test_secret_steps_stay_with_the_person(self):
        sheet = filing_sheet(FACTS)
        self.assertTrue(field(sheet, "National ID").person_only)
        self.assertEqual(field(sheet, "National ID").value, "")
        self.assertTrue(field(sheet, "OTP and captcha").person_only)

    def test_changes_are_shown_to_the_person(self):
        self.assertTrue(filing_sheet(FACTS).changes)


class MessagesTests(unittest.TestCase):
    def test_one_field_per_message_in_order(self):
        sheet = filing_sheet(FACTS)
        messages = as_messages(sheet)
        self.assertEqual(len(messages), len(sheet.fields))
        self.assertIn(f"1 of {len(messages)}", messages[0])

    def test_person_only_steps_say_so(self):
        messages = as_messages(filing_sheet(FACTS))
        self.assertTrue(any("do not send" in message.lower() for message in messages))


class RefundSheetTests(unittest.TestCase):
    def test_remarks_pass_and_fit(self):
        sheet = refund_sheet("20000000000012", "I lost money to a scam and my complaint is registered. I didn't get it back. " * 12)
        remarks = field(sheet, "Remarks").value
        self.assertEqual(pr.check_remarks(remarks), [])

    def test_banking_details_stay_with_the_person(self):
        sheet = refund_sheet("20000000000012", "Please return the held amount.")
        self.assertTrue(field(sheet, "Account number and IFSC").person_only)
        self.assertTrue(field(sheet, "PAN card").person_only)

    def test_bad_acknowledgement_is_flagged(self):
        sheet = refund_sheet("123", "Please return the held amount.")
        self.assertIn("bad_ack", field(sheet, "Acknowledgement ID").problems)

    def test_hold_above_the_limit_notes_the_fir(self):
        sheet = refund_sheet("20000000000012", "Please return it.", held=Decimal(60000))
        self.assertTrue(any("FIR" in note for note in sheet.notes))

    def test_bond_is_explained(self):
        sheet = refund_sheet("20000000000012", "Please return it.")
        self.assertTrue(any("indemnity bond" in note.lower() for note in sheet.notes))


class SuspectReportTests(unittest.TestCase):
    TEXT = "This account moved our chat to WhatsApp, then asked me to invest in a trading app and said I can't withdraw without paying tax first."

    def test_description_passes(self):
        sheet = suspect_report_sheet("Tamil Nadu", "Social Media URL", "https://example.test/profile", self.TEXT)
        self.assertEqual(pr.check_suspect_report(field(sheet, "Description").value), [])
        self.assertTrue(sheet.ready)

    def test_short_description_is_flagged(self):
        sheet = suspect_report_sheet("Tamil Nadu", "Phone Number", "9876543210", "Too short.")
        self.assertIn("too_short", field(sheet, "Description").problems)

    def test_only_portal_types_are_allowed(self):
        with self.assertRaises(ValueError):
            suspect_report_sheet("Tamil Nadu", "Dating Profile", "x", self.TEXT)


if __name__ == "__main__":
    unittest.main()
