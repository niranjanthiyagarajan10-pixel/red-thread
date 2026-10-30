"""Pulls identifiers from text, and checks what someone said against what their screenshot shows.

A mismatch becomes a question to the person. Nothing is guessed.
"""
import re
from dataclasses import dataclass, field

from redthread.amounts import format_rupees, parse_amounts, spoken_digits
from redthread.portal_rules import normalize_mobile

_ID = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*")
_LINK = re.compile(r"(?:https?://|www\.|wa\.me/|t\.me/)\S+", re.I)
_BARE_LINK = re.compile(
    r"(?<![@\w.\-/])(?:[a-z0-9\-]+\.)+(?:com|in|ly|me|org|net|io|co|xyz|app|link|info|site|online|top|shop|club)(?:/\S*)?(?![\w@])",
    re.I,
)
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?|0)?[6-9]\d{4}[\s\-]?\d{5}(?!\d)")
_REFERENCE = re.compile(r"(?<![\dA-Za-z])(?:\d{12}|(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)(?:[A-Z0-9]{16}|[A-Z0-9]{22}))(?![\dA-Za-z])")


def extract_identifiers(text):
    found = {"upi": [], "email": [], "phone": [], "link": [], "reference": []}
    for match in _ID.findall(text):
        found["email" if "." in match.split("@", 1)[1] else "upi"].append(match)
    found["link"] = [link.rstrip(".,;:!?)") for link in _LINK.findall(text) + _BARE_LINK.findall(text)]
    for match in _PHONE.findall(text):
        phone = normalize_mobile(match)
        if phone:
            found["phone"].append(phone)
    found["reference"] = _REFERENCE.findall(text)
    return {kind: list(dict.fromkeys(values)) for kind, values in found.items()}


@dataclass
class Reconciled:
    amount: object = None
    reference: str = None
    questions: list = field(default_factory=list)


def reconcile(voice_text, screenshot_text):
    result = Reconciled()
    said = parse_amounts(voice_text)
    shown = [amount.value for amount in parse_amounts(screenshot_text)]

    if shown:
        total = sum(shown)
        if not said:
            result.amount = total
        elif said[0].value in shown or said[0].value == total:
            result.amount = said[0].value
        else:
            result.questions.append(
                f"You said {format_rupees(said[0].value)}, but the screenshot shows {format_rupees(total)}. Which is right?"
            )
    elif said:
        result.questions.append(f"You said {format_rupees(said[0].value)}. Please send the payment screenshot so we can confirm it.")

    said_digits = spoken_digits(voice_text) + extract_identifiers(voice_text)["reference"]
    shown_references = extract_identifiers(screenshot_text)["reference"]
    if shown_references:
        if not said_digits or said_digits[0] in shown_references:
            result.reference = shown_references[0]
        else:
            result.questions.append(
                f"You read out {said_digits[0]}, but the screenshot shows {shown_references[0]}. Please check the bank message."
            )
    elif said_digits:
        result.questions.append(f"You read out {said_digits[0]}. Please send the payment screenshot so we can confirm it.")
    return result
