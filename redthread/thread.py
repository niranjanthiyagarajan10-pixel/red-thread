"""One thread per match: their profile, the chat, where the story breaks, and the checks.

Everything lives in memory, and is gone after a day or the moment the person taps forget. Secrets are scrubbed from the
chat before any model sees it. Code decides every level and headline. Models find, quote and search.
"""
import base64
import binascii
import re
import secrets
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlparse

from redthread import breaks as breaks_logic
from redthread import investigate
from redthread import profile as profile_logic
from redthread import proof, stages
from redthread.conversation import Conversation, Turn, parse_whatsapp_export
from redthread.model import BudgetExceeded
from redthread.shield import scrub

MAX_THREADS = 500
THREAD_SECONDS = 24 * 60 * 60
MAX_CHECKS = 6
MAX_READS = 6
MAX_CHAT_CHARS = 200_000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_NAME_CHARS = 40
_DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$")
_PLAIN_LINE = re.compile(r"^\s*([^:\n]{1,40}?)\s*:\s+(.+)$")
_ME = {"me", "you", "i", "myself"}
HEADLINES = {
    "known_script": "This chat follows a known scam script.",
    "early_signs": "Some early warning signs.",
    "nothing_found": "Nothing worrying found in this chat.",
}


class NotAllowed(Exception):
    pass


@dataclass
class Thread:
    token: str
    name: str
    example: bool
    created: float
    profile: object = None
    chat: object = None
    other: str = ""
    report: object = None
    breaks: list = field(default_factory=list)
    items: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    reads: int = 0
    call: str = ""


def parse_chat(text, name):
    """A WhatsApp export, or plain lines like "Rohan: hi". The match is the speaker with their name, or the busiest other one."""
    text = str(text or "")[:MAX_CHAT_CHARS]
    chat = parse_whatsapp_export(text)
    if len(chat.turns) < 2:
        turns = []
        for line in text.splitlines():
            match = _PLAIN_LINE.match(line)
            if match:
                turns.append(Turn(len(turns), match.group(1).strip(), match.group(2).strip()))
            elif turns and line.strip():
                turns[-1].text = f"{turns[-1].text} {line.strip()}"
        chat = Conversation("chat", turns)
    if len(chat.turns) < 2:
        raise NotAllowed("We couldn't find a chat in that. Paste it as lines like \"Rohan: hi\", or share a WhatsApp export.")
    for turn in chat.turns:
        turn.text = scrub(turn.text).text
    wanted = " ".join(str(name or "").split()).casefold()
    speakers = chat.speakers()
    other = next((speaker for speaker in speakers if wanted and (speaker.casefold() == wanted or speaker.casefold().startswith(wanted))), None)
    if not other:
        counts = Counter(turn.speaker for turn in chat.turns if turn.speaker.casefold() not in _ME)
        if not counts:
            raise NotAllowed("We couldn't tell which person in the chat is your match.")
        other = counts.most_common(1)[0][0]
    return chat, other


def decode_image(data_url):
    match = _DATA_URL.match(str(data_url or ""))
    if not match:
        raise NotAllowed("Share screenshots as PNG, JPEG or WebP images.")
    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except (binascii.Error, ValueError) as error:
        raise NotAllowed("That image could not be read.") from error
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise NotAllowed("Each screenshot must be under 5 MB.")
    return raw, match.group(1)


def _day(moment):
    return f"{moment.day} {moment.strftime('%b')}" if moment else ""


def _turn_day(chat, index):
    return _day(chat.turns[index].when) if chat and 0 <= index < len(chat.turns) else ""


def _host(url):
    host = urlparse(url).netloc.casefold()
    return host[4:] if host.startswith("www.") else host


def _check_view(result, example=False):
    note = investigate.note(result)
    if example and result.reports:
        note = f"{note} The earlier report here is part of the example.".strip()
    return {
        "kind": result.item.kind,
        "label": result.item.label,
        "headline": investigate.headline(result),
        "note": note,
        "reports": result.reports,
        "lines": [{"text": line.text, "sources": [{"url": url, "host": _host(url)} for url in line.sources]} for line in result.lines],
    }


