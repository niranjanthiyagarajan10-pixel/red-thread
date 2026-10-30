import unittest

from redthread.conversation import Conversation, Turn
from redthread.quotes import find_quote

CHAT = Conversation("x", [
    Turn(0, "Riya", "Hi, I   feel like I have known you forever."),
    Turn(1, "Arjun", "Haha, that is sweet of you."),
    Turn(2, "Riya", "My uncle\u2019s trading app made me Rs 2 lakh."),
    Turn(3, "Riya", "\u092a\u0948\u0938\u0947 \u0915\u0932 \u0924\u0915 \u092d\u0947\u091c \u0926\u094b"),
    Turn(4, "Riya", "I feel like I have known you forever."),
])


class FindQuoteTests(unittest.TestCase):
    def test_exact_quote(self):
        self.assertEqual(find_quote(CHAT, "that is sweet of you"), 1)

    def test_case_spacing_and_quote_marks_do_not_matter(self):
        self.assertEqual(find_quote(CHAT, "i feel like i HAVE known you"), 0)
        self.assertEqual(find_quote(CHAT, "my uncle's trading app"), 2)

    def test_invented_quote_is_refused(self):
        self.assertIsNone(find_quote(CHAT, "send me your OTP right now"))

    def test_altered_digit_is_refused(self):
        self.assertIsNone(find_quote(CHAT, "made me Rs 3 lakh"))

    def test_abbreviated_quote_is_refused(self):
        self.assertIsNone(find_quote(CHAT, "My uncle's ... made me Rs 2 lakh"))

    def test_too_short_is_refused(self):
        self.assertIsNone(find_quote(CHAT, "Hi"))
        self.assertIsNone(find_quote(CHAT, ""))

    def test_non_latin_quote(self):
        self.assertEqual(find_quote(CHAT, "\u0915\u0932 \u0924\u0915 \u092d\u0947\u091c \u0926\u094b"), 3)

    def test_speaker_filter(self):
        self.assertEqual(find_quote(CHAT, "that is sweet of you", speaker="Arjun"), 1)
        self.assertIsNone(find_quote(CHAT, "that is sweet of you", speaker="Riya"))

    def test_near_hint_picks_the_closest_match(self):
        self.assertEqual(find_quote(CHAT, "known you forever"), 0)
        self.assertEqual(find_quote(CHAT, "known you forever", near=4), 4)

    def test_quote_cannot_span_two_turns(self):
        self.assertIsNone(find_quote(CHAT, "you forever. Haha, that is"))


if __name__ == "__main__":
    unittest.main()
