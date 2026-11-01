"""Speaks WhatsApp Cloud API: reads webhooks, checks they are real, and sends replies and fetches media.

Formats come from Meta's Cloud API docs, Graph API v26.0.
"""
import hashlib
import hmac
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from redthread import templates
from redthread.reply import render
from redthread.sheets import as_messages

GRAPH = "https://graph.facebook.com/v26.0"
MAX_MEDIA_BYTES = 32 * 1024 * 1024
_MEDIA_HOSTS = (".fbsbx.com", ".fbcdn.net", ".facebook.com", ".whatsapp.net")
_RETRY = {408, 429, 500, 502, 503, 504}
_MEDIA_KINDS = {"audio", "image", "document"}


class WhatsAppError(Exception):
    """A call to WhatsApp failed. The message never holds the access token."""


@dataclass
class Inbound:
    id: str
    sender: str
    kind: str
    text: str = ""
    button_id: str = ""
    media_id: str = ""
    mime: str = ""
    filename: str = ""


def parse_webhook(payload):
    """Delivery receipts and anything unexpected give nothing back, and never raise."""
    found = []
    try:
        changes = [change for entry in payload.get("entry", []) for change in entry.get("changes", [])]
        messages = [message for change in changes for message in change.get("value", {}).get("messages", [])]
    except (AttributeError, TypeError):
        return found
    for message in messages:
        try:
            found.append(_read(message))
        except (AttributeError, KeyError, TypeError):
            continue
    return found


def _read(message):
    kind = message["type"]
    base = {"id": message["id"], "sender": message["from"]}
    if kind == "text":
        return Inbound(kind="text", text=message["text"]["body"], **base)
    if kind == "interactive" and message["interactive"]["type"] == "button_reply":
        return Inbound(kind="button", button_id=message["interactive"]["button_reply"]["id"], **base)
    if kind == "button":
        return Inbound(kind="text", text=message["button"]["text"], **base)
    if kind in _MEDIA_KINDS:
        media = message[kind]
        return Inbound(kind=kind, media_id=media["id"], mime=media.get("mime_type", ""),
                       filename=media.get("filename", ""), text=media.get("caption", ""), **base)
    return Inbound(kind="unsupported", **base)


def verify_signature(app_secret, body, header):
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def verify_challenge(params, verify_token):
    if not verify_token or params.get("hub.mode") != "subscribe":
        return None
    if not hmac.compare_digest(params.get("hub.verify_token", ""), verify_token):
        return None
    return params.get("hub.challenge")


def text_payload(to, body):
    return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text", "text": {"body": body}}


def payloads_for(to, outgoing):
    answer = outgoing.answer
    body = render(answer)
    if answer.buttons:
        buttons = [{"type": "reply", "reply": {"id": templates.button_key(label), "title": label}} for label in answer.buttons]
        first = {
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "interactive",
            "interactive": {"type": "button", "body": {"text": body}, "action": {"buttons": buttons}},
        }
    else:
        first = text_payload(to, body)
    sent = [first]
    if outgoing.sheet and not outgoing.sheet_draft:
        sent += [text_payload(to, line) for line in as_messages(outgoing.sheet)]
    return sent


def _http(method, url, headers, body, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return 0, b""


class Client:
    def __init__(self, token, phone_number_id, transport=_http, sleep=time.sleep, timeout=30):
        self._token = token
        self.phone_number_id = phone_number_id
        self.transport = transport
        self.sleep = sleep
        self.timeout = timeout

    def _call(self, method, url, body=None, attempts=3):
        headers = {"Authorization": f"Bearer {self._token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        status = None
        for attempt in range(attempts):
            status, raw = self.transport(method, url, headers, body, self.timeout)
            if status == 200:
                return raw
            if status != 0 and status not in _RETRY:
                break
            if attempt + 1 < attempts:
                self.sleep(2 ** attempt + random.random())
        raise WhatsAppError(f"WhatsApp call failed with status {status}.")

    def send(self, payload):
        self._call("POST", f"{GRAPH}/{self.phone_number_id}/messages", json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def media(self, media_id):
        """Two steps: ask where the file is, then fetch it. The token only ever goes to Meta's own hosts."""
        try:
            info = json.loads(self._call("GET", f"{GRAPH}/{media_id}"))
            url, mime, size = info["url"], info.get("mime_type", ""), int(info.get("file_size", 0))
        except (ValueError, KeyError, TypeError) as error:
            raise WhatsAppError("WhatsApp media reply had an unexpected shape.") from error
        if size > MAX_MEDIA_BYTES:
            raise WhatsAppError("That file is too large.")
        parts = urllib.parse.urlparse(url)
        if parts.scheme != "https" or not (parts.hostname or "").endswith(_MEDIA_HOSTS):
            raise WhatsAppError("Media link pointed somewhere unexpected.")
        return self._call("GET", url), mime
