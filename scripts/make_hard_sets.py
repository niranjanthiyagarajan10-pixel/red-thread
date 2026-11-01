"""Builds three harder test sets outside the repo. All chats are invented.

normal_dating: real couples who flirt fast, split bills and talk money. These should not be flagged.
quiet_scams: scams that never use the obvious words.
early_romance: public benchmark practice chats cut before the first money pitch, to measure how early a warning comes.
Run with: python -m scripts.make_hard_sets
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from redthread.benchmark import load_set, split_of
from redthread.model import HOME, BudgetExceeded, Ledger, MetaModel, ModelError, load_api_key

OWN = HOME / "benchmarks" / "own"
PUBLIC = HOME / "benchmarks" / "love-lies"
BUDGET = 12.0
OBVIOUS = re.compile(r"crypto|bitcoin|invest|trading|forex|platform|\bprofit|\breturns\b", re.I)
PITCH = re.compile(r"crypto|bitcoin|\binvest|trading|forex|\bplatform\b|\bportfolio\b", re.I)
SCHEMA = {
    "type": "object",
    "properties": {
        "conversation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"Role": {"type": "string", "enum": ["assistant", "user"]}, "Statement": {"type": "string"}},
                "required": ["Role", "Statement"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["conversation"],
    "additionalProperties": False,
}
_NORMAL = (
    "Write an invented text chat between two adults who really are dating, for testing a scam detector for false alarms. "
    "Nothing in it is a scam. The person called assistant is honest. 40 to 60 turns, realistic and casual. They do meet or plan real meetings. Situation: {}"
)
_SCAM = (
    "Write an invented text chat for training a scam detector that protects victims. The person called assistant is running a scam on the person called user, "
    "slowly over 40 to 60 turns. Do not include real names of companies or real account numbers. "
    "Never use these words: crypto, bitcoin, invest, trading, forex, platform, profit, returns. Situation: {}"
)
NORMAL_SITUATIONS = [
    "they fell for each other very fast and say I love you within a week, then meet for coffee.",
    "they split bills and one pays the other back by UPI after a dinner.",
    "one of them really works with crypto at a software firm and talks about it, and never asks for anything.",
    "they moved from a dating app to WhatsApp after one great date.",
    "long distance couple in two cities, who video call most nights and plan a trip.",
    "one lends the other Rs 2,000 for a cab after a real date, and is paid back next day.",
    "one is a bank employee who talks about their job and keeping customers safe from OTP scams.",
    "one has a sick parent and is stressed, and turns down an offer of money.",
    "one is abroad for work and sends gifts by post, and they talk about their savings goals.",
    "they discuss buying a flat together one day and compare home loan rates.",
]
SCAM_SITUATIONS = [
    "a match says their parcel with a gift for the user is stuck and a clearance fee must be paid.",
    "a match working abroad has a sudden hospital bill and needs help for a few days.",
    "a match says their visa fee is short and they can finally visit if the user helps.",
    "a match proposes a joint savings scheme for their future home run by their cousin.",
    "a match says their business account is frozen and asks the user to receive and forward money.",
    "a match asks for gift card codes for a family emergency.",
    "a match builds love for weeks, then pressures the user to send private photos and then threatens to share them.",
    "a match says a family elder needs surgery, and asks for a loan with a promise to repay double.",
    "a match says they are in the army overseas and need leave fees paid to come home.",
    "a match helps the user get a part-time online task job that pays small sums, then asks for a deposit to unlock earnings.",
]


def generate(model, folder, index, prompt):
    path = folder / f"{index}.json"
    if path.exists():
        return True
    try:
        reply = model.chat([{"role": "user", "content": prompt}], schema=SCHEMA, effort="low", max_output=9000)
        data = json.loads(reply.text)
    except (ModelError, ValueError, KeyError):
        return False
    text = " ".join(row["Statement"] for row in data["conversation"] if row["Role"] == "assistant")
    if folder.name == "quiet_scams" and OBVIOUS.search(text):
        return False
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def cut_romance(limit=20):
    """Keeps the turns before the other person first mentions the pitch. Chats with too little before it are skipped."""
    folder = OWN / "early_romance"
    folder.mkdir(parents=True, exist_ok=True)
    made = 0
    for chat in load_set(PUBLIC, "romance_baiting"):
        if split_of(chat.id) != "dev" or made >= limit:
            continue
        first = next((turn.index for turn in chat.turns if turn.speaker == "assistant" and PITCH.search(turn.text)), None)
        if first is None or first < 20:
            continue
        rows = [{"Role": turn.speaker, "Statement": turn.text} for turn in chat.turns[:first]]
        (folder / f"{made}.json").write_text(json.dumps({"conversation": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        made += 1
    return made


def main():
    model = MetaModel(load_api_key(), Ledger(BUDGET, HOME / "results" / "ledger.json"), cache_dir=HOME / "cache")
    jobs = []
    for name, template, situations in (("normal_dating", _NORMAL, NORMAL_SITUATIONS), ("quiet_scams", _SCAM, SCAM_SITUATIONS)):
        folder = OWN / name
        folder.mkdir(parents=True, exist_ok=True)
        for index, situation in enumerate(situations * 2):
            jobs.append((folder, index, template.format(situation) + f" Version {index // len(situations) + 1}: use different people and details."))
    try:
        with ThreadPoolExecutor(6) as pool:
            results = list(pool.map(lambda job: generate(model, *job), jobs))
    except BudgetExceeded as error:
        print(error)
        return 1
    print(f"Generated {sum(results)} of {len(jobs)}. Early romance chats cut: {cut_romance()}. Ledger: ${model.ledger.spent:.2f}.")
    for name in ("normal_dating", "quiet_scams", "early_romance"):
        print(name, len(list((OWN / name).glob('*.json'))))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
