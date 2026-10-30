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

## Run it

Python 3.10 or later. No packages needed yet.

```bash
python -m unittest discover -s tests -t .
```

The benchmark needs the [public data](https://github.com/Offensive-AI-Lab/love-lies) in `~/muse-spike/benchmarks/love-lies`. Model arms need `MODEL_API_KEY` in the environment or in `~/muse-spike/.env`.

```bash
python -m redthread.benchmark --arms keyword,published --split all
```

## Credit

Benchmark data: Gressel et al., "Love, Lies, and Language Models: Investigating AI's Role in Romance-Baiting Scams", USENIX Security 2026, used under CC BY 4.0. We do not redistribute it here.
