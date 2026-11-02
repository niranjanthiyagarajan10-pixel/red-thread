"""One live First Knot, from the phrase to the last answer, and the later re-asks.

The browser streams the person's own mic here and reads events back. Audio passes through to live speech and is
gone. A knot lives in memory: the phrase, the questions and the details code verified in their own answers.
"""
import random
import secrets
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from redthread import knot as knots_logic
from redthread.senses import LANGUAGE_BIAS
from redthread.shield import scrub

MAX_AUDIO_CHUNK = 64 * 1024
PHRASE_SECONDS = 30
MAX_EVENTS = 2000
MAX_KNOTS = 500
SESSION_SECONDS = 30 * 60
KNOT_SECONDS = 7 * 24 * 60 * 60


class NotAllowed(Exception):
    pass


class KnotSession:
    """Steps run in order: ready, the phrase, each question, done. A re-ask has one step."""

    def __init__(self, knot, model, speech_factory, pool, clock, reask=None, on_recheck=None, token=None):
        self.token = token or knot.token
        self.knot = knot
        self.model = model
        self.speech_factory = speech_factory
        self.pool = pool
        self.clock = clock
        self.reask = reask
        self.on_recheck = on_recheck
        self.order = ["reask"] if reask else ["phrase"] + [item.id for item in knot.questions]
        self.step = "ready"
        self.heard = {}
        self.created = clock()
        self.started = None
        self.speech = None
        self.judging = 0
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

    def state(self):
        state = {"step": self.step, "mode": "reask" if self.reask else "intro"}
        if self.reask:
            state["question"] = self.reask.text
            return state
        state["questions"] = [{"id": item.id, "interest": item.interest, "text": item.text} for item in self.knot.questions]
        state["results"] = {str(key): value.label for key, value in self.knot.results.items()}
        if self.step != "ready":
            state["phrase"] = self.knot.phrase
        return state

    def begin(self):
        with self._changed:
            if self.step != "ready":
                return
            self.started = self.clock()
            self.step = self.order[0]
        self._open_speech()
        event = {"type": "step", "step": self.step}
        if self.step == "phrase":
            event["phrase"] = self.knot.phrase
        else:
            event["question"] = self.reask.text
        self._emit(**event)

    def _open_speech(self):
        if not self.speech_factory:
            return
        words = self.knot.phrase.split() + [word for item in self.knot.interests for word in [item] + item.split() if len(word) > 2]
        try:
            self.speech = self.speech_factory(self._heard, self._partial, self._lost, list(dict.fromkeys(words))[:30])
            if self.speech:
                self.speech.start()
        except Exception:
            traceback.print_exc()
            self.speech = None

    def audio(self, pcm):
        if self.step in ("ready", "done") or len(pcm) % 2 or len(pcm) > MAX_AUDIO_CHUNK or not self.speech:
            return False
        return self.speech.send(pcm)

    def say(self, text):
        """Typed instead of spoken, for anyone who can't or won't talk."""
        if self.step not in ("ready", "done"):
            self._heard(text)

    def _partial(self, text):
        if self.step not in ("ready", "done"):
            self._emit(type="partial", text=scrub(text).text)

    def _lost(self):
        self.speech = None
        self._emit(type="captions_off")

    def _heard(self, text):
        text = " ".join(scrub(text).text.split())
        if not text or self.step in ("ready", "done"):
            return
        with self._changed:
            step = self.step
            self.heard.setdefault(step, []).append(text)
            heard = " ".join(self.heard[step])
        self._emit(type="caption", step=step, text=text)
        if step == "phrase" and not self.knot.phrase_heard and knots_logic.phrase_said(self.knot.phrase, heard):
            if self.clock() - self.started <= PHRASE_SECONDS:
                self.knot.phrase_heard = True
                self._emit(type="phrase_ok")
                self._advance()

    def next(self):
        """The person says they're done with this step. Judging runs in the background while they carry on."""
        with self._changed:
            step = self.step
            if step in ("ready", "done"):
                return
            said = " ".join(self.heard.get(step, []))
            if step != "phrase":
                self.judging += 1
        if step == "reask":
            self.pool.submit(self._recheck, said)
        elif step != "phrase":
            question = next(item for item in self.knot.questions if item.id == step)
            self.pool.submit(self._judge, question, said)
        self._advance()

    def _advance(self):
        with self._changed:
            position = self.order.index(self.step)
            self.step = self.order[position + 1] if position + 1 < len(self.order) else "done"
            step, finished = self.step, self.step == "done" and self.judging == 0
        if step == "done":
            self._close_speech()
        else:
            question = next(item for item in self.knot.questions if item.id == step)
            self._emit(type="step", step=step, question=question.text, interest=question.interest)
        if finished:
            self._finish()

    def _judge(self, question, said):
        try:
            self.knot.results[question.id] = knots_logic.judge_answer(self.model, question, said)
            self._emit(type="judged", question=question.id, label=self.knot.results[question.id].label)
        except Exception:
            traceback.print_exc()
            self.knot.results[question.id] = knots_logic.Judged("unscored", said)
        self._judged()

    def _recheck(self, said):
        try:
            label = knots_logic.check_reask(self.model, self.reask, said)
        except Exception:
            traceback.print_exc()
            label = "unscored"
        if self.on_recheck:
            self.on_recheck(label)
        self._emit(type="rechecked", label=label)
        self._judged()

    def _judged(self):
        with self._changed:
            self.judging -= 1
            finished = self.step == "done" and self.judging == 0
        if finished:
            self._finish()

    def _finish(self):
        if self.reask:
            self._emit(type="ended")
            return
        result = knots_logic.outcome(self.knot)
        if result.tied and not self.knot.tied_at:
            self.knot.tied_at = time.time()
        self._emit(type="outcome", tied=result.tied, reason=result.reason, checked_by=result.checked_by,
                   personal=result.personal, asked=result.asked, card=self.knot.card_token if result.tied else None)

    def _close_speech(self):
        speech, self.speech = self.speech, None
        if speech:
            try:
                speech.close()
            except Exception:
                traceback.print_exc()


