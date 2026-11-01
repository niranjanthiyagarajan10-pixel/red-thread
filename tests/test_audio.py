import io
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from redthread import audio, senses
from tests.test_senses import Scripted, wav_bytes

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def make_wav(rate=16000, channels=1):
    out = io.BytesIO()
    with wave.open(out, "wb") as note:
        note.setnchannels(channels)
        note.setsampwidth(2)
        note.setframerate(rate)
        note.writeframes(b"\0\0" * channels * rate)
    return out.getvalue()


class DetectTests(unittest.TestCase):
    def test_known_audio_types_are_named(self):
        self.assertEqual(audio.detect_format(b"OggS\0\2"), "ogg")
        self.assertEqual(audio.detect_format(make_wav()), "wav")
        self.assertEqual(audio.detect_format(b"ID3\3\0"), "mp3")
        self.assertEqual(audio.detect_format(b"\xff\xfb\x90"), "mp3")
        self.assertEqual(audio.detect_format(b"\0\0\0\x20ftypM4A "), "mov")
        self.assertEqual(audio.detect_format(b"#!AMR\n"), "amr")

    def test_anything_else_is_refused(self):
        self.assertIsNone(audio.detect_format(b"#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n"))
        self.assertIsNone(audio.detect_format(b"ffconcat version 1.0\nfile secret.txt\n"))
        self.assertIsNone(audio.detect_format(b"<html>"))
        self.assertIsNone(audio.detect_format(b""))


class ToWavTests(unittest.TestCase):
    def fake_runner(self, args, **options):
        self.args = args
        self.options = options
        Path(args[-1]).write_bytes(make_wav(24000))
        return subprocess.CompletedProcess(args, 0)

    def test_a_wav_voice_transcribe_accepts_is_passed_straight_through(self):
        def must_not_run(*_, **__):
            raise AssertionError("ffmpeg should not run")

        wav = make_wav(16000)
        self.assertEqual(audio.to_wav(wav, runner=must_not_run), wav)

    def test_a_wav_it_rejects_is_converted(self):
        self.assertEqual(audio.to_wav(make_wav(44100, 2), runner=self.fake_runner), make_wav(24000))

    def test_ffmpeg_is_locked_to_the_detected_type_and_a_time_limit(self):
        audio.to_wav(b"OggS\0\2 data", runner=self.fake_runner)
        command = self.args
        self.assertEqual(command[command.index("-f") + 1], "ogg")
        self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file")
        self.assertIn("-nostdin", command)
        self.assertEqual(command[command.index("-t") + 1], "600")
        self.assertEqual(command[command.index("-ar") + 1], "24000")
        self.assertEqual(command[command.index("-c:a") + 1], "pcm_s16le")
        self.assertTrue(self.options["timeout"])

    def test_unknown_types_never_reach_ffmpeg(self):
        def must_not_run(*_, **__):
            raise AssertionError("ffmpeg should not run")

        self.assertIsNone(audio.to_wav(b"#EXTM3U\nfile:///etc/passwd", runner=must_not_run))

    def test_oversized_notes_never_reach_ffmpeg(self):
        def must_not_run(*_, **__):
            raise AssertionError("ffmpeg should not run")

        self.assertIsNone(audio.to_wav(b"OggS" + b"\0" * audio.MAX_INPUT_BYTES, runner=must_not_run))

    def test_failures_give_nothing(self):
        def missing(*_, **__):
            raise FileNotFoundError("ffmpeg")

        def timed_out(args, **_):
            raise subprocess.TimeoutExpired(args, 1)

        def broken(args, **_):
            raise subprocess.CalledProcessError(1, args)

        for runner in (missing, timed_out, broken):
            self.assertIsNone(audio.to_wav(b"OggS\0\2", runner=runner))


class HearTests(unittest.TestCase):
    def test_hear_refuses_audio_it_cannot_convert_without_calling_the_model(self):
        model = Scripted(hear="should not be used")
        self.assertIsNone(senses.hear(model, b"<html>not audio"))

    def test_hear_still_takes_a_plain_wav(self):
        self.assertEqual(senses.hear(Scripted(hear="ok"), wav_bytes()), "ok")


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg is not installed")
class RealFfmpegTests(unittest.TestCase):
    def test_a_real_ogg_opus_voice_note_becomes_a_wav_voice_transcribe_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.wav"
            target = Path(tmp) / "note.ogg"
            source.write_bytes(make_wav(16000))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-c:a", "libopus", str(target)], check=True)
            converted = audio.to_wav(target.read_bytes())
        with wave.open(io.BytesIO(converted), "rb") as note:
            self.assertEqual((note.getnchannels(), note.getsampwidth(), note.getframerate()), (1, 2, 24000))
            self.assertGreater(note.getnframes(), 0)


if __name__ == "__main__":
    unittest.main()
