"""A whole Proof Call against the real models, with invented voices and a hard spending cap.

Measures each step, because a live call lives or dies on how long a card takes to appear.
Run with: python -m redthread.proof_live --budget 1
Needs clips from scripts/make_proof_clips.ps1. Without them, words go straight in and live speech is not measured.
"""
import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from redthread import proof, proof_demo
from redthread.model import HOME, Ledger, MetaModel, load_api_key
from redthread.proof_call import Calls

CLIPS = HOME / "clips" / "proof"
CARD_WAIT_SECONDS = 30


def read_memory(model):
    print("\n--- Reading the chat before the call")
    started = time.perf_counter()
    memory = proof.build_memory(model, proof_demo.chat(), proof_demo.OTHER)
    read = time.perf_counter() - started
    print(f"Read in {read:.1f}s. Questions {len(memory.questions)}, claims {len(memory.claims)}, fallback {memory.fallback}.")
    started = time.perf_counter()
    before = len(memory.questions)
    memory = proof.drop_guessable(model, memory)
    print(f"Blind guess check in {time.perf_counter() - started:.1f}s. Dropped {before - len(memory.questions)}.")
    for item in memory.questions:
        print(f"  Q{item.id}: {item.text}\n      chat: {item.quote}")
    print(f"  keywords: {memory.keywords}")
    return memory


def wait_for(call, seen, kinds, question=None):
    deadline = time.monotonic() + CARD_WAIT_SECONDS
    while time.monotonic() < deadline:
        rows, seen = call.events("asker", seen, wait=0.5)
        for row in rows:
            if row["type"] in kinds and (question is None or row.get("question") == question):
                return row, seen
    return None, seen


def clip_seconds(pcm):
    return len(pcm) / 32000 if pcm else 0.0


def run_call(model, kind):
    print(f"\n--- A call answered by the {kind}")
    calls = Calls(model, pool=ThreadPoolExecutor(4))
    person = proof_demo.person(kind, clips_dir=CLIPS)
    call = calls.create(proof_demo.memory(), person=person)
    call.consent("asker")
    seen = 0
    timings = []
    for question in call.memory.questions:
        clip = person.clip(question.id)
        started = time.monotonic()
        call.play_guest(question.id)
        spoken_end = started + clip_seconds(clip)
        words, seen = wait_for(call, seen, {"partial", "caption"})
        words_at = time.monotonic()
        answer, seen = wait_for(call, seen, {"answer"}, question.id)
        card_at = time.monotonic()
        if answer is None:
            print(f"  Q{question.id}: no card")
            continue
        while call.guest_busy:
            rows, seen = call.events("asker", seen, wait=0.2)
            for row in rows:
                if row["type"] == "answer" and row["question"] == question.id:
                    answer = row
        rows, seen = call.events("asker", seen, wait=2)
        for row in rows:
            if row["type"] == "answer" and row["question"] == question.id:
                answer = row
        timings.append({"words": words_at - started, "card": card_at - spoken_end, "label": answer["label"]})
        print(f"  Q{question.id} [{answer['label']}] first words {words_at - started:.1f}s after they began, first card {card_at - spoken_end:+.1f}s from when they stopped")
        print(f"      said: {answer['said']}")
    call.play_line(proof_demo.MONEY_LINE)
    alarm, seen = wait_for(call, seen, {"alarm"})
    print(f"  Money ask: {alarm['headline'] if alarm else 'NO ALARM'}")
    if kind == "stand_in":
        call.play_line(proof_demo.JOB_LINE)
        differs, seen = wait_for(call, seen, {"differs"})
        print(f"  Job line: {('They wrote: ' + differs['quote']) if differs else 'no card'}")
    call.finish()
    receipt, seen = wait_for(call, seen, {"receipt"})
    print("  Receipt:\n    " + receipt["text"].replace("\n", "\n    "))
    print(f"  Revealed: {receipt['person']}")
    calls.close()
    return timings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, default=1.0)
    args = parser.parse_args()
    ledger = Ledger(args.budget, HOME / "results" / "proof-live-ledger.json")
    model = MetaModel(load_api_key(), ledger, timeout=60)
    print(f"Clips: {'found' if (CLIPS / 'writer_1.wav').exists() else 'missing, so words go straight in'}")
    read_memory(model)
    timings = run_call(model, "writer") + run_call(model, "stand_in")
    if timings:
        print(f"\nFirst words after they began: median {statistics.median(row['words'] for row in timings):.1f}s, worst {max(row['words'] for row in timings):.1f}s")
        print(f"Card after speech ended: median {statistics.median(row['card'] for row in timings):.1f}s, worst {max(row['card'] for row in timings):.1f}s")
        print("Labels:", json.dumps({label: sum(row['label'] == label for row in timings) for label in proof.LABELS + ('unscored',)}))
    print(f"Ledger: ${ledger.spent:.4f} of ${args.budget:.2f}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
