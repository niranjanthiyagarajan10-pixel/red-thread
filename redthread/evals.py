"""Plants errors on purpose and measures that the fixed rules catch them, with no model and no key.

Every group has things that must be caught and normal text that must be left alone.
Run with: python -m redthread.evals
"""
import sys
from dataclasses import dataclass, field
from decimal import Decimal

from redthread.amounts import parse_amounts
from redthread.portal_rules import clean_text, portal_error
from redthread.rules import during_call, money_has_moved
from redthread.shield import check_relay, find_sensitive

AMOUNTS = [
    ("1.5L", 150000), ("2.5 L", 250000), ("Rs 1.5 lakh", 150000), ("₹ 2,00,000/-", 200000), ("₹2,00,000", 200000),
    ("50K", 50000), ("50k only", 50000), ("dhai lakh", 250000), ("INR 75,000.50", Decimal("75000.50")),
    ("2 crore", 20000000), ("2cr", 20000000), ("Rs.5000", 5000), ("Rs. 5,000/-", 5000), ("paanch hazaar", 5000),
    ("ek lakh pachas hazaar", 150000), ("1,80,000", 180000), ("1.5 lac", 150000), ("डेढ़ लाख", 150000),
    ("இரண்டு லட்சம்", 200000), ("sawa lakh", 125000), ("saade teen lakh", 350000), ("a lakh and a half", 150000),
    ("Paid Rs 38,000 to scam@ybl", 38000), ("I already paid 38,000 38,000 rupees to that man.", 38000),
    ("thirty-eight thousand", 38000),
]
NOT_AMOUNTS = [
    "UTR 533827652689", "Acknowledgement No 30000000000012", "call 9876543210", "1,000 people", "room 204",
    "see you at 5", "I have two kids", "pin code 600001",
]
SECRETS = [
    "my otp 123456", "otp: 4821", "code is 482913 otp", "pin 1234", "my upi pin is 1234", "atm pin 4455",
    "4111 1111 1111 1111", "Paid Rs 500 ref 4111111111111111", "aadhaar 2345 6789 0123", "आधार 2345 6789 0123",
    "ABCPK1234L", "password: hunter2", "my password hunter2", "cvv 123", "my otp is four eight two one",
    "otp hai char aath do ek", "ओटीपी 482913",
]
NOT_SECRETS = [
    "UTR 533827652689", "Acknowledgement No 30000000000012", "Complaint 20000000000012 registered",
    "Your request ID is MR20261101000003", "pin code 600001", "call 9876543210", "Rs 2,50,000 debited",
    "he asked for my otp", "what is an otp", "UTR 533827652689 31 Oct 2026",
]
RELAY_BLOCKS = [
    "call me on nine eight seven six five four three two one zero", "telegram rahul_88", "my insta is @rahul",
    "my telegram is rahul", "whatsapp me 98765 43210", "mail rahul at gmail dot com", "rahul@ybl", "bit.ly/abc",
    "go to refund-help.in/start", "I know someone who can get your money back",
    "contact my lawyer he helps for a small fee", "a hacker recovered mine", "my otp is 123456",
]
RELAY_ALLOWS = [
    "I lost money too", "the police station took my complaint on Monday", "he moved me to telegram after a week",
    "telegram is where it started", "my bank said to wait for the hold", "I got the refund after 40 days",
    "it took 3 weeks", "my lawyer said to file an FIR",
]
LIVE_CALLS = [
    ("CBI officer on video call", "fake_authority"), ("he says digital arrest", "fake_authority"),
    ("police on skype asking to stay on call", "fake_authority"), ("RBI officer called me", "fake_authority"),
    ("customs officer says parcel has drugs", "fake_authority"), ("they asked me to install anydesk", "remote_app"),
    ("share screen with bank man", "remote_app"), ("move money to safe account", "safe_account"),
    ("tell me the code you just got", "code_request"), ("caller wants my otp", "code_request"),
]
NOT_LIVE_CALLS = [
    "my friend is a cbi officer lol", "I called the police and filed a complaint", "my trainer is on a video call",
    "what is the pin code for Chennai, can you give it", "I will go to the police station tomorrow",
    "herbivore diet on the phone",
]
PAID = [
    "I already paid 40k", "sent the money yesterday", "paise bhej diye", "Rs 500 debited", "I lost 2 lakh",
    "I did not know it was a scam and paid 40k", "I didn't check and sent 5000", "मैंने पैसे भेज दिए",
    "Rs 500 kat gaye", "transferred everything",
]
NOT_PAID = [
    "I did not pay", "should I pay?", "he wants me to pay", "I sent him the screenshot", "I lost my phone",
    "paise nahi bheje", "I have not paid yet", "she paid for dinner on Friday",
]
PORTAL_TEXTS = [
    "I want to delete the app, drop the matter and update the bank",
    "Please insert this. He told me to DELETE the chat and UPDATE the wallet.",
    "same same same word was sent",
    "He said <urgent> pay now | send 'proof' ~ today ^ now * please",
    "\"Pay\" he said",
    "Truncate the story: I paid. I lost it.",
]
PORTAL_UNFIXABLE = ["12345"]

