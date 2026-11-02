import json
import tempfile
import unittest
from pathlib import Path

from redthread.model import WONDER, Ledger, MetaModel, ModelError
from tests.test_model import KEY, FakeTransport

TEXT = "I'll look this up. Police raided a cafe in March. The site is not registered with SEBI."


def searched(text=TEXT, citations=((19, 46, "https://news.example/raid"),), searches=2, status=200):
    annotations = [{"type": "url_citation", "start_index": start, "end_index": end, "url": url, "title": "t"} for start, end, url in citations]
    body = {
        "output": [{"type": "web_search_call", "id": f"ws{n}"} for n in range(searches)] + [
            {"type": "message", "content": [{"type": "output_text", "text": text, "annotations": annotations}]},
        ],
        "usage": {"input_tokens": 10000, "output_tokens": 500, "output_tokens_details": {"reasoning_tokens": 100}},
    }
    return status, json.dumps(body).encode("utf-8")


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def model(self, *replies, cap=1.0):
        self.transport = FakeTransport(*replies)
        self.ledger = Ledger(cap, Path(self.tmp.name) / "ledger.json")
        return MetaModel(KEY, self.ledger, model=WONDER, transport=self.transport, sleep=lambda _: None)

    def test_request_shape_uses_responses_with_web_search(self):
        model = self.model(searched())
        model.search("Check this", "goldvista-trade.com")
        url, _, body = self.transport.calls[0]
        self.assertEqual(url, "https://api.meta.ai/v1/responses")
        self.assertEqual(body["model"], WONDER)
        self.assertEqual(body["tools"], [{"type": "web_search"}])
        self.assertEqual(body["input"][0]["role"], "developer")
        self.assertEqual(body["input"][1]["content"], "goldvista-trade.com")

    def test_text_citations_and_searches_are_read(self):
        found = self.model(searched()).search("Check this", "x")
        self.assertEqual(found.text, TEXT)
        self.assertEqual(found.citations, [(19, 46, "https://news.example/raid")])
        self.assertEqual(found.searches, 2)

    def test_cost_counts_tokens_and_each_search(self):
        found = self.model(searched()).search("Check this", "x")
        expected = (10000 * 4.0 + 500 * 20.0) / 1_000_000 + 2 * 0.0025
        self.assertAlmostEqual(found.cost, expected)
        self.assertAlmostEqual(self.ledger.spent, expected)

    def test_a_failed_search_raises_and_costs_nothing(self):
        model = self.model(searched(status=400))
        with self.assertRaises(ModelError):
            model.search("Check this", "x")
        self.assertEqual(self.ledger.spent, 0.0)

    def test_an_odd_reply_raises(self):
        model = self.model((200, b'{"output": "nope"}'))
        with self.assertRaises(ModelError):
            model.search("Check this", "x")


if __name__ == "__main__":
    unittest.main()
