"""The whole loop against the real models, with invented people and a hard spending cap.

Make the media first: scripts/make_test_media.ps1. Run with: python -m redthread.live
"""
import argparse
import sys
import time
from datetime import datetime
from decimal import Decimal

from redthread.engine import Engine, Incoming
from redthread.model import HOME, Ledger, MetaModel, load_api_key
from redthread.network import Network
from redthread.reply import render
from redthread.sheets import as_messages
from redthread.world import run_world

NOW = datetime(2026, 11, 1, 10, 0)
CHAT = """01/03/2026, 21:00 - Riya: Hi, you seem so genuine.
01/03/2026, 21:05 - Arjun: Thanks, you too.
02/03/2026, 21:00 - Riya: You are my soulmate, I feel it already.
04/03/2026, 21:00 - Riya: Let us move to Telegram, the app is glitchy.
09/03/2026, 21:00 - Riya: My uncle's desk doubled my savings in one month.
14/03/2026, 21:00 - Arjun: That sounds great for him.
20/03/2026, 21:00 - Riya: Dear, try this platform today, start small, pay scam.one@ybl
"""


class Run:
    def __init__(self, engine):
        self.engine = engine
        self.spent_before = engine.model.ledger.spent

    def step(self, title, user, **fields):
        started = time.perf_counter()
        before = self.engine.model.ledger.spent
        out = self.engine.handle(user, Incoming(now=NOW, **fields))
        seconds = time.perf_counter() - started
        print(f"\n--- {title}  [{seconds:.1f}s, ${self.engine.model.ledger.spent - before:.4f}]")
        print(render(out.answer))
        if out.answer.buttons:
            print(f"[buttons: {', '.join(out.answer.buttons)}]")
        if out.sheet:
            print(f"[sheet with {len(out.sheet.fields)} fields, {'ready' if out.sheet.ready else 'not ready'}]")
        if out.link_offer:
            print("[offer: talk to the other person, only if you both agree]")
        return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, default=1.0)
    args = parser.parse_args(argv)

    clips = HOME / "clips"
    model = MetaModel(load_api_key(), Ledger(args.budget, HOME / "results" / "live-ledger.json"))
    engine = Engine(network=Network(":memory:", salt=b"live"), model=model)
    run = Run(engine)
    png = ((clips / "payment.png").read_bytes(), "image/png")

    run.step("Spark catches pressure the rules miss", "sam", text="the bank fellow is making me open a helper program on my mobile")
    run.step("Spark sees a loss the rules miss", "sam", text="that man emptied everything, nothing left with me")
    run.step("A voice note and a screenshot", "victim", audio=(clips / "loss.wav").read_bytes(), images=(png,))
    run.step("The helpline number arrives", "victim", text="Acknowledgement No 30000000000012")
    out = run.step("One voice note fills the form", "victim", audio=(clips / "story.wav").read_bytes())
    session = engine.session("victim")
    if session.asking:
        print(f"[still asking: {session.asking}]")
    if out.sheet:
        print("\nFirst filing fields:")
        for line in as_messages(out.sheet)[2:7]:
            print(line.replace("\n", " | "))
    print("\nSimulated weeks:")
    for event, reply in run_world(engine, "victim", NOW, held=Decimal(40000), restorable=Decimal(38000)):
        print(f"Day {event.day}: {reply.answer.headline}")
    print(f"Case state: {session.case.state}")
    run.step("A fake refund offer arrives", "victim", text="Your Rs 38,000 refund is ready. Pay processing fee of Rs 999 to our recovery agent.")
    run.step("Case update in Hindi", "hindi", text="मेरे पैसे चले गए, Rs 500 debited")
    run.step("Hindi helpline number", "hindi", text="Acknowledgement No 30000000000029")
    run.step("The next person forwards a whole chat", "next", chat_export=CHAT)
    run.step("Spark reads the whole chat", "next", button="Arjun")
    run.step("Report it", "next", button="report")
    print(f"\nTotal this run: ${model.ledger.spent - run.spent_before:.4f}. All live runs: ${model.ledger.spent:.4f} of ${args.budget:.2f} cap.")
    engine.network.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
