"""Rules the government portals enforce, written as plain code.

The text checks copy the validator in the public web code of cybercrime.gov.in, read on Oct 30, 2026.
Limits come from the portal's official instructions, its Report Suspect page and the refund manual.
Models write the words. Nothing reaches a person until this code passes it.
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal

INCIDENT_MIN = 200
INCIDENT_MAX = 1500
DELAY_MAX = 200
REMARKS_MAX = 500
SUSPECT_REPORT_MIN = 100
SUSPECT_REPORT_MAX = 500
FIR_THRESHOLD = Decimal(50000)
MB = 1024 * 1024

_BLOCKED_WORD = re.compile(r"\b(insert|update|delete|truncate|drop)\b", re.I | re.A)
_REPEAT = re.compile(r"(\S+)(\s+\1){2,}", re.I)
_TAG = re.compile(r"<[^>]*>")
_BAD_START = re.compile(r"^[<>'\"~|^*]")
_ONLY_DIGITS = re.compile(r"^\d+$", re.A)
_BAD_CHAR = re.compile(r"[<>'\"~|^*]")
_INSTRUCTION_CHAR = re.compile("[#$@^*`’~|!]")


def portal_error(text):
    """The first error the portal's form would raise, in the portal's own order."""
    if not text:
        return None
    if _BLOCKED_WORD.search(text):
        return "blocked_word"
    if _REPEAT.search(text):
        return "repeated_word"
    if _TAG.search(text):
        return "script_tag"
    if _BAD_START.search(text):
        return "invalid_start"
    if _ONLY_DIGITS.search(text):
        return "only_numbers"
    if _BAD_CHAR.search(text):
        return "blocked_char"
    return None


def portal_would_keep(text):
    """The portal silently deletes blocked words from what the person typed."""
    return _BLOCKED_WORD.sub("", text)


def _check(text, min_len=None, max_len=None):
    problems = []
    error = portal_error(text)
    if error:
        problems.append(error)
    if _INSTRUCTION_CHAR.search(text):
        problems.append("instruction_char")
    if min_len is not None and len(text.strip()) < min_len:
        problems.append("too_short")
    if max_len is not None and len(text.strip()) > max_len:
        problems.append("too_long")
    return problems


def check_incident(text):
    return _check(text, INCIDENT_MIN, INCIDENT_MAX)


def check_delay_reason(text):
    return _check(text, None, DELAY_MAX)


def check_remarks(text):
    return _check(text, None, REMARKS_MAX)


def check_suspect_report(text):
    return _check(text, SUSPECT_REPORT_MIN, SUSPECT_REPORT_MAX)


@dataclass
class Cleaned:
    text: str
    moved_ids: list = field(default_factory=list)
    changes: list = field(default_factory=list)


_ID = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*")
_REAL_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_KYC_NOUN = re.compile(r"\bkyc\s+update\b", re.I)
_KYC_VERB = re.compile(r"\bupdate\s+((?:my|your|the|his|her|our)\s+)?kyc\b", re.I)
_INFORM = re.compile(r"\bupdate\s+(me|us|him|her|them|you)\b", re.I)
_AN_UPDATE = re.compile(r"\ban\s+update\b", re.I)
_SAFE_WORDS = {"update": "change", "delete": "remove", "insert": "enter", "truncate": "shorten", "drop": "leave"}
_CONTRACTIONS = [
    (re.compile(r"\bcan't\b", re.I), "cannot"),
    (re.compile(r"\bwon't\b", re.I), "will not"),
    (re.compile(r"n't\b", re.I), " not"),
    (re.compile(r"\b(it|that|he|she|there|what|who|here)'s\b", re.I), r"\1 is"),
    (re.compile(r"\blet's\b", re.I), "let us"),
    (re.compile(r"'m\b", re.I), " am"),
    (re.compile(r"'re\b", re.I), " are"),
    (re.compile(r"'ve\b", re.I), " have"),
    (re.compile(r"'ll\b", re.I), " will"),
    (re.compile(r"'d\b", re.I), " would"),
]


def _match_case(old, new):
    return new[0].upper() + new[1:] if old[0].isupper() else new


def _replace_recording(pattern, new, text, changes):
    def swap(match):
        replacement = match.expand(new) if "\\" in new else new
        replacement = _match_case(match.group(0), replacement)
        changes.append(f"Changed '{match.group(0)}' to '{replacement}'")
        return replacement

    return pattern.sub(swap, text)


def clean_text(text):
    """Make text pass the portal's rules without changing any fact or digit. Every change is recorded."""
    changes = []
    original = text
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')

    moved_ids = _ID.findall(text)
    if moved_ids:
        changes.append("Moved IDs with @ to suspect details")
    text = _ID.sub("the ID in suspect details", text)

    tagless = _REAL_TAG.sub("", text)
    if tagless != text:
        changes.append("Removed tags")
    text = tagless

    text = _replace_recording(_KYC_NOUN, "KYC renewal", text, changes)
    text = _KYC_VERB.sub(lambda m: _kyc_verb(m, changes), text)
    text = _replace_recording(_INFORM, r"inform \1", text, changes)
    text = _replace_recording(_AN_UPDATE, "a change", text, changes)
    text = _BLOCKED_WORD.sub(lambda m: _safe_word(m, changes), text)

    for pattern, new in _CONTRACTIONS:
        text = pattern.sub(new, text)

    symbols = text
    text = re.sub(r"#\s*", "number ", text)
    text = re.sub(r"\$\s*", "USD ", text)
    text = re.sub(r"!+", ".", text)
    text = text.replace("@", " at ")
    text = re.sub(r"[<>'\"`~|^*]", "", text)
    if text != symbols:
        changes.append("Replaced or removed symbols the form rejects")

    collapsed = _REPEAT.sub(r"\1", text)
    if collapsed != text:
        changes.append("Removed words repeated three or more times")
    text = collapsed

    text = re.sub(r"\s+", " ", text).strip().lstrip(".,;:- ")
    if text != original and not changes:
        changes.append("Tidied spacing")
    return Cleaned(text, moved_ids, changes)


