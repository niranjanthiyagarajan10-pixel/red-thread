"""Keeps a person's case between messages, so a server restart loses nothing.

Only the case and the person's own words are saved. The other person's chat never reaches this file.
"""
import json
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal


def _encode(value):
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, set):
        return {"$set": sorted(value)}
    raise TypeError(f"Cannot save {type(value).__name__}")


def _decode(item):
    if "$decimal" in item:
        return Decimal(item["$decimal"])
    if "$datetime" in item:
        return datetime.fromisoformat(item["$datetime"])
    if "$set" in item:
        return set(item["$set"])
    return item


class Store:
    def __init__(self, path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(
            "create table if not exists sessions (user text primary key, data text);"
            "create table if not exists seen (id text primary key);"
        )

    def close(self):
        self._db.close()

    def save(self, user, data):
        encoded = json.dumps(data, default=_encode, ensure_ascii=False)
        with self._lock, self._db:
            self._db.execute("insert or replace into sessions values (?, ?)", (user, encoded))

    def load(self, user):
        with self._lock:
            row = self._db.execute("select data from sessions where user = ?", (user,)).fetchone()
        return json.loads(row[0], object_hook=_decode) if row else None

    def delete(self, user):
        with self._lock, self._db:
            self._db.execute("delete from sessions where user = ?", (user,))

    def mark_seen(self, message_id):
        """True the first time. WhatsApp can deliver the same message more than once."""
        with self._lock, self._db:
            return self._db.execute("insert or ignore into seen values (?)", (message_id,)).rowcount == 1