DEV = {
    "amounts": AMOUNTS, "not_amounts": NOT_AMOUNTS, "secrets": SECRETS, "not_secrets": NOT_SECRETS,
    "relay": RELAY_BLOCKS, "not_relay": RELAY_ALLOWS, "live": LIVE_CALLS, "not_live": NOT_LIVE_CALLS,
    "paid": PAID, "not_paid": NOT_PAID, "portal": PORTAL_TEXTS, "not_portal": PORTAL_UNFIXABLE,
}
# Written after the rules were fixed against DEV, so its first run measures rules on text they had not seen.
FRESH = {
    "amounts": [
        ("Rs 1,25,000.00", 125000), ("rupees 5000", 5000), ("5000 rupees", 5000), ("paid 12,500 rs", 12500),
        ("3 lakh 20 thousand", 320000), ("teen lakh bees hazaar", 320000), ("₹75k", 75000), ("Rs75,000", 75000),
        ("1 lakh 50k", 150000), ("पचास हज़ार", 50000), ("ஐம்பதாயிரம்", 50000), ("two and a half lakh", 250000),
        ("10 lacs", 1000000), ("Rs 1.25L", 125000), ("one lakh rupees", 100000),
    ],
    "not_amounts": ["Order 12,345", "OTP 482913", "bought 2 l of milk", "version 1.5", "Flat 3A", "in 2026"],
    "secrets": [
        "OTP-482913", "my OTP was 4 8 2 9 1 3", "otp 482 913", "verification code: 7731", "mpin 445566",
        "card no 5500 0000 0000 0004", "5500-0000-0000-0004", "pan abcpk1234l", "aadhar no 234567890123",
        "my aadhaar number is 2345-6789-0123", "passcode 9911", "cvv is 123", "ATM pin is 1234", "mera otp 4821 hai",
        "ஓடிபி 482913",
    ],
    "not_secrets": [
        "Reference 412345678901", "Ticket 4111", "pan card lost", "my pin code is 560001", "card was blocked by bank",
        "UPI ref 533827652689 debit card", "Complaint 30000000000012 about my credit card",
    ],
    "relay": [
        "dm me on ig rahul.k99", "whats app 9 8 7 6 5 4 3 2 1 0", "+91 98765 43210", "reach me at rahul99@gmail.com",
        "t.me/refundhelp", "join my group on telegram, link in bio", "I paid a recovery expert and got it back",
        "pay 2000 and my friend will get it back", "snap: rahul99", "wa.me/919876543210",
        "my cousin in the bank can release your funds for a fee",
    ],
    "not_relay": [
        "I called 1930 within an hour", "my case moved to the next stage", "the officer asked me for a bank statement",
        "I filed at Koramangala station", "he used the same photos with me", "do not pay anyone who says they can recover it",
        "beware of recovery agents, they took more from me",
    ],
    "live": [
        ("Narcotics dept is threatening me right now", "fake_authority"),
        ("someone from TRAI says my number will be cut", "fake_authority"),
        ("bank man says install this apk", "remote_app"), ("he told me to download rustdesk", "remote_app"),
        ("they want me to keep money in RBI safe account", "safe_account"), ("he says read out the otp", "code_request"),
        ("police wale video call pe hain", "fake_authority"), ("पुलिस वाले फोन पर धमका रहे हैं", "fake_authority"),
        ("stay on skype, do not tell anyone, ED case", "fake_authority"),
        ("sir is asking for the 6 digit number i got", "code_request"),
    ],
    "not_live": [
        "my dad retired from customs", "video call with my mom", "what should I do if police call",
        "my code is not working at work, can you give advice", "the ED of my company is on a call",
        "I shared the code on github",
    ],
    "paid": [
        "gave him 20000", "I have transferred 1.2 lakh", "mistakenly paid", "money gone from my account",
        "He took 40k from my account", "maine 5000 de diye", "பணம் அனுப்பிட்டேன்", "they told me to pay and I paid",
        "my account was debited twice", "Rs 2000 cut from my account",
    ],
    "not_paid": [
        "I will pay tomorrow", "he asked me to send 5000", "do not send money", "am I about to lose money",
        "I sent him 5 photos", "paid leave tomorrow", "i almost sent 5000", "I nearly paid him",
    ],
    "portal": [
        "He said don't update, drop & delete it!!!", "UPDATE UPDATE UPDATE my account", "Insert card; then he said #1 $500 @ once",
    ],
    "not_portal": ["1234567890"],
}


