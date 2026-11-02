"""Calls Muse Spark through the Meta Model API, with a spending cap, a cache and retries."""
import hashlib
import io
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

BASE_URL = "https://api.meta.ai/v1"
SPARK = "muse-spark-1.3"
WONDER = "muse-wonder-1.0"
PRICE_PER_MILLION = {SPARK: (1.25, 4.25), WONDER: (4.0, 20.0)}
WEB_SEARCH_EACH = 0.0025
WORST_CASE_SEARCHES = 10
WORST_CASE_SEARCH_TOKENS = 40000
HOME = Path(os.environ.get("REDTHREAD_HOME", Path.home() / "muse-spike"))
MAX_ATTEMPTS = 5
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
CHARS_PER_TOKEN_WORST_CASE = 2
IMAGE_CHARS_WORST_CASE = 20000
TRANSCRIBE = "muse-voice-transcribe-1.0"
TRANSCRIBE_PER_HOUR = 0.18


def _content_chars(content):
    if isinstance(content, str):
        return len(content)
    return sum(len(part.get("text", "")) if part.get("type") == "text" else IMAGE_CHARS_WORST_CASE for part in content)


class ModelError(Exception):
    """A model call failed. Callers fall back to rules or templates."""


class BudgetExceeded(Exception):
    """The next call could pass the spending cap, so it was not sent."""


def load_api_key(environ=os.environ, home=HOME):
    """The environment first, then a .env file kept outside the project folder."""
    key = environ.get("MODEL_API_KEY")
    if key:
        return key
    env_file = Path(home) / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("MODEL_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


class Ledger:
    """Reserves the worst-case cost before a call, then settles the real cost."""

    def __init__(self, cap_usd, path):
        self.cap = cap_usd
        self.path = Path(path)
        self.reserved = 0.0
        self._lock = threading.Lock()
        self.spent = json.loads(self.path.read_text(encoding="utf-8"))["spent"] if self.path.exists() else 0.0

    def reserve(self, worst_case):
        with self._lock:
            if self.spent + self.reserved + worst_case > self.cap:
                raise BudgetExceeded(f"Next call could pass the ${self.cap:.2f} cap. Spent ${self.spent:.2f}.")
            self.reserved += worst_case
        return worst_case

    def settle(self, ticket, actual):
        with self._lock:
            self.reserved = max(0.0, self.reserved - ticket)
            self.spent += actual
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"spent": self.spent}), encoding="utf-8")


@dataclass
class Found:
    """A web-searched answer. Citations are (start, end, url) spans of text, so code can keep only sourced sentences."""
    text: str
    citations: list
    searches: int = 0
    cost: float = 0.0


@dataclass
class Reply:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    finish_reason: str = ""
    cost: float = 0.0
    cached: bool = False


def _wav_seconds(wav):
    try:
        with wave.open(io.BytesIO(wav), "rb") as audio:
            return audio.getnframes() / audio.getframerate()
    except (wave.Error, EOFError) as error:
        raise ModelError("Audio is not a WAV file.") from error


