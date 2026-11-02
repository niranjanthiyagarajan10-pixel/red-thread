"""The running service: the WhatsApp webhook and the judge API, on the Python standard library.

Judges and WhatsApp share one engine design but never share data. Judge sessions run on their own
network, seeded with one invented case, and live only in memory.
Run with: python -m redthread.server
"""
import base64
import binascii
import json
import os
import re
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

from redthread import proof_demo, templates
from redthread.channel import WhatsAppChannel
from redthread.engine import Engine, Incoming
from redthread.knot_session import Knots
from redthread.knot_session import NotAllowed as KnotRefused
from redthread.model import HOME, Ledger, MetaModel, load_api_key
from redthread.network import Network
from redthread.proof_call import Calls, NotAllowed
from redthread.store import Store
from redthread.whatsapp import Client, parse_webhook, verify_challenge, verify_signature
from redthread.world import demo_events

MAX_BODY_BYTES = 12 * 1024 * 1024
SEED_UPI = "seeded.scam@ybl"
DEMO_HELD = Decimal(40000)
DEMO_RESTORABLE = Decimal(38000)
RATE_WINDOW_SECONDS = 60
MAX_JUDGE_SESSIONS = 2000
MAX_SIGNAL_BYTES = 64 * 1024
MAX_EVENT_WAIT_SECONDS = 25
_PROOF_PATH = re.compile(r"^/api/proof/([A-Za-z0-9_-]{10,64})/([a-z]+)(?:/(\d+))?$")
_KNOT_PATH = re.compile(r"^/api/knot/([A-Za-z0-9_-]{10,64})/([a-z]+)$")
_CARD_PATH = re.compile(r"^/api/card/([A-Za-z0-9_-]{10,64})$")
_TOKEN_IN_PATH = re.compile(r"(/(?:proof|knot|card)/)[A-Za-z0-9_-]{10,64}")
WEB = Path(__file__).resolve().parent / "web"
_PAGES = {"/": "index.html", "/knot": "knot.html"}
_STATIC = re.compile(r"^/static/([a-z0-9_-]+\.(css|js|svg|png|webp|ico|woff2))$")
_TYPES = {
    "html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8",
    "svg": "image/svg+xml", "png": "image/png", "webp": "image/webp", "ico": "image/x-icon", "woff2": "font/woff2",
}
_PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
                               "style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "Permissions-Policy": "microphone=(self), camera=(self)",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-cache",
}


class _BadRequest(Exception):
    pass


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (set, tuple)):
        return sorted(value) if isinstance(value, set) else list(value)
    return str(value)


class App:
    def __init__(self, engine, judge_engine, channel, store, app_secret, verify_token, inline_webhook=False, clips=proof_demo.CLIPS):
        self.engine = engine
        self.judge_engine = judge_engine
        self.channel = channel
        self.store = store
        self.app_secret = app_secret
        self.verify_token = verify_token
        self.inline_webhook = inline_webhook
        self.clips = clips
        self.pool = ThreadPoolExecutor(8)
        self.knots = None
        self.rate_limit = 60
        self._calls = defaultdict(deque)
        self._lock = threading.Lock()

    @classmethod
    def build(cls, data_dir, model, client, app_secret, verify_token, salt, inline_webhook=False, public_url="", clips=proof_demo.CLIPS):
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        store = Store(data_dir / "store.sqlite")
        engine = Engine(Network(data_dir / "network.sqlite", salt=salt), model=model, store=store)
        judge_network = Network(":memory:", salt=salt)
        judge_network.add_case("simulated-earlier-victim", {"upi": [SEED_UPI]})
        judge_engine = Engine(judge_network, model=model)
        channel = WhatsAppChannel(engine, client, store) if client else None
        app = cls(engine, judge_engine, channel, store, app_secret, verify_token, inline_webhook, clips)
        engine.calls = Calls(model, on_end=app._call_ended, base_url=public_url)
        judge_engine.calls = Calls(model, base_url=public_url)
        app.knots = Knots(model)
        return app

    def close(self):
        if self.knots:
            self.knots.close()
        self.engine.calls.close()
        self.judge_engine.calls.close()
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

    def _call_ended(self, call, receipt):
        if call.owner and self.channel:
            self.channel.notify(call.owner, receipt)

    def find_call(self, token):
        call, role = self.engine.calls.find(token)
        return (call, role) if call else self.judge_engine.calls.find(token)

    def demo_call(self):
        """A simulated person, either the writer or a stand-in, chosen at random and revealed at the end."""
        call = self.judge_engine.calls.create(proof_demo.memory(), person=proof_demo.person(clips_dir=self.clips))
        return {"token": call.tokens["asker"]}

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
            "other_reports": out.other_reports,
            "also": out.also,
            "call": out.call,
            "language": session.language,
        }

    def world(self):
        return {"events": [{"day": event.day, "text": event.text, "simulated": event.simulated}
                           for event in demo_events(DEMO_HELD, DEMO_RESTORABLE)]}


