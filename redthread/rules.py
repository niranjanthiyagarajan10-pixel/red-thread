"""Fixed rules that answer at once, with no model and no waiting."""
import re
from dataclasses import dataclass

from redthread.facts import extract_identifiers

_AUTHORITY = r"\bcbi\b|police|customs|narcotics|(?-i:\bED\b)(?! of)|\brbi\b|\btrai\b|cyber ?crime|पुलिस|सीबीआई|போலீஸ்"
_LIVE = r"on the (?:phone|line|call)|on (?:a )?(?:video )?call|video call|disconnect|is calling|calling me|called me|phone par|फोन पर|கால்"
_THREAT = r"parcel|drugs|warrant|laundering|will arrest|arrest (?:you|me)\b|threaten|धमका|number will be"
_ARREST = re.compile(r"digital arrest|डिजिटल अरेस्ट|டிஜிட்டல் அரெஸ்ட்", re.I)
_ISOLATE = re.compile(r"(?:do not|don't|dont) tell anyone|stay on (?:skype|the call|video)|kisi ko mat", re.I)
_AUTHORITY_RE = re.compile(_AUTHORITY, re.I)
_LIVE_RE = re.compile(_LIVE, re.I)
_THREAT_RE = re.compile(_THREAT, re.I)
_POSTAL = re.compile(r"\bpin ?code\b", re.I)
_CODE = re.compile(
    r"\b(?:otp|pin)\b|\b\d[\- ]digit\b|\bsms code\b|\bcode (?:you|i|we|that) (?:just )?(?:got|received)|\bcode (?:from|in) (?:the )?(?:sms|message)",
    re.I,
)
_ASKING = re.compile(r"asking|asked|wants|caller|share|\btell\b|give|read (?:out|it|the)|maang|मांग", re.I)
_REMOTE = re.compile(
    r"anydesk|teamviewer|rustdesk|quicksupport|remote app|screen ?share|share (?:your |my |the )?screen|\bapk\b", re.I
)
_SAFE = re.compile(r"safe account|secure account|safety account|सेफ अकाउंट", re.I)


def during_call(text):
    """Returns the kind of pressure on a live call, or None."""
    text = _POSTAL.sub(" ", text)
    if _ARREST.search(text) or _ISOLATE.search(text):
        return "fake_authority"
    if _AUTHORITY_RE.search(text) and (_LIVE_RE.search(text) or _THREAT_RE.search(text)):
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


_NEGATED = re.compile(
    r"(?:did not|didn't|have not|haven't|not yet|never|not|almost|nearly)\s+(?:\w+\s+)?(?:pay|paid|send|sent|transfer\w*)"
    r"|\b(?:he|she|they)\s+paid\b|nahi bheje|नहीं भेजे|भेजे नहीं",
    re.I,
)
_ONLY_ASKED = re.compile(r"should i|asked me|wants me|told me to", re.I)
_THEN_PAID = re.compile(r"\b(?:and|so|then)\s+(?:i\s+)?(?:paid|sent|transferred)\b", re.I)
_PAID = re.compile(
    r"\bpaid\b(?!\s+(?:leave|holiday|plan|version))|\b(?:transferred|debited|deducted)\b"
    r"|\b(?:sent|lost|gave)\b(?=.{0,30}(?:money|paise|rupee|\brs\b|₹|lakh|lac\b|crore|savings|everything|\d{3,}|\d+\s*k\b))"
    r"|\b(?:gone|took|taken|cut|withdrawn|missing)\b.{0,20}\bfrom my (?:account|bank)"
    r"|bhej (?:diye|diya)|kat gaye|de di(?:ye|ya)|transfer panni\w*|anupp\w+"
    r"|भेज (?:दिए|दिया)|कट गए|அனுப்பி",
    re.I | re.S,
)


def money_has_moved(text):
    if re.search(r"already paid", text, re.I):
        return True
    if _NEGATED.search(text):
        return False
    if _ONLY_ASKED.search(text) and not _THEN_PAID.search(text):
        return False
    return bool(_PAID.search(text))
