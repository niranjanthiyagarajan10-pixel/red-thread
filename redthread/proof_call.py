"""One live Proof Call: two people, one consent gate, and cards only the person who forwarded the chat can see.

Nothing here is written to disk. Audio passes through and is gone. Calls live in memory and are swept within the hour,
the same time we keep a forwarded chat.
"""
import queue
import secrets
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from redthread import proof
from redthread.reply import render
from redthread.senses import LANGUAGE_BIAS
from redthread.shield import scrub

CONSENT_TEXT = (
    "An AI is on this call. It hears both of you and compares what is said with an earlier chat. "
    "Only the person who invited you sees the results. Audio is not recorded. You can leave at any time."
)
MAX_AUDIO_CHUNK = 64 * 1024
MAX_LINE_CHECKS = 30
MAX_SCORES = 20
MAX_ANSWER_TURNS = 3
MAX_AGE_SECONDS = 60 * 60
MAX_CALLS = 200
MAX_EVENTS = 4000
_LAST_WORDS_WAIT = 5
_TURN_SETTLES_SECONDS = 1.0
_SAME_ANSWER_SECONDS = 4
_CHUNK_SECONDS = 0.1
_CHUNK_BYTES = int(_CHUNK_SECONDS * 32000)


class NotAllowed(Exception):
    pass


