# Red Thread

The one contact you forward anything to, before you trust someone and after it goes wrong.

Now you know someone.

Work in progress for the Meta Global AI Developer Hackathon, Indie track, theme "Build for Your World". Everything in this repository is new work written during the event.

## What exists today

| Part | What it does |
|---|---|
| `redthread/portal_rules.py` | Checks text, files, IDs and amounts against the rules of India's cybercrime and refund portals |
| `redthread/conversation.py` | Reads Android and iPhone WhatsApp chat exports into numbered turns |
| `redthread/quotes.py` | Refuses any quote that is not word for word in the chat |
| `redthread/stages.py` | Reads a whole chat for the signs of a scam script. Muse Spark finds and quotes, code checks, fixed rules decide. |
| `redthread/model.py` | Calls Muse Spark with a spending cap, a cache and retries |
| `redthread/benchmark.py` | Compares detection approaches on a public benchmark |
| `redthread/amounts.py`, `facts.py` | Reads amounts the way people say them and checks a voice note against a payment screenshot |
| `redthread/shield.py` | Removes secrets before anything is kept, and blocks contact details and recovery offers between victims |
| `redthread/rules.py` | Fixed answers for a live call and for money signs, with no model and no waiting |
| `redthread/templates.py`, `reply.py` | The three-line answer in English, Hindi and Tamil, and the checks every answer must pass |
| `redthread/case.py`, `sheets.py` | Tracks a recovery case from forwarded messages and lays out each form field by field |
| `redthread/network.py` | Joins cases that share an account, number, link, image or script. Stores salted hashes only. |
| `redthread/engine.py` | The one contact. Everything a person sends comes here. |
| `redthread/world.py`, `demo.py` | A labelled simulation of a case from loss to credit, in plain text |
| `redthread/evals.py` | Plants errors on purpose and measures what the fixed rules catch |

## Run it

Python 3.10 or later. No packages needed yet.

```bash
python -m unittest discover -s tests -t .
```

The whole loop in plain text, with invented people and no key:

```bash
python -m redthread.demo
```

Planted errors, with no key:

```bash
python -m redthread.evals
```

## Results so far

Fixed rules only, on planted errors. No model is involved yet.

| Batch | Planted errors caught | Normal messages wrongly flagged |
|---|---|---|
| First batch, before any fixes | 51 of 80 | 6 of 39 |
| Second batch, unseen text, first run | 46 of 64 (72%) | 10 of 35 |

The second batch is the honest figure for text the rules have not seen. Both batches pass today, because the rules were fixed against them, so a pass now only guards against regressions.

The benchmark needs the [public data](https://github.com/Offensive-AI-Lab/love-lies) in `~/muse-spike/benchmarks/love-lies`. Model arms need `MODEL_API_KEY` in the environment or in `~/muse-spike/.env`.

```bash
python -m redthread.benchmark --arms keyword,published --split all
```

## Credit

Benchmark data: Gressel et al., "Love, Lies, and Language Models: Investigating AI's Role in Romance-Baiting Scams", USENIX Security 2026, used under CC BY 4.0. We do not redistribute it here.
