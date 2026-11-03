import json
import tempfile
import unittest
from pathlib import Path

from redthread import bill, lab
from redthread.model import Ledger, MetaModel
from tests.test_bill import MENU
from tests.test_model import KEY, FakeTransport
from tests.test_proof import Scripted


def menu():
    return bill.Menu(MENU["venue"], [bill.MenuItem(item["name"], item["price"]) for item in MENU["items"]])


def as_read(case):
    return bill.Bill(case.venue, [bill.Line(name, quantity, amount) for name, quantity, amount in case.lines],
                     [bill.Charge(name, amount) for name, amount in case.charges], case.total, f"Pay to {case.payee}")


class CaseTests(unittest.TestCase):
    def test_cases_cover_planted_and_fair_bills(self):
        kinds = {case.expect_level for case in lab.BILLS}
        self.assertIn("fair", kinds)
        self.assertGreaterEqual(sum(1 for case in lab.BILLS if case.expect_level != "fair" or case.expect_kinds), 6)
        self.assertEqual(len({case.id for case in lab.BILLS}), len(lab.BILLS))

    def test_every_case_is_right_when_the_read_is_perfect(self):
        """The logic alone, with no model: a perfect read of each bill must give exactly what was planted."""
        for case in lab.BILLS:
            check = bill.compare(menu(), as_read(case))
            self.assertEqual(check.level, case.expect_level, case.id)
            self.assertEqual({item.kind for item in check.findings}, case.expect_kinds, case.id)

    def test_the_image_prompt_holds_every_number(self):
        for case in lab.BILLS:
            prompt = lab.bill_prompt(case)
            for name, quantity, amount in case.lines:
                self.assertIn(f"{name} x{quantity} {amount}", prompt)
            self.assertIn(f"TOTAL {case.total}", prompt)
            self.assertIn(case.payee, prompt)

    def test_a_padded_total_is_printed_as_planted(self):
        padded = next(case for case in lab.BILLS if case.expect_level == "total_off")
        added = sum(amount for _, _, amount in padded.lines) + sum(amount for _, amount in padded.charges)
        self.assertGreater(padded.total, added)


class ScoreTests(unittest.TestCase):
    def case(self, level):
        if level == "fair":
            return next(case for case in lab.BILLS if not case.planted)
        return next(case for case in lab.BILLS if case.expect_level == level)

    def test_a_planted_problem_found_is_caught(self):
        case = self.case("overcharged")
        row = lab.score_bill(case, as_read(case), bill.compare(menu(), as_read(case)), qr=None)
        self.assertTrue(row["caught"])
        self.assertTrue(row["read_exact"])

    def test_a_missed_problem_is_not_caught(self):
        case = self.case("overcharged")
        fair = bill.Check("fair", "This bill matches the menu.")
        self.assertFalse(lab.score_bill(case, as_read(case), fair, qr=None)["caught"])

    def test_an_alarm_on_a_fair_bill_is_a_false_alarm(self):
        case = self.case("fair")
        wrong = bill.Check("overcharged", "x", [bill.Finding("overcharge", "x")])
        row = lab.score_bill(case, as_read(case), wrong, qr=None)
        self.assertTrue(row["false_alarm"])
        self.assertFalse(row["caught"])

    def test_a_misread_amount_is_counted(self):
        case = self.case("fair")
        read = as_read(case)
        read.lines[0].amount += 1
        self.assertFalse(lab.score_bill(case, read, bill.compare(menu(), read), qr=None)["read_exact"])

    def test_summary_numbers_come_from_code(self):
        rows = [{"planted": True, "caught": True, "false_alarm": False, "read_exact": True, "qr_found": True},
                {"planted": True, "caught": False, "false_alarm": False, "read_exact": False, "qr_found": False},
                {"planted": False, "caught": True, "false_alarm": False, "read_exact": True, "qr_found": True}]
        self.assertEqual(lab.summarize_bills(rows), {"planted": 2, "caught": 1, "fair": 1, "false_alarms": 0, "read_exact": 2, "bills": 3, "qr_found": 2})


class BluffTests(unittest.TestCase):
    def test_every_lab_chat_is_invented_and_has_the_match_speaking(self):
        for name, chat, other in lab.chats():
            self.assertGreater(sum(1 for turn in chat.turns if turn.speaker == other), 5, name)

    def test_the_bluff_run_records_what_was_guessed(self):
        memory = {"name": "Rohan", "job": "product", "city": "Bengaluru", "names": [], "claims": [], "questions": [
            {"question": "Who taught you to make filter coffee?", "quote": "My paati taught me filter coffee in Mylapore", "answer": "paati"},
            {"question": "What makes your dog run and hide?", "quote": "He hides under the bed every time the pressure cooker whistles", "answer": "pressure cooker"},
            {"question": "What do you call that scooter of yours?", "quote": "My old scooter Bijli", "answer": "Bijli"},
        ]}
        spark = Scripted(memory=memory, scores={"scores": [{"id": 1, "label": "consistent"}, {"id": 2, "label": "contradicts"}, {"id": 3, "label": "no_answer"}]})
        wonder = Scripted(bluff={"answers": [{"id": 1, "spoken": "My grandmother"}, {"id": 2, "spoken": "Thunder"}, {"id": 3, "spoken": "Rocket"}]})
        rows = lab.run_bluff(spark, wonder, [("demo", lab.proof_demo.chat(), "Rohan")])
        self.assertEqual(rows[0]["tried"], 3)
        self.assertEqual(rows[0]["guessed"], [{"question": "Who taught you to make filter coffee?", "spoken": "My grandmother"}])
        self.assertEqual(lab.summarize_bluff(rows), {"chats": 1, "tried": 3, "guessed": 1, "kept": 2, "unguarded": 0})


class ImageTests(unittest.TestCase):
    def test_muse_image_request_and_cost(self):
        import base64

        with tempfile.TemporaryDirectory() as tmp:
            transport = FakeTransport((200, json.dumps({"data": [{"b64_json": base64.b64encode(b"jpegbytes").decode()}]}).encode()))
            ledger = Ledger(1.0, Path(tmp) / "ledger.json")
            model = MetaModel(KEY, ledger, transport=transport, sleep=lambda _: None)
            self.assertEqual(model.image("A bill"), b"jpegbytes")
            url, _, body = transport.calls[0]
            self.assertTrue(url.endswith("/images/generations"))
            self.assertEqual(body["model"], "muse-image-1.0")
            self.assertEqual(body["output_format"], "jpeg")
            self.assertAlmostEqual(ledger.spent, 0.01)


if __name__ == "__main__":
    unittest.main()
