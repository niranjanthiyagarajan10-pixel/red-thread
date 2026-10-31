"""One very small paid call, to prove the key works before anything bigger runs.

Meta offers an alert but no hard spending cap, so the cap here is enforced by our own ledger.
Run with: python -m redthread.smoke
"""
import sys
from pathlib import Path

from redthread.model import HOME, SPARK, BudgetExceeded, Ledger, MetaModel, ModelError, _http_post, load_api_key

SMOKE_CAP = 0.05
MAX_OUTPUT = 300
_PROMPT = "Reply with the single word OK."


def main(environ=None, home=HOME, transport=_http_post):
    home = Path(home)
    key = load_api_key({} if environ is None else environ, home)
    if not key:
        if (home / ".env.txt").exists():
            print(f"Found {home / '.env.txt'}. Windows added .txt to the name. Rename it to .env and run this again.")
        else:
            print(f"No key found. Add this line to {home / '.env'}: MODEL_API_KEY=your_key")
        return 1
    if not key.startswith("LLM_"):
        print("Warning: Meta keys start with LLM_. This one does not, so it may be rejected.")

    model = MetaModel(key, Ledger(SMOKE_CAP, home / "results" / "smoke-ledger.json"), transport=transport, timeout=60)
    try:
        answer = model.chat([{"role": "user", "content": _PROMPT}], effort="minimal", max_output=MAX_OUTPUT)
    except BudgetExceeded:
        print(f"Stopped before calling. This test has used its ${SMOKE_CAP:.2f} cap.")
        return 1
    except ModelError as error:
        print(f"The call failed and cost nothing we can see. {error}")
        print("401 or 403 means a problem with the key or account. 402 or 429 can mean billing or limits.")
        return 1

    print(f"Key works. Model: {SPARK}. Reply: {answer.text.strip()[:60]}")
    print(f"Tokens: {answer.prompt_tokens} in, {answer.completion_tokens} out ({answer.reasoning_tokens} reasoning).")
    print(f"Cost at list price: ${answer.cost:.6f}. Hard cap for this test: ${SMOKE_CAP:.2f}.")
    return 0


if __name__ == "__main__":
    import os

    sys.exit(main(os.environ))
