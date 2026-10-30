"""Protects people from the second scam.

Secrets sent in chat are removed before anything is stored or sent to a model.
In a masked chat between two victims, contact details and recovery offers never get through.
"""
import re
from dataclasses import dataclass, field

from redthread.facts import extract_identifiers

_OTP_AFTER = re.compile(r"\b(?:otp|one[\s\-]time[\s\-]password|verification code)\b\D{0,20}?(\d{4,8})\b", re.I)
_OTP_BEFORE = re.compile(r"\b(\d{4,8})\b\D{0,20}?\b(?:otp|one[\s\-]time[\s\-]password|verification code)\b", re.I)
_CARD = re.compile(r"(?<!\d)(\d(?:[ \-]?\d){12,18})(?!\d)")
_PAN = re.compile(r"\b([A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z])\b", re.I)
_AADHAAR = re.compile(r"(?:aadhaar|aadhar|आधार)\D{0,20}?(\d{4}[ \-]?\d{4}[ \-]?\d{4})(?!\d)", re.I)
_PASSWORD = re.compile(r"\b(?:password|passcode|mpin|upi pin|pin)\s*(?:is|:|=)\s*(\S+)", re.I)
_CVV = re.compile(r"\bcvv\b\D{0,10}?(\d{3,4})\b", re.I)
_RECOVERY = re.compile(
    r"recovery (?:agent|expert|service|company|team)|refund agent|recover (?:your|ur) money|get your money back"
    r"|processing fee|advance fee|paise? wapas|paisa wapas|पैसे वापस",
    re.I,
)


@dataclass
class Sensitive:
    kind: str
    start: int
    end: int


@dataclass
class Scrubbed:
    text: str
    kinds: list = field(default_factory=list)


@dataclass
class RelayCheck:
    blocked: bool
    reason: str = None


def _luhn_ok(digits):
    total = 0
    for position, character in enumerate(reversed(digits)):
        number = int(character)
        if position % 2:
            number = number * 2 - 9 if number > 4 else number * 2
        total += number
    return total % 10 == 0


def find_sensitive(text):
    items = []
    for kind, pattern in (
        ("otp", _OTP_AFTER), ("otp", _OTP_BEFORE), ("pan", _PAN), ("aadhaar", _AADHAAR),
        ("password", _PASSWORD), ("cvv", _CVV),
    ):
        items += [Sensitive(kind, match.start(1), match.end(1)) for match in pattern.finditer(text)]
    for match in _CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group(1))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            items.append(Sensitive("card", match.start(1), match.end(1)))
    return items


def scrub(text):
    items = sorted(find_sensitive(text), key=lambda item: item.start, reverse=True)
    for item in items:
        text = text[:item.start] + "[removed]" + text[item.end:]
    return Scrubbed(text, list(dict.fromkeys(item.kind for item in items)))


def check_relay(text):
    if find_sensitive(text):
        return RelayCheck(True, "sensitive")
    if _RECOVERY.search(text):
        return RelayCheck(True, "recovery_offer")
    found = extract_identifiers(text)
    if found["phone"] or found["upi"] or found["email"] or found["link"]:
        return RelayCheck(True, "contact_details")
    return RelayCheck(False)
