import unittest
from decimal import Decimal

from redthread import portal_rules as pr

MB = 1024 * 1024
JPEG = bytes.fromhex("ffd8ffe0")
PNG = bytes.fromhex("89504e47")
PDF = bytes.fromhex("25504446")


class PortalValidatorTests(unittest.TestCase):
    """The same checks, in the same order, as the portal's public form code."""

    def test_clean_text_passes(self):
        self.assertIsNone(pr.portal_error("I did not pay the fee"))

    def test_blocked_words_any_case(self):
        for text in ["Please update my KYC", "UPDATE", "they said delete it", "drop", "Insert card", "truncate"]:
            self.assertEqual(pr.portal_error(text), "blocked_word", text)

    def test_only_whole_words_are_blocked(self):
        for text in ["I updated my phone", "the deleted chat", "dropped call", "insertion"]:
            self.assertIsNone(pr.portal_error(text), text)

    def test_three_repeats_are_blocked_but_two_are_fine(self):
        self.assertEqual(pr.portal_error("please please please help"), "repeated_word")
        self.assertEqual(pr.portal_error("No no NO"), "repeated_word")
        self.assertIsNone(pr.portal_error("please please help"))

    def test_tags_start_numbers_and_characters(self):
        self.assertEqual(pr.portal_error("<b>hello</b>"), "script_tag")
        self.assertEqual(pr.portal_error("*hello"), "invalid_start")
        self.assertEqual(pr.portal_error("533827652689"), "only_numbers")
        self.assertEqual(pr.portal_error("I didn't pay"), "blocked_char")
        self.assertEqual(pr.portal_error('he said "pay"'), "blocked_char")

    def test_blocked_word_wins_over_other_errors(self):
        self.assertEqual(pr.portal_error("update <b>"), "blocked_word")

    def test_portal_silently_removes_blocked_words(self):
        self.assertEqual(pr.portal_would_keep("Asked me to update my KYC"), "Asked me to  my KYC")
        self.assertEqual(pr.portal_would_keep("I paid"), "I paid")


class CheckTextTests(unittest.TestCase):
    def test_incident_length_limits(self):
        self.assertIn("too_short", pr.check_incident("a b " * 49 + "abc"))
        self.assertNotIn("too_short", pr.check_incident("x" * 200))
        self.assertNotIn("too_long", pr.check_incident("x" * 1500))
        self.assertIn("too_long", pr.check_incident("x" * 1501))

    def test_official_instruction_characters_are_also_rejected(self):
        for ch in "#$@!`’":
            self.assertIn("instruction_char", pr.check_incident("x" * 200 + ch), ch)

    def test_remarks_limit(self):
        self.assertEqual(pr.check_remarks("x" * 500), [])
        self.assertIn("too_long", pr.check_remarks("x" * 501))

    def test_suspect_report_limits_ignore_outer_spaces(self):
        self.assertIn("too_short", pr.check_suspect_report(" " * 20 + "x" * 99))
        self.assertEqual(pr.check_suspect_report("x" * 100), [])
        self.assertIn("too_long", pr.check_suspect_report("x" * 501))

    def test_delay_reason_limit(self):
        self.assertEqual(pr.check_delay_reason("x" * 200), [])
        self.assertIn("too_long", pr.check_delay_reason("x" * 201))


class CleanTextTests(unittest.TestCase):
    def test_upi_ids_move_out_of_the_text(self):
        cleaned = pr.clean_text("I paid scammer.one@ybl and then good-man_2@okhdfcbank.")
        self.assertNotIn("@", cleaned.text)
        self.assertEqual(cleaned.moved_ids, ["scammer.one@ybl", "good-man_2@okhdfcbank"])

    def test_blocked_words_become_safe_words(self):
        self.assertEqual(pr.clean_text("Delete the chat").text, "Remove the chat")
        self.assertEqual(pr.clean_text("they asked for a KYC update").text, "they asked for a KYC renewal")
        self.assertEqual(pr.clean_text("please update my KYC").text, "please renew my KYC")
        self.assertEqual(pr.clean_text("he will update me").text, "he will inform me")

    def test_contractions_are_expanded(self):
        self.assertEqual(pr.clean_text("I didn't know, I can't pay").text, "I did not know, I cannot pay")
        self.assertEqual(pr.clean_text("it’s my money").text, "it is my money")
        self.assertEqual(pr.clean_text("Priya's phone").text, "Priyas phone")

    def test_symbols_become_words_or_go(self):
        self.assertEqual(pr.clean_text("Txn #55 for $20!").text, "Txn number 55 for USD 20.")
        self.assertEqual(pr.clean_text('he said "pay" *now*').text, "he said pay now")

    def test_tags_and_repeats_go(self):
        self.assertEqual(pr.clean_text("<b>help</b> me please please please").text, "help me please")

    def test_leading_symbols_go(self):
        self.assertEqual(pr.clean_text("~ *I paid").text, "I paid")

    def test_digits_are_never_changed(self):
        text = "On 12/03/2026 I paid Rs 1,80,000 and ₹40,000, UTR 533827652689, to 9876543210."
        cleaned = pr.clean_text(text)
        self.assertEqual(cleaned.text, text)
        self.assertEqual(cleaned.changes, [])

    def test_every_change_is_recorded(self):
        cleaned = pr.clean_text("Delete it")
        self.assertTrue(any("delete" in change.lower() for change in cleaned.changes))

    def test_cleaned_text_passes_every_rule_except_length(self):
        nasty = (
            "<i>URGENT!!!</i> He said “update your KYC” & don't tell anyone. Pay pay pay to a@b. "
            "Insert the card, drop it, truncate the note. #1 $5 ^ * ~ | ` ' \" < > @"
        )
        problems = pr.check_incident(pr.clean_text(nasty).text + " " + "x" * 200)
        self.assertEqual(problems, [])


