import json
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from redthread.live_speech import MAX_SECONDS, LiveSpeech
from redthread.model import Ledger


class FakeMeta:
    """The server side of the live speech socket, written to the documented protocol."""

    def __init__(self):
        self.ours, self.theirs = socket.socketpair()
        self.handshake = None
        self.audio = bytearray()
        self.texts = []
        self.buffer = b""

    def _read(self, count):
        while len(self.buffer) < count:
            piece = self.theirs.recv(65536)
            if not piece:
                raise ConnectionError("closed")
            self.buffer += piece
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def accept(self):
        request = b""
        while b"\r\n\r\n" not in request:
            request += self.theirs.recv(1)
        self.request = request.decode("ascii")
        self.theirs.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        opcode, payload = self.read_frame()
        self.handshake = json.loads(payload)
        self.send_json({"sessionId": "s1"})

    def read_frame(self):
        first, second = self._read(2)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack(">H", self._read(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", self._read(8))[0]
        self.masked = bool(second & 0x80)
        mask = self._read(4) if self.masked else b"\0\0\0\0"
        body = self._read(size)
        return first & 0x0F, bytes(byte ^ mask[index % 4] for index, byte in enumerate(body))

    def collect_until_end(self):
        while True:
            opcode, payload = self.read_frame()
            if opcode == 2:
                self.audio += payload
            elif opcode == 1:
                message = json.loads(payload)
                self.texts.append(message)
                if message.get("type") == "endStream":
                    return

    def send_json(self, value):
        self.send_frame(1, json.dumps(value).encode("utf-8"))

    def send_frame(self, opcode, payload):
        size = len(payload)
        head = bytes([0x80 | opcode])
        if size < 126:
            head += bytes([size])
        else:
            head += bytes([126]) + struct.pack(">H", size)
        self.theirs.sendall(head + payload)

    def close(self):
        self.send_frame(8, struct.pack(">H", 1000))
        self.theirs.close()


def wait_until(check, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.005)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ledger = Ledger(1.0, Path(tmp.name) / "ledger.json")
        self.turns, self.partials, self.lost = [], [], []

    def speech(self, connect, **options):
        speech = LiveSpeech(
            "secret-key", self.ledger, on_turn=self.turns.append, on_partial=self.partials.append,
            on_lost=lambda: self.lost.append(True), connect=connect, finish_wait=0.05, **options,
        )
        self.addCleanup(speech.close)
        return speech


class StreamTests(Base):
    def start(self, **options):
        self.meta = FakeMeta()
        self.addCleanup(self.meta.theirs.close)
        server = threading.Thread(target=self.meta.accept, daemon=True)
        server.start()
        speech = self.speech(lambda: self.meta.ours, **options)
        speech.start()
        server.join(3)
        return speech

    def test_handshake_carries_the_key_in_the_first_frame_as_documented(self):
        self.start()
        self.assertEqual(self.meta.handshake["authorization"]["accessToken"], "Bearer secret-key")
        self.assertEqual(self.meta.handshake["model"], "muse-voice-transcribe-1.0")
        self.assertEqual(self.meta.handshake["mode"], "ENDPOINTING")
        self.assertEqual(self.meta.handshake["audioEncoding"], "PCM_16KHZ")
        self.assertNotIn("secret-key", self.meta.request)

    def test_chat_names_go_to_the_ear(self):
        self.start(keywords=["Kavya"], language_bias=["English", "Tamil"])
        self.assertEqual(self.meta.handshake["keywords"], ["Kavya"])
        self.assertEqual(self.meta.handshake["languageBias"], ["English", "Tamil"])

    def test_audio_goes_as_masked_binary_then_end_of_stream(self):
        speech = self.start()
        speech.send(b"\x01\x02" * 800)
        reader = threading.Thread(target=self.meta.collect_until_end, daemon=True)
        reader.start()
        self.assertTrue(wait_until(lambda: len(self.meta.audio) == 1600))
        self.assertTrue(self.meta.masked)
        speech.close()
        reader.join(3)
        self.assertEqual(self.meta.texts[-1], {"type": "endStream"})

    def test_partial_words_and_finished_turns_reach_the_callbacks(self):
        self.start()
        self.meta.send_json({"type": "speechStart", "turnId": 0})
        self.meta.send_json({"type": "transcript", "transcript": "my sister", "final": False})
        self.meta.send_json({"type": "speechEnd", "turnId": 0})
        self.meta.send_json({"type": "speechComplete", "turnId": 0, "transcript": "My sister Kavya."})
        self.assertTrue(wait_until(lambda: self.turns == ["My sister Kavya."]))
        self.assertEqual(self.partials, ["my sister"])

    def test_an_empty_turn_is_ignored(self):
        self.start()
        self.meta.send_json({"type": "speechComplete", "turnId": 0, "transcript": "   "})
        self.meta.send_json({"type": "speechComplete", "turnId": 1, "transcript": "Hello there."})
        self.assertTrue(wait_until(lambda: self.turns == ["Hello there."]))

    def test_unknown_frames_and_pings_are_harmless(self):
        self.start()
        self.meta.send_json({"type": "somethingNew"})
        self.meta.send_frame(9, b"hi")
        self.meta.send_json({"type": "speechComplete", "transcript": "Still here."})
        self.assertTrue(wait_until(lambda: self.turns == ["Still here."]))

    def test_cost_is_settled_from_the_audio_actually_sent(self):
        speech = self.start()
        threading.Thread(target=self.meta.collect_until_end, daemon=True).start()
        for second in range(1, 11):
            self.assertTrue(speech.send(b"\0\0" * 16000))
            self.assertTrue(wait_until(lambda: speech.seconds_sent >= second))
        speech.close()
        self.assertAlmostEqual(self.ledger.spent, 10 / 3600 * 0.18, places=6)
        self.assertEqual(self.ledger.reserved, 0.0)

    def test_audio_far_ahead_of_the_stream_is_dropped_as_the_docs_ask(self):
        speech = self.start()
        self.assertFalse(speech.send(b"\0\0" * 16000 * 6))

    def test_audio_past_the_session_limit_is_refused(self):
        speech = self.start()
        speech.seconds_sent = MAX_SECONDS
        self.assertFalse(speech.send(b"\0\0"))

    def test_close_is_safe_to_call_twice_and_stops_sending(self):
        speech = self.start()
        threading.Thread(target=self.meta.collect_until_end, daemon=True).start()
        speech.close()
        speech.close()
        self.assertFalse(speech.send(b"\0\0"))


class FailureTests(Base):
    def test_a_dropped_stream_reconnects_and_goes_on(self):
        first, second = FakeMeta(), FakeMeta()
        self.addCleanup(second.theirs.close)
        sockets = iter([first.ours, second.ours])
        servers = [threading.Thread(target=meta.accept, daemon=True) for meta in (first, second)]
        for server in servers:
            server.start()
        speech = self.speech(lambda: next(sockets), retry_wait=0)
        speech.start()
        servers[0].join(3)
        first.close()
        servers[1].join(3)
        second.send_json({"type": "speechComplete", "transcript": "Back again."})
        self.assertTrue(wait_until(lambda: self.turns == ["Back again."]))
        self.assertEqual(self.lost, [])

    def test_gives_up_quietly_when_it_cannot_connect(self):
        def refuse():
            raise OSError("no route")

        speech = self.speech(refuse, retry_wait=0)
        speech.start()
        self.assertTrue(wait_until(lambda: self.lost == [True]))
        self.assertFalse(speech.send(b"\0\0"))

    def test_a_stream_that_keeps_failing_at_once_gives_up_instead_of_looping(self):
        made = []

        def refused():
            meta = FakeMeta()
            self.addCleanup(meta.theirs.close)
            made.append(meta)

            def reject():
                meta.accept()
                meta.close()

            threading.Thread(target=reject, daemon=True).start()
            return meta.ours

        speech = self.speech(refused, retry_wait=0)
        speech.start()
        self.assertTrue(wait_until(lambda: self.lost == [True]))
        self.assertLessEqual(len(made), 5)

    def test_no_budget_means_no_connection(self):
        self.ledger.cap = 0.0
        calls = []
        speech = self.speech(lambda: calls.append(1), retry_wait=0)
        speech.start()
        self.assertTrue(wait_until(lambda: self.lost == [True]))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