class ProofCall:
    def __init__(self, memory, model, speech_factory, pool, on_end=None, owner=None, person=None):
        self.memory = memory
        self.model = model
        self.speech_factory = speech_factory
        self.pool = pool
        self.on_end = on_end
        self.owner = owner
        self.person = person
        self.tokens = {"asker": secrets.token_urlsafe(18), "guest": secrets.token_urlsafe(18)}
        self.phase = "waiting"
        self.created = time.monotonic()
        self.consents = set()
        self.speech = {}
        self.speech_lost = set()
        self.line_checks = 0
        self.scores_used = 0
        self.alarms = set()
        self.asked = []
        self.results = {}
        self.differs = []
        self.pending = None
        self.clock = time.monotonic
        self._guest_turns = 0
        self._last_guest_turn = 0.0
        self._lines = queue.Queue()
        self._line_playing = False
        self._feeder = None
        self._events = {"asker": [], "guest": []}
        self._changed = threading.Condition()

    def _emit(self, role, **event):
        with self._changed:
            rows = self._events[role]
            if len(rows) < MAX_EVENTS:
                rows.append({**event, "seq": len(rows)})
            self._changed.notify_all()

    def events(self, role, after, wait=0):
        """Returns new events and where to read from next. Waits a little, so a browser can hold the request open."""
        with self._changed:
            if len(self._events[role]) <= after and wait:
                self._changed.wait(wait)
            rows = self._events[role]
            return rows[after:], len(rows)

    def state(self, role):
        state = {"role": role, "phase": self.phase, "consent_text": CONSENT_TEXT, "consented": role in self.consents, "simulated": bool(self.person)}
        if role == "asker" and self.memory:
            state["questions"] = [{"id": item.id, "text": item.text, "quote": item.quote} for item in self.memory.questions]
            state["other_joined"] = "guest" in self.consents
        return state

    def consent(self, role):
        with self._changed:
            if self.phase != "waiting" or role in self.consents:
                return
            self.consents.add(role)
            if self.person:
                self.consents.add("guest")
            both = self.consents == {"asker", "guest"}
            if both:
                self.phase = "live"
        if role == "guest":
            self._emit("asker", type="joined")
        if both:
            self._begin()

    def _begin(self):
        for role in ("asker", "guest"):
            self._emit(role, type="live")
            if not self.person:
                self._open_speech(role)

    def _open_speech(self, role):
        """A simulated call opens a stream only when there is audio, since a silent stream is closed by the server."""
        if role in self.speech or role in self.speech_lost or not self.speech_factory:
            return self.speech.get(role)
        speech = self.speech_factory(
            role,
            lambda text, who=role: self._heard(who, text),
            lambda text, who=role: self._partial(who, text),
            lambda who=role: self._lost(who),
            self.memory.keywords,
        )
        if speech:
            self.speech[role] = speech
            speech.start()
        return speech

    def audio(self, role, pcm):
        """Refused until both people have agreed, and after the call ends."""
        if self.phase != "live" or len(pcm) % 2 or len(pcm) > MAX_AUDIO_CHUNK:
            return False
        speech = self._open_speech(role)
        return speech.send(pcm) if speech else False

    def signal(self, role, data):
        """Sets up the direct audio and video between the two browsers. It carries no call content."""
        other = "guest" if role == "asker" else "asker"
        self._emit(other, type="signal", data=data)

    def _who(self, role):
        return "you" if role == "asker" else "them"

    def _partial(self, role, text):
        if self.phase == "live":
            self._emit("asker", type="partial", who=self._who(role), text=scrub(text).text)

    def _lost(self, role):
        self.speech.pop(role, None)
        self.speech_lost.add(role)
        self._emit("asker", type="captions_off", who=self._who(role))

    def _heard(self, role, text):
        if self.phase != "live":
            return
        text = scrub(text).text
        self._emit("asker", type="caption", who=self._who(role), text=text)
        if role == "asker":
            found = proof.question_said(self.memory, text)
            if found:
                self._start_question(found)
            return
        now = self.clock()
        self._guest_turns += 1
        self._last_guest_turn = now
        alarm = proof.live_alarm(text)
        if alarm:
            if alarm.kind not in self.alarms:
                self.alarms.add(alarm.kind)
                self._emit("asker", type="alarm", kind=alarm.kind, headline=alarm.answer.headline, next_step=alarm.answer.next_step, said=text)
            return
        with self._changed:
            pending = self._still_answering(now)
            if pending:
                pending["said"].append(text)
                pending["last"] = now
                said, turns = " ".join(pending["said"]), len(pending["said"])
        if pending:
            self.pool.submit(self._score, pending["question"], said, turns)
        elif self.line_checks < MAX_LINE_CHECKS and proof.worth_checking(self.memory, text):
            self.line_checks += 1
            self.pool.submit(self._check_line, text)

    def _still_answering(self, now):
        """Speech can split one answer into several turns, so a quick follow-on still counts, even after a clear answer."""
        pending = self.pending
        if pending and pending["said"] and pending["settled"] and now - pending["last"] > _SAME_ANSWER_SECONDS:
            self.pending = pending = None
        if pending and len(pending["said"]) >= MAX_ANSWER_TURNS:
            self.pending = pending = None
        return pending

    def _start_question(self, question):
        with self._changed:
            if question.id not in self.asked:
                self.asked.append(question.id)
            self.pending = {"question": question, "said": [], "settled": False, "last": self.clock()}
        self._emit("asker", type="asked", question=question.id)

    def ask(self, role, question_id):
        if role != "asker":
            raise NotAllowed("Only the person who sent the invite can ask.")
        question = next((item for item in self.memory.questions if item.id == question_id), None)
        if question is None:
            raise NotAllowed("No such question.")
        self._start_question(question)

    def _label(self, question, said):
        if self.scores_used >= MAX_SCORES:
            label = "unscored"
        else:
            self.scores_used += 1
            label = proof.score_answer(self.model, question, said)
        if label == "unscored" and self.person:
            return self.person.labels.get(question.id, label)
        return label

    def _score(self, question, said, turns):
        try:
            label = self._label(question, said)
            with self._changed:
                current = self.results.get(question.id)
                if current and current["turns"] > turns:
                    return
                self.results[question.id] = {"result": proof.Result(question, label, said), "turns": turns}
                pending = self.pending
                if pending and pending["question"].id == question.id:
                    pending["settled"] = label != "no_answer"
            self._emit("asker", type="answer", question=question.id, text=question.text, quote=question.quote, said=said, label=label)
        except Exception:
            traceback.print_exc()

    def _check_line(self, text):
        try:
            found = proof.check_line(self.model, self.memory, text)
            if found:
                self.differs.append(found)
                self._emit("asker", type="differs", topic=found.topic, quote=found.quote, said=found.said)
        except Exception:
            traceback.print_exc()

    def play_guest(self, question_id):
        """The simulated person answers through the same live path, when clips and speech are there. Otherwise the words go straight in."""
        if not self.person:
            raise NotAllowed("Only a simulated call has a simulated person.")
        if self.phase != "live":
            return
        text = self.person.answers.get(question_id)
        question = next((item for item in self.memory.questions if item.id == question_id), None)
        if text is None or question is None:
            raise NotAllowed("No such question.")
        self._guest_says(text, self.person.clip(question_id), question)

    def play_line(self, text, clip=None):
        if not self.person:
            raise NotAllowed("Only a simulated call has a simulated person.")
        if self.phase == "live":
            self._guest_says(text, clip, None)

    @property
    def guest_busy(self):
        return self._line_playing or not self._lines.empty()

    def _guest_says(self, text, clip, question):
        """One line at a time, so the end of one answer can never land on the next question."""
        if not clip or not self._open_speech("guest"):
            while self.guest_busy and self.phase == "live":
                time.sleep(_CHUNK_SECONDS)
            if question:
                self._start_question(question)
            self._heard("guest", text)
            return
        self._lines.put((text, clip, question))
        with self._changed:
            if not self._feeder:
                self._feeder = threading.Thread(target=self._feed_guest, daemon=True, name="simulated-guest")
                self._feeder.start()

    def _feed_guest(self):
        """The live stream expects audio at real-time pace, or the server closes it. Between lines it hears silence."""
        silence = b"\0" * _CHUNK_BYTES
        tick = time.monotonic()
        line = None
        offset = 0
        ended_at = None
        turns_before = 0
        while self.phase == "live":
            if line is None:
                try:
                    line = self._lines.get_nowait()
                except queue.Empty:
                    line = None
                else:
                    self._line_playing = True
                    offset, ended_at, turns_before = 0, None, self._guest_turns
                    if line[2]:
                        self._start_question(line[2])
            chunk = silence
            if line and offset < len(line[1]):
                chunk = line[1][offset:offset + _CHUNK_BYTES]
                chunk += b"\0" * (len(chunk) % 2)
                offset += _CHUNK_BYTES
            self.audio("guest", chunk)
            if line and offset >= len(line[1]):
                ended_at = ended_at or time.monotonic()
                heard = self._guest_turns > turns_before
                settled = heard and self.clock() - self._last_guest_turn >= _TURN_SETTLES_SECONDS
                if settled or time.monotonic() - ended_at >= _LAST_WORDS_WAIT:
                    if not heard:
                        self._heard("guest", line[0])
                    line = None
                    self._line_playing = False
            tick += _CHUNK_SECONDS
            delay = tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def finish(self):
        with self._changed:
            if self.phase == "ended":
                return
            joined = "guest" in self.consents
            self.phase = "ended"
        for speech in list(self.speech.values()):
            try:
                speech.close()
            except Exception:
                traceback.print_exc()
        self.speech = {}
        receipt = self._receipt(joined)
        answer = proof.receipt_answer(receipt)
        event = {"type": "receipt", "answer": {"headline": answer.headline, "proof": answer.proof, "next_step": answer.next_step}, "text": render(answer)}
        if self.person:
            event["person"] = self.person.kind
        self._emit("asker", **event)
        self._emit("guest", type="ended")
        if self.on_end:
            try:
                self.on_end(self, receipt)
            except Exception:
                traceback.print_exc()

    def _receipt(self, joined):
        by_id = {item.id: item for item in self.memory.questions}
        results = []
        for question_id in self.asked:
            found = self.results.get(question_id)
            results.append(found["result"] if found else proof.Result(by_id[question_id], "no_answer", ""))
        return proof.Receipt(results, list(self.differs), joined, len(self.memory.questions) - len(results))

    def forget(self):
        self.memory = None
        self.results = {}
        self.differs = []
        self.pending = None
        self._events = {"asker": [], "guest": []}


