"""The test lab. We attack our own checks with fakes made by Meta models, and publish what got through.

Bills: Muse Image prints bills from numbers we choose, some with a planted problem and some fair. The real pipeline reads
each photo (Spark), rings its QR (SAM) and compares it with the menu (code). Code knows the truth, so code scores the run.

Bluffs: Muse Wonder plays a scam worker who never saw the chat, and tries to answer every Proof Call question blind.
Each question it gets right is one the guard drops before a call.

Run with: python -m redthread.lab bills|bluff|all
"""
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from redthread import bill, proof, proof_demo
from redthread.conversation import Conversation, Turn
from redthread.model import ModelError

VENUE = "MOONLIT TERRACE CAFE"
BUSINESS = "moonlitterrace@hdfcbank"
RESULTS = Path(__file__).resolve().parent / "web" / "static" / "lab.json"


@dataclass
class BillCase:
    id: str
    title: str
    plant: str
    lines: list
    charges: list = field(default_factory=list)
    payee: str = BUSINESS
    expect_level: str = "fair"
    expect_kinds: set = field(default_factory=set)
    printed_total: int = 0
    venue: str = VENUE

    @property
    def total(self):
        return self.printed_total or sum(amount for _, _, amount in self.lines) + sum(amount for _, amount in self.charges)

    @property
    def planted(self):
        return self.expect_level != "fair" or bool(self.expect_kinds)


BILLS = [
    BillCase("ten-times", "Ten times the menu", "Three lines at ten times the menu price, a service charge, and a QR to a personal UPI ID.",
             [("Virgin Mojito", 2, 4800), ("Paneer Tikka", 1, 320), ("Hookah", 1, 9500), ("Whisky 30 ml", 4, 28000)],
             [("Service charge", 4262)], "rohit.k@okaxis", "overcharged", {"overcharge", "service", "payee"}),
    BillCase("one-double", "One line doubled", "Two cold coffees billed at twice the menu price.",
             [("Cold Coffee", 2, 720), ("Paneer Tikka", 1, 320)], [("GST 5%", 52)], expect_level="overcharged", expect_kinds={"overcharge"}),
    BillCase("subtle", "Fifteen percent over", "The veg platter billed at 520 against 450 on the menu.",
             [("Veg Platter", 1, 520), ("Virgin Mojito", 2, 480)], [("GST 5%", 50)], expect_level="overcharged", expect_kinds={"overcharge"}),
    BillCase("padded-total", "A padded total", "Every line is fair, but the printed total is 1,000 more than they add up to.",
             [("Paneer Tikka", 1, 320), ("Cold Coffee", 2, 360)], [("GST 5%", 34)], printed_total=1714,
             expect_level="total_off", expect_kinds={"total_off"}),
    BillCase("ghost-item", "A ghost item", "A 1,500 table reservation that is not on the menu.",
             [("Virgin Mojito", 1, 240), ("Table reservation", 1, 1500), ("Veg Platter", 1, 450)],
             expect_level="check", expect_kinds={"not_on_menu"}),
    BillCase("personal-upi", "A personal UPI ID", "Fair prices, but the QR pays a person instead of the cafe.",
             [("Hookah", 1, 950), ("Cold Coffee", 1, 180)], payee="anita.r@oksbi", expect_kinds={"payee"}),
    BillCase("service-added", "Service charge added", "Fair prices with a service charge added by default.",
             [("Paneer Tikka", 2, 640), ("Virgin Mojito", 2, 480)], [("Service charge", 112)], expect_kinds={"service"}),
    BillCase("fair-a", "Fair bill", "Nothing planted.", [("Whisky 30 ml", 2, 1400), ("Paneer Tikka", 1, 320)], [("GST 5%", 86)]),
    BillCase("fair-b", "Fair bill", "Nothing planted.", [("Cold Coffee", 3, 540), ("Veg Platter", 1, 450)]),
    BillCase("fair-c", "Fair bill", "Nothing planted.", [("Virgin Mojito", 1, 240), ("Hookah", 1, 950), ("Cold Coffee", 1, 180)], [("GST 5%", 69)]),
]


