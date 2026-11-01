import hashlib
import hmac
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from redthread.channel import WhatsAppChannel, read_export
from redthread.engine import Engine
from redthread.network import Network
from redthread.reply import Answer
from redthread.engine import Outgoing
from redthread.server import App, make_server
from redthread.sheets import Sheet, SheetField
from redthread.store import Store
from redthread.whatsapp import Client, WhatsAppError, parse_webhook, payloads_for, verify_challenge, verify_signature
from tests.test_senses import wav_bytes


def webhook(*messages):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15550783881", "phone_number_id": "106"},
            "messages": list(messages),
        }}]}],
    }


def text_message(body, message_id="wamid.1", sender="919876543210"):
    return {"from": sender, "id": message_id, "timestamp": "1749416383", "type": "text", "text": {"body": body}}


class ParseTests(unittest.TestCase):
    def test_text(self):
        found = parse_webhook(webhook(text_message("hello")))[0]
        self.assertEqual((found.kind, found.text, found.sender, found.id), ("text", "hello", "919876543210", "wamid.1"))

    def test_button_tap(self):
        found = parse_webhook(webhook({"from": "9", "id": "m", "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": "report", "title": "Report it"}}}))[0]
        self.assertEqual((found.kind, found.button_id), ("button", "report"))

    def test_voice_note(self):
        found = parse_webhook(webhook({"from": "9", "id": "m", "type": "audio", "audio": {"mime_type": "audio/ogg; codecs=opus", "id": "55", "voice": True}}))[0]
        self.assertEqual((found.kind, found.media_id, found.mime), ("audio", "55", "audio/ogg; codecs=opus"))

    def test_image_with_caption(self):
        found = parse_webhook(webhook({"from": "9", "id": "m", "type": "image", "image": {"mime_type": "image/png", "id": "66", "caption": "is this real"}}))[0]
        self.assertEqual((found.kind, found.media_id, found.text), ("image", "66", "is this real"))

    def test_document(self):
        found = parse_webhook(webhook({"from": "9", "id": "m", "type": "document", "document": {"filename": "chat.txt", "mime_type": "text/plain", "id": "77"}}))[0]
        self.assertEqual((found.kind, found.filename), ("document", "chat.txt"))

    def test_delivery_receipts_and_odd_payloads_give_nothing(self):
        statuses = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "read"}]}}]}]}
        self.assertEqual(parse_webhook(statuses), [])
        self.assertEqual(parse_webhook({}), [])
        self.assertEqual(parse_webhook({"entry": "nonsense"}), [])

    def test_a_sticker_is_kept_as_an_unsupported_kind(self):
        self.assertEqual(parse_webhook(webhook({"from": "9", "id": "m", "type": "sticker", "sticker": {"id": "1"}}))[0].kind, "unsupported")


class SecurityTests(unittest.TestCase):
    def test_signature(self):
        body = b'{"a":1}'
        good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature("secret", body, good))
        self.assertFalse(verify_signature("secret", body + b" ", good))
        self.assertFalse(verify_signature("secret", body, None))
        self.assertFalse(verify_signature("", body, good))

    def test_challenge(self):
        params = {"hub.mode": "subscribe", "hub.verify_token": "t", "hub.challenge": "123"}
        self.assertEqual(verify_challenge(params, "t"), "123")
        self.assertIsNone(verify_challenge(params, "other"))
        self.assertIsNone(verify_challenge({**params, "hub.mode": "x"}, "t"))
        self.assertIsNone(verify_challenge(params, ""))


