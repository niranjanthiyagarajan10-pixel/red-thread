"""A conversation as numbered turns, and a parser for WhatsApp chat exports."""
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Turn:
    index: int
    speaker: str
    text: str
    when: datetime = None
    attachments: list = field(default_factory=list)


@dataclass
class Conversation:
    id: str
    turns: list
    label: str = None

    def speakers(self):
        return list(dict.fromkeys(turn.speaker for turn in self.turns))

    def transcript(self):
        lines = []
        for turn in self.turns:
            text = turn.text or ", ".join(f"({name})" for name in turn.attachments)
            lines.append(f"[{turn.index}] {turn.speaker}: {text}")
        return "\n".join(lines)


_STAMP = r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4}),?\s+(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?(?:[\s ]*([ap])\.?m\.?)?"
_ANDROID_LINE = re.compile(rf"^{_STAMP}\s+-\s+(.*)$", re.I | re.S)
_IOS_LINE = re.compile(rf"^\[{_STAMP}\]\s+(.*)$", re.I | re.S)
_SYSTEM_PHRASES = ("end-to-end encrypted", "security code", "changed their phone number", "is a contact")
_OMITTED = re.compile(r"^(image|video|audio|sticker|document|gif|contact card) omitted$", re.I)
_FILE_ATTACHED = re.compile(r"^(.+?)\s+\(file attached\)", re.I)
_IOS_ATTACHED = re.compile(r"^<attached:\s*(.+?)>$", re.I)


def _match_line(line):
    return _ANDROID_LINE.match(line) or _IOS_LINE.match(line)


def _attachment(text):
    if text.lower() == "<media omitted>":
        return "media"
    omitted = _OMITTED.match(text)
    if omitted:
        return omitted.group(1).lower()
    for pattern in (_FILE_ATTACHED, _IOS_ATTACHED):
        found = pattern.match(text)
        if found:
            return found.group(1)
    return None


def _day_first(matches):
    """Assume day first, as in India, unless a date can only be month first."""
    for match in matches:
        if int(match.group(2)) > 12:
            return False
    return True


def _when(match, day_first):
    first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    day, month = (first, second) if day_first else (second, first)
    hour, minute, second_value = int(match.group(4)), int(match.group(5)), int(match.group(6) or 0)
    meridiem = (match.group(7) or "").lower()
    if meridiem == "p" and hour != 12:
        hour += 12
    if meridiem == "a" and hour == 12:
        hour = 0
    year = year + 2000 if year < 100 else year
    return datetime(year, month, day, hour, minute, second_value)


def parse_whatsapp_export(text, conversation_id="chat"):
    """Handles Android and iPhone export formats. Lines that fit neither continue the last message."""
    lines = [line.replace("‎", "").replace("‏", "") for line in text.splitlines()]
    day_first = _day_first([match for match in map(_match_line, lines) if match])

    turns = []
    in_message = False
    for line in lines:
        match = _match_line(line)
        if not match:
            if in_message and turns:
                turns[-1].text = f"{turns[-1].text}\n{line}".strip()
            continue
        body = match.groups()[-1]
        speaker, separator, message = body.partition(": ")
        if not separator or any(phrase in message for phrase in _SYSTEM_PHRASES):
            in_message = False
            continue
        attachment = _attachment(message.strip())
        turns.append(Turn(
            index=len(turns),
            speaker=speaker.strip(),
            text="" if attachment else message.strip(),
            when=_when(match, day_first),
            attachments=[attachment] if attachment else [],
        ))
        in_message = True
    return Conversation(conversation_id, turns)
