import unittest
from decimal import Decimal

from redthread.facts import extract_identifiers, reconcile


class IdentifierTests(unittest.TestCase):
    def test_finds_each_kind(self):
        found = extract_identifiers(
            "Pay scam.one@ybl or mail a.b@mail.com, call +91 98765 43210, see https://bad.example/x, UTR 533827652689."
        )
        self.assertEqual(found["upi"], ["scam.one@ybl"])
        self.assertEqual(found["email"], ["a.b@mail.com"])
        self.assertEqual(found["phone"], ["9876543210"])
        self.assertEqual(found["link"], ["https://bad.example/x"])
        self.assertEqual(found["reference"], ["533827652689"])

    def test_phone_is_not_a_reference(self):
        self.assertEqual(extract_identifiers("call 9876543210")["reference"], [])


class ReconcileTests(unittest.TestCase):
    def test_matching_facts_are_confirmed_with_no_question(self):
        result = reconcile("maine dhai lakh bheje, UTR five three three eight two seven six five two six eight nine",
                           "Paid Rs 2,50,000 UTR 533827652689")
        self.assertEqual(result.amount, Decimal(250000))
        self.assertEqual(result.reference, "533827652689")
        self.assertEqual(result.questions, [])

    def test_amount_mismatch_becomes_a_question_and_nothing_is_guessed(self):
        result = reconcile("I sent two lakh", "Paid Rs 2,50,000")
        self.assertIsNone(result.amount)
        self.assertEqual(len(result.questions), 1)
        self.assertIn("2,00,000", result.questions[0])
        self.assertIn("2,50,000", result.questions[0])

    def test_one_wrong_digit_in_the_reference_is_caught(self):
        result = reconcile("UTR five three three eight two seven six five two six eight zero",
                           "UTR 533827652689")
        self.assertIsNone(result.reference)
        self.assertEqual(len(result.questions), 1)

    def test_only_the_voice_note_is_unconfirmed(self):
        result = reconcile("I sent dedh lakh", "")
        self.assertIsNone(result.amount)
        self.assertIn("1,50,000", result.questions[0])

    def test_only_the_screenshot_is_enough(self):
        result = reconcile("", "Paid Rs 500 UTR 533827652689")
        self.assertEqual(result.amount, Decimal(500))
        self.assertEqual(result.reference, "533827652689")

    def test_several_payments_on_the_screenshot_can_match_the_total(self):
        result = reconcile("total 57 thousand", "Rs 7,000 and Rs 50,000")
        self.assertEqual(result.amount, Decimal(57000))
        self.assertEqual(result.questions, [])


if __name__ == "__main__":
    unittest.main()
