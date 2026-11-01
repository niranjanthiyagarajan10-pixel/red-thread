"""The running service: the WhatsApp webhook and the judge API, on the Python standard library.

Judges and WhatsApp share one engine design but never share data. Judge sessions run on their own
network, seeded with one invented case, and live only in memory.
Run with: python -m redthread.server
"""
import base64
import binascii
import json
import os
import sys
import threading
import time
import traceback
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from redthread import templates
from redthread.channel import WhatsAppChannel
from redthread.engine import Engine, Incoming
from redthread.model import HOME, Ledger, MetaModel, load_api_key
from redthread.network import Network
from redthread.store import Store
from redthread.whatsapp import Client, parse_webhook, verify_challenge, verify_signature
from redthread.world import demo_events

MAX_BODY_BYTES = 12 * 1024 * 1024
SEED_UPI = "seeded.scam@ybl"
DEMO_HELD = Decimal(40000)
DEMO_RESTORABLE = Decimal(38000)
RATE_WINDOW_SECONDS = 60
MAX_JUDGE_SESSIONS = 2000


class _BadRequest(Exception):
    pass


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (set, tuple)):
        return sorted(value) if isinstance(value, set) else list(value)
    return str(value)


class App:
    def __init__(self, engine, judge_engine, channel, store, app_secret, verify_token, inline_webhook=False):
        self.engine = engine
        self.judge_engine = judge_engine
        self.channel = channel
        self.store = store
        self.app_secret = app_secret
        self.verify_token = verify_token
        self.inline_webhook = inline_webhook
        self.pool = ThreadPoolExecutor(8)
        self.rate_limit = 60
        self._calls = defaultdict(deque)
        self._lock = threading.Lock()

    @classmethod
    def build(cls, data_dir, model, client, app_secret, verify_token, salt, inline_webhook=False):
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        store = Store(data_dir / "store.sqlite")
        engine = Engine(Network(data_dir / "network.sqlite", salt=salt), model=model, store=store)
        judge_network = Network(":memory:", salt=salt)
        judge_network.add_case("simulated-earlier-victim", {"upi": [SEED_UPI]})
        judge_engine = Engine(judge_network, model=model)
        channel = WhatsAppChannel(engine, client, store) if client else None
        return cls(engine, judge_engine, channel, store, app_secret, verify_token, inline_webhook)

    def close(self):
        self.pool.shutdown(wait=False)
        self.engine.network.close()
        self.judge_engine.network.close()
        self.store.close()

    def allowed(self, address):
        now = time.monotonic()
        with self._lock:
            calls = self._calls[address]
            while calls and now - calls[0] > RATE_WINDOW_SECONDS:
                calls.popleft()
            if len(calls) >= self.rate_limit:
                return False
            calls.append(now)
            return True

    def webhook(self, payload):
        if not self.channel:
            return
        for message in parse_webhook(payload):
            if self.inline_webhook:
                self.channel.receive(message)
            else:
                self.pool.submit(self.channel.receive, message)

    def judge_message(self, data):
        session_id = data.get("session")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 64:
            raise _BadRequest("A session is required.")
        if session_id not in self.judge_engine.sessions and len(self.judge_engine.sessions) >= MAX_JUDGE_SESSIONS:
            self.judge_engine.sessions.clear()
        incoming = Incoming(
            text=str(data.get("text") or ""), chat_export=str(data.get("chat_export") or ""), button=str(data.get("button") or ""),
        )
        try:
            if data.get("audio"):
                incoming.audio = base64.b64decode(data["audio"], validate=True)
            if data.get("image"):
                incoming.images = ((base64.b64decode(data["image"], validate=True), str(data.get("image_mime") or "image/jpeg")),)
        except (binascii.Error, ValueError, TypeError) as error:
            raise _BadRequest("That file could not be read.") from error
        out = self.judge_engine.handle(session_id, incoming)
        session = self.judge_engine.session(session_id)
        answer = out.answer
        return {
            "answer": {
                "headline": answer.headline, "proof": answer.proof, "next_step": answer.next_step,
                "buttons": [{"id": templates.button_key(label), "label": label} for label in answer.buttons],
            },
            "sheet": None if out.sheet is None else {
                "ready": out.sheet.ready, "draft": out.sheet_draft, "changes": out.sheet.changes,
                "fields": [asdict(item) for item in out.sheet.fields],
            },
            "case": None if session.case is None else {
                "state": session.case.state, "expected": session.case.expected, "held": session.case.held,
                "credited": session.case.credited, "ack_id": session.case.ack_id, "request_id": session.case.request_id,
            },
            "link_offer": out.link_offer,
            "language": session.language,
        }

    def world(self):
        return {"events": [{"day": event.day, "text": event.text, "simulated": event.simulated}
                           for event in demo_events(DEMO_HELD, DEMO_RESTORABLE)]}