class PayloadTests(unittest.TestCase):
    def test_plain_answer_is_a_text_message(self):
        sent = payloads_for("9", Outgoing(Answer("Head.", ["Proof."], "Next.")))
        self.assertEqual(sent, [{"messaging_product": "whatsapp", "recipient_type": "individual", "to": "9", "type": "text", "text": {"body": "Head.\nProof.\nNext."}}])

    def test_buttons_carry_our_key_not_the_translated_label(self):
        sent = payloads_for("9", Outgoing(Answer("Head.", [], "Next.", ["रिपोर्ट करें"])))[0]
        button = sent["interactive"]["action"]["buttons"][0]
        self.assertEqual(button, {"type": "reply", "reply": {"id": "report", "title": "रिपोर्ट करें"}})

    def test_a_name_button_keeps_the_name_as_its_id(self):
        sent = payloads_for("9", Outgoing(Answer("Which?", [], "Tap.", ["Riya", "Arjun"])))[0]
        self.assertEqual(sent["interactive"]["action"]["buttons"][1]["reply"]["id"], "Arjun")

    def sheet(self):
        return Sheet([SheetField("State", "Goa", "Pick this."), SheetField("OTP", meaning="You do this.", person_only=True)])

    def test_a_finished_sheet_sends_one_message_per_field_so_each_can_be_copied(self):
        sent = payloads_for("9", Outgoing(Answer("Ready.", [], "Copy."), self.sheet()))
        self.assertEqual(len(sent), 3)
        self.assertIn("1 of 2: State", sent[1]["text"]["body"])
        self.assertIn("Goa", sent[1]["text"]["body"])

    def test_a_draft_sheet_is_not_sent(self):
        self.assertEqual(len(payloads_for("9", Outgoing(Answer("Q.", [], "Next."), self.sheet(), sheet_draft=True))), 1)


class ClientTests(unittest.TestCase):
    def make(self, *replies):
        self.calls = []
        queue = list(replies)

        def transport(method, url, headers, body, timeout):
            self.calls.append((method, url, headers, body))
            return queue.pop(0)

        return Client("tok", "106", transport=transport, sleep=lambda _: None)

    def test_send_posts_to_the_documented_url_with_the_token(self):
        client = self.make((200, b"{}"))
        client.send({"a": 1})
        method, url, headers, body = self.calls[0]
        self.assertEqual((method, url), ("POST", "https://graph.facebook.com/v26.0/106/messages"))
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertEqual(json.loads(body), {"a": 1})

    def test_media_takes_two_steps_and_keeps_the_token(self):
        client = self.make((200, json.dumps({"url": "https://lookaside.fbsbx.com/x", "mime_type": "audio/ogg", "file_size": 3}).encode()), (200, b"abc"))
        self.assertEqual(client.media("55"), (b"abc", "audio/ogg"))
        self.assertEqual(self.calls[0][1], "https://graph.facebook.com/v26.0/55")
        self.assertEqual(self.calls[1][2]["Authorization"], "Bearer tok")

    def test_media_too_large_is_refused_before_download(self):
        client = self.make((200, json.dumps({"url": "https://x", "mime_type": "a", "file_size": 999999999}).encode()))
        with self.assertRaises(WhatsAppError):
            client.media("55")
        self.assertEqual(len(self.calls), 1)

    def test_a_token_is_never_sent_to_a_non_meta_host(self):
        client = self.make((200, json.dumps({"url": "https://evil.example/x", "mime_type": "a", "file_size": 3}).encode()))
        with self.assertRaises(WhatsAppError):
            client.media("55")
        self.assertEqual(len(self.calls), 1)

    def test_rate_limits_are_retried_and_errors_raised_without_the_token(self):
        client = self.make((429, b"slow"), (200, b"{}"))
        client.send({})
        self.assertEqual(len(self.calls), 2)
        client = self.make((401, b"tok bad"))
        with self.assertRaises(WhatsAppError) as caught:
            client.send({})
        self.assertNotIn("Bearer", str(caught.exception))


