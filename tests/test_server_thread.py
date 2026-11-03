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

    def test_the_example_match_starts_a_simulated_call_at_once(self):
        token = self.create(example=True)
        self.request(f"/api/thread/{token}/chat", {"text": thread_demo.CHAT})
        status, reply = self.request(f"/api/thread/{token}/call", {})
        self.assertEqual(status, 200)
        status, state = self.request(f"/api/proof/{reply['token']}/state")
        self.assertTrue(state["simulated"])
        self.assertEqual(state["other"], "Rohan")
        self.assertEqual(self.request(f"/api/thread/{token}")[1]["call"], reply["token"])

    def test_a_real_match_call_is_built_from_its_own_chat(self):
        token = self.create()
        self.assertEqual(self.request(f"/api/thread/{token}/call", {})[0], 400, "no chat yet")
        self.request(f"/api/thread/{token}/chat", {"text": thread_demo.CHAT})
        status, reply = self.request(f"/api/thread/{token}/call", {})
        self.assertEqual(status, 200)
        state = self.request(f"/api/proof/{reply['token']}/state")[1]
        self.assertFalse(state["simulated"])
        self.assertGreaterEqual(len(state["questions"]), 3)

    def test_the_call_page_is_served(self):
        import urllib.request
        token = self.create(example=True)
        self.request(f"/api/thread/{token}/chat", {"text": thread_demo.CHAT})
        call = self.request(f"/api/thread/{token}/call", {})[1]["token"]
        with urllib.request.urlopen(f"{self.base}/proof/{call}", timeout=10) as response:
            self.assertIn(b"Red Thread", response.read())

    def test_the_date_and_the_table_answer_calmly_with_no_keys(self):
        token = self.create()
        self.request(f"/api/thread/{token}/chat", {"text": thread_demo.CHAT})
        status, reply = self.request(f"/api/thread/{token}/plan", {})
        self.assertEqual(status, 200)
        self.assertTrue(reply["state"]["plan"]["fallback"])
        image = "data:image/png;base64,iVBORw0KGgo="
        status, reply = self.request(f"/api/thread/{token}/bill", {"image": image})
        self.assertEqual(status, 200)
        self.assertEqual(reply["state"]["table"]["check"]["level"], "unread")
        self.assertEqual(self.request(f"/api/thread/{token}/menu", {"image": "not an image"})[0], 400)

    def test_the_example_photos_are_served(self):
        import urllib.request
        example = self.request("/api/example")[1]
        for key in ("menu_image", "bill_image"):
            with urllib.request.urlopen(self.base + example[key], timeout=10) as response:
                self.assertEqual(response.headers["Content-Type"], "image/jpeg")

    def test_the_lab_page_and_its_results_are_served(self):
        import json
        import urllib.request
        with urllib.request.urlopen(self.base + "/lab", timeout=10) as response:
            self.assertIn(b"attack our own checks", response.read())
        with urllib.request.urlopen(self.base + "/static/lab.json", timeout=10) as response:
            self.assertEqual(response.headers["Content-Type"], "application/json")
            results = json.loads(response.read())
        self.assertIn("summary", results["bills"])

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
