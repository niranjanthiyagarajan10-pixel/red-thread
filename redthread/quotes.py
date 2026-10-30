"""Checks that a quote a model gives really appears in the conversation.

A claim without a verified quote is never shown to anyone.
"""
import re
import unicodedata

MIN_QUOTE_CHARS = 8


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().casefold()


def find_quote(conversation, quote, speaker=None, near=None):
    """The index of the turn holding this exact quote, or None. near picks the closest of several matches."""
    wanted = normalize(quote)
    if len(wanted) < MIN_QUOTE_CHARS:
        return None
    matches = [
        turn.index for turn in conversation.turns
        if (speaker is None or turn.speaker == speaker) and wanted in normalize(turn.text)
    ]
    if not matches:
        return None
    if near is None:
        return matches[0]
    return min(matches, key=lambda index: abs(index - near))