class IdentifierTests(unittest.TestCase):
    def test_acknowledgement_id(self):
        self.assertEqual(pr.parse_ack_id("20000000000012").source, "portal")
        self.assertEqual(pr.parse_ack_id("30000000000012").source, "helpline")
        self.assertEqual(pr.parse_ack_id("2000 0000 0000 12").value, "20000000000012")
        self.assertEqual(pr.parse_ack_id("90000000000012").source, "unknown")
        self.assertIsNone(pr.parse_ack_id("2000000000001"))
        self.assertIsNone(pr.parse_ack_id("2000000000001A"))

    def test_request_id(self):
        self.assertTrue(pr.is_request_id("MR20251208000003"))
        self.assertTrue(pr.is_request_id("MR123456789012"))
        self.assertFalse(pr.is_request_id("MR123"))
        self.assertFalse(pr.is_request_id("20251208000003"))

    def test_mobile(self):
        self.assertEqual(pr.normalize_mobile("+91 98765 43210"), "9876543210")
        self.assertEqual(pr.normalize_mobile("09876543210"), "9876543210")
        self.assertIsNone(pr.normalize_mobile("5876543210"))
        self.assertIsNone(pr.normalize_mobile("98765"))

    def test_utr_warning_never_blocks(self):
        self.assertIsNone(pr.utr_warning("533827652689", "upi"))
        self.assertIsNotNone(pr.utr_warning("53382765268", "upi"))
        self.assertIsNone(pr.utr_warning("HDFCN52026031200", "neft"))
        self.assertIsNotNone(pr.utr_warning("abc", "unknown"))


class FileTests(unittest.TestCase):
    def test_id_proof_limits_and_types(self):
        self.assertEqual(pr.check_file("national_id", "id.jpg", 5 * MB, JPEG), [])
        self.assertEqual(pr.check_file("national_id", "id.PNG", 100, PNG), [])
        self.assertIn("too_big", pr.check_file("national_id", "id.jpg", 5 * MB + 1, JPEG))
        self.assertIn("wrong_type", pr.check_file("national_id", "id.pdf", 100, PDF))

    def test_evidence_can_be_larger(self):
        self.assertEqual(pr.check_file("evidence", "bank.pdf", 10 * MB, PDF), [])
        self.assertIn("too_big", pr.check_file("evidence", "bank.pdf", 10 * MB + 1, PDF))

    def test_suspect_files_are_capped_at_five(self):
        self.assertIn("too_big", pr.check_file("suspect_photo", "a.jpg", 5 * MB + 1, JPEG))
        self.assertIn("too_big", pr.check_file("suspect_report", "a.jpg", 5 * MB + 1, JPEG))

    def test_renamed_or_empty_files_fail(self):
        self.assertIn("renamed_file", pr.check_file("evidence", "a.jpg", 100, PNG))
        self.assertIn("renamed_file", pr.check_file("evidence", "a.png", 100, PDF))
        self.assertIn("empty", pr.check_file("evidence", "a.jpg", 0, JPEG))

    def test_some_real_jpegs_need_converting(self):
        self.assertIn("renamed_file", pr.check_file("evidence", "a.jpg", 100, bytes.fromhex("ffd8ffdb")))
        self.assertEqual(pr.check_file("evidence", "a.jpg", 100, bytes.fromhex("ffd8ffe1")), [])


class MoneyRuleTests(unittest.TestCase):
    def test_fir_rule_per_bank(self):
        self.assertEqual(pr.fir_rule(Decimal("49999.99")), "no_fir")
        self.assertEqual(pr.fir_rule(Decimal("50000")), "ask_police")
        self.assertEqual(pr.fir_rule(Decimal("50000.01")), "fir_required")

    def test_rows_must_add_up(self):
        rows = [Decimal("7000"), Decimal("50000"), Decimal("27800")]
        self.assertTrue(pr.rows_add_up(rows, Decimal("84800")))
        self.assertFalse(pr.rows_add_up(rows, Decimal("84000")))

    def test_restorable_cannot_exceed_held(self):
        self.assertTrue(pr.restorable_is_valid(Decimal("40000"), Decimal("50000")))
        self.assertFalse(pr.restorable_is_valid(Decimal("50001"), Decimal("50000")))
        self.assertFalse(pr.restorable_is_valid(Decimal("-1"), Decimal("50000")))

    def test_credit_must_match_to_close(self):
        self.assertEqual(pr.credit_status(Decimal("40000"), Decimal("40000")), "matches")
        self.assertEqual(pr.credit_status(Decimal("30000"), Decimal("40000")), "partial")
        self.assertEqual(pr.credit_status(Decimal("50000"), Decimal("40000")), "more_than_expected")


if __name__ == "__main__":
    unittest.main()
