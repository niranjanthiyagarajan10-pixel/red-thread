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


class PressRouteTests(Running):
    def test_a_new_key_is_long_enough(self):
        status, reply = self.request("/api/press", {})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(reply["key"]), 22)

    def test_a_press_with_no_model_still_answers_as_plain_text(self):
        key = self.request("/api/press", {})[1]["key"]
        status, reply = self.request(f"/api/press/{key}", raw=b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        self.assertEqual(status, 200)
        self.assertIn("hang up", reply.decode("utf-8").lower())
        status, history = self.request(f"/api/press/{key}")
        self.assertEqual(len(history["presses"]), 1)

    def test_a_short_key_is_not_found(self):
        self.assertEqual(self.request("/api/press/short", raw=b"x")[0], 404)

    def test_the_setup_page_is_served(self):
        with urllib.request.urlopen(self.base + "/press", timeout=10) as response:
            self.assertIn(b"Red Thread", response.read())


class HealthTests(Running):
    def test_health_says_which_key_and_models_without_the_key(self):
        self.app.key_check = {"key": "4848b83d70", "spark": False, "models": ["muse-spark-1.2"]}
        status, reply = self.request("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual((reply["ok"], reply["key"], reply["spark"]), (True, "4848b83d70", False))


class ProxyTests(Running):
    def test_behind_a_proxy_each_visitor_gets_their_own_limit(self):
        self.app.trust_proxy, self.app.rate_limit = True, 1
        def press(address):
            request = urllib.request.Request(self.base + "/api/press", data=b"{}", method="POST", headers={"X-Forwarded-For": address})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
        self.assertEqual(press("1.1.1.1"), 200)
        self.assertEqual(press("2.2.2.2"), 200)
        self.assertEqual(press("1.1.1.1"), 429)
