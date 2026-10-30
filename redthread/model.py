"""Calls Muse Spark through the Meta Model API, with a spending cap, a cache and retries."""
import hashlib
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE_URL = "https://api.meta.ai/v1"
SPARK = "muse-spark-1.3"
PRICE_PER_MILLION = {SPARK: (1.25, 4.25)}
HOME = Path(os.environ.get("REDTHREAD_HOME", Path.home() / "muse-spike"))
MAX_ATTEMPTS = 5
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
CHARS_PER_TOKEN_WORST_CASE = 2


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
        for line in env_file.read_text(encoding="utf-8").splitlines():
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
class Reply:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    finish_reason: str = ""
    cost: float = 0.0
    cached: bool = False


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

    def chat(self, messages, schema=None, effort="low", max_output=4000):
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

        input_chars = sum(len(message["content"]) for message in messages)
        ticket = self.ledger.reserve(self._cost(input_chars / CHARS_PER_TOKEN_WORST_CASE, max_output))
        try:
            reply = self._send(encoded)
        except Exception:
            self.ledger.settle(ticket, 0.0)
            raise
        self.ledger.settle(ticket, reply.cost)
        self._write_cache(encoded, reply)
        return reply

    def _send(self, encoded):
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        last_status = None
        for attempt in range(MAX_ATTEMPTS):
            status, raw = self.transport(f"{BASE_URL}/chat/completions", headers, encoded, self.timeout)
            if status == 200:
                return self._parse(raw)
            last_status = status
            if status != 0 and status not in RETRY_STATUSES:
                break
            if attempt + 1 < MAX_ATTEMPTS:
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
