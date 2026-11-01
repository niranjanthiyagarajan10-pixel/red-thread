"""Converts voice notes to the only format Voice Transcribe takes: mono 16-bit WAV at 16 or 24 kHz.

Notes come from strangers. ffmpeg can follow playlists to local files and web addresses, so it only ever
sees bytes that start like a real audio file, read as that exact type, with file access limited and a time limit.
"""
import io
import subprocess
import tempfile
import wave
from pathlib import Path

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_SECONDS = 600
TIMEOUT_SECONDS = 60
_RATES = {16000, 24000}


def detect_format(data):
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    if data[4:8] == b"ftyp":
        return "mov"
    if data[:5] == b"#!AMR":
        return "amr"
    return None


def _accepted_wav(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as note:
            return note.getnchannels() == 1 and note.getsampwidth() == 2 and note.getframerate() in _RATES
    except (wave.Error, EOFError):
        return False


def to_wav(data, runner=subprocess.run):
    """Returns WAV bytes, or None if the note is not audio we accept or cannot be converted."""
    kind = detect_format(data)
    if kind is None or len(data) > MAX_INPUT_BYTES:
        return None
    if kind == "wav" and _accepted_wav(data):
        return data
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "in.audio"
        target = Path(folder) / "out.wav"
        source.write_bytes(data)
        command = [
            "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-protocol_whitelist", "file",
            "-f", kind, "-i", str(source), "-t", str(MAX_SECONDS),
            "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", "-map_metadata", "-1", str(target),
        ]
        try:
            runner(command, check=True, capture_output=True, timeout=TIMEOUT_SECONDS)
            return target.read_bytes()
        except (OSError, subprocess.SubprocessError):
            return None