class Knots:
    def __init__(self, model, speech_factory="default", pool=None, clock=time.monotonic, rng=None):
        self.model = model
        self.speech_factory = self._default_speech if speech_factory == "default" else speech_factory
        self.pool = pool or ThreadPoolExecutor(4)
        self.clock = clock
        self.rng = rng or random.SystemRandom()
        self._sessions = {}
        self._knots = {}
        self._cards = {}
        self._lock = threading.Lock()

    def _default_speech(self, on_turn, on_partial, on_lost, keywords):
        if not self.model:
            return None
        return self.model.live_speech(on_turn, on_partial, on_lost, keywords, LANGUAGE_BIAS)

    def create(self, interests):
        self.sweep()
        cleaned = knots_logic.clean_interests(interests)
        if not cleaned:
            raise NotAllowed("Add at least one interest.")
        with self._lock:
            if len(self._knots) >= MAX_KNOTS:
                raise NotAllowed("Too many sign-ups right now. Try again soon.")
        knot = knots_logic.Knot.new(cleaned)
        knot.questions, knot.fallback = knots_logic.write_questions(self.model, cleaned)
        session = KnotSession(knot, self.model, self.speech_factory, self.pool, self.clock)
        with self._lock:
            self._knots[knot.token] = knot
            self._cards[knot.card_token] = knot
            self._sessions[knot.token] = session
        return session

    def find(self, token):
        return self._sessions.get(token)

    def card(self, card_token):
        knot = self._cards.get(card_token)
        return knots_logic.card(knot) if knot else None

    def reask(self, token):
        """Later, one of their own details comes back in new words. A new session, so the old one stays as it was."""
        knot = self._knots.get(token)
        if not knot or not knot.tied_at:
            raise NotAllowed("Tie your first knot before a re-check.")
        questions = knots_logic.reask_questions(knot)
        if not questions:
            raise NotAllowed("There is nothing to re-check yet.")
        question = self.rng.choice(questions)

        def recorded(label):
            knot.rechecks.append((knots_logic._day(time.time()), label))

        session = KnotSession(knot, self.model, self.speech_factory, self.pool, self.clock, reask=question, on_recheck=recorded,
                              token=secrets.token_urlsafe(18))
        with self._lock:
            self._sessions[session.token] = session
        return session

    def sweep(self):
        now = self.clock()
        with self._lock:
            stale = [token for token, session in self._sessions.items() if now - session.created > SESSION_SECONDS]
            sessions = [self._sessions.pop(token) for token in stale]
            old = [token for token, knot in self._knots.items() if time.time() - knot.created > KNOT_SECONDS]
            for token in old:
                knot = self._knots.pop(token)
                self._cards.pop(knot.card_token, None)
        for session in sessions:
            session._close_speech()

    def close(self):
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session._close_speech()