def bill_prompt(case):
    lines = ", ".join(f"{name} x{quantity} {amount}" for name, quantity, amount in case.lines)
    charges = "".join(f", {name} {amount}" for name, amount in case.charges)
    return (
        "Flat photo of a printed restaurant bill on a table, shot from directly above, sharp and fully legible. "
        f"Header: {case.venue}. Lines with item, quantity and amount in Indian rupees: {lines}{charges}. "
        f"TOTAL {case.total}. At the bottom a black and white UPI QR code with the text: Pay to {case.payee}. "
        "Plain black text on white paper. No other prices or amounts anywhere."
    )


def score_bill(case, read, check, qr):
    """Code knows what was planted, so code says whether it was caught."""
    found = {item.kind for item in check.findings}
    right = check.level == case.expect_level and found == case.expect_kinds
    truth = [(quantity, amount) for _, quantity, amount in case.lines]
    got = [(line.quantity, line.amount) for line in read.lines] if read else []
    return {
        "id": case.id, "title": case.title, "plant": case.plant, "planted": case.planted,
        "expected": sorted(case.expect_kinds) or ["nothing"], "found": sorted(found) or ["nothing"],
        "caught": right, "false_alarm": not case.planted and bool(found or check.level != "fair"),
        "read_exact": got == truth and bool(read) and read.total == case.total,
        "qr_found": qr is not None, "headline": check.headline, "findings": [item.text for item in check.findings],
    }


def summarize_bills(rows):
    planted = [row for row in rows if row["planted"]]
    return {
        "bills": len(rows), "planted": len(planted), "caught": sum(1 for row in planted if row["caught"]),
        "fair": len(rows) - len(planted), "false_alarms": sum(1 for row in rows if row["false_alarm"]),
        "read_exact": sum(1 for row in rows if row["read_exact"]), "qr_found": sum(1 for row in rows if row["qr_found"]),
    }


def run_bills(model, menu_raw, images_dir):
    """Images are made once and kept, so a rerun reads the same photos and costs only the reads."""
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    menu = bill.read_menu(model, menu_raw, "image/jpeg")
    rows = []
    for case in BILLS:
        path = images_dir / f"lab-{case.id}.jpg"
        for attempt in range(3):
            if path.exists():
                break
            try:
                path.write_bytes(model.image(bill_prompt(case)))
            except ModelError:
                # The image service sometimes refuses a prompt once and accepts it the next time.
                if attempt == 2:
                    raise
        raw = path.read_bytes()
        read = bill.read_bill(model, raw, "image/jpeg")
        qr = bill.find_qr(model, raw, "image/jpeg")
        rows.append(score_bill(case, read, bill.compare(menu, read), qr))
        print(f"{case.id}: caught={rows[-1]['caught']} found={rows[-1]['found']} read_exact={rows[-1]['read_exact']} qr={qr is not None}")
    return rows


def _chat(name, other, lines):
    return name, Conversation(name, [Turn(index, other if speaker == "them" else "Me", text) for index, (speaker, text) in enumerate(lines)]), other