class Calls:
    def __init__(self, model, speech_factory="default", pool=None, on_end=None, base_url=""):
        self.model = model
        self.speech_factory = self._default_speech if speech_factory == "default" else speech_factory
        self.pool = pool or ThreadPoolExecutor(8)
        self.on_end = on_end
        self.base_url = base_url.rstrip("/")
        self._calls = {}
        self._lock = threading.Lock()

    def _default_speech(self, role, on_turn, on_partial, on_lost, keywords):
        if not self.model:
            return None
        return self.model.live_speech(on_turn, on_partial, on_lost, keywords, LANGUAGE_BIAS)

    def create(self, memory, owner=None, person=None):
        self.sweep()
        factory = self.speech_factory if self.model or self.speech_factory != self._default_speech else None
        call = ProofCall(memory, self.model, factory, self.pool, self.on_end, owner, person)
        with self._lock:
            if len(self._calls) >= MAX_CALLS * 2:
                raise NotAllowed("Too many calls right now.")
            self._calls[call.tokens["asker"]] = (call, "asker")
            self._calls[call.tokens["guest"]] = (call, "guest")
        return call

    def find(self, token):
        return self._calls.get(token, (None, None))

    def link(self, token):
        return f"{self.base_url}/proof/{token}"

    def sweep(self):
        now = time.monotonic()
        with self._lock:
            old = {call for call, _ in self._calls.values() if now - call.created > MAX_AGE_SECONDS}
            self._calls = {token: entry for token, entry in self._calls.items() if entry[0] not in old}
        for call in old:
            call.finish()
            call.forget()

    def close(self):
        with self._lock:
            calls = {call for call, _ in self._calls.values()}
        for call in calls:
            call.finish()