@dataclass
class Group:
    name: str
    misses: list = field(default_factory=list)
    false_alarms: list = field(default_factory=list)
    planted: int = 0
    controls: int = 0

    @property
    def caught(self):
        return self.planted - len(self.misses)


def _first_amount(text):
    found = parse_amounts(text)
    return found[0].value if found else None


def _group(name, planted, is_caught, controls, is_alarm):
    group = Group(name, planted=len(planted), controls=len(controls))
    group.misses = [item for item in planted if not is_caught(item)]
    group.false_alarms = [item for item in controls if is_alarm(item)]
    return group


def _portal_ok(text):
    cleaned = clean_text(text)
    return portal_error(cleaned.text) is None


def run_all(batch=DEV):
    return [
        _group(
            "Amounts", batch["amounts"], lambda item: _first_amount(item[0]) == item[1],
            batch["not_amounts"], lambda text: _first_amount(text) is not None,
        ),
        _group(
            "Secrets", batch["secrets"], lambda text: bool(find_sensitive(text)),
            batch["not_secrets"], lambda text: bool(find_sensitive(text)),
        ),
        _group(
            "Relay", batch["relay"], lambda text: check_relay(text).blocked,
            batch["not_relay"], lambda text: check_relay(text).blocked,
        ),
        _group(
            "Live calls", batch["live"], lambda item: during_call(item[0]) == item[1],
            batch["not_live"], lambda text: during_call(text) is not None,
        ),
        _group(
            "Money moved", batch["paid"], money_has_moved,
            batch["not_paid"], money_has_moved,
        ),
        _group("Portal text", batch["portal"], _portal_ok, batch["not_portal"], _portal_ok),
    ]


def render(groups):
    lines = [f"{'Group':<14}{'Caught':>10}{'False alarms':>15}"]
    for group in groups:
        lines.append(f"{group.name:<14}{f'{group.caught}/{group.planted}':>10}{f'{len(group.false_alarms)}/{group.controls}':>15}")
    for group in groups:
        lines += [f"MISS  {group.name}: {item}" for item in group.misses]
        lines += [f"FALSE {group.name}: {item}" for item in group.false_alarms]
    return "\n".join(lines)


def main():
    groups = run_all(FRESH if "--fresh" in sys.argv else DEV)
    print(render(groups))
    return 1 if any(group.misses or group.false_alarms for group in groups) else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
