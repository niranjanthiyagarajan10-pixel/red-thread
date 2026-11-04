"""After the call. A scam press becomes a case: the words to say on 1930, a timer for the first hour, what official
sources say, and the cyber crime form filled from the person's own words.

Code supplies every fact: times and the caller's number come from the presses, amounts and IDs from what the person
said or typed. Spark only restates their story in plain English, and a value holding a number they never gave is dropped.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from redthread import senses
from redthread.amounts import format_rupees, parse_amounts
from redthread.facts import extract_identifiers
from redthread.investigate import domain_of
from redthread.sheets import filing_sheet, suspect_report_sheet
from redthread.shield import scrub

GOLDEN = timedelta(hours=1)
WORDS_LIMIT = 4000
PORTAL = "https://cybercrime.gov.in"
_REPORT_TYPES = {"call": "Phone Number", "chat": "WhatsApp Number / Telegram Handle", "message": "SMS Header / Number"}
_CHAT_ABOUT = {"asks_money": "asking me for money", "investment_pitch": "pushing an investment",
               "pay_to_withdraw": "asking for a fee before I could withdraw", "asks_codes_or_details": "asking for my codes",
               "install_app": "asking me to install an app"}


@dataclass
class Story:
    words: str = ""
    details: dict = field(default_factory=dict)
    amount: object = None
    paid: object = None
    paid_at: object = None
    told_at: object = None


def tell(model, story, words, facts, now):
    """Keeps what the person said, with codes and card numbers removed, and pulls the form details from it."""
    words = scrub(" ".join(str(words or "").split())).text
    if not words:
        return story
    story.words = f"{story.words} {words}".strip()[:WORDS_LIMIT]
    story.told_at = now
    spoken = parse_amounts(story.words)
    if spoken and story.amount is None:
        story.amount = max(amount.value for amount in spoken)
    if model:
        story.details = senses.pull_details(model, story.words, facts, now) or story.details
    return story


def _unique(values):
    return list(dict.fromkeys(value for value in values if value))


def _opening(scam, who):
    """The first line of the complaint, from the press itself."""
    when = f"On {scam.at:%d/%m/%Y} at {scam.at:%H:%M}"
    if scam.kind == "chat":
        return f"{when}, someone in an online chat was {_CHAT_ABOUT.get(scam.live_kind, 'pressing me to pay')}."
    return f"{when} I got {scam.about}{who}."


def case_view(entries, story, checked, now):
    """Everything the case page shows, decided by code. None if no press found a scam."""
    danger = [entry for entry in entries if entry.level == "danger" and entry.about]
    if not danger:
        return None
    scam = next((entry for entry in danger if entry.kind in ("call", "message", "chat")), danger[0])
    heard = extract_identifiers(story.words)
    callers = [phone for entry in danger if entry.caller for phone in extract_identifiers(entry.caller)["phone"][:1]]
    phones = _unique(callers + heard["phone"])
    upis = _unique([entry.upi for entry in entries if entry.upi] + heard["upi"])
    who = f" from {phones[0]}" if phones and scam.kind == "call" else ""
    amount = format_rupees(story.amount) if story.amount else ""

    if story.paid:
        to = f" to {upis[0]}" if upis else ""
        at = f" at {story.paid_at:%H:%M}" if story.paid_at else ""
        script = f'Say: "I lost {amount or "money"} to {scam.about}{who}. I paid{to}{at}. Please freeze it."'
    else:
        script = f'Say: "I got {scam.about}{who} at {scam.at:%H:%M}. I did not pay."'

    timer = None
    if story.paid and story.paid_at:
        deadline = story.paid_at + GOLDEN
        timer = {"deadline": f"{deadline:%Y-%m-%dT%H:%M:%S}", "minutes_left": max(0, int((deadline - now).total_seconds() // 60))}

    details = story.details or {}
    opening = [_opening(scam, who)]
    if story.paid:
        opening.append(f"I paid {amount or 'money'}" + (f" to UPI ID {upis[0]}" if upis else "")
                       + (f" at {story.paid_at:%H:%M}" if story.paid_at else "") + ".")
    told = " ".join([*opening, details.get("story_english") or story.words]).strip()

    if story.paid:
        form = "Report financial fraud"
        sheet = filing_sheet({
            "story": told, "when": details.get("when") or f"{scam.at:%d/%m/%Y %H:%M}", "state": details.get("state", ""),
            "district": details.get("district", ""), "police_station": details.get("police_station", ""), "bank": details.get("bank", ""),
            "paid_on": details.get("paid_on") or (f"{story.paid_at:%d/%m/%Y}" if story.paid_at else ""),
            "amount": str(story.amount) if story.amount else None, "suspect_upi": upis[:3], "suspect_mobile": phones[0] if phones else "",
        })
    else:
        form = "Report a suspect"
        report_type = _REPORT_TYPES.get(scam.kind, "Phone Number")
        target = phones[0] if phones else ""
        sheet = suspect_report_sheet(details.get("state", ""), report_type, target, told)

    return {
        "title": f"{scam.about[0].upper()}{scam.about[1:]}, {scam.at:%H:%M}",
        "opened": f"{scam.at:%Y-%m-%d %H:%M}",
        "kind": scam.kind,
        "paid": story.paid,
        "amount": amount,
        "script": script,
        "timer": timer,
        "checked": [{"text": line.text, "source": domain_of(line.sources[0]), "url": line.sources[0]}
                    for line in (checked.lines if checked else [])][:2],
        "heard": story.words,
        "form": form,
        "sheet": [{"label": item.label, "value": item.value, "meaning": item.meaning, "problems": item.problems, "yours": item.person_only}
                  for item in sheet.fields],
        "portal": PORTAL,
    }
