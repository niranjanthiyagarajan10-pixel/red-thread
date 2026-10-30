import sqlite3
import tempfile
import unittest
from pathlib import Path

from redthread.network import Network, fingerprint, similarity

SCRIPT = (
    "My uncle runs a private trading desk and he showed me how to double my savings in a month. "
    "I never tell anyone this but you feel different. Open an account on the platform and start small."
)
REWORDED_COPY = (
    "Hi dear. My uncle runs a private trading desk and he showed me how to double my savings in a month. "
    "Open an account on the platform and start small."
)
UNRELATED = "We went to the beach on Sunday and the food at the small cafe near the station was really good."


class FingerprintTests(unittest.TestCase):
    def test_copied_script_matches_and_unrelated_text_does_not(self):
        self.assertGreater(similarity(fingerprint(SCRIPT), fingerprint(REWORDED_COPY)), 0.5)
        self.assertLess(similarity(fingerprint(SCRIPT), fingerprint(UNRELATED)), 0.1)

    def test_short_text_has_no_fingerprint(self):
        self.assertEqual(fingerprint("hello there"), set())
        self.assertEqual(similarity(set(), set()), 0.0)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "network.sqlite"
        self.network = Network(self.path, salt=b"test-salt")
        self.addCleanup(self.network.close)

    def test_same_upi_links_two_cases(self):
        self.network.add_case("a", {"upi": ["Scam@ybl"]})
        matches = self.network.matches("b", {"upi": ["scam@ybl"]})
        self.assertEqual(matches[0].case_id, "a")
        self.assertEqual(matches[0].kinds, ["upi"])

    def test_own_case_never_matches_itself(self):
        self.network.add_case("a", {"upi": ["scam@ybl"]})
        self.assertEqual(self.network.matches("a", {"upi": ["scam@ybl"]}), [])

    def test_no_match(self):
        self.network.add_case("a", {"phone": ["9876543210"]})
        self.assertEqual(self.network.matches("b", {"phone": ["9876543211"]}), [])

    def test_script_match_across_cases(self):
        self.network.add_case("a", {}, script_text=SCRIPT)
        matches = self.network.matches("b", {}, script_text=REWORDED_COPY)
        self.assertEqual(matches[0].kinds, ["script"])

    def test_exact_image_match(self):
        self.network.add_case("a", {}, images=[b"same photo bytes"])
        self.assertEqual(self.network.matches("b", {}, images=[b"same photo bytes"])[0].kinds, ["image"])
        self.assertEqual(self.network.matches("b", {}, images=[b"other bytes"]), [])

    def test_report_count_gives_a_number_and_nothing_else(self):
        self.network.add_case("a", {"upi": ["scam@ybl"]})
        self.network.add_case("c", {"upi": ["scam@ybl"]})
        self.assertEqual(self.network.report_count("b", {"upi": ["scam@ybl"]}), 2)

    def test_raw_identifiers_and_scripts_are_never_stored(self):
        self.network.add_case("a", {"upi": ["scam@ybl"], "phone": ["9876543210"]}, script_text=SCRIPT)
        self.network.close()
        raw = self.path.read_bytes()
        self.assertNotIn(b"scam@ybl", raw)
        self.assertNotIn(b"9876543210", raw)
        self.assertNotIn(b"trading desk", raw)
        connection = sqlite3.connect(self.path)
        self.addCleanup(connection.close)
        self.assertGreater(connection.execute("select count(*) from identifiers").fetchone()[0], 0)

    def test_different_salt_cannot_match(self):
        self.network.add_case("a", {"upi": ["scam@ybl"]})
        other = Network(Path(self.tmp.name) / "other.sqlite", salt=b"another")
        self.addCleanup(other.close)
        other.add_case("a", {"upi": ["x@ybl"]})
        self.assertEqual(other.matches("b", {"upi": ["scam@ybl"]}), [])


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.network = Network(":memory:", salt=b"s")
        self.addCleanup(self.network.close)

    def test_both_must_say_yes(self):
        self.assertFalse(self.network.linked("a", "b"))
        self.network.opt_in("a", "b")
        self.assertFalse(self.network.linked("a", "b"))
        self.network.opt_in("b", "a")
        self.assertTrue(self.network.linked("a", "b"))
        self.assertTrue(self.network.linked("b", "a"))

    def test_no_relay_without_both_opting_in(self):
        self.network.opt_in("a", "b")
        result = self.network.relay("a", "b", "I lost money too")
        self.assertFalse(result.delivered)
        self.assertEqual(result.reason, "not_linked")

    def test_relay_translates_and_keeps_the_original(self):
        self.network.opt_in("a", "b")
        self.network.opt_in("b", "a")
        result = self.network.relay("a", "b", "naanum panam izhandhen", translate=lambda text: "I lost money too")
        self.assertTrue(result.delivered)
        self.assertEqual(result.text, "I lost money too")
        self.assertEqual(result.original, "naanum panam izhandhen")

    def test_failed_translation_still_delivers_the_original(self):
        self.network.opt_in("a", "b")
        self.network.opt_in("b", "a")

        def broken(_):
            raise RuntimeError("down")

        result = self.network.relay("a", "b", "I lost money too", translate=broken)
        self.assertTrue(result.delivered)
        self.assertEqual(result.text, "I lost money too")

    def test_recovery_offer_is_blocked_even_when_linked(self):
        self.network.opt_in("a", "b")
        self.network.opt_in("b", "a")
        result = self.network.relay("a", "b", "I know a recovery agent, call 9876543210")
        self.assertFalse(result.delivered)
        self.assertIsNone(result.text)
        self.assertEqual(result.reason, "recovery_offer")


if __name__ == "__main__":
    unittest.main()
