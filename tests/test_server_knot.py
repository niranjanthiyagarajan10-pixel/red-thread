import json
import time

from redthread import server
from tests.test_server_proof import Running

LONG = "I support Kerala Blasters and I watched the final at my uncle's house in Kochi with my cousin Anu"


class KnotRouteTests(Running):
    def start(self, interests=("Football", "Filter coffee", "Ilaiyaraaja songs")):
        status, reply = self.request("/api/knot", {"interests": list(interests)})
        self.assertEqual(status, 200)
        return reply["token"], reply["state"]

    def events(self, token):
        return [row["type"] for row in self.request(f"/api/knot/{token}/events?after=0")[1]["events"]]

    def test_a_whole_knot_ties_with_no_keys_by_code_alone(self):
        token, state = self.start()
        self.assertEqual(len(state["questions"]), 3)
        self.assertNotIn("phrase", state)
        self.assertEqual(self.request(f"/api/knot/{token}/begin", {})[0], 200)
        phrase = self.request(f"/api/knot/{token}/state")[1]["phrase"]
        self.request(f"/api/knot/{token}/say", {"text": phrase})
        for _ in range(3):
            self.request(f"/api/knot/{token}/say", {"text": LONG})
            self.request(f"/api/knot/{token}/next", {})
        for _ in range(50):
            if "outcome" in self.events(token):
                break
            time.sleep(0.02)
        rows = self.request(f"/api/knot/{token}/events?after=0")[1]["events"]
        outcome = next(row for row in rows if row["type"] == "outcome")
        self.assertTrue(outcome["tied"])
        status, card = self.request(f"/api/card/{outcome['card']}")
        self.assertEqual(status, 200)
        self.assertTrue(card["tied"])
        self.assertNotIn("Kochi", json.dumps(card))

    def test_no_interests_is_a_clear_error(self):
        status, reply = self.request("/api/knot", {"interests": ["  "]})
        self.assertEqual(status, 400)
        self.assertIn("interest", reply["error"])

    def test_unknown_tokens_are_not_found(self):
        self.assertEqual(self.request("/api/knot/nope-nope-nope/state")[0], 404)
        self.assertEqual(self.request("/api/card/nope-nope-nope")[0], 404)

    def test_audio_before_starting_is_refused(self):
        token, _ = self.start()
        self.assertEqual(self.request(f"/api/knot/{token}/audio", raw=b"\0\0")[0], 409)

    def test_a_reask_needs_a_tied_knot(self):
        token, _ = self.start()
        status, reply = self.request(f"/api/knot/{token}/reask", {})
        self.assertEqual(status, 403)


class PageTests(Running):
    def get(self, path):
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, response.headers.get("Content-Type"), response.read()
        except urllib.error.HTTPError as error:
            return error.code, None, b""

    def test_the_pages_are_served(self):
        for path in ("/", "/knot", "/card/abcdefghijkl"):
            status, kind, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn("text/html", kind)
            self.assertIn(b"Red Thread", body)

    def test_static_files_are_served_by_name_only(self):
        status, kind, _ = self.get("/static/app.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", kind)
        self.assertEqual(self.get("/static/../server.py")[0], 404)
        self.assertEqual(self.get("/static/%2e%2e/server.py")[0], 404)

    def test_no_dash_characters_in_any_page(self):
        for name in server.WEB.rglob("*"):
            if name.is_file() and name.suffix in (".html", ".css", ".js"):
                text = name.read_text(encoding="utf-8")
                self.assertNotIn("—", text, name)
                self.assertNotIn("–", text, name)


class LogTests(Running):
    def test_tokens_are_hidden_in_logs(self):
        for path in ("/api/knot/abcdefghijklmnop/state", "/api/card/abcdefghijklmnop", "/card/abcdefghijklmnop", "/proof/abcdefghijklmnop"):
            self.assertNotIn("abcdefghijklmnop", server._TOKEN_IN_PATH.sub(r"\1*", path))


class QuietTimeoutTests(Running):
    def test_logging_a_request_that_never_arrived_does_not_fail(self):
        handler = server._handler(self.app).__new__(server._handler(self.app))
        handler.client_address = ("127.0.0.1", 1)
        handler.log_message("Request timed out: %r", "x")
