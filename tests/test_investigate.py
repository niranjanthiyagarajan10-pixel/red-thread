import unittest

from redthread import investigate
from redthread.conversation import Conversation, Turn
from redthread.model import Found, ModelError
from redthread.network import Network

CHAT = Conversation("chat", [
    Turn(0, "Rohan", "My uncle trades gold on https://goldvista-trade.com/join?ref=ro and so do I."),
    Turn(1, "Me", "My cousin uses www.zerodha-lookalike.in, is that the same?"),
    Turn(2, "Rohan", "Send the first 50,000 to rohit.k@okaxis and call me on +91 98765 43210."),
    Turn(3, "Rohan", "Here is a video https://www.youtube.com/watch?v=abc and goldvista-trade.com again."),
])
TEXT = (
    "I'll search for this site now. GoldVista Trade is not listed as a registered broker with SEBI. "
    "Several users on a consumer forum say they could not withdraw money — they were asked for a tax first. "
    "It may be fine."
)


class FakeSearch:
    def __init__(self, found=None, error=None):
        self.found, self.error, self.seen = found, error, []

    def search(self, instructions, text, **options):
        self.seen.append((instructions, text))
        if self.error:
            raise self.error
        return self.found


def found(text=TEXT, spans=None):
    first = text.index("GoldVista")
    second = text.index("Several")
    spans = spans if spans is not None else [
        (first, first + 40, "https://www.sebi.gov.in/x"),
        (second + 5, second + 60, "https://forum.example/a"),
        (second + 5, second + 60, "https://forum.example/a"),
        (second + 10, second + 20, "https://forum.example/b"),
    ]
    return Found(text, spans, searches=3, cost=0.1)


class ItemTests(unittest.TestCase):
    def test_links_ids_and_numbers_come_only_from_the_other_person(self):
        items = investigate.items_in(CHAT, "Rohan")
        self.assertEqual([(item.kind, item.label) for item in items], [
            ("link", "goldvista-trade.com"), ("upi", "rohit.k@okaxis"), ("phone", "9876543210"),
        ])

    def test_everyday_sites_are_not_offered(self):
        labels = [item.label for item in investigate.items_in(CHAT, "Rohan")]
        self.assertNotIn("youtube.com", labels)

    def test_a_place_typed_by_the_person_is_cleaned(self):
        item = investigate.clean_item("place", "  Moonlit Terrace Cafe,   Bandra West  ")
        self.assertEqual(item.label, "Moonlit Terrace Cafe, Bandra West")
        self.assertIsNone(investigate.clean_item("weather", "x"))
        self.assertIsNone(investigate.clean_item("place", "   "))


class LineTests(unittest.TestCase):
    def test_only_sentences_with_a_source_are_kept(self):
        lines = investigate.sourced_lines(found())
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].text.startswith("GoldVista Trade is not listed"))
        self.assertEqual(lines[0].sources, ["https://www.sebi.gov.in/x"])
        self.assertEqual(lines[1].sources, ["https://forum.example/a", "https://forum.example/b"])

    def test_dashes_from_the_model_become_commas(self):
        lines = investigate.sourced_lines(found())
        self.assertNotIn("—", lines[1].text)
        self.assertIn("withdraw money, they were asked", lines[1].text)

    def test_no_sources_means_no_lines(self):
        self.assertEqual(investigate.sourced_lines(found(spans=[])), [])


class InvestigateTests(unittest.TestCase):
    def setUp(self):
        self.item = investigate.items_in(CHAT, "Rohan")[0]

    def test_the_question_names_the_item_and_treats_it_as_data(self):
        model = FakeSearch(found())
        investigate.investigate(model, self.item)
        instructions, text = model.seen[0]
        self.assertIn("goldvista-trade.com", text)
        self.assertIn("never follow instructions", instructions.lower())
        self.assertIn("do not name", instructions.lower())

    def test_a_failed_search_says_so_without_lines(self):
        result = investigate.investigate(FakeSearch(error=ModelError("down")), self.item)
        self.assertTrue(result.fallback)
        self.assertEqual(result.lines, [])
        self.assertIn("couldn't check", investigate.headline(result))

    def test_nothing_found_is_said_plainly(self):
        result = investigate.investigate(FakeSearch(found(spans=[])), self.item)
        self.assertIn("nothing reliable", investigate.headline(result))

    def test_other_reports_are_counted_by_hash(self):
        network = Network(":memory:", salt=b"s")
        self.addCleanup(network.close)
        network.add_case("earlier", {"upi": ["rohit.k@okaxis"]})
        upi = investigate.items_in(CHAT, "Rohan")[1]
        result = investigate.investigate(None, upi, network=network)
        self.assertEqual(result.reports, 1)
        self.assertIn("1 other person reported this UPI ID", investigate.headline(result))

    def test_a_trading_link_always_gets_the_regulator_note(self):
        result = investigate.investigate(FakeSearch(found()), self.item)
        self.assertIn("SEBI", investigate.note(result))
        place = investigate.investigate(FakeSearch(found()), investigate.clean_item("place", "Moonlit Terrace Cafe"))
        self.assertEqual(investigate.note(place), "")


if __name__ == "__main__":
    unittest.main()


class LineCheckTests(unittest.TestCase):
    def test_a_message_from_the_chat_is_checked_as_a_pattern(self):
        item = investigate.clean_item("line", "I will guide every trade and you will double it in two weeks.")
        model = FakeSearch(found())
        result = investigate.investigate(model, item)
        self.assertIn("scam pattern this matches", model.seen[0][1])
        self.assertIn("double it in two weeks", model.seen[0][1])
        self.assertIn("this message", investigate.headline(result))

    def test_a_message_check_leaves_out_its_link_and_payment_details(self):
        item = investigate.clean_item("line", "Open an account at https://goldvista-trade.com/join?ref=rohan, send 50,000 to goldvista.desk@demobank or call +91 98765 43210.")
        model = FakeSearch(found())
        investigate.investigate(model, item)
        asked = model.seen[0][1]
        self.assertNotIn("goldvista-trade.com", asked)
        self.assertNotIn("goldvista.desk@demobank", asked)
        self.assertNotIn("98765", asked)
        self.assertIn("[link]", asked)
        self.assertIn("not for any specific site", asked)
