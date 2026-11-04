"""The second layer. When a press finds a known scam script, Wonder checks the scammer's claim against official sources.

The question is fixed for each kind of script, so nothing from the person's screen is sent. Code keeps only sentences
that cite an official Indian government or regulator site. A check is kept for a day, because the same scripts reach
many people, so it runs once and everyone after gets it at once. Web results only add a source to a warning that
code already gave. They never make a warning weaker.
"""
import re
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timedelta

from redthread.investigate import Line, domain_of, sourced_lines

KEEP = timedelta(hours=24)
KEEP_EMPTY = timedelta(hours=1)
NOTICE_LIMIT = 180
TITLE = "Checked with official sources."
TRIES = 2
SEARCH_TIMEOUT = 90
WARM = ("digital_arrest",)
_OFFICIAL_ENDINGS = (".gov.in", ".nic.in")
_OFFICIAL = {"rbi.org.in", "npci.org.in", "cert-in.org.in"}

INSTRUCTIONS = (
    "You check a claim that scammers in India make to people. Search the web. "
    "Answer in at most three short, plain sentences. Each sentence must say what an official Indian government "
    "or regulator source says about the claim, and cite it. Do not name any person or business. "
    "If you find nothing official, say so."
)
QUESTIONS = {
    "digital_arrest": ("Callers claim to be police, CBI, ED, customs or narcotics officers, say the person is under digital arrest, "
                       "keep them on a video call and ask for money to clear their name. What do official Indian government sources say?"),
    "telecom": ("Callers claim to be from TRAI or the telecom department and say the person's mobile number or SIM will be blocked "
                "or is part of a case. What do official Indian government sources say?"),
    "parcel": ("Callers say a parcel in the person's name holds drugs or illegal items, then pass the call to police or customs "
               "on video. What do official Indian government sources say?"),
    "safe_account": ("Callers claiming to be from a bank, RBI or police ask people to move their money to a safe or secure account. "
                     "What do official Indian sources say?"),
    "remote_app": ("Callers claiming to be from a bank or an agency ask people to install a screen sharing or remote access app. "
                   "What do official Indian sources say?"),
    "code_request": "Callers or messages claiming to be from a bank ask people to share an OTP or PIN. What do official Indian sources say?",
    "investment": ("Apps or chats promise trading or task profits, pay a small amount first, then ask for deposits or fees "
                   "before money can be withdrawn. What do SEBI, RBI or Indian government sources say?"),
}
_TELECOM = re.compile(r"\btrai\b|telecom|\bsim\b|number (?:will be |is |has been )?(?:blocked|disconnected|suspended)", re.I)
_PARCEL = re.compile(r"parcel|courier|customs|consignment", re.I)


def family_for(live_kind, text=""):
    """Which fixed question fits this scam. Code picks it from the press, so the screen itself never goes to the web."""
    if live_kind == "fake_authority":
        if _TELECOM.search(text):
            return "telecom"
        if _PARCEL.search(text):
            return "parcel"
        return "digital_arrest"
    if live_kind in ("safe_account", "remote_app", "code_request"):
        return live_kind
    if live_kind in ("investment_pitch", "pay_to_withdraw"):
        return "investment"
    return None


def official(url):
    host = domain_of(url)
    return host.endswith(_OFFICIAL_ENDINGS) or host in _OFFICIAL


def official_lines(found):
    """Sentences that cite at least one official site, with only those sources kept."""
    lines = []
    for line in sourced_lines(found):
        sources = [url for url in line.sources if official(url)]
        if sources:
            lines.append(Line(line.text, sources))
    return lines


@dataclass
class Checked:
    family: str
    lines: list
    at: datetime

    def fresh(self, now):
        """A check with sources is kept a day. One that found nothing official is tried again after an hour."""
        return now - self.at <= (KEEP if self.lines else KEEP_EMPTY)


def _fit(text, limit):
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def notice(checked):
    """The second notification, written by code from the first official line. Empty if nothing official was found."""
    if not checked or not checked.lines:
        return ""
    line = checked.lines[0]
    return f"{TITLE}\n{_fit(f'{line.text} Source: {domain_of(line.sources[0])}', NOTICE_LIMIT)}"


class Claims:
    def __init__(self, model, workers=2):
        self.model = model
        self._done = {}
        self._running = {}
        self._failures = deque(maxlen=20)
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="claims")

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

    def start(self, family, now):
        """Starts a check unless a fresh one is kept or one is already running."""
        if not family or not self.model or family not in QUESTIONS:
            return None
        with self._lock:
            kept = self._done.get(family)
            if kept and kept.fresh(now):
                return None
            if family not in self._running:
                self._running[family] = self._pool.submit(self._check, family, now)
            return self._running[family]

    def warm(self, now):
        """Starts the checks the demo and most victims hit first, so the first press gets notice 2 at once."""
        for family in WARM:
            self.start(family, now)

    def failures(self):
        """Recent failed checks: the time and the reason only. The question is fixed, so nothing personal is in it."""
        with self._lock:
            return list(self._failures)

    def _check(self, family, now):
        lines = None
        for attempt in range(TRIES):
            try:
                found = official_lines(self.model.search(INSTRUCTIONS, QUESTIONS[family], effort="low", max_output=1500,
                                                         timeout=SEARCH_TIMEOUT))
            except Exception as error:  # Any failure must free the slot, or the check would stay stuck as running.
                why = f"{family}: {type(error).__name__}: {str(error)[:80]}"
                print(f"claim check failed: {why}", file=sys.stderr)
                with self._lock:
                    self._failures.appendleft({"at": f"{now:%H:%M}", "why": why})
                continue
            lines = found
            if found:
                break
            with self._lock:
                self._failures.appendleft({"at": f"{now:%H:%M}", "why": f"{family}: nothing official found"})
        with self._lock:
            self._running.pop(family, None)
            if lines is not None:
                self._done[family] = Checked(family, lines, now)
        return lines

    def result(self, family, now, wait=0.0):
        """The kept check for this kind of scam, waiting up to `wait` seconds for one that is running."""
        if not family:
            return None
        with self._lock:
            kept = self._done.get(family)
            running = self._running.get(family)
        if kept and kept.fresh(now):
            return kept
        if running is None or wait <= 0:
            return None
        try:
            running.result(timeout=wait)
        except FutureTimeout:
            return None
        with self._lock:
            kept = self._done.get(family)
        return kept if kept and kept.fresh(now) else None
