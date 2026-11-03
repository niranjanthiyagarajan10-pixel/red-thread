"""The one contact: everything a person sends comes here and gets one short answer.

Order matters. Secrets are refused first, then live danger gets an instant fixed answer, and only then
does anything slower run. Spark can raise an alarm the rules missed, but it can never clear one.
With no model at all, every path still answers from rules and templates.
"""
import re
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from redthread import proof, senses, templates
from redthread.amounts import format_rupees, parse_amounts
from redthread.case import Case, apply_message, classify_message, next_step
from redthread.conversation import Conversation, Turn, parse_whatsapp_export
from redthread.facts import Reconciled, extract_identifiers, payment_date, reconcile
from redthread.model import BudgetExceeded
from redthread.reply import Answer, check_answer
from redthread.rules import during_call, money_flags, money_has_moved
from redthread.sheets import filing_sheet, refund_sheet, suspect_report_sheet
from redthread.shield import check_relay, scrub
from redthread.stages import SIGNALS, Verdict, analyze, decide, keyword_findings

_GREETING = re.compile(r"^\s*(hi|hello|hey|namaste|namaskar|vanakkam)\W*$", re.I)
_ON_TOPIC = re.compile(
    r"scam|fraud|\breal\b|trust|money|\bpay|bank|\bupi\b|\botp\b|\blink\b|profile|\bmatch\b|dating|\bloan\b|\bjob\b"
    r"|invest|police|forward|message|\bcall\b|ठग|पैसे|மோசடி|பணம்",
    re.I,
)
_DELETE = re.compile(r"^\s*(delete my data|delete my case|मेरा डेटा हटाओ|என் தரவை நீக்கு)\W*$", re.I)
_CHAT_KEPT_FOR = timedelta(hours=1)
_SEND_THE_CHAT = "Open the chat, tap More, then Export chat, then Without media, and share it here."
_NEEDS_A_CASE = {"held", "eligible", "request", "credit"}
_STORY_LIMIT = 1500
_QUESTIONS = {
    "story": "What happened? Tell us in your own words, by voice or text: how it started, what they said and how you paid.",
    "when": "When did this happen?",
    "state": "Which state do you live in?",
    "district": "Which district?",
    "bank": "Which bank or wallet did the money leave?",
    "paid_on": "On what date did you pay?",
    "amount": "How much money left your account? Send the amount.",
    "reference": "What is the transaction ID or UTR? It is on the payment screen or the bank SMS.",
}
_MORE_STORY = "Please tell us a little more: how it started, what they said and how you paid."
_REFUND_REMARKS = "I request restoration of the amount held against my complaint, to my own bank account."


@dataclass
class Incoming:
    text: str = ""
    voice_text: str = ""
    screenshot_text: str = ""
    chat_export: str = ""
    button: str = ""
    now: datetime = None
    audio: bytes = None
    images: tuple = ()


@dataclass
class Outgoing:
    answer: Answer
    sheet: object = None
    other_reports: bool = False
    sheet_draft: bool = False
    also: list = field(default_factory=list)
    call: dict = None


@dataclass
class Session:
    user: str
    language: str = "en"
    case: Case = None
    facts: dict = field(default_factory=dict)
    chat: Conversation = None
    other: str = None
    findings: list = field(default_factory=list)
    identifiers: dict = field(default_factory=dict)
    spoken: str = ""
    words: list = field(default_factory=list)
    asking: str = None
    images: list = field(default_factory=list)
    chat_at: datetime = None


def _detect_language(text, current):
    if re.search("[ऀ-ॿ]", text):
        return "hi"
    if re.search("[஀-௿]", text):
        return "ta"
    return current


def _join(*parts):
    return " ".join(part for part in parts if part)


