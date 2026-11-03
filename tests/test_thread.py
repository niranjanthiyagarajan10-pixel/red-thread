import json
import unittest

from redthread import thread_demo
from redthread.model import Found, ModelError, Reply
from redthread.network import Network
from redthread.thread import MAX_CHECKS, NotAllowed, Threads, parse_chat

PNG = "data:image/png;base64," + "iVBORw0KGgo="


class Fake:
    """Answers by schema title. The whole-chat reader's schema has no title, so it is "stages"."""

    def __init__(self, **answers):
        self.answers = answers
        self.seen = []
        self.searched = []

    def chat(self, messages, schema=None, **options):
        title = (schema or {}).get("title") or "stages"
        self.seen.append(title)
        answer = self.answers[title]
        if isinstance(answer, Exception):
            raise answer
        return Reply(text=json.dumps(answer))

    def search(self, instructions, text, **options):
        self.searched.append(text)
        answer = self.answers["search"]
        if isinstance(answer, Exception):
            raise answer
        return answer


READ = {
    "lines": thread_demo.PROFILE_LINES, "name": "Rohan", "age": "31", "job": "Product manager", "city": "Bengaluru",
    "claims": [{"topic": topic, "quote": quote} for topic, quote in thread_demo.PROFILE_CLAIMS],
}
STAGES = {
    "findings": [
        {"signal": "moves_off_platform", "turn": 12, "quote": "Can we move to WhatsApp?"},
        {"signal": "fast_intimacy", "turn": 14, "quote": "I have never felt this connected to anyone"},
        {"signal": "wealth_story", "turn": 19, "quote": "I made 4.2 lakh last month on GoldVista"},
        {"signal": "investment_pitch", "turn": 21, "quote": "Open an account at https://goldvista-trade.com/join?ref=rohan and start with 50,000"},
        {"signal": "asks_money", "turn": 22, "quote": "Send the first 50,000 to goldvista.desk@demobank"},
    ],
    "contradictions": [{"topic": "home town", "first": "Grew up in Chennai.", "second": "Honestly I grew up in Pune."}],
}
BREAKS = {"breaks": [{"claim": 4, "topic": "home town", "quote": "Honestly I grew up in Pune."}]}
SEARCH = Found("Nothing about it. GoldVista Trade is not registered with SEBI.", [(18, 60, "https://www.sebi.gov.in/x")], 2, 0.1)


class Base(unittest.TestCase):
    def setUp(self):
        self.model = Fake(profile=READ, stages=STAGES, breaks=BREAKS, search=SEARCH)
        self.network = Network(":memory:", salt=b"s")
        self.example_network = Network(":memory:", salt=b"s")
        self.example_network.add_case("simulated-earlier-report", {"upi": [thread_demo.SEEDED_UPI]})
        self.addCleanup(self.network.close)
        self.addCleanup(self.example_network.close)
        self.threads = Threads(self.model, self.model, self.network, self.example_network)

    def full(self, example=True):
        thread = self.threads.create("Rohan", example=example)
        self.threads.add_profile(thread.token, images=[PNG])
        self.threads.add_chat(thread.token, thread_demo.CHAT)
        return thread


class ParseTests(unittest.TestCase):
    def test_an_export_is_read_and_the_match_is_found_by_name(self):
        chat, other = parse_chat(thread_demo.CHAT, "rohan")
        self.assertEqual(other, "Rohan")
        self.assertEqual(len(chat.turns), 25)

    def test_plain_name_colon_lines_work_too(self):
        chat, other = parse_chat("Priya: hi there\nMe: hello\nPriya: let's meet at the cafe\nnear the station", "Priya")
        self.assertEqual(other, "Priya")
        self.assertEqual([turn.speaker for turn in chat.turns], ["Priya", "Me", "Priya"])
        self.assertEqual(chat.turns[2].text, "let's meet at the cafe near the station")

    def test_without_the_name_the_busiest_other_speaker_is_the_match(self):
        _, other = parse_chat("You: hi\nAnu: hello\nAnu: how are you\nYou: good", "Priya")
        self.assertEqual(other, "Anu")

    def test_text_that_is_not_a_chat_is_refused(self):
        with self.assertRaises(NotAllowed):
            parse_chat("just some words without speakers", "Rohan")