class ExportTests(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(read_export("hello".encode("utf-8"), "chat.txt"), "hello")

    def test_zip_with_a_chat_inside(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("photo.jpg", b"x")
            bundle.writestr("_chat.txt", "from zip")
        self.assertEqual(read_export(out.getvalue(), "chat.zip"), "from zip")

    def test_a_zip_bomb_is_refused(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("_chat.txt", "0" * (30 * 1024 * 1024))
        self.assertIsNone(read_export(out.getvalue(), "chat.zip"))

    def test_other_files_are_refused(self):
        self.assertIsNone(read_export(b"%PDF-1.7", "report.pdf"))


class FakeClient:
    def __init__(self, media=None, media_error=False):
        self.sent = []
        self.media_data = media
        self.media_error = media_error

    def send(self, payload):
        self.sent.append(payload)

    def media(self, media_id):
        if self.media_error:
            raise WhatsAppError("gone")
        return self.media_data

    def bodies(self):
        return [item.get("text", {}).get("body") or item["interactive"]["body"]["text"] for item in self.sent]


class ChannelTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.network = Network(self.folder / "n.sqlite", salt=b"s")
        self.store = Store(self.folder / "s.sqlite")
        self.addCleanup(self.network.close)
        self.addCleanup(self.store.close)
        self.engine = Engine(self.network, store=self.store)

    def channel(self, client):
        return WhatsAppChannel(self.engine, client, self.store)

    def test_first_contact_says_it_is_an_ai_before_anything_else(self):
        client = FakeClient()
        self.channel(client).receive(parse_webhook(webhook(text_message("write me a poem")))[0])
        self.assertIn("AI guide", client.bodies()[0])
        self.assertIn("only help", client.bodies()[1])

    def test_a_greeting_gets_the_notice_only_once(self):
        client = FakeClient()
        self.channel(client).receive(parse_webhook(webhook(text_message("hi")))[0])
        self.assertEqual(len(client.bodies()), 1)

    def test_a_repeat_delivery_is_answered_once(self):
        client = FakeClient()
        channel = self.channel(client)
        message = parse_webhook(webhook(text_message("hi")))[0]
        channel.receive(message)
        channel.receive(message)
        self.assertEqual(len(client.sent), 1)

    def test_phone_numbers_are_not_stored(self):
        client = FakeClient()
        self.channel(client).receive(parse_webhook(webhook(text_message("I paid Rs 500 to scam@ybl")))[0])
        self.assertNotIn(b"919876543210", (self.folder / "s.sqlite").read_bytes())
        self.assertNotIn(b"919876543210", (self.folder / "n.sqlite").read_bytes())
        self.assertEqual(client.sent[0]["to"], "919876543210")

    def test_lost_media_asks_them_to_send_again(self):
        client = FakeClient(media_error=True)
        message = parse_webhook(webhook({"from": "9", "id": "m", "type": "audio", "audio": {"mime_type": "audio/ogg", "id": "5"}}))[0]
        self.channel(client).receive(message)
        self.assertIn("send it again", client.bodies()[-1].lower())

    def test_a_chat_export_file_reaches_the_engine(self):
        export = "01/03/2026, 21:00 - Riya: Hi there\n01/03/2026, 21:05 - Arjun: Hello\n".encode()
        client = FakeClient(media=(export, "text/plain"))
        message = parse_webhook(webhook({"from": "9", "id": "m", "type": "document", "document": {"filename": "chat.txt", "mime_type": "text/plain", "id": "5"}}))[0]
        self.channel(client).receive(message)
        self.assertIn("Which name is you", client.bodies()[-1])

    def test_a_file_we_cannot_read_explains_what_to_send(self):
        client = FakeClient(media=(b"%PDF", "application/pdf"))
        message = parse_webhook(webhook({"from": "9", "id": "m", "type": "document", "document": {"filename": "a.pdf", "mime_type": "application/pdf", "id": "5"}}))[0]
        self.channel(client).receive(message)
        self.assertIn("Export chat", client.bodies()[-1])

    def test_a_send_failure_does_not_raise(self):
        class Broken(FakeClient):
            def send(self, payload):
                raise WhatsAppError("down")

        self.channel(Broken()).receive(parse_webhook(webhook(text_message("hi")))[0])


class ServerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = FakeClient()
        self.app = App.build(data_dir=Path(tmp.name), model=None, client=self.client, app_secret="secret", verify_token="vt", salt=b"s", inline_webhook=True)
        self.addCleanup(self.app.close)
        self.server = make_server(self.app, port=0)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        import threading

        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, path, body=None, headers=None):
        request = urllib.request.Request(self.base + path, data=body, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def api(self, **fields):
        status, raw = self.call("/api/message", json.dumps(fields).encode(), {"Content-Type": "application/json"})
        return status, json.loads(raw)

    def signed(self, payload):
        body = json.dumps(payload).encode()
        return body, {"X-Hub-Signature-256": "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()}

    def test_health(self):
        self.assertEqual(self.call("/healthz")[0], 200)

    def test_webhook_verification(self):
        self.assertEqual(self.call("/webhook?hub.mode=subscribe&hub.verify_token=vt&hub.challenge=42"), (200, b"42"))
        self.assertEqual(self.call("/webhook?hub.mode=subscribe&hub.verify_token=no&hub.challenge=42")[0], 403)

    def test_a_signed_message_is_answered(self):
        body, headers = self.signed(webhook(text_message("hi")))
        self.assertEqual(self.call("/webhook", body, headers)[0], 200)
        self.assertIn("AI guide", self.client.bodies()[0])

    def test_a_forged_message_is_refused(self):
        body, _ = self.signed(webhook(text_message("hi")))
        self.assertEqual(self.call("/webhook", body, {"X-Hub-Signature-256": "sha256=00"})[0], 401)
        self.assertEqual(self.client.sent, [])

    def test_judge_api_runs_the_same_engine_and_returns_structure(self):
        status, data = self.api(session="j1", text="I already paid Rs 38,000 to scam@ybl")
        self.assertEqual(status, 200)
        self.assertIn("careful people", data["answer"]["headline"])
        self.assertEqual(data["case"]["state"], "new")
        self.assertEqual(data["answer"]["buttons"][0]["id"], "script")

    def test_judge_sessions_are_kept_apart(self):
        self.api(session="j1", text="I already paid Rs 38,000")
        self.assertIsNone(self.api(session="j2", text="hi")[1]["case"])

    def test_judge_data_never_mixes_with_real_cases(self):
        self.api(session="j1", text="I already paid Rs 38,000 to shared@ybl")
        self.assertEqual(self.app.engine.network.report_count("real", {"upi": ["shared@ybl"]}), 0)

    def test_judge_voice_and_image_are_accepted_as_base64(self):
        import base64

        status, data = self.api(session="j3", audio=base64.b64encode(wav_bytes()).decode())
        self.assertEqual(status, 200)
        self.assertIn("type", data["answer"]["next_step"].lower())

    def test_the_seeded_case_warns_a_judge_and_the_world_can_be_played(self):
        status, data = self.api(session="j4", text="Is this real? pay seeded.scam@ybl for the platform")
        self.assertIn("1 other report", json.dumps(data["answer"], ensure_ascii=False))
        self.api(session="j5", text="I already paid Rs 38,000")
        replies = None
        for event in self.call("/api/world")[1] and json.loads(self.call("/api/world")[1])["events"]:
            replies = self.api(session="j5", text=event["text"])[1]
            self.assertTrue(event["simulated"])
        self.assertEqual(replies["case"]["state"], "closed")

    def test_bad_requests_get_a_clean_error(self):
        self.assertEqual(self.call("/api/message", b"not json", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.api(text="no session")[0], 400)
        self.assertEqual(self.call("/nothing")[0], 404)

    def test_oversized_requests_are_refused(self):
        huge = json.dumps({"session": "j", "text": "x" * (16 * 1024 * 1024)}).encode()
        try:
            status = self.call("/api/message", huge, {"Content-Type": "application/json"})[0]
        except (urllib.error.URLError, ConnectionError):
            status = 413
        self.assertEqual(status, 413)

    def test_too_many_judge_calls_are_slowed(self):
        self.app.rate_limit = 2
        statuses = [self.api(session="j", text="hi")[0] for _ in range(4)]
        self.assertEqual(statuses[-1], 429)


if __name__ == "__main__":
    unittest.main()