def _http_post(url, headers, body, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        return 0, str(error).encode("utf-8")


class MetaModel:
    def __init__(self, api_key, ledger, model=SPARK, cache_dir=None, transport=_http_post, sleep=time.sleep, timeout=300):
        if "contributor" in model:
            raise ValueError("Contributor models let Meta train on inputs. Never send user data to them.")
        if not api_key:
            raise ValueError("No MODEL_API_KEY found.")
        self._api_key = api_key
        self.ledger = ledger
        self.model = model
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.transport = transport
        self.sleep = sleep
        self.timeout = timeout

    def using(self, model):
        """The same key, ledger and transport for another hosted model, such as Wonder for the investigator."""
        return MetaModel(self._api_key, self.ledger, model=model, cache_dir=None, transport=self.transport, sleep=self.sleep, timeout=self.timeout)

    def transcribe(self, wav, language_bias=None, keywords=None):
        """Voice Transcribe takes only WAV. Other audio must be converted first."""
        seconds = _wav_seconds(wav)
        settings = {"mode": "ENDPOINTING", "model": TRANSCRIBE, "audioEncoding": "WAV"}
        if language_bias:
            settings["languageBias"] = language_bias
        if keywords:
            settings["keywords"] = keywords
        encoded = json.dumps(settings, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n" + hashlib.sha256(wav).digest()
        cached = self._read_cache(encoded)
        if cached:
            return cached

        boundary = "redthread" + hashlib.sha256(wav).hexdigest()[:24]
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="request"\r\nContent-Type: application/json\r\n\r\n'.encode("ascii")
            + json.dumps(settings, ensure_ascii=False).encode("utf-8")
            + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="note.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode("ascii")
            + wav
            + f"\r\n--{boundary}--\r\n".encode("ascii")
        )
        ticket = self.ledger.reserve(seconds / 3600 * TRANSCRIBE_PER_HOUR)
        try:
            raw = self._post("/asr/transcribe", body, f"multipart/form-data; boundary={boundary}", self.timeout, MAX_ATTEMPTS)
            data = json.loads(raw)
            reply = Reply(text=data["transcript"], cost=int(data.get("audioDurationMs", seconds * 1000)) / 3_600_000 * TRANSCRIBE_PER_HOUR)
        except (ValueError, KeyError, TypeError) as error:
            self.ledger.settle(ticket, 0.0)
            raise ModelError("Transcribe reply had an unexpected shape.") from error
        except Exception:
            self.ledger.settle(ticket, 0.0)
            raise
        self.ledger.settle(ticket, reply.cost)
        self._write_cache(encoded, reply)
        return reply

    def live_speech(self, on_turn, on_partial, on_lost, keywords=None, language_bias=None):
        """The key stays in this class and in the server. It never reaches a browser."""
        from redthread.live_speech import LiveSpeech

        return LiveSpeech(self._api_key, self.ledger, on_turn, on_partial, on_lost, keywords, language_bias)

    def chat(self, messages, schema=None, effort="low", max_output=4000, timeout=None, attempts=None):
        body = {
            "model": self.model,
            "messages": messages,
            "reasoning_effort": effort,
            "max_completion_tokens": max_output,
        }
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}

        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        cached = self._read_cache(encoded)
        if cached:
            return cached

        input_chars = sum(_content_chars(message["content"]) for message in messages)
        ticket = self.ledger.reserve(self._cost(input_chars / CHARS_PER_TOKEN_WORST_CASE, max_output))
        try:
            raw = self._post("/chat/completions", encoded, "application/json", timeout or self.timeout, attempts or MAX_ATTEMPTS)
            reply = self._parse(raw)
        except Exception:
            self.ledger.settle(ticket, 0.0)
            raise
        self.ledger.settle(ticket, reply.cost)
        self._write_cache(encoded, reply)
        return reply

    def search(self, instructions, text, effort="low", max_output=4000, timeout=None, attempts=None):
        """The Responses API with web search. Never cached: the web changes, and the question can hold a person's chat words."""
        body = {
            "model": self.model,
            "input": [{"role": "developer", "content": instructions}, {"role": "user", "content": text}],
            "tools": [{"type": "web_search"}],
            "reasoning": {"effort": effort},
            "max_output_tokens": max_output,
        }
        worst = self._cost((len(instructions) + len(text)) / CHARS_PER_TOKEN_WORST_CASE + WORST_CASE_SEARCH_TOKENS, max_output)
        ticket = self.ledger.reserve(worst + WEB_SEARCH_EACH * WORST_CASE_SEARCHES)
        try:
            raw = self._post("/responses", json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json", timeout or self.timeout, attempts or 2)
            found = self._parse_found(raw)
        except Exception:
            self.ledger.settle(ticket, 0.0)
            raise
        self.ledger.settle(ticket, found.cost)
        return found

    def _parse_found(self, raw):
        try:
            data = json.loads(raw)
            text, citations, searches = "", [], 0
            for item in data["output"]:
                if item.get("type") == "web_search_call":
                    searches += 1
                elif item.get("type") == "message":
                    for part in item.get("content") or []:
                        if part.get("type") != "output_text":
                            continue
                        offset = len(text)
                        text += part.get("text", "")
                        for note in part.get("annotations") or []:
                            if note.get("type") == "url_citation" and note.get("url"):
                                citations.append((offset + int(note["start_index"]), offset + int(note["end_index"]), note["url"]))
            usage = data.get("usage") or {}
            cost = self._cost(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))) + WEB_SEARCH_EACH * searches
            return Found(text, citations, searches, cost)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
            raise ModelError("Search reply had an unexpected shape.") from error

    def _post(self, path, body, content_type, timeout, attempts):
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": content_type}
        last_status = None
        for attempt in range(attempts):
            status, raw = self.transport(f"{BASE_URL}{path}", headers, body, timeout)
            if status == 200:
                return raw
            last_status = status
            if status != 0 and status not in RETRY_STATUSES:
                break
            if attempt + 1 < attempts:
                self.sleep(min(30, 2 ** attempt) + random.random())
        raise ModelError(f"Model call failed with status {last_status}.")

    def _parse(self, raw):
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            usage = data.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            prompt = int(usage.get("prompt_tokens", 0))
            completion = int(usage.get("completion_tokens", 0))
            return Reply(
                text=choice["message"]["content"] or "",
                prompt_tokens=prompt,
                completion_tokens=completion,
                reasoning_tokens=int(details.get("reasoning_tokens", 0)),
                finish_reason=choice.get("finish_reason", ""),
                cost=self._cost(prompt, completion),
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ModelError("Model reply had an unexpected shape.") from error

    def _cost(self, prompt_tokens, completion_tokens):
        price_in, price_out = PRICE_PER_MILLION[self.model]
        return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000

    def _cache_path(self, encoded):
        return self.cache_dir / f"{hashlib.sha256(encoded).hexdigest()}.json"

    def _read_cache(self, encoded):
        if not self.cache_dir or not self._cache_path(encoded).exists():
            return None
        data = json.loads(self._cache_path(encoded).read_text(encoding="utf-8"))
        return Reply(**{**data, "cost": 0.0, "cached": True})

    def _write_cache(self, encoded, reply):
        if not self.cache_dir:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(encoded).write_text(json.dumps(reply.__dict__, ensure_ascii=False), encoding="utf-8")
