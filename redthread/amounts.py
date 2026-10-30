"""Reads amounts and digits the way people say them in Hindi, Tamil and English.

The transcription model gives no confidence score, so amounts are worked out here by a fixed table
and then checked against the payment screenshot. A model never does this sum.
"""
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal


def _table(entries):
    return {unicodedata.normalize("NFC", word).casefold(): value for words, value in entries for word in words.split()}


_NUMBERS = _table([
    ("one ek oru onnu ஒரு ஒன்று एक", 1),
    ("two do rendu irandu இரண்டு दो", 2),
    ("three teen moonu மூன்று तीन", 3),
    ("four char chaar naalu நான்கு चार", 4),
    ("five paanch panch anju ainthu ஐந்து पांच पाँच", 5),
    ("six chhe chah aaru ஆறு छह छः", 6),
    ("seven saat ezhu ஏழு सात", 7),
    ("eight aath ettu எட்டு आठ", 8),
    ("nine nau onbadhu ஒன்பது नौ", 9),
    ("ten das pathu பத்து दस", 10),
    ("twenty bees iruvadhu இருபது बीस", 20),
    ("twenty-five pachees पच्चीस", 25),
    ("thirty tees muppadhu முப்பது तीस", 30),
    ("forty chalis chaalis narpadhu நாற்பது चालीस", 40),
    ("fifty pachas pachaas aimbadhu ஐம்பது पचास", 50),
    ("sixty saath arubadhu அறுபது साठ", 60),
    ("seventy sattar ezhupadhu எழுபது सत्तर", 70),
    ("eighty assi enbadhu எண்பது अस्सी", 80),
    ("ninety nabbe thonnooru தொண்ணூறு नब्बे", 90),
])
_FIXED_FRACTIONS = _table([("dedh डेढ़ डेढ", Decimal("1.5")), ("dhai adhai ढाई", Decimal("2.5"))])
_MODIFIERS = _table([
    ("sawa सवा", Decimal("0.25")),
    ("paune पौने", Decimal("-0.25")),
    ("saade sade saadhe साढ़े साढे", Decimal("0.5")),
])
_HUNDREDS = _table([("hundred sau nooru सौ நூறு", 100)])
_LARGE = _table([
    ("thousand hazaar hazar aayiram ayiram हज़ार हजार ஆயிரம்", 1000),
    ("lakh lakhs lac lacs latcham lacham laksham latchathu latchathi lakshathu lakshathi लाख லட்சம் லட்சத்து", 100000),
    ("crore crores cr करोड़ करोड கோடி", 10000000),
])
_COMPOUNDS = _table([
    ("pathayiram பத்தாயிரம்", 10000),
    ("iruvadhayiram இருபதாயிரம்", 20000),
    ("muppadhayiram முப்பதாயிரம்", 30000),
    ("narpadhayiram நாற்பதாயிரம்", 40000),
    ("aimbadhayiram ஐம்பதாயிரம்", 50000),
])
_RUPEE_WORDS = _table([("rs inr rupee rupees rupay rupaye rupaiye rupai रुपये रुपए रूपये ரூபாய் ரூபா", 1)])
_DIGIT_WORDS = _table([
    ("zero oh shunya sunya sifar sunnam pujyam शून्य பூஜ்யம்", "0"),
    ("one ek onnu ஒன்று एक", "1"),
    ("two do rendu இரண்டு दो", "2"),
    ("three teen moonu மூன்று तीन", "3"),
    ("four char chaar naalu நான்கு चार", "4"),
    ("five paanch panch anju ஐந்து पांच पाँच", "5"),
    ("six chhe chah aaru ஆறு छह", "6"),
    ("seven saat ezhu ஏழு सात", "7"),
    ("eight aath ettu எட்டு आठ", "8"),
    ("nine nau onbadhu ஒன்பது नौ", "9"),
])
_NUMERIC = re.compile(r"^\d[\d,]*(?:\.\d+)?$")
_NUMERIC_UNIT = re.compile(r"^(\d[\d,]*(?:\.\d+)?)(k|l|lac|lacs|lakh|lakhs|cr|crore|crores)$")
_UNIT_VALUES = {"k": 1000, "l": 100000, "cr": 10000000}
_LAKH_GROUPED = re.compile(r"^\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?$")
_SHORT_UNITS = {"l", "cr"}
_STRIP = ".,!?;:()[]{}\"'…"
DIGIT_WORD_PATTERN = "|".join(sorted(map(re.escape, _DIGIT_WORDS), key=len, reverse=True))


@dataclass
class Amount:
    value: Decimal
    raw: str


