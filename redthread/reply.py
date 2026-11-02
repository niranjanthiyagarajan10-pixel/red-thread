"""The one answer format, with the limits WhatsApp sets and the claims we never make."""
import re
from dataclasses import dataclass, field

MAX_BODY = 1024
MAX_BUTTONS = 3
MAX_BUTTON_LABEL = 20
MAX_PROOF_LINES = 3
_BANNED = re.compile(
    r"\bfake\b|\bscammer|\bfraudster|\bcriminal|will get your money back|money will come back"
    r"|we guarantee|guaranteed refund|\bscore\b|—",
    re.I,
)


@dataclass
class Answer:
    headline: str
    proof: list = field(default_factory=list)
    next_step: str = ""
    buttons: list = field(default_factory=list)


# These rules are about our claims. A quote is someone else's words, and a scam chat may well say "this is not fake".
_QUOTED = re.compile(r'"[^"]*"')


def render(answer):
    return "\n".join([answer.headline, *answer.proof, answer.next_step])


def check_answer(answer):
    problems = []
    body = render(answer)
    if not answer.headline.strip() or not answer.next_step.strip():
        problems.append("empty")
    if len(body) > MAX_BODY:
        problems.append("too_long")
    if len(answer.proof) > MAX_PROOF_LINES:
        problems.append("too_much_proof")
    if len(answer.buttons) > MAX_BUTTONS:
        problems.append("too_many_buttons")
    if any(len(label) > MAX_BUTTON_LABEL for label in answer.buttons):
        problems.append("button_too_long")
    if len({label.casefold() for label in answer.buttons}) != len(answer.buttons):
        problems.append("duplicate_buttons")
    if _BANNED.search(_QUOTED.sub("", body)):
        problems.append("banned_claim")
    return problems