def _kyc_verb(match, changes):
    replacement = _match_case(match.group(0), f"renew {match.group(1) or ''}KYC")
    changes.append(f"Changed '{match.group(0)}' to '{replacement}'")
    return replacement


def _safe_word(match, changes):
    replacement = _match_case(match.group(0), _SAFE_WORDS[match.group(0).lower()])
    changes.append(f"Changed '{match.group(0)}' to '{replacement}'")
    return replacement


@dataclass
class AckId:
    value: str
    source: str


def parse_ack_id(text):
    """14 digits. A law firm summary says 2 means filed on the portal and 3 means the helpline."""
    value = re.sub(r"[\s\-]", "", text)
    if not re.fullmatch(r"\d{14}", value, re.A):
        return None
    source = {"2": "portal", "3": "helpline"}.get(value[0], "unknown")
    return AckId(value, source)


def is_request_id(text):
    """The portal says MR plus 12 characters. Its manual shows MR plus 14. Accept both."""
    return bool(re.fullmatch(r"MR\d{12,14}", text.strip(), re.A))


def normalize_mobile(text):
    digits = re.sub(r"\D", "", text)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits, re.A) else None


_UTR_FORMATS = {
    "upi": r"\d{12}",
    "imps": r"\d{12}",
    "neft": r"[A-Za-z0-9]{16}",
    "rtgs": r"[A-Za-z0-9]{22}",
}


def utr_warning(value, mode="unknown"):
    """Formats vary by bank, so a mismatch asks the person to check. It never blocks."""
    patterns = [_UTR_FORMATS[mode]] if mode in _UTR_FORMATS else list(_UTR_FORMATS.values())
    if any(re.fullmatch(pattern, value.strip()) for pattern in patterns):
        return None
    return f"'{value}' does not look like a usual transaction reference. Please check it against the bank message."


_IMAGE_TYPES = {"jpg", "jpeg", "png"}
_FILE_ROLES = {
    "national_id": (5 * MB, _IMAGE_TYPES),
    "suspect_photo": (5 * MB, _IMAGE_TYPES),
    "suspect_report": (5 * MB, None),
    "evidence": (10 * MB, None),
}
_SIGNATURES = {
    "jpg": ("ffd8ffe0", "ffd8ffe1", "ffd8ffe2"),
    "jpeg": ("ffd8ffe0", "ffd8ffe1", "ffd8ffe2"),
    "png": ("89504e47",),
    "pdf": ("25504446",),
}


def check_file(role, name, size, head):
    """head is the first bytes of the file. The portal compares them with the extension."""
    limit, allowed = _FILE_ROLES[role]
    extension = name.rsplit(".", 1)[-1].lower()
    problems = []
    if size == 0:
        problems.append("empty")
    if size > limit:
        problems.append("too_big")
    if allowed is not None and extension not in allowed:
        problems.append("wrong_type")
    signatures = _SIGNATURES.get(extension)
    if size and signatures and head[:4].hex() not in signatures:
        problems.append("renamed_file")
    return problems


def fir_rule(held_in_bank):
    """Official FAQ: below Rs 50,000 held in a bank needs no FIR, above it does. It does not cover exactly 50,000."""
    if held_in_bank < FIR_THRESHOLD:
        return "no_fir"
    if held_in_bank > FIR_THRESHOLD:
        return "fir_required"
    return "ask_police"


def rows_add_up(rows, claimed_total):
    return sum(rows, Decimal(0)) == claimed_total


def restorable_is_valid(restorable, held):
    return Decimal(0) <= restorable <= held


def credit_status(credited, expected):
    if credited == expected:
        return "matches"
    return "partial" if credited < expected else "more_than_expected"
