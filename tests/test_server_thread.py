from redthread import thread_demo
from tests.test_server_proof import Running


class ThreadRouteTests(Running):
    def create(self, name="Rohan", example=False):
        status, reply = self.request("/api/thread", {"name": name, "example": example})
        self.assertEqual(status, 200)
        return reply["token"]

    def test_a_thread_reads_a_chat_with_no_keys_by_rules_alone(self):
        token = self.create()
        status, reply = self.request(f"/api/thread/{token}/chat", {"text": thread_demo.CHAT})
        self.assertEqual(status, 200)
        reading = reply["state"]["reading"]
        self.assertTrue(reading["fallback"])
        self.assertEqual(reading["level"], "early_signs", "rules alone miss an ask with no money word, Spark catches it")
        self.assertEqual(reply["state"]["chat"]["other"], "Rohan")
        self.assertIn("goldvista-trade.com", [item["label"] for item in reply["state"]["items"]])

    def test_pasted_profile_text_is_kept_as_lines(self):
        token = self.create()
        status, reply = self.request(f"/api/thread/{token}/profile", {"text": "\n".join(thread_demo.PROFILE_LINES)})
        self.assertEqual(status, 200)
        self.assertEqual(reply["state"]["profile"]["lines"][0], "Rohan, 31")

    def test_the_example_thread_finds_the_simulated_earlier_report(self):
        token = self.create(example=True)
        self.request(f"/api/thread/{token}/chat", {"text": thread_demo.CHAT})
        status, reply = self.request(f"/api/thread/{token}/check", {"kind": "upi", "value": thread_demo.SEEDED_UPI})
        self.assertEqual(status, 200)
        self.assertIn("1 other person reported", reply["check"]["headline"])

    def test_a_real_thread_never_sees_example_reports(self):
        token = self.create()
        status, reply = self.request(f"/api/thread/{token}/check", {"kind": "upi", "value": thread_demo.SEEDED_UPI})
        self.assertEqual(status, 200)
        self.assertNotIn("reported", reply["check"]["headline"])

    def test_bad_input_is_a_clear_error(self):
        token = self.create()
        status, reply = self.request(f"/api/thread/{token}/chat", {"text": "no speakers here"})
        self.assertEqual(status, 400)
        self.assertIn("chat", reply["error"])
        status, reply = self.request(f"/api/thread/{token}/profile", {"images": ["data:text/html;base64,PGh0bWw+"]})
        self.assertEqual(status, 400)
        status, reply = self.request(f"/api/thread/{token}/check", {"kind": "weather", "value": "x"})
        self.assertEqual(status, 400)

    def test_forget_removes_the_thread(self):
        token = self.create()
        self.assertEqual(self.request(f"/api/thread/{token}/forget", {})[0], 200)
        self.assertEqual(self.request(f"/api/thread/{token}")[0], 404)

    def test_unknown_threads_are_not_found(self):
        self.assertEqual(self.request("/api/thread/nope-nope-nope")[0], 404)
        self.assertEqual(self.request("/api/thread/nope-nope-nope/chat", {"text": "a: b"})[0], 404)

    def test_the_match_page_is_served(self):
        import urllib.request
        with urllib.request.urlopen(self.base + "/match", timeout=10) as response:
            self.assertIn(b"Red Thread", response.read())
