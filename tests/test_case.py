import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from redthread.case import Case, apply_message, classify_message, next_step, overdue

NOW = datetime(2026, 11, 1, 10, 0)


class ClassifyTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(classify_message("Your complaint is registered. Acknowledgement No 30000000000012").kind, "ack")
        self.assertEqual(classify_message("Rs 40,000 put on hold against your complaint").kind, "held")
        self.assertEqual(classify_message("Rs 38,000 is eligible for restoration. Apply on the Money Restoration portal.").kind, "eligible")
        self.assertEqual(classify_message("Your request ID is MR20261101000003").kind, "request")
        self.assertEqual(classify_message("Rs 38,000 credited to your account").kind, "credit")

    def test_a_debit_is_not_a_credit(self):
        self.assertNotEqual(classify_message("Rs 38,000 debited from your account").kind, "credit")

    def test_values_are_read_by_code(self):
        self.assertEqual(classify_message("Rs 38,000 credited").amount, Decimal(38000))
        self.assertEqual(classify_message("MR20261101000003 received").request_id, "MR20261101000003")

    def test_unknown_message(self):
        self.assertEqual(classify_message("hello there").kind, "unknown")


class FlowTests(unittest.TestCase):
    def test_full_case_closes_only_on_a_matching_credit(self):
        case = Case("c1")
        apply_message(case, "Acknowledgement No 30000000000012", NOW)
        self.assertEqual(case.state, "helpline_ack")
        apply_message(case, "Complaint 20000000000012 registered on the portal", NOW)
        self.assertEqual(case.state, "filed")
        apply_message(case, "Rs 40,000 put on hold", NOW)
        self.assertEqual(case.state, "held")
        apply_message(case, "Rs 38,000 eligible for restoration", NOW)
        self.assertEqual(case.expected, Decimal(38000))
        apply_message(case, "Request ID MR20261101000003", NOW)
        self.assertEqual(case.state, "requested")
        apply_message(case, "Rs 38,000 credited", NOW)
        self.assertEqual(case.state, "closed")

    def test_partial_credit_keeps_the_case_open(self):
        case = Case("c1", state="requested", expected=Decimal(38000))
        apply_message(case, "Rs 20,000 credited", NOW)
        self.assertEqual(case.state, "partly_credited")
        apply_message(case, "Rs 18,000 credited", NOW)
        self.assertEqual(case.state, "closed")
        self.assertEqual(case.credited, Decimal(38000))

    def test_credit_with_no_expected_amount_does_not_close(self):
        case = Case("c1", state="filed")
        apply_message(case, "Rs 5,000 credited", NOW)
        self.assertNotEqual(case.state, "closed")

    def test_same_credit_forwarded_twice_counts_once(self):
        case = Case("c1", state="requested", expected=Decimal(38000))
        apply_message(case, "Rs 19,000 credited", NOW)
        apply_message(case, "Rs 19,000 credited", NOW)
        self.assertEqual(case.credited, Decimal(19000))

    def test_state_never_moves_backwards(self):
        case = Case("c1", state="requested", expected=Decimal(38000))
        apply_message(case, "Rs 40,000 put on hold", NOW)
        self.assertEqual(case.state, "requested")

    def test_every_message_is_logged(self):
        case = Case("c1")
        apply_message(case, "hello", NOW)
        self.assertEqual(len(case.log), 1)


class DeadlineTests(unittest.TestCase):
    def test_helpline_ack_starts_the_24_hour_clock(self):
        case = Case("c1")
        apply_message(case, "Acknowledgement No 30000000000012", NOW)
        self.assertEqual(overdue(case, NOW + timedelta(hours=23)), [])
        self.assertEqual(overdue(case, NOW + timedelta(hours=25)), ["finish_portal_filing"])

    def test_filing_clears_the_clock(self):
        case = Case("c1")
        apply_message(case, "Acknowledgement No 30000000000012", NOW)
        apply_message(case, "Complaint 20000000000012 registered", NOW)
        self.assertEqual(overdue(case, NOW + timedelta(days=3)), [])


class NextStepTests(unittest.TestCase):
    def test_each_state_has_a_next_step(self):
        for state in ("new", "helpline_ack", "filed", "held", "eligible", "requested", "partly_credited", "closed"):
            self.assertTrue(next_step(Case("c1", state=state)), state)

    def test_large_hold_mentions_the_fir(self):
        case = Case("c1", state="eligible", held=Decimal(60000))
        self.assertIn("FIR", next_step(case))
        small = Case("c1", state="eligible", held=Decimal(40000))
        self.assertNotIn("FIR", next_step(small))

    def test_steps_never_promise_money(self):
        for state in ("new", "helpline_ack", "filed", "held", "eligible", "requested"):
            self.assertNotIn("will get", next_step(Case("c1", state=state)).lower())


if __name__ == "__main__":
    unittest.main()
