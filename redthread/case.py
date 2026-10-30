"""Tracks a recovery case from what the person forwards.

There is no government API, so forwarded messages are the only way to see progress.
A case closes only when code checks a credit against the expected amount.
"""
import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from redthread.amounts import format_rupees, parse_amounts
from redthread.portal_rules import credit_status, fir_rule, parse_ack_id

ORDER = ["new", "helpline_ack", "filed", "held", "eligible", "requested", "partly_credited", "closed"]
FILING_WINDOW = timedelta(hours=24)

_REQUEST = re.compile(r"\bMR\d{12,14}\b")
_ACK = re.compile(r"(?<!\d)\d{14}(?!\d)")
_DEBIT = re.compile(r"\bdebited\b", re.I)
_CREDIT = re.compile(r"\bcredited\b|\bdeposited\b|जमा|வரவு", re.I)
_ELIGIBLE = re.compile(r"eligible|restorable|restoration", re.I)
_HOLD = re.compile(r"\bon hold\b|\blien\b|\bfrozen\b", re.I)


@dataclass
class Message:
    kind: str
    amount: Decimal = None
    ack: object = None
    request_id: str = None


@dataclass
class Case:
    id: str
    state: str = "new"
    expected: Decimal = None
    held: Decimal = None
    ack_id: str = None
    request_id: str = None
    credited: Decimal = Decimal(0)
    file_by: object = None
    log: list = field(default_factory=list)
    seen_credits: set = field(default_factory=set)


def classify_message(text):
    amounts = parse_amounts(text)
    amount = amounts[0].value if amounts else None
    request = _REQUEST.search(text)
    if request:
        return Message("request", amount, request_id=request.group(0))
    if _DEBIT.search(text):
        return Message("debit", amount)
    if _CREDIT.search(text):
        return Message("credit", amount)
    if _ELIGIBLE.search(text):
        return Message("eligible", amount)
    if _HOLD.search(text):
        return Message("held", amount)
    ack = _ACK.search(text)
    if ack:
        return Message("ack", ack=parse_ack_id(ack.group(0)))
    return Message("unknown", amount)


def _advance(case, state):
    if ORDER.index(state) > ORDER.index(case.state):
        case.state = state


def _apply_credit(case, text, amount):
    if amount is None or text in case.seen_credits:
        return
    case.seen_credits.add(text)
    case.credited += amount
    if case.expected is None:
        return
    status = credit_status(case.credited, case.expected)
    if status == "matches":
        _advance(case, "closed")
    elif status == "partial":
        _advance(case, "partly_credited")


def apply_message(case, text, now):
    message = classify_message(text)
    case.log.append((now, message.kind))
    if message.kind == "ack":
        case.ack_id = message.ack.value
        if message.ack.source == "helpline":
            _advance(case, "helpline_ack")
            if case.state == "helpline_ack":
                case.file_by = now + FILING_WINDOW
        else:
            _advance(case, "filed")
    elif message.kind == "held":
        case.held = message.amount if message.amount is not None else case.held
        _advance(case, "held")
    elif message.kind == "eligible":
        case.expected = message.amount if message.amount is not None else case.expected
        _advance(case, "eligible")
    elif message.kind == "request":
        case.request_id = message.request_id
        _advance(case, "requested")
    elif message.kind == "credit":
        _apply_credit(case, text, message.amount)
    return message


def overdue(case, now):
    if case.state == "helpline_ack" and case.file_by and now > case.file_by:
        return ["finish_portal_filing"]
    return []


def next_step(case):
    if case.state == "new":
        return "Call 1930 now, then send us the acknowledgement number from the SMS."
    if case.state == "helpline_ack":
        return "Finish your complaint on cybercrime.gov.in within 24 hours. We will give you each field."
    if case.state == "filed":
        return "Your complaint is in. Forward every SMS or email about it, and check status with your acknowledgement number."
    if case.state == "held":
        return "Some money is on hold. A hold does not mean it is restored. Forward the next message about the restorable amount."
    if case.state == "eligible":
        step = "Raise a refund request on mrm-ncrp.mha.gov.in. We will give you each field."
        if case.held is not None and fir_rule(case.held) != "no_fir":
            step += " The official rule needs an FIR when more than Rs 50,000 is held in one bank, so ask your police station."
        return step
    if case.state == "requested":
        return "Police now check your request. You may need to sign an indemnity bond. Forward any message about it."
    if case.state == "partly_credited":
        if case.expected is None:
            return f"{format_rupees(case.credited)} has come in. Ask your police station for an update on the rest."
        left = case.expected - case.credited
        return f"{format_rupees(case.credited)} has come in and {format_rupees(left)} is still expected. Ask your police station for an update on the rest."
    return "Case closed. The credit matches the expected amount."
