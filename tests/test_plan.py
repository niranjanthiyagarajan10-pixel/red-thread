import json
import unittest

from redthread import plan, thread_demo
from redthread.model import ModelError, Reply
from redthread.thread import parse_chat

CHAT, OTHER = parse_chat(thread_demo.CHAT, "Rohan")
READ = {
    "venue": "Moonlit Terrace Cafe",
    "venue_quote": "Let's finally meet at Moonlit Terrace Cafe in Bandra",
    "signals": [
        {"kind": "they_picked", "quote": "Let's finally meet at Moonlit Terrace Cafe in Bandra"},
        {"kind": "knows_owner", "quote": "my friend runs it"},
        {"kind": "they_picked", "quote": "I will book the table"},
        {"kind": "come_alone", "quote": "Come alone, I hate crowds"},
        {"kind": "keep_quiet", "quote": "Let me think about it."},
    ],
}


class Fake:
    def __init__(self, answer):
        self.answer = answer
        self.seen = []

    def chat(self, messages, schema=None, **options):
        self.seen.append((messages, schema))
        if isinstance(self.answer, Exception):
            raise self.answer
        return Reply(text=json.dumps(self.answer))


class PlanTests(unittest.TestCase):
    def test_the_venue_and_its_tripwires_come_with_real_quotes(self):
        result = plan.read_plan(Fake(READ), CHAT, OTHER)
        self.assertEqual(result.venue, "Moonlit Terrace Cafe")
        self.assertEqual([item.kind for item in result.tripwires], ["they_picked", "knows_owner"])
        self.assertEqual(result.tripwires[0].when, "28 Oct")

    def test_a_made_up_quote_never_shows(self):
        result = plan.read_plan(Fake(READ), CHAT, OTHER)
        self.assertNotIn("come_alone", [item.kind for item in result.tripwires])

    def test_words_from_the_person_checking_never_count(self):
        result = plan.read_plan(Fake(READ), CHAT, OTHER)
        self.assertNotIn("keep_quiet", [item.kind for item in result.tripwires])

    def test_a_venue_that_is_not_in_the_chat_is_dropped(self):
        result = plan.read_plan(Fake(dict(READ, venue="Blue Lagoon Bar")), CHAT, OTHER)
        self.assertEqual(result.venue, "")

    def test_labels_and_advice_come_from_code(self):
        result = plan.read_plan(Fake(READ), CHAT, OTHER)
        self.assertEqual(result.tripwires[1].label, plan.LABELS["knows_owner"])
        self.assertEqual(result.headline, "2 things about this plan to check.")
        self.assertTrue(result.advice)

    def test_a_chat_with_no_plan(self):
        result = plan.read_plan(Fake({"venue": "", "venue_quote": "", "signals": []}), CHAT, OTHER)
        self.assertEqual(result.headline, "No date plan in this chat yet.")

    def test_a_failed_read_says_so(self):
        result = plan.read_plan(Fake(ModelError("down")), CHAT, OTHER)
        self.assertTrue(result.fallback)
        self.assertEqual(result.tripwires, [])

    def test_the_chat_is_data_not_instructions(self):
        model = Fake(READ)
        plan.read_plan(model, CHAT, OTHER)
        messages = model.seen[0][0]
        self.assertEqual(messages[0]["role"], "developer")
        self.assertNotIn("Moonlit", messages[0]["content"])

    def test_the_note_for_a_friend_is_written_by_code(self):
        result = plan.read_plan(Fake(READ), CHAT, OTHER)
        note = plan.friend_note(result, "Rohan")
        self.assertIn("Rohan", note)
        self.assertIn("Moonlit Terrace Cafe", note)
        self.assertNotIn("—", note)


if __name__ == "__main__":
    unittest.main()
