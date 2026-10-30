"""The whole loop in plain text, with invented people and no model or key.

Run with: python -m redthread.demo
"""
import sys
from datetime import datetime
from decimal import Decimal

from redthread.engine import Engine, Incoming
from redthread.network import Network
from redthread.reply import render
from redthread.world import run_world

NOW = datetime(2026, 11, 1, 10, 0)
CHAT = """01/03/2026, 21:00 - Riya: Hi, you seem so genuine.
01/03/2026, 21:05 - Arjun: Thanks, you too.
02/03/2026, 21:00 - Riya: You are my soulmate, I feel it already.
04/03/2026, 21:00 - Riya: Let us move to Telegram, the app is glitchy.
09/03/2026, 21:00 - Riya: My uncle's crypto desk doubled my savings.
20/03/2026, 21:00 - Riya: You should invest on this platform today, pay scam.one@ybl
"""


def show(title, out):
    print(f"\n--- {title}")
    print(render(out.answer))
    if out.sheet:
        ready = "ready" if out.sheet.ready else "needs fixing"
        print(f"[sheet with {len(out.sheet.fields)} fields, {ready}]")
    if out.link_offer:
        print("[offer: talk to the other person, only if you both agree]")


def main():
    engine = Engine(network=Network(":memory:", salt=b"demo"), model=None)
    show("Someone forwards one message", engine.handle("meera", Incoming(text="Is this real? To withdraw profit pay 10% tax first.", now=NOW)))
    show("Someone is on a call right now", engine.handle("sam", Incoming(text="CBI officer on video call says digital arrest", now=NOW)))
    show("Someone sends a code by mistake", engine.handle("sam", Incoming(text="my OTP is 482913", now=NOW)))
    show("A victim sends a voice note and a screenshot", engine.handle("victim", Incoming(
        voice_text="I already paid thirty eight thousand", screenshot_text="Paid Rs 38,000 to scam.one@ybl UTR 533827652689", now=NOW,
    )))
    for event, out in run_world(engine, "victim", NOW, held=Decimal(40000), restorable=Decimal(38000)):
        show(f"Day {event.day}, simulated message: {event.text}", out)
    engine.handle("next", Incoming(chat_export=CHAT, now=NOW))
    show("The next person forwards a whole chat before paying", engine.handle("next", Incoming(button="Arjun", now=NOW)))
    engine.network.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
