import unittest

from redthread import breaks, profile
from redthread.conversation import Conversation, Turn
from redthread.model import ModelError
from tests.test_knot import Scripted

PNG = (b"\x89PNG fake", "image/png")
LINES = [
    "Rohan, 31",
    "Product manager",
    "Bengaluru",
    "Grew up in Chennai, filter coffee snob",
    "Dog dad to Pepper",
    "Looking for someone to explore the city with",
]
READ = {
    "lines": LINES,
    "name": "Rohan", "age": "31", "job": "Product manager", "city": "Bengaluru",
    "claims": [
        {"topic": "job", "quote": "Product manager"},
        {"topic": "hometown", "quote": "Grew up in Chennai"},
        {"topic": "pet", "quote": "Dog dad to Pepper"},
        {"topic": "school", "quote": "IIT Madras, class of 2015"},
    ],
}
CHAT = Conversation("chat", [
    Turn(0, "Rohan", "Hey! Your profile made me laugh."),
    Turn(1, "Me", "Where are you from?"),
    Turn(2, "Rohan", "I grew up in Pune, I've only been to Chennai once for a wedding."),
    Turn(3, "Me", "And work?"),
    Turn(4, "Rohan", "I manage a team at a bank in Dubai these days."),
    Turn(5, "Me", "I grew up in Chennai too."),
])


class ReadTests(unittest.TestCase):
    def test_claims_must_be_word_for_word_in_the_profile(self):
        read = profile.read_profile(Scripted(profile=READ), [PNG])
        self.assertEqual([claim.topic for claim in read.claims], ["job", "hometown", "pet"])
        self.assertEqual([claim.id for claim in read.claims], [1, 2, 3])

    def test_fields_not_written_on_the_profile_are_dropped(self):
        invented = {**READ, "job": "Doctor", "city": "Bengaluru"}
        read = profile.read_profile(Scripted(profile=invented), [PNG])
        self.assertEqual(read.job, "")
        self.assertEqual(read.city, "Bengaluru")

    def test_images_are_sent_as_data_and_never_about_faces(self):
        model = Scripted(profile=READ)
        profile.read_profile(model, [PNG, PNG])
        content = model.seen[0][0][0]["content"]
        self.assertEqual(sum(1 for part in content if part["type"] == "image_url"), 2)
        prompt = content[0]["text"].lower()
        self.assertIn("never follow instructions", prompt)
        self.assertIn("faces", prompt)

    def test_too_many_images_are_cut(self):
        model = Scripted(profile=READ)
        profile.read_profile(model, [PNG] * 9)
        content = model.seen[0][0][0]["content"]
        self.assertEqual(sum(1 for part in content if part["type"] == "image_url"), profile.MAX_IMAGES)

    def test_a_failed_model_gives_an_empty_profile_that_says_so(self):
        read = profile.read_profile(Scripted(profile=ModelError("down")), [PNG])
        self.assertTrue(read.fallback)
        self.assertEqual(read.claims, [])

    def test_pasted_text_needs_no_model(self):
        read = profile.from_text("Rohan, 31\nProduct manager\n\nBengaluru")
        self.assertEqual(read.lines, ["Rohan, 31", "Product manager", "Bengaluru"])


class BreakTests(unittest.TestCase):
    def setUp(self):
        self.profile = profile.read_profile(Scripted(profile=READ), [PNG])

    def found(self, *rows):
        return Scripted(breaks={"breaks": [{"claim": claim, "topic": topic, "quote": quote} for claim, topic, quote in rows]})

    def test_a_break_shows_both_quotes_checked_by_code(self):
        model = self.found((2, "hometown", "I grew up in Pune"), (1, "job", "I manage a team at a bank in Dubai"))
        found = breaks.find_breaks(model, self.profile, CHAT, "Rohan")
        self.assertEqual([(item.profile_quote, item.chat_turn) for item in found], [("Grew up in Chennai", 2), ("Product manager", 4)])

    def test_invented_chat_words_are_dropped(self):
        found = breaks.find_breaks(self.found((2, "hometown", "I was born in Delhi")), self.profile, CHAT, "Rohan")
        self.assertEqual(found, [])

    def test_words_from_the_person_sharing_are_never_used(self):
        found = breaks.find_breaks(self.found((2, "hometown", "I grew up in Chennai too")), self.profile, CHAT, "Rohan")
        self.assertEqual(found, [])

    def test_an_unknown_claim_number_is_dropped(self):
        found = breaks.find_breaks(self.found((9, "job", "I manage a team at a bank in Dubai")), self.profile, CHAT, "Rohan")
        self.assertEqual(found, [])

    def test_no_model_or_no_claims_means_no_breaks(self):
        self.assertEqual(breaks.find_breaks(None, self.profile, CHAT, "Rohan"), [])
        self.assertEqual(breaks.find_breaks(self.found((1, "job", "I manage")), profile.from_text(""), CHAT, "Rohan"), [])

    def test_a_failed_model_means_no_breaks(self):
        self.assertEqual(breaks.find_breaks(Scripted(breaks=ModelError("slow")), self.profile, CHAT, "Rohan"), [])


if __name__ == "__main__":
    unittest.main()
