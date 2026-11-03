"""Listen mode. A scam call on speaker, and this page open on a second device.

A web page can't hear a normal phone call on the same phone, so the call plays out loud and the page hears the room.
Fixed rules fire the moment the script appears, with no model and no waiting. Nothing is recorded: audio passes through
to live speech and is gone, captions are scrubbed of secrets, and the words are handed back only to the person listening.
"""
import secrets
import threading
import time
import traceback

from redthread import proof
from redthread.senses import LANGUAGE_BIAS
from redthread.shield import scrub

MAX_AUDIO_CHUNK = 64 * 1024
MAX_SESSIONS = 200
MAX_EVENTS = 4000
MAX_SAID_CHARS = 6000
MAX_AGE_SECONDS = 60 * 60
KEYWORDS = ["CBI", "digital arrest", "safe account", "RBI", "customs", "narcotics", "FedEx", "Aadhaar", "money laundering",
            "warrant", "Skype", "AnyDesk", "OTP", "1930", "TRAI", "ED"]


class ListenSession:
    def __init__(self, speech_factory):
        self.token = secrets.token_urlsafe(18)
        self.speech_factory = speech_factory
        self.created = time.monotonic()
        self.phase = "ready"
        self.speech = None
        self.alarms = set()
        self.said = []
        self._events = []
        self._changed = threading.Condition()

    def _emit(self, **event):
        with self._changed:
            if len(self._events) < MAX_EVENTS:
                self._events.append({**event, "seq": len(self._events)})
            self._changed.notify_all()

    def events(self, after, wait=0):
        with self._changed:
            if len(self._events) <= after and wait:
                self._changed.wait(wait)
            return self._events[after:], len(self._events)

    def start(self):
        if self.phase != "ready":
            return
        self.phase = "live"
        self._emit(type="live")
        speech = self.speech_factory(self._heard, self._partial, self._lost, KEYWORDS) if self.speech_factory else None
        if not speech:
            self._lost()
            return
        self.speech = speech
        speech.start()

    def audio(self, pcm):
        if self.phase != "live" or not self.speech or len(pcm) % 2 or len(pcm) > MAX_AUDIO_CHUNK:
            return False
        return self.speech.send(pcm)

    def _partial(self, text):
        if self.phase == "live":
            self._emit(type="partial", text=scrub(text).text)

    def _lost(self):
        self.speech = None
        self._emit(type="captions_off")

    def _heard(self, text):
        if self.phase != "live":
            return
        text = scrub(text).text
        self.said.append(text)
        self._emit(type="caption", text=text)
        alarm = proof.live_alarm(text)
        if alarm and alarm.kind not in self.alarms:
            self.alarms.add(alarm.kind)
            self._emit(type="alarm", kind=alarm.kind, headline=alarm.answer.headline, proof=alarm.answer.proof,
                       next_step=alarm.answer.next_step, said=text)

    def stop(self):
        """Hands back what was said, so the person can choose to check it. Then nothing of it stays here."""
        if self.phase == "ended":
            return ""
        self.phase = "ended"
        if self.speech:
            try:
                self.speech.close()
            except Exception:
                traceback.print_exc()
        self.speech = None
        said = " ".join(self.said)[-MAX_SAID_CHARS:]
        self.said = []
        self._emit(type="ended")
        return said


class Listens:
    def __init__(self, model=None, speech_factory="default"):
        self.model = model
        self.speech_factory = self._default_speech if speech_factory == "default" else speech_factory
        self._sessions = {}
        self._lock = threading.Lock()

    def _default_speech(self, on_turn, on_partial, on_lost, keywords):
        if not self.model:
            return None
        return self.model.live_speech(on_turn, on_partial, on_lost, keywords, LANGUAGE_BIAS)

    def create(self):
        self.sweep()
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                raise ValueError("Too many people are listening right now.")
            session = ListenSession(self.speech_factory)
            self._sessions[session.token] = session
        return session

    def find(self, token):
        return self._sessions.get(token)

    def sweep(self):
        now = time.monotonic()
        with self._lock:
            old = [token for token, session in self._sessions.items() if now - session.created > MAX_AGE_SECONDS]
            gone = [self._sessions.pop(token) for token in old]
        for session in gone:
            session.stop()

    def close(self):
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.stop()