def chats():
    """Invented chats, each full of small details, as a real match would write."""
    return [
        ("Rohan", proof_demo.chat(), proof_demo.OTHER),
        _chat("Priya", "Priya", [
            ("them", "Hi! I see you like trekking too. I just got back from Kalsubai, my knees are still angry with me."),
            ("me", "Ha, how was it?"),
            ("them", "Foggy. We got lost for an hour because my cousin Tanvi insisted on a shortcut past a temple."),
            ("me", "What do you do in Pune?"),
            ("them", "I'm an architect, mostly old wadas being turned into homestays. My current one has a broken well in the courtyard."),
            ("me", "That sounds lovely."),
            ("them", "It is, until the owner's goat eats our measuring tape. That happened on Thursday."),
            ("me", "No way."),
            ("them", "Yes way. I also make terrible misal on Sundays, my flatmate rates it four out of ten."),
            ("me", "Only four?"),
            ("them", "She says the farsan goes soggy because I add it too early. She is right, sadly."),
            ("them", "Next weekend I want to try the Sinhagad night trek if the rain stops."),
        ]),
        _chat("Kabir", "Kabir", [
            ("them", "Your dog in the third photo looks like he runs the house."),
            ("me", "He does. Do you have pets?"),
            ("them", "A grey cat called Masala who only drinks water from the bathroom tap."),
            ("me", "Picky."),
            ("them", "Very. I teach chemistry at a school in Jaipur, so I'm used to fussy creatures."),
            ("me", "Haha. Fun weekend?"),
            ("them", "I went to my nani's in Ajmer, she made me grind spices for gatte all morning."),
            ("me", "Did you like it?"),
            ("them", "My arms didn't. I also broke her old brass lota and blamed the cat, who wasn't even there."),
            ("them", "I play the harmonium badly at family weddings, it's my one party trick."),
            ("me", "I need to hear that."),
            ("them", "Only if you promise not to record it. My brother Ishaan posted the last one and it went round the whole family group."),
        ]),
        _chat("Meera", "Meera", [
            ("them", "Okay I have to know, is that really you on a camel in your second picture?"),
            ("me", "Sadly yes. You?"),
            ("them", "I'm a nurse at a hospital in Kochi, night shifts this month, so I am replying at 4 am, sorry."),
            ("me", "No worries. What do you do on days off?"),
            ("them", "I sleep, and then I row. I joined a club on the backwaters near Kumarakom last year."),
            ("me", "Rowing! Is it hard?"),
            ("them", "I fell in on my first day and lost my left slipper. The coach still calls me Cinderella."),
            ("me", "Amazing."),
            ("them", "My appa grows pepper and nutmeg at home in Idukki, I'm bringing a bag back after Onam."),
            ("them", "Also I'm learning Kannada from my roommate Divya, I can only count to six."),
            ("me", "Six is a start."),
            ("them", "That's what I said! She makes me practise with the auto drivers."),
        ]),
    ]


def run_bluff(spark, wonder, chat_list):
    rows = []
    for name, chat, other in chat_list:
        memory = proof.drop_guessable(spark, proof.build_memory(spark, chat, other), wonder)
        guard = memory.guard or {"tried": 0, "guessed": []}
        rows.append({"chat": name, "tried": guard["tried"], "guessed": guard["guessed"], "fallback": memory.guard is None})
        print(f"{name}: guessed {len(guard['guessed'])} of {guard['tried']}")
    return rows


def summarize_bluff(rows):
    tried = sum(row["tried"] for row in rows)
    guessed = sum(len(row["guessed"]) for row in rows)
    return {"chats": len(rows), "tried": tried, "guessed": guessed, "kept": tried - guessed,
            "unguarded": sum(1 for row in rows if row["fallback"])}


def main(argv=sys.argv[1:]):
    from redthread.model import HOME, WONDER, Ledger, MetaModel, load_api_key

    which = argv[0] if argv else "all"
    model = MetaModel(load_api_key(), Ledger(5.0, HOME / "results" / "lab-ledger.json"))
    before = model.ledger.spent
    results = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    if which in ("bills", "all"):
        static = RESULTS.parent
        rows = run_bills(model, (static / "example-menu.jpg").read_bytes(), static)
        results["bills"] = {"summary": summarize_bills(rows), "rows": rows}
    if which in ("bluff", "all"):
        rows = run_bluff(model, model.using(WONDER), chats())
        results["bluff"] = {"summary": summarize_bluff(rows), "rows": rows}
    results["ran"] = time.strftime("%d %b %Y")
    RESULTS.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Spent ${model.ledger.spent - before:.3f}. Results in {RESULTS}")


if __name__ == "__main__":
    main()
