import json
import unittest

from redthread import bill
from redthread.model import ModelError, Reply

MENU = {"venue": "MOONLIT TERRACE CAFE", "items": [
    {"name": "Virgin Mojito", "price": 240}, {"name": "Cold Coffee", "price": 180}, {"name": "Paneer Tikka", "price": 320},
    {"name": "Veg Platter", "price": 450}, {"name": "Hookah", "price": 950}, {"name": "Whisky 30 ml", "price": 700},
]}
RACKET = {"venue": "MOONLIT TERRACE CAFE", "total": 46882, "payee": "Pay to rohit.k@okaxis",
          "lines": [{"name": "Virgin Mojito", "quantity": 2, "amount": 4800}, {"name": "Paneer Tikka", "quantity": 1, "amount": 320},
                    {"name": "Hookah", "quantity": 1, "amount": 9500}, {"name": "Whisky 30ml", "quantity": 4, "amount": 28000}],
          "charges": [{"name": "Service charge", "amount": 4262}]}
FAIR = {"venue": "Moonlit Terrace Cafe", "total": 800, "payee": "moonlitterrace@hdfcbank",
        "lines": [{"name": "Virgin Mojito", "quantity": 1, "amount": 240}, {"name": "Paneer Tikka", "quantity": 1, "amount": 320},
                  {"name": "Cold Coffee", "quantity": 1, "amount": 180}],
        "charges": [{"name": "GST", "amount": 60}]}


class Fake:
    def __init__(self, **answers):
        self.answers = answers
        self.seen = []

    def chat(self, messages, schema=None, **options):
        self.seen.append((messages, schema))
        answer = self.answers[schema["title"]]
        if isinstance(answer, Exception):
            raise answer
        return Reply(text=json.dumps(answer))


def menu():
    return bill.read_menu(Fake(menu=MENU), b"img", "image/jpeg")


def read(data):
    return bill.read_bill(Fake(bill=data), b"img", "image/jpeg")


