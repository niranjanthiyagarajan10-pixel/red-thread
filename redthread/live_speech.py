"""Live Voice Transcribe over its WebSocket, on the standard library.

One thread does all the reading and writing on the socket, because a TLS socket is not safe to use from two threads.
The key goes in the first frame, as the docs require, and only ever from the server, never from a browser.
"""
import base64
import json
import os
import queue
import select
import socket
import ssl
import struct
import threading
import time
import traceback

from redthread.model import TRANSCRIBE, TRANSCRIBE_PER_HOUR, BudgetExceeded

HOST = "api.meta.ai"
PATH = "/v1/asr/realtime"
SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
MAX_SECONDS = 30 * 60
MAX_AHEAD_BYTES = BYTES_PER_SECOND * 5
MAX_RECONNECTS = 3
_CONNECT_SECONDS = 15
_IO_TIMEOUT = 10
_STABLE_SECONDS = 30
_TEXT, _BINARY, _CLOSE, _PING, _PONG = 1, 2, 8, 9, 10


def _connect_to_meta():
    raw = socket.create_connection((HOST, 443), timeout=_CONNECT_SECONDS)
    return ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)


def _frame(opcode, payload):
    """Client frames must be masked."""
    mask = os.urandom(4)
    size = len(payload)
    if size < 126:
        head = bytes([0x80 | opcode, 0x80 | size])
    elif size < 65536:
        head = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", size)
    else:
        head = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", size)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return head + mask + masked


class _Closed(Exception):
    pass


class _Frames:
    """Reads whole frames from whatever bytes have arrived so far."""

    def __init__(self):
        self.buffer = b""

    def feed(self, data):
        self.buffer += data

    def next(self):
        if len(self.buffer) < 2:
            return None
        first, second = self.buffer[0], self.buffer[1]
        size, offset = second & 0x7F, 2
        if size == 126:
            if len(self.buffer) < 4:
                return None
            size, offset = struct.unpack(">H", self.buffer[2:4])[0], 4
        elif size == 127:
            if len(self.buffer) < 10:
                return None
            size, offset = struct.unpack(">Q", self.buffer[2:10])[0], 10
        if len(self.buffer) < offset + size:
            return None
        payload = self.buffer[offset:offset + size]
        self.buffer = self.buffer[offset + size:]
        return first & 0x0F, payload


class LiveSpeech:
    def __init__(self, api_key, ledger, on_turn, on_partial=None, on_lost=None, keywords=None, language_bias=None,
                 connect=_connect_to_meta, retry_wait=1.0, finish_wait=3.0):
        self._key = api_key
        self.ledger = ledger
        self.on_turn = on_turn
        self.on_partial = on_partial or (lambda text: None)
        self.on_lost = on_lost or (lambda: None)
        self.keywords = keywords
        self.language_bias = language_bias
        self._connect = connect
        self.retry_wait = retry_wait
        self.finish_wait = finish_wait
        self.seconds_sent = 0.0
        self._outgoing = queue.Queue()
        self._waiting_bytes = 0
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._gone = False
        self._ticket = None
        self._thread = None
        self._settled = False

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="live-speech")
        self._thread.start()

    def send(self, pcm):
        """Audio past the limits is dropped. A call must keep going even when captions cannot."""
        with self._lock:
            if self._gone or self._closing.is_set() or self.seconds_sent >= MAX_SECONDS:
                return False
            if self._waiting_bytes + len(pcm) > MAX_AHEAD_BYTES:
                return False
            self._waiting_bytes += len(pcm)
        self._outgoing.put(pcm)
        return True

    def close(self):
        self._closing.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(5)
        self._settle()

    def _settle(self):
        with self._lock:
            if self._settled or self._ticket is None:
                return
            self._settled = True
        self.ledger.settle(self._ticket, self.seconds_sent / 3600 * TRANSCRIBE_PER_HOUR)

    def _give_up(self):
        with self._lock:
            self._gone = True
        self._settle()
        try:
            self.on_lost()
        except Exception:
            traceback.print_exc()

    def _run(self):
        try:
            self._ticket = self.ledger.reserve(MAX_SECONDS / 3600 * TRANSCRIBE_PER_HOUR)
        except BudgetExceeded:
            self._give_up()
            return
        failures = 0
        while not self._closing.is_set():
            try:
                sock = self._connect()
            except OSError:
                failures += 1
                if failures > MAX_RECONNECTS:
                    self._give_up()
                    return
                self._closing.wait(self.retry_wait)
                continue
            opened_at = time.monotonic()
            try:
                self._open(sock)
                self._pump(sock)
            except (OSError, _Closed, ValueError, ssl.SSLError):
                # A stream that lasted a while was working, so only quick failures count towards giving up.
                failures = 1 if time.monotonic() - opened_at > _STABLE_SECONDS else failures + 1
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
            if self._closing.is_set():
                break
            if failures > MAX_RECONNECTS:
                self._give_up()
                return
            self._closing.wait(self.retry_wait)
        self._settle()

    def _open(self, sock):
        sock.settimeout(_IO_TIMEOUT)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        sock.sendall((
            f"GET {PATH} HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            piece = sock.recv(1)
            if not piece:
                raise _Closed()
            response += piece
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise _Closed()
        handshake = {
            "authorization": {"accessToken": f"Bearer {self._key}"},
            "audioEncoding": "PCM_16KHZ",
            "model": TRANSCRIBE,
            "mode": "ENDPOINTING",
        }
        if self.keywords:
            handshake["keywords"] = self.keywords
        if self.language_bias:
            handshake["languageBias"] = self.language_bias
        sock.sendall(_frame(_TEXT, json.dumps(handshake, ensure_ascii=False).encode("utf-8")))

    def _pump(self, sock):
        frames = _Frames()
        ended = False
        ended_at = None
        while True:
            ended = self._write(sock, ended)
            if ended:
                ended_at = ended_at or time.monotonic()
                if time.monotonic() - ended_at > self.finish_wait:
                    return
            pending = getattr(sock, "pending", lambda: 0)()
            readable = pending or select.select([sock], [], [], 0.02)[0]
            if readable:
                data = sock.recv(65536)
                if not data:
                    raise _Closed()
                frames.feed(data)
            while True:
                frame = frames.next()
                if frame is None:
                    break
                if self._handle(sock, *frame):
                    return

    def _write(self, sock, ended):
        while True:
            try:
                pcm = self._outgoing.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                self._waiting_bytes -= len(pcm)
            sock.sendall(_frame(_BINARY, pcm))
            self.seconds_sent += len(pcm) / BYTES_PER_SECOND
        if self._closing.is_set() and not ended:
            sock.sendall(_frame(_TEXT, b'{"type": "endStream"}'))
            return True
        return ended

    def _handle(self, sock, opcode, payload):
        """True when the stream has finished."""
        if opcode == _CLOSE:
            if self._closing.is_set():
                return True
            raise _Closed()
        if opcode == _PING:
            sock.sendall(_frame(_PONG, payload))
            return False
        if opcode != _TEXT:
            return False
        try:
            message = json.loads(payload)
        except ValueError:
            return False
        kind = message.get("type")
        text = str(message.get("transcript") or "").strip()
        try:
            if kind == "transcript" and not message.get("final") and text:
                self.on_partial(text)
            elif kind == "speechComplete" and text:
                self.on_turn(text)
            elif kind == "error":
                print(f"Live speech error: {str(message.get('message'))[:200]}", flush=True)
        except Exception:
            traceback.print_exc()
        return False