class Engine:
    def __init__(self, network, model=None, store=None):
        self.network = network
        self.model = model
        self.store = store
        self.sessions = {}
        self.calls = None

    def session(self, user):
        if user not in self.sessions:
            self.sessions[user] = self._load(user)
        return self.sessions[user]

    def is_new(self, user):
        return user not in self.sessions and not (self.store and self.store.load(user))

    def _load(self, user):
        saved = self.store.load(user) if self.store else None
        if not saved:
            return Session(user)
        case = Case(**saved.pop("case")) if saved.get("case") else None
        return Session(user, case=case, **{key: value for key, value in saved.items() if key != "case"})

    def _save(self, session):
        if self.store:
            self.store.save(session.user, {
                "language": session.language, "facts": session.facts, "spoken": session.spoken,
                "words": session.words, "asking": session.asking,
                "case": asdict(session.case) if session.case else None,
            })

    def forget(self, user):
        self.network.forget_case(user)
        if self.store:
            self.store.delete(user)
        self.sessions[user] = Session(user)

    def handle(self, user, incoming):
        """A crash never reaches the person. They get the safe welcome, and the error goes to the log."""
        incoming.now = incoming.now or datetime.now()
        session = self.session(user)
        try:
            out = self._route(session, incoming)
        except Exception:
            traceback.print_exc()
            return self._finish(session, templates.welcome(session.language))
        if self.sessions.get(user) is session:
            self._save(session)
        return out

    def _route(self, session, incoming):
        user = session.user
        if session.chat_at and incoming.now - session.chat_at > _CHAT_KEPT_FOR:
            session.chat, session.other, session.findings, session.identifiers, session.chat_at = None, None, [], {}, None
        if _DELETE.match(incoming.text):
            self.forget(user)
            session = self.session(user)
            return self._finish(session, Answer("Your data is deleted.", [], "Send anything to start again."))
        session.images = [image for image, _ in incoming.images]
        voice = incoming.voice_text
        if incoming.audio:
            voice = senses.hear(self.model, incoming.audio)
            if voice is None:
                return self._say(session, Answer("We could not hear that voice note.", [], "Please type it here instead."))
        said = _join(incoming.text, voice)
        session.language = _detect_language(said, session.language)

        cleaned = scrub(said)
        if cleaned.kinds:
            return self._finish(session, templates.secret_refused(cleaned.kinds, session.language))
        if incoming.button:
            return self._press(session, incoming.button)
        if incoming.chat_export:
            session.chat_at = incoming.now
            return self._receive_chat(session, scrub(incoming.chat_export).text)

        screenshot = scrub(incoming.screenshot_text).text
        paid_on_screen = False
        for image, mime in incoming.images:
            seen = senses.see(self.model, image, mime)
            if seen is None:
                if not said:
                    return self._say(session, Answer("We could not read that image.", [], "Please type what it says, with the amount and any reference number."))
                continue
            text = scrub(seen.text).text
            if seen.kind != "payment":
                said = _join(said, text)
            elif seen.payment_state == "failed":
                return self._say(session, Answer("This payment shows as failed.", [], "Check your bank app to be sure. Do not try the payment again."))
            else:
                screenshot = _join(screenshot, text)
                paid_on_screen = True

        kind = during_call(said)
        if kind:
            return self._finish(session, templates.during_call(kind, session.language))
        if check_relay(said).reason == "recovery_offer":
            return self._say(session, Answer(
                "This looks like a second scam.", [],
                "Real refunds never ask for a fee and never use agents. Do not pay. Ask your police station.",
            ))

        message = classify_message(said)
        if message.kind == "ack" or (message.kind in _NEEDS_A_CASE and session.case):
            return self._track(session, said, incoming.now)
        if session.asking and said and not paid_on_screen:
            return self._collect(session, said, incoming.now)
        if paid_on_screen or money_has_moved(f"{said} {screenshot}"):
            return self._loss(session, voice or incoming.text, screenshot)
        found = senses.triage(self.model, said) if said and not _GREETING.match(said) else None
        if found and found[0] == "live_pressure":
            live = found[1] if found[1] != "none" else "fake_authority"
            return self._finish(session, templates.during_call(live, session.language))
        if found and found[0] == "money_gone":
            return self._loss(session, said, screenshot)
        return self._check_message(session, said, off_topic=bool(found) and found[0] == "off_topic")

    def _finish(self, session, answer, sheet=None, other_reports=False):
        """Nothing leaves without passing the format check. If something slips, the safe welcome goes instead."""
        answer.proof = answer.proof[:3]
        if check_answer(answer):
            answer = templates.welcome(session.language)
        return Outgoing(answer, sheet, other_reports)

    def _say(self, session, answer, sheet=None):
        """For English wording. Spark translates it when it can, and numbers are checked, otherwise English goes."""
        if session.language != "en" and self.model:
            lines = [answer.headline, *answer.proof, answer.next_step]
            out = senses.translate(self.model, lines, session.language)
            if out:
                answer = Answer(out[0], out[1:-1], out[-1], answer.buttons)
        return self._finish(session, answer, sheet)

    def _receive_chat(self, session, export):
        session.chat = parse_whatsapp_export(export, session.user)
        session.other = None
        speakers = session.chat.speakers()
        if not speakers:
            return self._say(session, Answer("We could not read that chat.", [], "Try the export again, or send screenshots."))
        if len(speakers) == 1:
            return self._read_chat(session, speakers[0])
        names = [name[:20] for name in speakers[:3]]
        return self._say(session, Answer("Which name is you?", [], "Tap your name, so we read the other person.", names))

    def _analyze(self, chat, other):
        try:
            return analyze(chat, self.model, other)
        except BudgetExceeded:
            return analyze(chat, None, other)

    def _read_chat(self, session, other):
        session.other = other
        chat = session.chat
        report = self._analyze(chat, other)
        said = "\n".join(turn.text for turn in chat.turns if turn.speaker == other)
        session.findings = report.findings
        session.identifiers = extract_identifiers(said)
        return self._report(session, report.verdict, chat, said)

    def _report(self, session, verdict, chat, said):
        flags = money_flags(said)
        count = self.network.report_count(session.user, session.identifiers, script_text=said, images=session.images)
        if flags and verdict.level == "nothing_found":
            verdict = Verdict("early_signs", [flags[0].detail])
        if any(flag.name == "pay_to_withdraw" for flag in flags):
            verdict = Verdict("known_script", [flags[0].detail])
        if count and verdict.level == "nothing_found":
            verdict = Verdict("early_signs", ["Details match other reports"])
        answer = templates.script_report(verdict, session.findings, chat, session.language)
        extra = [f"{flag.detail} {flag.source}" if flag.name == "broker_without_valid_handle" else flag.detail for flag in flags[:1]]
        if count:
            extra.append("1 other report shares these details." if count == 1 else f"{count} other reports share these details.")
        if extra:
            answer.proof = (extra + answer.proof)[:3]
        return self._finish(session, answer)

    def _check_message(self, session, said, off_topic=False):
        if not said.strip() or _GREETING.match(said):
            return self._finish(session, templates.welcome(session.language))
        chat = Conversation(session.user, [Turn(0, "them", said)])
        flags = money_flags(said)
        if not flags and not keyword_findings(chat, "them") and (off_topic or not _ON_TOPIC.search(said)):
            return self._finish(session, templates.decline(session.language))
        report = self._analyze(chat, "them")
        session.chat, session.other, session.findings = chat, "them", report.findings
        session.identifiers = extract_identifiers(said)
        return self._report(session, report.verdict, chat, said)

    def _loss(self, session, spoken, screenshot):
        session.spoken = spoken or session.spoken
        result = reconcile(session.spoken, screenshot)
        session.case = session.case or Case(session.user)
        if result.amount is not None:
            session.facts["amount"] = result.amount
        if result.reference:
            session.facts["reference"] = result.reference
        if spoken:
            session.words.append(spoken)
        printed_date = payment_date(screenshot)
        if printed_date:
            session.facts["paid_on"] = printed_date
        if result.amount is None and parse_amounts(session.spoken) and parse_amounts(screenshot):
            session.asking = "amount"
        self._write_story(session)
        identifiers = extract_identifiers(f"{spoken} {screenshot}")
        session.facts["suspect_upi"] = identifiers["upi"]
        if identifiers["phone"]:
            session.facts["suspect_mobile"] = identifiers["phone"][0]
        kept = {kind: identifiers[kind] for kind in ("upi", "phone", "link")}
        matches = self.network.matches(session.user, kept, images=session.images)
        self.network.add_case(session.user, kept, images=session.images)
        answer = templates.after_loss(result, session.language)
        if result.questions and session.language != "en" and self.model:
            answer.proof = senses.translate(self.model, answer.proof, session.language) or answer.proof
        if matches:
            answer.proof = answer.proof + ["Someone else reported this same account."]
        return self._finish(session, answer, other_reports=bool(matches))

    def _write_story(self, session, english=None):
        """True facts from code, then the person's own account. Nothing is padded or invented."""
        facts = session.facts
        opening = []
        if facts.get("amount") is not None:
            opening.append(f"I lost {format_rupees(facts['amount'])} in an online scam.")
        if facts.get("reference"):
            opening.append(f"The transaction reference is {facts['reference']}.")
        story = " ".join([*opening, english or " ".join(session.words)])
        session.facts["story"] = story[:_STORY_LIMIT].rsplit(" ", 1)[0] if len(story) > _STORY_LIMIT else story

    def _missing(self, session):
        sheet = filing_sheet(session.facts)
        problems = {item.label for item in sheet.fields if item.problems}
        labels = {
            "Fraud amount": "amount", "Incident details": "story", "Approximate Date & Time": "when", "State": "state",
            "District": "district", "Bank, wallet or merchant": "bank", "Transaction ID or UTR": "reference",
            "Date of transaction": "paid_on",
        }
        order = [labels[label] for label in labels if label in problems]
        return sheet, order

    def _collect(self, session, said, now):
        field_name = session.asking
        if field_name == "amount":
            amount = self._read_amount(said)
            if amount is None:
                return self._say(session, Answer("We need the exact amount.", [], _QUESTIONS["amount"]))
            session.facts["amount"] = amount
            self._write_story(session)
            return self._next_question(session)
        if field_name == "reference":
            found = extract_identifiers(said)["reference"]
            session.facts["reference"] = found[0] if found else said.strip()
            self._write_story(session)
            return self._next_question(session)
        if field_name == "story":
            session.words.append(said)
        pulled = senses.pull_details(self.model, " ".join(session.words + [said]), self._known(session), now)
        for name in ("when", "state", "district", "police_station", "bank", "paid_on"):
            if pulled.get(name) and not session.facts.get(name):
                session.facts[name] = pulled[name]
        self._write_story(session, pulled.get("story_english"))
        if field_name != "story" and not pulled.get(field_name):
            session.facts[field_name] = self._read_answer(field_name, said, now)
        return self._next_question(session)

    def _read_amount(self, said):
        found = parse_amounts(said)
        if found:
            return found[0].value
        bare = re.fullmatch(r"\s*(\d[\d,]*(?:\.\d+)?)\s*", said)
        try:
            return Decimal(bare.group(1).replace(",", "")) if bare else None
        except InvalidOperation:
            return None

    def _known(self, session):
        return {key: session.facts[key] for key in ("amount", "reference") if session.facts.get(key) is not None}

    def _read_answer(self, field_name, said, now):
        if field_name == "state":
            return senses.match_state(said) or said.strip()
        if field_name in ("when", "paid_on"):
            return senses.resolve_date(said, now)
        return said.strip()

    def _next_question(self, session):
        sheet, missing = self._missing(session)
        if session.case and session.case.state == "new":
            session.asking = None
            amount = format_rupees(session.facts["amount"])
            return self._say(session, Answer(f"Thanks. We have {amount}.", [], next_step(session.case)))
        if not missing:
            session.asking = None
            return self._say(session, Answer(
                "Your filing sheet is ready.", [],
                "Open cybercrime.gov.in and copy each field across. You submit it yourself.",
            ), sheet)
        repeat = session.asking == "story" and missing[0] == "story"
        session.asking = missing[0]
        question = _MORE_STORY if repeat else _QUESTIONS[session.asking]
        out = self._say(session, Answer("Thanks. One more thing.", [], question), sheet)
        out.sheet_draft = True
        return out

    def _track(self, session, said, now):
        session.case = session.case or Case(session.user)
        apply_message(session.case, said, now)
        sheet = None
        proof = []
        if session.case.state == "helpline_ack":
            sheet, missing = self._missing(session)
            if missing:
                session.asking = missing[0]
                proof = [
                    "Still needed before you file: " + ", ".join(item.label for item in sheet.fields if item.problems) + ".",
                    _QUESTIONS[session.asking],
                ]
        elif session.case.state == "eligible" and session.case.ack_id:
            sheet = refund_sheet(session.case.ack_id, _REFUND_REMARKS, held=session.case.held)
        headline = "Got it. Your case is updated."
        if session.case.state == "closed":
            headline = f"{format_rupees(session.case.credited)} is back in your account."
        out = self._say(session, Answer(headline, proof, next_step(session.case)), sheet)
        out.sheet_draft = bool(sheet) and not sheet.ready
        return out

    def _press(self, session, button):
        if session.chat and not session.other and button in [name[:20] for name in session.chat.speakers()]:
            me = next(name for name in session.chat.speakers() if name[:20] == button)
            others = [name for name in session.chat.speakers() if name != me]
            return self._read_chat(session, others[0])
        if button == "quotes":
            return self._finish(session, self._quotes(session))
        if button == "report":
            return self._report_suspect(session)
        if button == "paid":
            session.case = session.case or Case(session.user)
            return self._finish(session, templates.after_loss(Reconciled(), session.language))
        if button == "called":
            return self._say(session, Answer("Well done. That was the most important step.", [], "Forward the SMS with your acknowledgement number here."))
        if button == "script":
            if session.case:
                # After a loss, the script is what to say when 1930 answers. The person says their own figures, so
                # a stated amount can go in the script before a screenshot confirms it for the complaint.
                stated = parse_amounts(session.spoken)
                amount = session.facts.get("amount", stated[0].value if stated else None)
                references = extract_identifiers(session.spoken)["reference"]
                reference = session.facts.get("reference", references[0] if references else None)
                return self._finish(session, templates.call_script(amount, reference, session.language))
            return self._say(session, Answer("Send us the whole chat.", [], _SEND_THE_CHAT))
        if button == "call":
            return self._start_call(session)
        return self._finish(session, templates.welcome(session.language))

    def _start_call(self, session):
        """Reads the chat once, up front. The links go out untranslated, in messages of their own, so the guest's can be forwarded."""
        if not self.calls or not session.chat or not session.other or len(session.chat.turns) < 2:
            return self._say(session, Answer("Send us the whole chat first.", [], _SEND_THE_CHAT))
        memory = proof.drop_guessable(self.model, proof.build_memory(self.model, session.chat, session.other))
        call = self.calls.create(memory, owner=session.user)
        out = self._say(session, Answer(
            "Your call is ready.", ["Both of you will be told an AI is on the call before it starts."],
            "Open your link first. Then forward the second link to them.",
        ))
        out.also = [
            f"Your side: {self.calls.link(call.tokens['asker'])}",
            f"Can we talk on a quick call? Open this link: {self.calls.link(call.tokens['guest'])}",
        ]
        out.call = dict(call.tokens)
        return out

    def finish_call(self, user, receipt):
        """The receipt goes back into the same chat, in the person's language."""
        return self._say(self.session(user), proof.receipt_answer(receipt))

    def _quotes(self, session):
        lines = [f'"{found.quote[:200]}"' for found in session.findings[:3]]
        return Answer("These are the exact words.", lines, "You decide what to do next.")

    def _report_suspect(self, session):
        ids = session.identifiers or {}
        if ids.get("phone"):
            report_type, target = "Phone Number", ids["phone"][0]
        elif ids.get("link"):
            report_type, target = "Website URL", ids["link"][0]
        else:
            report_type, target = "WhatsApp Number / Telegram Handle", ""
        signs = list(dict.fromkeys(SIGNALS[found.signal][1].lower() for found in session.findings))
        description = "Reporting a suspected scam attempt in a private chat. Signs we saw: " + "; ".join(signs or ["a request for money"]) + "."
        description = description[:500].rsplit(" ", 1)[0] if len(description) > 500 else description
        sheet = suspect_report_sheet(session.facts.get("state", ""), report_type, target, description)
        kept = {kind: ids.get(kind, []) for kind in ("upi", "phone", "link")}
        said = "\n".join(turn.text for turn in session.chat.turns if turn.speaker == session.other) if session.chat else ""
        self.network.add_case(session.user, kept, script_text=said)
        missing = [item.label for item in sheet.fields if item.problems]
        proof = ["You will need to add: " + ", ".join(missing) + "."] if missing else []
        answer = Answer("Here is your report, one field at a time.", proof, "Enter it under Report Suspect on cybercrime.gov.in. You submit it.")
        return self._say(session, answer, sheet)