class ThreadTests(Base):
    def test_a_whole_thread_reads_profile_chat_and_breaks(self):
        state = self.threads.state(self.full())
        self.assertEqual(state["profile"]["name"], "Rohan")
        self.assertEqual(state["reading"]["level"], "known_script")
        self.assertEqual([item["signal"] for item in state["reading"]["findings"]][:2], ["moves_off_platform", "fast_intimacy"])
        self.assertEqual(state["breaks"][0]["profile_quote"], "Grew up in Chennai")
        self.assertEqual(state["breaks"][0]["chat_quote"], "Honestly I grew up in Pune.")
        self.assertEqual(state["breaks"][0]["when"], "12 Oct")
        self.assertEqual([item["label"] for item in state["items"]], ["goldvista-trade.com", "goldvista.desk@demobank"])

    def test_breaks_wait_for_both_profile_and_chat(self):
        thread = self.threads.create("Rohan")
        self.threads.add_chat(thread.token, thread_demo.CHAT)
        self.assertEqual(self.threads.state(thread)["breaks"], [])
        self.assertNotIn("breaks", self.model.seen)
        self.threads.add_profile(thread.token, images=[PNG])
        self.assertEqual(len(self.threads.state(thread)["breaks"]), 1)

    def test_a_check_shows_sources_and_the_example_report(self):
        thread = self.full()
        upi = next(item for item in self.threads.state(thread)["items"] if item["kind"] == "upi")
        result = self.threads.check(thread.token, "upi", upi["value"])
        self.assertIn("1 other person reported this UPI ID", result["headline"])
        link = self.threads.check(thread.token, "link", "https://goldvista-trade.com/join?ref=rohan")
        self.assertEqual(link["lines"][0]["sources"][0]["host"], "sebi.gov.in")
        self.assertIn("SEBI", link["note"])

    def test_example_reports_never_reach_real_threads(self):
        thread = self.full(example=False)
        result = self.threads.check(thread.token, "upi", thread_demo.SEEDED_UPI)
        self.assertNotIn("reported", result["headline"])

    def test_checks_are_capped_per_thread(self):
        thread = self.full()
        for _ in range(MAX_CHECKS):
            self.threads.check(thread.token, "place", "Moonlit Terrace Cafe")
        with self.assertRaises(NotAllowed):
            self.threads.check(thread.token, "place", "Moonlit Terrace Cafe")

    def test_a_failed_profile_read_still_lets_the_thread_go_on(self):
        self.model.answers["profile"] = ModelError("down")
        thread = self.full()
        state = self.threads.state(thread)
        self.assertTrue(state["profile"]["fallback"])
        self.assertEqual(state["breaks"], [])
        self.assertEqual(state["reading"]["level"], "known_script")

    def test_pasted_profile_text_is_read_for_claims(self):
        thread = self.threads.create("Rohan")
        self.threads.add_profile(thread.token, text="\n".join(thread_demo.PROFILE_LINES))
        self.assertEqual(len(self.threads.state(thread)["profile"]["claims"]), 5)

    def test_bad_images_are_refused(self):
        thread = self.threads.create("Rohan")
        with self.assertRaises(NotAllowed):
            self.threads.add_profile(thread.token, images=["data:text/html;base64,PGh0bWw+"])
        with self.assertRaises(NotAllowed):
            self.threads.add_profile(thread.token, images=["not a data url"])

    def test_forget_removes_everything(self):
        thread = self.full()
        self.threads.forget(thread.token)
        self.assertIsNone(self.threads.find(thread.token))

    def test_a_secret_in_the_chat_is_never_shown(self):
        thread = self.threads.create("Rohan")
        self.threads.add_chat(thread.token, thread_demo.CHAT + "29/10/26, 10:00 - Rohan: my otp is 482913 send yours\n")
        self.assertNotIn("482913", json.dumps(self.threads.state(thread)))


if __name__ == "__main__":
    unittest.main()


MEMORY = {
    "name": "Rohan", "job": "product", "city": "Bengaluru", "names": ["Pepper", "Bijli"], "claims": [],
    "questions": [
        {"question": "Who taught you to make filter coffee?", "quote": "My paati taught me filter coffee in Mylapore", "answer": "paati"},
        {"question": "What makes your dog run and hide?", "quote": "He hides under the bed every time the pressure cooker whistles", "answer": "pressure cooker"},
        {"question": "What do you call that scooter of yours?", "quote": "My old scooter Bijli", "answer": "Bijli"},
        {"question": "Who said this?", "quote": "Ha, someone had to say it.", "answer": "me"},
    ],
}


class CallTests(Base):
    def test_a_call_needs_the_chat_first(self):
        thread = self.threads.create("Rohan")
        with self.assertRaises(NotAllowed):
            self.threads.call_memory(thread.token)

    def test_questions_come_from_the_match_and_a_second_model_bluffs_them(self):
        self.model.answers.update(memory=MEMORY, bluff={"answers": [{"id": 1, "spoken": "My paati"}]},
                                  scores={"scores": [{"id": 1, "label": "consistent"}]})
        thread = self.full(example=False)
        memory = self.threads.call_memory(thread.token)
        self.assertEqual(memory.other, "Rohan")
        self.assertNotIn("Who said this?", [item.text for item in memory.questions])
        self.assertIn("bluff", self.model.seen)
        self.assertEqual(memory.guard["tried"], 1)

    def test_the_call_shows_in_the_thread(self):
        thread = self.full()
        self.threads.set_call(thread.token, "asker-token-123")
        self.assertEqual(self.threads.state(thread)["call"], "asker-token-123")


class ExampleLabelTests(Base):
    def test_an_example_report_says_it_is_part_of_the_example(self):
        thread = self.full()
        result = self.threads.check(thread.token, "upi", thread_demo.SEEDED_UPI)
        self.assertIn("part of the example", result["note"])
