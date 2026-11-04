import json
import urllib.error
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
        status, reply = self.request(f"/api/press/{key}", raw=PNG_BYTES)
        self.assertEqual(status, 200)
        self.assertIn("hang up", reply.decode("utf-8").lower())
        status, history = self.request(f"/api/press/{key}")
        self.assertEqual(len(history["presses"]), 1)

    def test_a_short_key_is_not_found(self):
        self.assertEqual(self.request("/api/press/short", raw=b"x")[0], 404)

    def test_the_setup_page_is_served(self):
        with urllib.request.urlopen(self.base + "/press", timeout=10) as response:
            self.assertIn(b"Red Thread", response.read())


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class PressCaseRouteTests(Running):
    def key(self):
        return self.request("/api/press", {})[1]["key"]

    def call(self, path, method, value=None):
        data = None if value is None else json.dumps(value).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                return response.status, json.loads(body) if body and response.headers.get("Content-Type") == "application/json" else body
        except urllib.error.HTTPError as error:
            return error.code, None

    def test_the_shortcut_reply_has_a_notice_an_alert_and_a_later_link(self):
        key = self.key()
        status, reply = self.request(f"/api/press/{key}?for=shortcut", raw=PNG_BYTES)
        self.assertEqual(status, 200)
        self.assertEqual(sorted(reply), ["alert", "later", "notice"])
        self.assertIn("hang up", reply["notice"].lower())
        self.assertEqual((reply["alert"], reply["later"]), ("", ""))

    def test_delete_removes_every_press(self):
        key = self.key()
        self.request(f"/api/press/{key}", raw=PNG_BYTES)
        self.assertEqual(self.call(f"/api/press/{key}", "DELETE"), (200, {"deleted": 1}))
        self.assertEqual(self.request(f"/api/press/{key}")[1]["presses"], [])

    def test_a_new_link_has_no_case(self):
        self.assertEqual(self.request(f"/api/press/{self.key()}/case"), (200, {"case": None}))

    def test_a_typed_story_and_payment_are_taken(self):
        key = self.key()
        status, reply = self.request(f"/api/press/{key}/story", {"text": "They said they were CBI"})
        self.assertEqual((status, reply["heard"]), (200, "They said they were CBI"))
        status, reply = self.request(f"/api/press/{key}/paid", {"paid": True, "at": "16:58", "amount": "50,000"})
        self.assertEqual(status, 200)

    def test_notice_two_is_empty_when_there_is_nothing_to_add(self):
        self.assertEqual(self.request(f"/api/press/{self.key()}/later?wait=0")[0], 204)

    def test_a_bad_payment_time_is_refused(self):
        self.assertEqual(self.request(f"/api/press/{self.key()}/paid", {"paid": True, "at": "25:99"})[0], 400)


class HealthTests(Running):
    def test_health_says_which_key_and_models_without_the_key(self):
        self.app.key_check = {"key": "4848b83d70", "spark": False, "models": ["muse-spark-1.2"]}
        status, reply = self.request("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual((reply["ok"], reply["key"], reply["spark"]), (True, "4848b83d70", False))

    def test_health_lists_failed_reads_without_screen_text(self):
        status, reply = self.request("/healthz")
        self.assertEqual((reply["press_failures"], reply["claim_failures"]), ([], []))
        key = self.request("/api/press", {})[1]["key"]
        self.request(f"/api/press/{key}", raw=PNG_BYTES)
        failures = self.request("/healthz")[1]["press_failures"]
        self.assertEqual(sorted(failures[0]), ["at", "why"])


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