class Threads:
    def __init__(self, model, searcher, network, example_network=None, clock=time.time):
        self.model = model
        self.searcher = searcher
        self.network = network
        self.example_network = example_network
        self.clock = clock
        self._threads = {}
        self._lock = threading.Lock()

    def create(self, name, example=False):
        self.sweep()
        with self._lock:
            if len(self._threads) >= MAX_THREADS:
                raise NotAllowed("Too many people are checking right now. Try again soon.")
        clean = " ".join(scrub(str(name or "")).text.split())[:MAX_NAME_CHARS] or "Your match"
        thread = Thread(secrets.token_urlsafe(18), clean, bool(example), self.clock())
        with self._lock:
            self._threads[thread.token] = thread
        return thread

    def find(self, token):
        return self._threads.get(token)

    def _get(self, token):
        thread = self.find(token)
        if not thread:
            raise NotAllowed("We can't find that match. It may have expired.")
        return thread

    def _read(self, thread):
        if thread.reads >= MAX_READS:
            raise NotAllowed("That's as much as one match can take. Start a new one.")
        thread.reads += 1

    def add_profile(self, token, images=None, text=None):
        thread = self._get(token)
        decoded = [decode_image(item) for item in (images or [])[:profile_logic.MAX_IMAGES]]
        if not decoded and not str(text or "").strip():
            raise NotAllowed("Add a screenshot of their profile, or paste its text.")
        self._read(thread)
        try:
            thread.profile = profile_logic.read_profile(self.model, decoded or None, text if not decoded else None)
        except BudgetExceeded:
            thread.profile = profile_logic.Profile(fallback=True)
        self._find_breaks(thread)
        return thread

    def add_chat(self, token, text):
        thread = self._get(token)
        chat, other = parse_chat(text, thread.name)
        self._read(thread)
        thread.chat, thread.other = chat, other
        try:
            thread.report = stages.analyze(chat, self.model, other)
        except BudgetExceeded:
            findings = stages.keyword_findings(chat, other)
            thread.report = stages.Report(stages.decide(findings), findings, [], fallback=True)
        thread.items = investigate.items_in(chat, other)
        self._find_breaks(thread)
        return thread

    def _find_breaks(self, thread):
        if not (thread.profile and thread.chat):
            thread.breaks = []
            return
        try:
            thread.breaks = breaks_logic.find_breaks(self.model, thread.profile, thread.chat, thread.other)
        except BudgetExceeded:
            thread.breaks = []

    def check(self, token, kind, value):
        thread = self._get(token)
        if len(thread.checks) >= MAX_CHECKS:
            raise NotAllowed("That's the most checks for one match.")
        item = investigate.clean_item(kind, value)
        if not item:
            raise NotAllowed("Tell us what to check: a link, a UPI ID, a number or a place.")
        network = self.example_network if thread.example else self.network
        view = _check_view(investigate.investigate(self.searcher, item, network=network, case_id=f"thread:{thread.token}"), thread.example)
        thread.checks.append(view)
        return view

    def call_memory(self, token):
        """Questions only the writer of this chat could answer. Spark writes them, and the second model tries to bluff them blind."""
        thread = self._get(token)
        if not thread.chat:
            raise NotAllowed("Add your chat first, so the questions come from it.")
        self._read(thread)
        memory = proof.build_memory(self.model, thread.chat, thread.other)
        return proof.drop_guessable(self.model, memory, self.searcher)

    def set_call(self, token, asker_token):
        self._get(token).call = asker_token

    def forget(self, token):
        with self._lock:
            self._threads.pop(token, None)

    def sweep(self):
        now = self.clock()
        with self._lock:
            for token in [token for token, thread in self._threads.items() if now - thread.created > THREAD_SECONDS]:
                del self._threads[token]

    def state(self, thread):
        chat, report, profile = thread.chat, thread.report, thread.profile
        state = {"token": thread.token, "name": thread.name, "example": thread.example, "profile": None, "chat": None,
                 "reading": None, "breaks": [], "items": [], "checks": list(thread.checks), "call": thread.call or None}
        if profile:
            state["profile"] = {
                "lines": profile.lines, "fallback": profile.fallback,
                "name": profile.name, "age": profile.age, "job": profile.job, "city": profile.city,
                "claims": [{"topic": claim.topic, "quote": claim.quote} for claim in profile.claims],
            }
        if chat:
            state["chat"] = {"turns": len(chat.turns), "other": thread.other,
                             "first": _day(chat.turns[0].when), "last": _day(chat.turns[-1].when)}
        if report:
            state["reading"] = {
                "level": report.verdict.level, "headline": HEADLINES[report.verdict.level], "reasons": report.verdict.reasons,
                "fallback": report.fallback,
                "findings": [{"signal": found.signal, "stage": found.stage, "label": stages.SIGNALS[found.signal][1],
                              "quote": found.quote, "when": _turn_day(chat, found.turn)} for found in report.findings],
                "contradictions": [{"topic": item.topic, "first": item.first, "second": item.second,
                                    "first_when": _turn_day(chat, item.first_turn), "second_when": _turn_day(chat, item.second_turn)}
                                   for item in report.contradictions],
            }
        state["breaks"] = [{"topic": item.topic, "profile_quote": item.profile_quote, "chat_quote": item.chat_quote,
                            "when": _turn_day(chat, item.chat_turn)} for item in thread.breaks]
        state["items"] = [{"kind": item.kind, "value": item.value, "label": item.label} for item in thread.items]
        return state