def _handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RedThread"
        sys_version = ""

        def log_message(self, format, *args):
            sys.stderr.write(f"{self.command} {urlparse(self.path).path} {args[1] if len(args) > 1 else ''}\n")

        def _reply(self, status, body=b"", content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, value):
            self._reply(status, json.dumps(value, ensure_ascii=False, default=_plain).encode("utf-8"))

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self.close_connection = True
                return None
            return self.rfile.read(length)

        def do_GET(self):
            parts = urlparse(self.path)
            if parts.path == "/healthz":
                self._json(200, {"ok": True})
            elif parts.path == "/webhook":
                challenge = verify_challenge(dict(parse_qsl(parts.query)), app.verify_token)
                if challenge is None:
                    self._reply(403)
                else:
                    self._reply(200, challenge.encode("utf-8"), "text/plain")
            elif parts.path == "/api/world":
                self._json(200, app.world())
            else:
                self._reply(404)

        def do_POST(self):
            path = urlparse(self.path).path
            body = self._body()
            if body is None:
                self._reply(413)
                return
            if path == "/webhook":
                self._webhook(body)
            elif path == "/api/message":
                self._judge(body)
            else:
                self._reply(404)

        def _webhook(self, body):
            if not verify_signature(app.app_secret, body, self.headers.get("X-Hub-Signature-256")):
                self._reply(401)
                return
            try:
                app.webhook(json.loads(body))
            except ValueError:
                pass
            self._reply(200)

        def _judge(self, body):
            if not app.allowed(self.client_address[0]):
                self._json(429, {"error": "Too many messages. Wait a minute and try again."})
                return
            try:
                self._json(200, app.judge_message(json.loads(body)))
            except (ValueError, AttributeError, _BadRequest) as error:
                self._json(400, {"error": str(error) if isinstance(error, _BadRequest) else "That request could not be read."})
            except Exception:
                traceback.print_exc()
                self._json(500, {"error": "Something went wrong. Try again."})

    return Handler


def make_server(app, port=8080, host="127.0.0.1"):
    return ThreadingHTTPServer((host, port), _handler(app))


def from_environment(environ=os.environ):
    data_dir = Path(environ.get("REDTHREAD_DATA", HOME / "service"))
    data_dir.mkdir(parents=True, exist_ok=True)
    key = load_api_key(environ)
    budget = float(environ.get("REDTHREAD_BUDGET", "30"))
    model = MetaModel(key, Ledger(budget, data_dir / "ledger.json"), cache_dir=data_dir / "cache") if key else None
    token, phone = environ.get("WHATSAPP_TOKEN"), environ.get("WHATSAPP_PHONE_ID")
    client = Client(token, phone) if token and phone else None
    salt = environ.get("REDTHREAD_SALT", "").encode("utf-8") or None
    return App.build(data_dir, model, client, environ.get("WHATSAPP_APP_SECRET", ""), environ.get("WHATSAPP_VERIFY_TOKEN", ""), salt)


def main():
    app = from_environment()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    print(f"Model: {'on' if app.engine.model else 'off, rules only'}. WhatsApp: {'on' if app.channel else 'off'}. Port {port}.")
    make_server(app, port, host).serve_forever()


if __name__ == "__main__":
    main()
