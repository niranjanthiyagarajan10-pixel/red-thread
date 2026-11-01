"""Joins cases that share an account, number, link, image or script.

Only salted hashes are stored, never raw identifiers or chat text. People learn a count, not who else reported.
Two people connect only if both say yes, and the shield checks every message between them.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field

from redthread.model import HOME
from redthread.shield import check_relay

SHINGLE_WORDS = 5
MIN_SCRIPT_WORDS = 8
SCRIPT_MATCH = 0.3


def _default_salt():
    value = os.environ.get("REDTHREAD_SALT")
    if value:
        return value.encode("utf-8")
    path = HOME / ".network_salt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
    return path.read_bytes()


def fingerprint(text, salt=b""):
    """Hashes of every run of five words. Spark puts other languages into English first, so scripts match across them."""
    words = re.findall(r"\w+", text.casefold())
    if len(words) < MIN_SCRIPT_WORDS:
        return set()
    shingles = (" ".join(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1))
    return {hmac.new(salt, shingle.encode("utf-8"), hashlib.sha256).hexdigest()[:16] for shingle in shingles}


def similarity(first, second):
    """Share of the shorter text found in the longer one, so a short copy of a long script still matches."""
    if not first or not second:
        return 0.0
    return len(first & second) / min(len(first), len(second))


def _normalize(kind, value):
    value = value.strip().casefold()
    return re.sub(r"\D", "", value) if kind == "phone" else value


@dataclass
class Match:
    case_id: str
    kinds: list = field(default_factory=list)


@dataclass
class RelayResult:
    delivered: bool
    text: str = None
    original: str = None
    reason: str = None


class Network:
    def __init__(self, path, salt=None):
        self.salt = salt if salt is not None else _default_salt()
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(
            "create table if not exists identifiers (case_id text, kind text, digest text);"
            "create table if not exists scripts (case_id text, hashes text);"
            "create table if not exists opt_ins (from_case text, to_case text, primary key (from_case, to_case));"
        )

    def close(self):
        self._db.close()

    def forget_case(self, case_id):
        with self._lock, self._db:
            self._db.execute("delete from identifiers where case_id = ?", (case_id,))
            self._db.execute("delete from scripts where case_id = ?", (case_id,))
            self._db.execute("delete from opt_ins where from_case = ? or to_case = ?", (case_id, case_id))

    def _digest(self, kind, value):
        return hmac.new(self.salt, f"{kind}:{_normalize(kind, value)}".encode("utf-8"), hashlib.sha256).hexdigest()

    def _digests(self, identifiers, images):
        pairs = [(kind, self._digest(kind, value)) for kind, values in identifiers.items() for value in values]
        pairs += [("image", hmac.new(self.salt, image, hashlib.sha256).hexdigest()) for image in images or ()]
        return pairs

    def add_case(self, case_id, identifiers, script_text=None, images=None):
        rows = [(case_id, kind, digest) for kind, digest in self._digests(identifiers, images)]
        hashes = fingerprint(script_text or "", self.salt)
        with self._lock, self._db:
            self._db.executemany("insert into identifiers values (?, ?, ?)", rows)
            if hashes:
                self._db.execute("insert into scripts values (?, ?)", (case_id, json.dumps(sorted(hashes))))

    def matches(self, case_id, identifiers, script_text=None, images=None):
        found = {}
        hashes = fingerprint(script_text or "", self.salt)
        with self._lock:
            for kind, digest in self._digests(identifiers, images):
                rows = self._db.execute(
                    "select distinct case_id from identifiers where kind = ? and digest = ? and case_id != ?",
                    (kind, digest, case_id),
                )
                for (other,) in rows.fetchall():
                    found.setdefault(other, []).append(kind)
            if hashes:
                stored_rows = self._db.execute("select case_id, hashes from scripts where case_id != ?", (case_id,)).fetchall()
                for other, stored in stored_rows:
                    if similarity(hashes, set(json.loads(stored))) >= SCRIPT_MATCH:
                        found.setdefault(other, []).append("script")
        return [Match(other, list(dict.fromkeys(kinds))) for other, kinds in found.items()]

    def report_count(self, case_id, identifiers, script_text=None, images=None):
        return len(self.matches(case_id, identifiers, script_text, images))

    def opt_in(self, from_case, to_case):
        with self._lock, self._db:
            self._db.execute("insert or ignore into opt_ins values (?, ?)", (from_case, to_case))

    def linked(self, first, second):
        with self._lock:
            rows = self._db.execute(
                "select count(*) from opt_ins where (from_case = ? and to_case = ?) or (from_case = ? and to_case = ?)",
                (first, second, second, first),
            )
            return rows.fetchone()[0] == 2

    def relay(self, from_case, to_case, text, translate=None):
        """Carries a message between two people who both agreed. If translation fails, the original still goes."""
        if not self.linked(from_case, to_case):
            return RelayResult(False, reason="not_linked")
        check = check_relay(text)
        if check.blocked:
            return RelayResult(False, reason=check.reason)
        delivered = text
        if translate:
            try:
                delivered = translate(text)
            except Exception:
                delivered = text
        return RelayResult(True, delivered, text)
