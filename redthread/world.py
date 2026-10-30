"""A labelled simulation of the weeks a real case takes, for the demo and the judge page.

Message wording is invented, because real police and bank messages cannot be sent on camera.
Everything here must be shown as simulated.
"""
from dataclasses import dataclass
from datetime import timedelta

from redthread.amounts import format_rupees
from redthread.engine import Incoming


@dataclass
class WorldEvent:
    day: int
    text: str
    simulated: bool = True


def demo_events(held, restorable, helpline_ack="30000000000012", portal_ack="20000000000012", request_id="MR20261101000003"):
    return [
        WorldEvent(0, f"Acknowledgement No {helpline_ack}"),
        WorldEvent(0, f"Complaint {portal_ack} registered on the portal"),
        WorldEvent(3, f"{format_rupees(held)} put on hold against your complaint"),
        WorldEvent(14, f"{format_rupees(restorable)} is eligible for restoration. Apply on the Money Restoration portal."),
        WorldEvent(15, f"Your request ID is {request_id}"),
        WorldEvent(40, f"{format_rupees(restorable)} credited to your account"),
    ]


def run_world(engine, user, start, held, restorable):
    """Plays each simulated message through the real engine. Returns (event, reply) pairs."""
    played = []
    for event in demo_events(held, restorable):
        played.append((event, engine.handle(user, Incoming(text=event.text, now=start + timedelta(days=event.day)))))
    return played