class RupeeTests(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(bill.rupees(4550), "₹4,550")
        self.assertEqual(bill.rupees(120000), "₹1,20,000")
        self.assertEqual(bill.rupees(38070), "₹38,070")
        self.assertEqual(bill.rupees(950), "₹950")


class ReadTests(unittest.TestCase):
    def test_the_photo_goes_to_spark_as_an_image(self):
        model = Fake(menu=MENU)
        bill.read_menu(model, b"img", "image/png")
        content = model.seen[0][0][0]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_menu_prices_are_kept_as_numbers(self):
        self.assertEqual([(item.name, item.price) for item in menu().items][:2], [("Virgin Mojito", 240), ("Cold Coffee", 180)])

    def test_nonsense_values_are_dropped(self):
        data = {"venue": "X", "items": [{"name": "", "price": 100}, {"name": "Tea", "price": -5}, {"name": "Chai", "price": 40}]}
        self.assertEqual([item.name for item in bill.read_menu(Fake(menu=data), b"i", "image/jpeg").items], ["Chai"])

    def test_a_failed_read_says_so(self):
        result = bill.read_bill(Fake(bill=ModelError("down")), b"i", "image/jpeg")
        self.assertTrue(result.fallback)
        self.assertEqual(result.lines, [])

    def test_no_model_is_a_fallback(self):
        self.assertTrue(bill.read_menu(None, b"i", "image/jpeg").fallback)


class CompareTests(unittest.TestCase):
    def test_the_racket_bill_is_caught_line_by_line(self):
        result = bill.compare(menu(), read(RACKET))
        self.assertEqual(result.level, "overcharged")
        over = [item for item in result.findings if item.kind == "overcharge"]
        self.assertEqual([item.name for item in over], ["Virgin Mojito", "Hookah", "Whisky 30ml"])
        self.assertEqual(result.extra, 4320 + 8550 + 25200)
        self.assertEqual(result.headline, "This bill charges ₹38,070 more than the menu.")
        self.assertEqual(result.menu_total, 4550)
        self.assertIn("₹240 each", over[0].text)
        self.assertIn("10 times", over[0].text)

    def test_the_service_charge_is_named_as_optional(self):
        result = bill.compare(menu(), read(RACKET))
        service = next(item for item in result.findings if item.kind == "service")
        self.assertIn("voluntary", service.text)
        self.assertIn("1915", service.text)

    def test_a_personal_upi_id_on_the_bill_is_a_tripwire(self):
        result = bill.compare(menu(), read(RACKET))
        payee = next(item for item in result.findings if item.kind == "payee")
        self.assertIn("rohit.k@okaxis", payee.text)
        self.assertEqual(result.payee, "rohit.k@okaxis")

    def test_a_fair_bill_matches(self):
        result = bill.compare(menu(), read(FAIR))
        self.assertEqual(result.level, "fair")
        self.assertEqual(result.headline, "This bill matches the menu.")
        self.assertEqual([item.kind for item in result.findings], [])

    def test_a_payee_in_the_venue_name_is_fine(self):
        self.assertNotIn("payee", [item.kind for item in bill.compare(menu(), read(FAIR)).findings])

    def test_a_total_that_does_not_add_up(self):
        data = dict(FAIR, total=1800)
        result = bill.compare(menu(), read(data))
        self.assertEqual(result.level, "total_off")
        self.assertIn("₹1,000 more than", next(item for item in result.findings if item.kind == "total_off").text)

    def test_an_item_not_on_the_menu(self):
        data = dict(FAIR, lines=FAIR["lines"] + [{"name": "Imported water", "quantity": 1, "amount": 900}], total=1700)
        result = bill.compare(menu(), read(data))
        self.assertIn("Imported water", next(item for item in result.findings if item.kind == "not_on_menu").text)
        self.assertEqual(result.level, "check")

    def test_without_a_menu_only_the_bills_own_sums_are_checked(self):
        result = bill.compare(None, read(RACKET))
        self.assertEqual(result.level, "no_menu")
        self.assertNotIn("overcharge", [item.kind for item in result.findings])
        self.assertIn("payee", [item.kind for item in result.findings])

    def test_a_bill_that_could_not_be_read(self):
        result = bill.compare(menu(), bill.Bill(fallback=True))
        self.assertEqual(result.level, "unread")

    def test_small_rounding_is_not_an_overcharge(self):
        data = dict(FAIR, lines=[{"name": "Virgin Mojito", "quantity": 1, "amount": 245}], charges=[], total=245)
        self.assertEqual(bill.compare(menu(), read(data)).level, "fair")

    def test_earlier_reports_of_the_payee_are_counted(self):
        class Network:
            def report_count(self, case_id, identifiers):
                return 2 if identifiers == {"upi": ["rohit.k@okaxis"]} else 0

        result = bill.compare(menu(), read(RACKET), network=Network())
        self.assertIn("2 other people", next(item for item in result.findings if item.kind == "payee").text)

    def test_no_long_dashes_reach_the_person(self):
        result = bill.compare(menu(), read(RACKET))
        for text in [result.headline, *[item.text for item in result.findings]]:
            self.assertNotIn("—", text)
            self.assertNotIn("–", text)


class QrTests(unittest.TestCase):
    def test_sam_boxes_are_parsed(self):
        boxes = bill.parse_boxes("found <|box;x1=325;y1=1209;x2=539;y2=1424;w=1600;h=1600|> done")
        self.assertEqual(boxes, [{"x1": 325, "y1": 1209, "x2": 539, "y2": 1424, "w": 1600, "h": 1600}])

    def test_odd_boxes_are_ignored(self):
        self.assertEqual(bill.parse_boxes("<|box;x1=5;y1=9|> <|box;x1=a;y1=1;x2=2;y2=3;w=4;h=5|>"), [])

    def test_no_model_finds_nothing(self):
        self.assertIsNone(bill.find_qr(None, b"i", "image/jpeg"))

    def test_a_failed_search_finds_nothing(self):
        class Broken:
            def locate(self, raw, mime, phrase):
                raise ModelError("down")

        self.assertIsNone(bill.find_qr(Broken(), b"i", "image/jpeg"))

    def test_the_biggest_box_wins(self):
        class Sam:
            def locate(self, raw, mime, phrase):
                return "<|box;x1=0;y1=0;x2=10;y2=10;w=100;h=100|><|box;x1=20;y1=20;x2=80;y2=80;w=100;h=100|>"

        self.assertEqual(bill.find_qr(Sam(), b"i", "image/jpeg")["x1"], 20)


if __name__ == "__main__":
    unittest.main()