def _tokens(text):
    text = unicodedata.normalize("NFC", text).replace("₹", " rs ").replace("/-", " ")
    text = re.sub(r"(?i)\b(rs|inr)\.?\s*(?=\d)", "rs ", text)
    return [token.strip(_STRIP).casefold() for token in text.split() if token.strip(_STRIP)]


def _is_part(token):
    return (
        token in _NUMBERS or token in _FIXED_FRACTIONS or token in _MODIFIERS or token in _HUNDREDS
        or token in _LARGE or token in _SHORT_UNITS or token in _COMPOUNDS or _NUMERIC.match(token) or _NUMERIC_UNIT.match(token)
    )


def _unit_value(unit):
    return _UNIT_VALUES.get(unit) or _LARGE[unit]


def _read_run(tokens, start):
    """Reads one spoken number from start. Returns its value, the next position and whether it had a unit."""
    total = Decimal(0)
    current = None
    modifier = Decimal(0)
    has_unit = False
    last_unit = None
    position = start
    while position < len(tokens):
        token = tokens[position]
        following = tokens[position + 1] if position + 1 < len(tokens) else ""
        if token == "and" and following == "a" and position + 2 < len(tokens) and tokens[position + 2] == "half":
            if current is None and last_unit:
                total += Decimal(last_unit) / 2
            else:
                current = (current or Decimal(0)) + Decimal("0.5")
            position += 3
            continue
        if token == "a" and following in _LARGE:
            current = Decimal(1)
        elif token == "and" and _is_part(following) and position > start:
            pass
        elif token in _COMPOUNDS:
            total += _COMPOUNDS[token]
            has_unit = True
        elif token in _SHORT_UNITS and current is None:
            break
        elif token in _LARGE or token in _SHORT_UNITS:
            last_unit = _unit_value(token)
            total += (current if current is not None else Decimal(1) + modifier) * last_unit
            current, modifier = None, Decimal(0)
            has_unit = True
        elif token in _HUNDREDS:
            current = (current if current is not None else Decimal(1) + modifier) * _HUNDREDS[token]
            modifier = Decimal(0)
            has_unit = True
        elif token in _MODIFIERS:
            modifier = _MODIFIERS[token]
        elif token in _FIXED_FRACTIONS:
            current = _FIXED_FRACTIONS[token]
        elif token in _NUMBERS:
            current = (current or Decimal(0)) + _NUMBERS[token] + modifier
            modifier = Decimal(0)
        elif _NUMERIC_UNIT.match(token):
            number, unit = _NUMERIC_UNIT.match(token).groups()
            total += Decimal(number.replace(",", "")) * _unit_value(unit)
            has_unit = True
        elif _NUMERIC.match(token):
            current = (current or Decimal(0)) + Decimal(token.replace(",", ""))
            has_unit = has_unit or bool(_LAKH_GROUPED.match(token))
        else:
            break
        position += 1
    return total + (current or Decimal(0)), position, has_unit


def parse_amounts(text):
    """A number counts as money only with a unit such as lakh, a currency mark or lakh commas. Bare numbers may be references."""
    tokens = _tokens(text)
    amounts = []
    position = 0
    while position < len(tokens):
        token = tokens[position]
        after_currency = position > 0 and tokens[position - 1] in _RUPEE_WORDS
        starts_run = _is_part(token) or (token == "a" and position + 1 < len(tokens) and tokens[position + 1] in _LARGE)
        if not starts_run:
            position += 1
            continue
        value, end, has_unit = _read_run(tokens, position)
        before_rupee = end < len(tokens) and tokens[end] in _RUPEE_WORDS
        litres = end < len(tokens) and tokens[end] == "of" and tokens[end - 1].endswith("l")
        if value > 0 and not litres and (has_unit or after_currency or before_rupee):
            amounts.append(Amount(value, " ".join(tokens[position:end])))
        position = max(end, position + 1)
    return amounts


def spoken_digits(text, minimum=6):
    """Finds references read out digit by digit. Plain written numbers are handled elsewhere."""
    runs = []
    digits = ""
    has_word = False
    for token in _tokens(text) + [""]:
        if token in _DIGIT_WORDS:
            digits += _DIGIT_WORDS[token]
            has_word = True
        elif token.isdigit() and token.isascii():
            digits += token
        else:
            if has_word and len(digits) >= minimum:
                runs.append(digits)
            digits, has_word = "", False
    return runs


def format_rupees(value):
    """Writes money the way people in India read it: Rs 1,80,000."""
    value = Decimal(value)
    whole = int(value)
    text = str(whole)
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        groups = []
        while head:
            groups.insert(0, head[-2:])
            head = head[:-2]
        text = ",".join(groups + [tail])
    cents = value - whole
    return f"Rs {text}" + (f"{cents:.2f}"[1:] if cents else "")
