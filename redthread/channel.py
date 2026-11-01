"""Connects WhatsApp to the engine: one person's messages run in order, and nothing raises to the server."""
import hashlib
import hmac
import io
import threading
import traceback
import zipfile
from collections import defaultdict

from redthread import templates
from redthread.engine import Incoming, Outgoing
from redthread.reply import Answer, render
from redthread.whatsapp import WhatsAppError, payloads_for, text_payload as _plain_text

MAX_EXPORT_BYTES = 8 * 1024 * 1024
_SEND_AGAIN = Answer("We could not open that.", [], "Please send it again, or type what it says.")
_NOT_READABLE = Answer(
    "We can read chats, voice notes and photos.", [],
    "For a chat: open it, tap More, then Export chat, then Without media, and share it here.",
)
_READING = "Reading it now. This takes a few seconds."


def read_export(data, filename):
    """The chat text from a .txt export, or from the text inside a .zip. Anything else, or anything too big, gives None."""
    name = filename.lower()
    if name.endswith(".txt"):
        return data.decode("utf-8", errors="replace") if len(data) <= MAX_EXPORT_BYTES else None
    if not name.endswith(".zip"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            for item in bundle.infolist():
                if item.filename.lower().endswith(".txt"):
                    if item.file_size > MAX_EXPORT_BYTES:
                        return None
                    return bundle.read(item).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, RuntimeError, OSError):
        return None
    return None


class WhatsAppChannel:
    def __init__(self, engine, client, store):
        self.engine = engine
        self.client = client
        self.store = store
        self._locks = defaultdict(threading.Lock)

    def _user(self, sender):
        """Phone numbers are never stored. Cases are filed under a salted hash."""
        return hmac.new(self.engine.network.salt, f"wa:{sender}".encode("utf-8"), hashlib.sha256).hexdigest()

    def receive(self, message):
        try:
            if not self.store.mark_seen(message.id):
                return
            user = self._user(message.sender)
            with self._locks[user]:
                self._answer(user, message)
        except Exception:
            traceback.print_exc()

    def _send(self, sender, outgoing):
        for payload in payloads_for(sender, outgoing):
            self.client.send(payload)

    def _answer(self, user, message):
        notice = self.engine.is_new(user)
        session = self.engine.session(user)
        try:
            incoming = self._incoming(message)
        except WhatsAppError:
            outgoing = Outgoing(_SEND_AGAIN)
        else:
            if incoming is None:
                outgoing = Outgoing(_NOT_READABLE)
            else:
                if incoming.chat_export:
                    self._quietly_send_text(message.sender, _READING)
                outgoing = self.engine.handle(user, incoming)
        welcome = templates.welcome(session.language)
        try:
            if notice and render(outgoing.answer) != render(welcome):
                self._send(message.sender, Outgoing(welcome))
            self._send(message.sender, outgoing)
        except WhatsAppError:
            traceback.print_exc()

    def _quietly_send_text(self, sender, text):
        try:
            self.client.send(_plain_text(sender, text))
        except WhatsAppError:
            traceback.print_exc()

    def _incoming(self, message):
        if message.kind == "text":
            return Incoming(text=message.text)
        if message.kind == "button":
            return Incoming(button=message.button_id)
        if message.kind == "audio":
            return Incoming(audio=self.client.media(message.media_id)[0], text=message.text)
        if message.kind == "image":
            data, mime = self.client.media(message.media_id)
            return Incoming(images=((data, mime or message.mime or "image/jpeg"),), text=message.text)
        if message.kind == "document":
            data, _ = self.client.media(message.media_id)
            export = read_export(data, message.filename)
            return Incoming(chat_export=export) if export else None
        return None
