"""Forms laid out field by field, in portal order, so a person can copy each value across.

Nothing is padded or invented. A missing fact is a problem to fix, and the person submits every form.
Field labels come from the portal's public code and manual. Category lists sit behind its login.
"""
from dataclasses import dataclass, field

from redthread import portal_rules as pr

SUSPECT_REPORT_TYPES = (
    "Website URL", "Phone Number", "SMS Header / Number", "WhatsApp Number / Telegram Handle",
    "Email ID", "Social Media URL", "Deepfake", "Mobile App",
)
_YOURS = "Do not send this here. You do this yourself on the form."


@dataclass
class SheetField:
    label: str
    value: str = ""
    meaning: str = ""
    problems: list = field(default_factory=list)
    person_only: bool = False


@dataclass
class Sheet:
    fields: list
    moved_ids: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def ready(self):
        return not any(item.problems for item in self.fields)


def _required(label, value, meaning=""):
    text = "" if value is None else str(value)
    return SheetField(label, text, meaning, [] if text else ["missing"])


def _yours(label, meaning=_YOURS):
    return SheetField(label, meaning=meaning, person_only=True)


def filing_sheet(facts):
    cleaned = pr.clean_text(facts.get("story", ""))
    utr_note = pr.utr_warning(facts["reference"]) if facts.get("reference") else None
    upi_ids = [*cleaned.moved_ids, *facts.get("suspect_upi", [])]
    fields = [
        SheetField("Category", "Online Financial Fraud", "Check this matches the form."),
        _yours("Sub Category", "Pick the closest type on the form, such as UPI or investment."),
        _required("Approximate Date & Time", facts.get("when")),
        _required("State", facts.get("state"), "This decides which police handle it."),
        _required("District", facts.get("district")),
        SheetField("Police Station", facts.get("police_station", ""), "Pick yours on the form if you are unsure."),
        SheetField("Incident details", cleaned.text, "Copy this as it is.", pr.check_incident(cleaned.text)),
        _required("Bank, wallet or merchant", facts.get("bank")),
        _required("Transaction ID or UTR", facts.get("reference"), utr_note or ""),
        _required("Date of transaction", facts.get("paid_on")),
        _required("Fraud amount", facts.get("amount"), "Rupees, numbers only."),
        _yours("National ID", "Upload it yourself: jpg or png, up to 5 MB. Do not send this here."),
        SheetField("Suspect UPI ID", ", ".join(upi_ids)),
        SheetField("Suspect mobile", facts.get("suspect_mobile", "")),
        SheetField("Suspect website or handle", facts.get("suspect_link", "")),
        _yours("Evidence", "Upload screenshots yourself, up to 10 MB each."),
        _yours("OTP and captcha"),
    ]
    return Sheet(fields, cleaned.moved_ids, cleaned.changes)


def as_messages(sheet):
    total = len(sheet.fields)
    messages = []
    for number, item in enumerate(sheet.fields, 1):
        lines = [f"{number} of {total}: {item.label}"]
        if item.value:
            lines.append(item.value)
        if item.meaning:
            lines.append(item.meaning)
        messages.append("\n".join(lines))
    return messages


def _shorten_to(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")


def refund_sheet(ack_id, remarks, held=None):
    parsed = pr.parse_ack_id(ack_id)
    cleaned = pr.clean_text(remarks)
    text = _shorten_to(cleaned.text, pr.REMARKS_MAX)
    changes = list(cleaned.changes)
    if text != cleaned.text:
        changes.append("Shortened the remarks to fit 500 characters")
    fields = [
        SheetField("Acknowledgement ID", parsed.value if parsed else ack_id, problems=[] if parsed else ["bad_ack"]),
        _yours("OTP"),
        _yours("Restoration details", "Check each bank and amount, then tick the ones to claim. The amount can change."),
        _yours("Account number and IFSC"),
        _yours("PAN card"),
        SheetField("Remarks", text, "Copy this as it is.", pr.check_remarks(text)),
        _yours("Captcha and declaration", "Read the declaration yourself before you tick it."),
    ]
    notes = [
        "A hold does not guarantee restoration, and the amount can change.",
        "The police may ask you to sign an indemnity bond. It is a promise to return the amount if a court asks. Ask them for the format.",
    ]
    if held is not None and pr.fir_rule(held) != "no_fir":
        notes.append("The official rule needs an FIR when more than Rs 50,000 is held in one bank. Ask your police station.")
    return Sheet(fields, cleaned.moved_ids, changes, notes)


def suspect_report_sheet(state, report_type, target, description):
    if report_type not in SUSPECT_REPORT_TYPES:
        raise ValueError(f"The form does not offer {report_type}.")
    cleaned = pr.clean_text(description)
    fields = [
        _required("State of Incident", state),
        SheetField("What do you want to report", report_type),
        _required(report_type, target),
        SheetField("Description", cleaned.text, "Copy this as it is.", pr.check_suspect_report(cleaned.text)),
        _yours("Supporting evidence", "Upload it yourself, up to 5 MB."),
    ]
    return Sheet(fields, cleaned.moved_ids, cleaned.changes)
