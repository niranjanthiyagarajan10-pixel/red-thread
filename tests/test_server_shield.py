import urllib.request

from tests.test_server_proof import Running


class ShieldRouteTests(Running):
    def test_the_shield_page_is_served(self):
        with urllib.request.urlopen(self.base + "/shield", timeout=10) as response:
            self.assertIn(b"Red Thread", response.read())

    def test_listen_mode_starts_and_stops_with_no_keys(self):
        status, reply = self.request("/api/listen", {})
        self.assertEqual(status, 200)
        token = reply["token"]
        self.assertEqual(self.request(f"/api/listen/{token}/start", {})[0], 200)
        status, events = self.request(f"/api/listen/{token}/events?after=0")
        self.assertEqual([row["type"] for row in events["events"]], ["live", "captions_off"])
        status, reply = self.request(f"/api/listen/{token}/stop", {})
        self.assertEqual((status, reply["said"]), (200, ""))

    def test_unknown_listen_sessions_are_not_found(self):
        self.assertEqual(self.request("/api/listen/nope-nope-nope/start", {})[0], 404)
        self.assertEqual(self.request("/api/listen/nope-nope-nope/events")[0], 404)

    def test_the_chat_answers_a_live_call_with_no_keys(self):
        status, reply = self.request("/api/message", {"session": "shield-test", "text": "CBI is on a video call, they say digital arrest"})
        self.assertEqual(status, 200)
        self.assertIn("1930", reply["answer"]["next_step"])
