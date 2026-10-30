"""The one contact: everything a person sends comes here and gets one short answer.

Order matters. Secrets are refused first, then live danger gets an instant fixed answer, and only then
does anything slower run. With no model at all, every path still answers from rules and templates.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from redthread import templates
from redthread.amounts import format_rupees
from redthread.case import Case, apply_message, classify_message, next_step
from redthread.conversation import Conversation, Turn, parse_whatsapp_export
from redthread.facts import Reconciled, extract_identifiers, reconcile
from redthread.reply import Answer, check_answer
from redthread.rules import during_call, money_flags, money_has_moved
from redthread.sheets import filing_sheet, refund_sheet, suspect_report_sheet
from redthread.shield import scrub
from redthread.stages import SIGNALS, Verdict, analyze, decide, keyword_findings

_GREETING = re.compile(r"^\s*(hi|hello|hey|namaste|namaskar|vanakkam)\W*$", re.I)
_ON_TOPIC = re.compile(
    r"scam|fraud|\breal\b|trust|money|\bpay|bank|\bupi\b|\botp\b|\blink\b|profile|\bmatch\b|dating|\bloan\b|\bjob\b"
    r"|invest|police|forward|message|\bcall\b|ठग|पैसे|மோசடி|பணம்",
    re.I,
)
_CASE_MESSAGES = {"ack", "held", "eligible", "request", "credit"}


@dataclass
class Incoming:
    text: str = ""
    voice_text: str = ""
    screenshot_text: str = ""
    chat_export: str = ""
    button: str = ""
    now: datetime = None
    images: tuple = ()


@dataclass
class Outgoing:
    answer: Answer
    sheet: object = None
    link_offer: bool = False


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


def _detect_language(text, current):
    if re.search("[ऀ-ॿ]", text):
        return "hi"
    if re.search("[஀-௿]", text):
        return "ta"
    return current


class Engine:
    def __init__(self, network, model=None):
        self.network = network
        self.model = model
        self.sessions = {}

    def session(self, user):
        return self.sessions.setdefault(user, Session(user))

    def handle(self, user, incoming):
        session = self.session(user)
        said = " ".join(part for part in (incoming.text, incoming.voice_text) if part)
        session.language = _detect_language(said, session.language)

        cleaned = scrub(said)
        if cleaned.kinds:
            return self._finish(session, templates.secret_refused(cleaned.kinds, session.language))
        if incoming.button:
            return self._press(session, incoming.button)
        if incoming.chat_export:
            return self._receive_chat(session, scrub(incoming.chat_export).text)

        kind = during_call(said)
        if kind:
            return self._finish(session, templates.during_call(kind, session.language))

        screenshot = scrub(incoming.screenshot_text).text
        message = classify_message(said)
        if message.kind in _CASE_MESSAGES:
            return self._track(session, said, incoming.now)
        if money_has_moved(f"{said} {screenshot}"):
            return self._loss(session, incoming.voice_text or incoming.text, screenshot)
        return self._check_message(session, said)

    def _finish(self, session, answer, sheet=None, link_offer=False):
        """Nothing leaves without passing the format check. If something slips, the safe welcome goes instead."""
        answer.proof = answer.proof[:3]
        if check_answer(answer):
            answer = templates.welcome(session.language)
        return Outgoing(answer, sheet, link_offer)

    def _receive_chat(self, session, export):
        session.chat = parse_whatsapp_export(export, session.user)
        speakers = session.chat.speakers()
        if not speakers:
            return self._finish(session, Answer("We could not read that chat.", [], "Try the export again, or send screenshots."))
        if len(speakers) == 1:
            return self._read_chat(session, speakers[0])
        names = [name[:20] for name in speakers[:3]]
        return self._finish(session, Answer("Which name is you?", [], "Tap your name, so we read the other person.", names))

    def _read_chat(self, session, other):
        session.other = other
        chat = session.chat
        report = analyze(chat, self.model, other)
        said = "\n".join(turn.text for turn in chat.turns if turn.speaker == other)
        session.findings = report.findings
        session.identifiers = extract_identifiers(said)
        return self._report(session, report.verdict, chat, said)

    def _report(self, session, verdict, chat, said):
        flags = money_flags(said)
        count = self.network.report_count(session.user, session.identifiers, script_text=said)
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

    def _check_message(self, session, said):
        if not said.strip() or _GREETING.match(said):
            return self._finish(session, templates.welcome(session.language))
        chat = Conversation(session.user, [Turn(0, "them", said)])
        findings = keyword_findings(chat, "them")
        flags = money_flags(said)
        session.chat, session.other, session.findings = chat, "them", findings
        session.identifiers = extract_identifiers(said)
        if not findings and not flags and not _ON_TOPIC.search(said):
            return self._finish(session, templates.decline(session.language))
        return self._report(session, decide(findings), chat, said)

    def _loss(self, session, spoken, screenshot):
        result = reconcile(spoken, screenshot)
        session.case = session.case or Case(session.user)
        if result.amount is not None:
            session.facts["amount"] = result.amount
        if result.reference:
            session.facts["reference"] = result.reference
        if spoken:
            session.facts["story"] = spoken
        identifiers = extract_identifiers(f"{spoken} {screenshot}")
        session.facts["suspect_upi"] = identifiers["upi"]
        if identifiers["phone"]:
            session.facts["suspect_mobile"] = identifiers["phone"][0]
        kept = {kind: identifiers[kind] for kind in ("upi", "phone", "link")}
        matches = self.network.matches(session.user, kept)
        self.network.add_case(session.user, kept)
        answer = templates.after_loss(result, session.language)
        if matches:
            answer.proof = answer.proof + ["Someone else reported this same account. If you both agree, you can talk."]
        return self._finish(session, answer, link_offer=bool(matches))

    def _track(self, session, said, now):
        session.case = session.case or Case(session.user)
        apply_message(session.case, said, now)
        sheet = None
        proof = []
        if session.case.state == "helpline_ack":
            sheet = filing_sheet(session.facts)
            missing = [item.label for item in sheet.fields if item.problems]
            if missing:
                proof.append("Still needed before you file: " + ", ".join(missing) + ".")
        elif session.case.state == "eligible" and session.case.ack_id:
            remarks = "I request restoration of the amount held against my complaint, to my own bank account."
            sheet = refund_sheet(session.case.ack_id, remarks, held=session.case.held)
        headline = "Got it. Your case is updated."
        if session.case.state == "closed":
            headline = f"{format_rupees(session.case.credited)} is back in your account."
        return self._finish(session, Answer(headline, proof, next_step(session.case)), sheet)

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
        return self._finish(session, templates.welcome(session.language))

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
        answer = Answer("Here is your report, one field at a time.", [], "Enter it under Report Suspect on cybercrime.gov.in. You submit it.")
        return self._finish(session, answer, sheet)
