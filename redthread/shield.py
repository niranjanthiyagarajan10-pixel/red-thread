"""Protects people from the second scam.

Secrets sent in chat are removed before anything is stored or sent to a model.
In a masked chat between two victims, contact details and recovery offers never get through.
"""
import re
from dataclasses import dataclass, field

from redthread.amounts import DIGIT_WORD_PATTERN, spoken_digits
from redthread.facts import extract_identifiers

_OTP_WORD = r"(?:\b(?:otp|one[\s\-]time[\s\-]password|verification code)\b|ओटीपी|ஓடிபி)"
_OTP_AFTER = re.compile(_OTP_WORD + r"\D{0,20}?(\d(?:[ \-]?\d){3,7})(?!\d)", re.I)
_OTP_BEFORE = re.compile(r"\b(\d{4,8})\b\D{0,20}?" + _OTP_WORD, re.I)
_SPOKEN_DIGIT = rf"(?:{DIGIT_WORD_PATTERN})"
_OTP_SPOKEN = re.compile(_OTP_WORD + rf"\W+(?:\w+\W+){{0,3}}?((?:{_SPOKEN_DIGIT}\W+){{3,7}}{_SPOKEN_DIGIT})(?!\w)", re.I)
_PIN = re.compile(r"\b(?:mpin|pin)\b(?!\s*code)\s*(?:number\s*)?(?:is|:|=|hai)?\s*(\d{4,6})\b", re.I)
_CARD = re.compile(r"(?<!\d)(\d{13,19}|\d{4}(?:[ \-]\d{4}){2,3}(?:[ \-]\d{1,3})?|\d{4}[ \-]\d{6}[ \-]\d{4,5})(?!\d)")
_PAN = re.compile(r"\b([A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z])\b", re.I)
_AADHAAR = re.compile(r"(?:aadhaar|aadhar|आधार)\D{0,20}?(\d{4}[ \-]?\d{4}[ \-]?\d{4})(?!\d)", re.I)
_PASSWORD = re.compile(r"\b(?:password|passcode)\s*(?:is|:|=)\s*(\S+)|\b(?:password|passcode)\s+(?=\S*\d)(\S+)", re.I)
_CVV = re.compile(r"\bcvv\b\D{0,10}?(\d{3,4})\b", re.I)
_CARD_WORDS = re.compile(r"\b(?:card|debit|credit|diners|expiry|cvv)\b", re.I)
_RECOVERY = re.compile(
    r"recovery (?:agent|expert|service|company|team)|refund agent|recover (?:your|ur) money|get your money back"
    r"|processing fee|advance fee|small fee|service fee|registration fee|for a fee|\bhacker\b|can recover|recovered mine"
    r"|helped me recover|release your (?:funds|money)|\bpay\b.{0,40}get it back"
    r"|paise? wapas|paisa wapas|पैसे वापस",
    re.I,
)
_WARNING = re.compile(r"(?:do not|don't|dont|never) (?:pay|trust|believe)|beware of|watch out for|avoid", re.I)
_SPACED_DIGITS = re.compile(r"(?<!\d)\d(?:[ \-.]\d){7,}(?!\d)")
_INVITE = re.compile(r"\bjoin my\b|\blink in (?:my )?bio\b|\bdm me\b", re.I)
_APPS = r"telegram|tg|insta|instagram|snap|snapchat|signal|facebook|fb|ig"
_HANDLE = re.compile(
    r"(?<![\w@.])@[A-Za-z_]\w{2,}"
    rf"|\b(?:{_APPS})\b\s*(?:id|handle|username)?\s*:?\s*[A-Za-z][\w.]*[_\d][\w.]*"
    rf"|\bmy\s+(?:{_APPS})\s*(?:id|handle|username)?\s+is\s+\S+",
    re.I,
)
_SPOKEN_EMAIL = re.compile(r"\b\w+\s+at\s+\w+\s+dot\s+\w+", re.I)


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


def _is_complaint_number(digits):
    """Complaint numbers have 14 digits and start with 2 or 3, and some pass the card check by chance."""
    return len(digits) == 14 and digits[0] in "23"


def find_sensitive(text):
    items = []
    for kind, pattern in (
        ("otp", _OTP_AFTER), ("otp", _OTP_BEFORE), ("otp", _OTP_SPOKEN), ("pan", _PAN), ("aadhaar", _AADHAAR),
        ("password", _PASSWORD), ("password", _PIN), ("cvv", _CVV),
    ):
        for match in pattern.finditer(text):
            group = match.lastindex or 0
            items.append(Sensitive(kind, match.start(group), match.end(group)))
    for match in _CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group(1))
        if _is_complaint_number(digits) and not _CARD_WORDS.search(text[max(0, match.start() - 25):match.start()]):
            continue
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
    found = extract_identifiers(text)
    if _RECOVERY.search(text) and not _WARNING.search(text):
        return RelayCheck(True, "recovery_offer")
    if found["phone"] or found["upi"] or found["email"] or found["link"]:
        return RelayCheck(True, "contact_details")
    if _HANDLE.search(text) or _SPOKEN_EMAIL.search(text) or _INVITE.search(text):
        return RelayCheck(True, "contact_details")
    if _SPACED_DIGITS.search(text) or spoken_digits(text, minimum=8):
        return RelayCheck(True, "contact_details")
    return RelayCheck(False)
