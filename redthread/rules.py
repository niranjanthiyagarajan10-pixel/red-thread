"""Fixed rules that answer at once, with no model and no waiting."""
import re
from dataclasses import dataclass

from redthread.facts import extract_identifiers

_AUTHORITY = r"cbi|police|customs|narcotics|\bed\b|rbi|trai|cyber ?crime|पुलिस|सीबीआई|போலீஸ்"
_LIVE = r"on the (?:phone|line|call)|on (?:a )?(?:video )?call|video call|disconnect|calling|phone par|फोन पर|கால்"
_ARREST = re.compile(r"digital arrest|डिजिटल अरेस्ट|டிஜிட்டல் அரெஸ்ட்", re.I)
_AUTHORITY_RE = re.compile(_AUTHORITY, re.I)
_LIVE_RE = re.compile(_LIVE, re.I)
_CODE = re.compile(r"\b(?:otp|code|pin)\b", re.I)
_ASKING = re.compile(r"asking|asked|wants|caller|share|tell him|give|maang|मांग", re.I)
_REMOTE = re.compile(r"anydesk|teamviewer|rustdesk|quicksupport|remote app|screen ?share", re.I)
_SAFE = re.compile(r"safe account|secure account|safety account|सेफ अकाउंट", re.I)


def during_call(text):
    """Returns the kind of pressure on a live call, or None."""
    if _ARREST.search(text) or (_AUTHORITY_RE.search(text) and _LIVE_RE.search(text)):
        return "fake_authority"
    if _REMOTE.search(text):
        return "remote_app"
    if _SAFE.search(text):
        return "safe_account"
    if _CODE.search(text) and _ASKING.search(text):
        return "code_request"
    return None


@dataclass
class MoneyFlag:
    name: str
    detail: str
    source: str


_BROKER = re.compile(r"\bsebi\b|\bbroker\b|registered (?:adviser|advisor|analyst)", re.I)
_WITHDRAW = re.compile(r"withdraw\w*.{0,60}\b(?:tax|fee|deposit|pay|charge)|\b(?:tax|fee|deposit|pay)\b.{0,60}withdraw", re.I | re.S)
_GUARANTEED = re.compile(r"guarantee\w*.{0,30}(?:return|profit|%)|\d+\s*%.{0,20}(?:return|profit).{0,20}(?:every|per|daily|guarantee)", re.I | re.S)


def money_flags(text):
    flags = []
    upi_ids = extract_identifiers(text)["upi"]
    if _BROKER.search(text) and upi_ids and not any("@valid" in upi.lower() for upi in upi_ids):
        flags.append(MoneyFlag(
            "broker_without_valid_handle",
            f"Claims to be a broker, but {upi_ids[0]} is not a validated handle. A real one looks like abc.brk@validhdfc.",
            "SEBI validated UPI handles, live since Oct 1, 2025. Check any ID on SEBI Check before paying.",
        ))
    if _WITHDRAW.search(text):
        flags.append(MoneyFlag(
            "pay_to_withdraw",
            "Says you must pay before you can take your own money out.",
            "A common pattern in scam investment platforms.",
        ))
    if _GUARANTEED.search(text):
        flags.append(MoneyFlag(
            "guaranteed_returns",
            "Promises fixed or guaranteed returns.",
            "Real market investments cannot promise this. Check with SEBI.",
        ))
    return flags


_NOT_PAID = re.compile(
    r"did not|didn't|not paid|not yet|haven't|have not|should i|asked me|wants me|told me to|nahi bheje|नहीं", re.I
)
_PAID = re.compile(
    r"\b(?:paid|sent|transferred|debited|deducted|lost)\b|bhej (?:diye|diya)|kat gaye|transfer panni\w*|anupp\w+"
    r"|भेज (?:दिए|दिया)|कट गए|அனுப்பி",
    re.I,
)


def money_has_moved(text):
    if re.search(r"already paid", text, re.I):
        return True
    if _NOT_PAID.search(text):
        return False
    return bool(_PAID.search(text))