def _handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RedThread"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 65

        def log_message(self, format, *args):
            path = _TOKEN_IN_PATH.sub(r"\1*", urlparse(self.path).path)
            sys.stderr.write(f"{self.command} {path} {args[1] if len(args) > 1 else ''}\n")

        def _reply(self, status, body=b"", content_type="application/json", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path):
            if not path.is_file():
                self._reply(404)
                return
            kind = path.suffix.lstrip(".")
            headers = _PAGE_HEADERS if kind == "html" else {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}
            self._reply(200, path.read_bytes(), _TYPES[kind], headers)

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
            elif parts.path.startswith("/api/proof/"):
                self._proof_get(parts)
            elif parts.path.startswith("/api/knot/"):
                self._knot_get(parts)
            elif _CARD_PATH.match(parts.path):
                card = app.knots.card(_CARD_PATH.match(parts.path).group(1))
                self._json(200, card) if card else self._reply(404)
            elif parts.path in _PAGES:
                self._file(WEB / _PAGES[parts.path])
            elif re.match(r"^/card/[A-Za-z0-9_-]{10,64}$", parts.path):
                self._file(WEB / "card.html")
            elif _STATIC.match(parts.path):
                self._file(WEB / "static" / _STATIC.match(parts.path).group(1))
            else:
                self._reply(404)

        def _knot_get(self, parts):
            match = _KNOT_PATH.match(parts.path)
            session = app.knots.find(match.group(1)) if match else None
            if not session:
                self._reply(404)
            elif match.group(2) == "state":
                self._json(200, session.state())
            elif match.group(2) == "events":
                query = dict(parse_qsl(parts.query))
                try:
                    after = max(0, int(query.get("after", 0)))
                    wait = min(MAX_EVENT_WAIT_SECONDS, max(0.0, float(query.get("wait", 0))))
                except ValueError:
                    self._reply(400)
                    return
                rows, seen = session.events(after, wait)
                self._json(200, {"events": rows, "next": seen})
            else:
                self._reply(404)

        def _knot_create(self, body):
            if not app.allowed(self.client_address[0]):
                self._json(429, {"error": "Too many tries. Wait a minute and try again."})
                return
            try:
                interests = json.loads(body or b"{}").get("interests")
                if not isinstance(interests, list):
                    raise ValueError("No interests.")
                session = app.knots.create([str(item) for item in interests[:10]])
                self._json(200, {"token": session.token, "state": session.state()})
            except KnotRefused as error:
                self._json(400, {"error": str(error)})
            except (ValueError, AttributeError):
                self._json(400, {"error": "Add at least one interest."})

        def _knot_post(self, path, body):
            match = _KNOT_PATH.match(path)
            session = app.knots.find(match.group(1)) if match else None
            if not session:
                self._reply(404)
                return
            action = match.group(2)
            if action == "audio":
                self._reply(204 if session.audio(body) else 409)
                return
            try:
                if action == "begin":
                    session.begin()
                elif action == "say":
                    session.say(str(json.loads(body or b"{}").get("text", ""))[:2000])
                elif action == "next":
                    session.next()
                elif action == "reask":
                    later = app.knots.reask(session.token)
                    self._json(200, {"token": later.token, "state": later.state()})
                    return
                else:
                    self._reply(404)
                    return
                self._json(200, {"ok": True})
            except KnotRefused as error:
                self._json(403, {"error": str(error)})
            except (ValueError, AttributeError):
                self._json(400, {"error": "That request could not be read."})
            except Exception:
                traceback.print_exc()
                self._json(500, {"error": "Something went wrong. Try again."})

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
            elif path == "/api/proof/demo":
                self._proof_demo()
            elif path.startswith("/api/proof/"):
                self._proof_post(path, body)
            elif path == "/api/knot":
                self._knot_create(body)
            elif path.startswith("/api/knot/"):
                self._knot_post(path, body)
            else:
                self._reply(404)

        def _proof_demo(self):
            if not app.allowed(self.client_address[0]):
                self._json(429, {"error": "Too many calls. Wait a minute and try again."})
                return
            try:
                self._json(200, app.demo_call())
            except NotAllowed as error:
                self._json(503, {"error": str(error)})

        def _proof_get(self, parts):
            match = _PROOF_PATH.match(parts.path)
            call, role = app.find_call(match.group(1)) if match else (None, None)
            if not call:
                self._reply(404)
            elif match.group(2) == "state":
                self._json(200, call.state(role))
            elif match.group(2) == "events":
                self._proof_events(call, role, dict(parse_qsl(parts.query)))
            elif match.group(2) == "clip" and match.group(3) and role == "asker" and call.person:
                self._proof_clip(call, match.group(3))
            else:
                self._reply(404)

        def _proof_events(self, call, role, query):
            try:
                after = max(0, int(query.get("after", 0)))
                wait = min(MAX_EVENT_WAIT_SECONDS, max(0.0, float(query.get("wait", 0))))
            except ValueError:
                self._reply(400)
                return
            rows, seen = call.events(role, after, wait)
            self._json(200, {"events": rows, "next": seen})

        def _proof_clip(self, call, question):
            path = call.person.clip_path(int(question))
            if path.exists():
                self._reply(200, path.read_bytes(), "audio/wav")
            else:
                self._reply(404)

        def _proof_post(self, path, body):
            match = _PROOF_PATH.match(path)
            call, role = app.find_call(match.group(1)) if match else (None, None)
            if not call:
                self._reply(404)
                return
            action = match.group(2)
            if action == "audio":
                self._reply(204 if call.audio(role, body) else 409)
                return
            try:
                if action == "signal" and len(body) > MAX_SIGNAL_BYTES:
                    self._reply(413)
                    return
                data = json.loads(body or b"{}")
                if action == "consent":
                    call.consent(role)
                elif action == "signal":
                    call.signal(role, data)
                elif action == "ask":
                    call.ask(role, int(data["question"]))
                elif action == "play":
                    self._proof_play(call, data)
                elif action == "finish":
                    call.finish()
                else:
                    self._reply(404)
                    return
                self._json(200, {"ok": True})
            except NotAllowed as error:
                self._json(403, {"error": str(error)})
            except (ValueError, KeyError, TypeError):
                self._json(400, {"error": "That request could not be read."})
            except Exception:
                traceback.print_exc()
                self._json(500, {"error": "Something went wrong. Try again."})

        def _proof_play(self, call, data):
            if data.get("line") == "money":
                call.play_line(proof_demo.MONEY_LINE)
            elif data.get("line") == "job" and call.person and call.person.kind == "stand_in":
                call.play_line(proof_demo.JOB_LINE)
            elif "question" in data:
                call.play_guest(int(data["question"]))
            else:
                raise ValueError("Nothing to play.")

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
    # No disk cache here. Replies can quote the other person's chat, and "delete my data" could not reach them.
    model = MetaModel(key, Ledger(budget, data_dir / "ledger.json")) if key else None
    token, phone = environ.get("WHATSAPP_TOKEN"), environ.get("WHATSAPP_PHONE_ID")
    client = Client(token, phone) if token and phone else None
    salt = environ.get("REDTHREAD_SALT", "").encode("utf-8") or None
    clips = environ.get("REDTHREAD_CLIPS") or proof_demo.CLIPS
    return App.build(
        data_dir, model, client, environ.get("WHATSAPP_APP_SECRET", ""), environ.get("WHATSAPP_VERIFY_TOKEN", ""), salt,
        public_url=environ.get("REDTHREAD_PUBLIC_URL", ""), clips=clips,
    )


def main():
    app = from_environment()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    print(f"Model: {'on' if app.engine.model else 'off, rules only'}. WhatsApp: {'on' if app.channel else 'off'}. Port {port}.")
    make_server(app, port, host).serve_forever()


if __name__ == "__main__":
    main()
