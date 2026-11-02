import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from redthread import proof, proof_demo, server
from redthread.engine import Engine, Incoming
from redthread.network import Network
from redthread.proof_call import Calls
from redthread.server import App, make_server
from redthread.whatsapp import Inbound, payloads_for
from tests.test_proof import GOOD, Scripted
from tests.test_proof_call import Inline

EXPORT = "\n".join([
    "01/11/26, 10:00 - Arjun: I moved to Bengaluru in March for my job at the hospital.",
    "01/11/26, 10:01 - Me: Which one?",
    "01/11/26, 10:02 - Arjun: Manipal, in cardiology. My sister Kavya still lives in Pune.",
    "01/11/26, 10:03 - Me: Do you cook?",
    "01/11/26, 10:04 - Arjun: Only my mother's lemon rice, I made it on Tuesday and burnt the peanuts.",
    "01/11/26, 10:05 - Me: What about the cat?",
    "01/11/26, 10:06 - Arjun: Biscuit knocked over my tea again, he sleeps on my laptop.",
    "01/11/26, 10:07 - Arjun: We should walk at Cubbon Park on Saturday, I like the mornings there.",
])


class Running(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = App.build(Path(tmp.name), None, None, "secret", "verify", b"salt", public_url="https://example.test")
        self.addCleanup(self.app.close)
        self.http = make_server(self.app, 0, "127.0.0.1")
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"

    def request(self, path, value=None, raw=None):
        data = raw if raw is not None else None if value is None else json.dumps(value).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                return response.status, json.loads(body) if response.headers.get("Content-Type") == "application/json" else body
        except urllib.error.HTTPError as error:
            body = error.read()
            return error.code, json.loads(body) if body else None

    def demo(self):
        status, reply = self.request("/api/proof/demo", {})
        self.assertEqual(status, 200)
        return reply["token"]


class DemoCallTests(Running):
    def test_a_judge_can_run_a_whole_simulated_call_with_no_keys(self):
        token = self.demo()
        status, state = self.request(f"/api/proof/{token}/state")
        self.assertEqual(status, 200)
        self.assertTrue(state["simulated"])
        self.assertEqual(state["phase"], "waiting")
        self.assertEqual(len(state["questions"]), 5)
        self.assertEqual(self.request(f"/api/proof/{token}/consent", {})[0], 200)
        self.assertEqual(self.request(f"/api/proof/{token}/ask", {"question": 1})[0], 200)
        self.assertEqual(self.request(f"/api/proof/{token}/play", {"question": 1})[0], 200)
        self.assertEqual(self.request(f"/api/proof/{token}/finish", {})[0], 200)
        status, reply = self.request(f"/api/proof/{token}/events?after=0")
        kinds = [row["type"] for row in reply["events"]]
        for wanted in ("live", "asked", "caption", "answer", "receipt"):
            self.assertIn(wanted, kinds)
        receipt = next(row for row in reply["events"] if row["type"] == "receipt")
        self.assertIn(receipt["person"], ("writer", "stand_in"))
        self.assertEqual(reply["next"], len(reply["events"]))

    def test_a_money_ask_can_be_played_and_fires_the_rule(self):
        token = self.demo()
        self.request(f"/api/proof/{token}/consent", {})
        self.assertEqual(self.request(f"/api/proof/{token}/play", {"line": "money"})[0], 200)
        _, reply = self.request(f"/api/proof/{token}/events?after=0")
        self.assertIn("alarm", [row["type"] for row in reply["events"]])

    def test_a_clip_that_was_not_recorded_is_just_absent(self):
        token = self.demo()
        self.assertEqual(self.request(f"/api/proof/{token}/clip/1")[0], 404)


class SafetyTests(Running):
    def test_a_wrong_token_gets_nothing(self):
        for path in ("state", "events?after=0"):
            self.assertEqual(self.request(f"/api/proof/wrongtoken123/{path}")[0], 404)
        self.assertEqual(self.request("/api/proof/wrongtoken123/consent", {})[0], 404)

    def test_audio_before_consent_is_refused(self):
        token = self.demo()
        self.assertEqual(self.request(f"/api/proof/{token}/audio", raw=b"\0\0")[0], 409)

    def test_a_bad_question_is_a_clean_refusal(self):
        token = self.demo()
        self.request(f"/api/proof/{token}/consent", {})
        self.assertEqual(self.request(f"/api/proof/{token}/ask", {"question": 99})[0], 403)
        self.assertEqual(self.request(f"/api/proof/{token}/ask", {"question": "x"})[0], 400)

    def test_a_huge_signal_is_refused(self):
        token = self.demo()
        self.assertEqual(self.request(f"/api/proof/{token}/signal", {"sdp": "x" * 70000})[0], 413)

    def test_creating_calls_is_rate_limited(self):
        self.app.rate_limit = 1
        self.assertEqual(self.request("/api/proof/demo", {})[0], 200)
        self.assertEqual(self.request("/api/proof/demo", {})[0], 429)

    def test_tokens_stay_out_of_the_log(self):
        token = self.demo()
        with mock.patch("sys.stderr") as log:
            self.request(f"/api/proof/{token}/state")
        written = " ".join(str(call) for call in log.write.call_args_list)
        self.assertNotIn(token, written)


class ModelCacheTests(unittest.TestCase):
    def test_the_service_never_writes_model_replies_to_disk(self):
        """Replies can hold quotes from the other person's chat, and delete my data could not reach them."""
        with tempfile.TemporaryDirectory() as folder:
            app = server.from_environment({"REDTHREAD_DATA": folder, "MODEL_API_KEY": "k"})
            cache_dir = app.engine.model.cache_dir
            app.close()
        self.assertIsNone(cache_dir)


class FakeClient:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)


class ReceiptDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.client = FakeClient()
        self.app = App.build(Path(self.folder.name), None, self.client, "secret", "verify", b"salt", inline_webhook=True)
        self.addCleanup(self.app.close)

    def text(self, number):
        return Inbound(id="m1", sender=number, kind="text", text="hello")

    def test_the_receipt_reaches_the_person_who_started_the_call(self):
        self.app.channel.receive(self.text("919"))
        user = self.app.channel._user("919")
        self.client.sent.clear()
        call = self.app.engine.calls.create(proof_demo.memory(), owner=user)
        call.finish()
        self.assertEqual(self.client.sent[0]["to"], "919")
        self.assertIn("did not join", self.client.sent[0]["text"]["body"])

    def test_a_judge_call_never_messages_anyone(self):
        self.app.channel.receive(self.text("919"))
        self.client.sent.clear()
        self.app.demo_call()
        call = next(iter(self.app.judge_engine.calls._calls.values()))[0]
        call.finish()
        self.assertEqual(self.client.sent, [])

    def test_the_phone_number_is_not_written_to_disk(self):
        self.app.channel.receive(self.text("919000111222"))
        self.app.store.close()
        for path in Path(self.folder.name).glob("*.sqlite*"):
            self.assertNotIn(b"919000111222", path.read_bytes())


class EngineTests(unittest.TestCase):
    def engine(self, model=None):
        network = Network(":memory:", salt=b"salt")
        self.addCleanup(network.close)
        engine = Engine(network, model=model)
        engine.calls = Calls(model, speech_factory=None, pool=Inline(), base_url="https://example.test")
        return engine

    def read_chat(self, engine):
        engine.handle("u", Incoming(chat_export=EXPORT))
        return engine.handle("u", Incoming(button="Me"))

    def test_a_whole_chat_offers_a_call(self):
        out = self.read_chat(self.engine())
        self.assertIn("Test on a call", out.answer.buttons)

    def test_one_forwarded_message_does_not_offer_a_call(self):
        engine = self.engine()
        out = engine.handle("u", Incoming(text="Is this real? He asked me to send money for his visa."))
        self.assertNotIn("Test on a call", out.answer.buttons)

    def test_starting_a_call_gives_two_links_that_are_not_translated(self):
        engine = self.engine(Scripted(memory=GOOD, bluff=proof.ModelError("x"), line={"claim": 0}, scores={"scores": []}))
        self.read_chat(engine)
        out = engine.handle("u", Incoming(button="call"))
        self.assertEqual(out.answer.headline, "Your call is ready.")
        self.assertEqual(len(out.also), 2)
        self.assertTrue(all("https://example.test/proof/" in line for line in out.also))
        call, role = engine.calls.find(out.also[0].rsplit("/", 1)[1])
        self.assertEqual(role, "asker")
        self.assertEqual(call.owner, "u")
        self.assertEqual(engine.calls.find(out.also[1].rsplit("/", 1)[1])[1], "guest")

    def test_the_links_go_out_as_their_own_messages_on_whatsapp(self):
        engine = self.engine()
        self.read_chat(engine)
        out = engine.handle("u", Incoming(button="call"))
        sent = payloads_for("91", out)
        self.assertEqual(len(sent), 3)
        self.assertIn("/proof/", sent[1]["text"]["body"])

    def test_with_no_chat_it_asks_for_one(self):
        out = self.engine().handle("u", Incoming(button="call"))
        self.assertIn("whole chat", out.answer.headline)
        self.assertEqual(out.also, [])

    def test_the_receipt_comes_back_through_the_engine(self):
        engine = self.engine()
        out = engine.finish_call("u", proof.Receipt(joined=False))
        self.assertIn("did not join", out.answer.headline)


if __name__ == "__main__":
    unittest.main()
