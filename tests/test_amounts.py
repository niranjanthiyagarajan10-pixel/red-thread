import unittest
from decimal import Decimal

from redthread.amounts import parse_amounts, spoken_digits


def values(text):
    return [amount.value for amount in parse_amounts(text)]


class DigitAmountTests(unittest.TestCase):
    def test_currency_marks(self):
        self.assertEqual(values("I paid Rs 1,80,000 today"), [Decimal(180000)])
        self.assertEqual(values("₹40,000 gone"), [Decimal(40000)])
        self.assertEqual(values("Rs.500 and INR 2,500.50"), [Decimal(500), Decimal("2500.50")])
        self.assertEqual(values("paid 7000 rupees"), [Decimal(7000)])

    def test_number_with_a_unit(self):
        self.assertEqual(values("lost 1.8 lakh"), [Decimal(180000)])
        self.assertEqual(values("40k went"), [Decimal(40000)])
        self.assertEqual(values("2 crore"), [Decimal(20000000)])

    def test_bare_references_are_not_amounts(self):
        self.assertEqual(values("UTR 533827652689 and phone 9876543210"), [])
        self.assertEqual(values("on 12/03/2026"), [])


class EnglishWordTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual(values("two lakh rupees"), [Decimal(200000)])
        self.assertEqual(values("one lakh eighty thousand"), [Decimal(180000)])
        self.assertEqual(values("forty five thousand"), [Decimal(45000)])
        self.assertEqual(values("a lakh"), [Decimal(100000)])
        self.assertEqual(values("one and a half lakh"), [Decimal(150000)])

    def test_common_words_do_not_become_amounts(self):
        self.assertEqual(values("do not pay one person"), [])


class HindiTests(unittest.TestCase):
    def test_fractions_roman(self):
        self.assertEqual(values("dedh lakh"), [Decimal(150000)])
        self.assertEqual(values("dhai lakh gaye"), [Decimal(250000)])
        self.assertEqual(values("sawa lakh"), [Decimal(125000)])
        self.assertEqual(values("paune do lakh"), [Decimal(175000)])
        self.assertEqual(values("saade teen lakh"), [Decimal(350000)])
        self.assertEqual(values("dhai hazaar rupaye"), [Decimal(2500)])

    def test_compound_roman(self):
        self.assertEqual(values("ek lakh assi hazaar"), [Decimal(180000)])
        self.assertEqual(values("pachas hazaar bheje"), [Decimal(50000)])
        self.assertEqual(values("do sau rupaye"), [Decimal(200)])

    def test_devanagari(self):
        self.assertEqual(values("डेढ़ लाख"), [Decimal(150000)])
        self.assertEqual(values("ढाई लाख रुपये"), [Decimal(250000)])
        self.assertEqual(values("पचास हज़ार"), [Decimal(50000)])
        self.assertEqual(values("पौने दो लाख"), [Decimal(175000)])


class TamilTests(unittest.TestCase):
    def test_roman(self):
        self.assertEqual(values("rendu latcham"), [Decimal(200000)])
        self.assertEqual(values("oru latchathu aimbadhu aayiram"), [Decimal(150000)])
        self.assertEqual(values("aimbadhayiram poyiduchu"), [Decimal(50000)])

    def test_script(self):
        self.assertEqual(values("இரண்டு லட்சம்"), [Decimal(200000)])
        self.assertEqual(values("ஒரு லட்சத்து ஐம்பதாயிரம்"), [Decimal(150000)])

    def test_mixed_with_english(self):
        self.assertEqual(values("naan rendu latcham transfer panninen"), [Decimal(200000)])


class SpokenDigitTests(unittest.TestCase):
    def test_digit_words_in_a_row(self):
        self.assertEqual(spoken_digits("UTR five three three eight two seven"), ["533827"])
        self.assertEqual(spoken_digits("ek do teen char paanch chhe"), ["123456"])

    def test_digits_and_words_mix(self):
        self.assertEqual(spoken_digits("number 533 eight two seven"), ["533827"])

    def test_short_runs_are_ignored(self):
        self.assertEqual(spoken_digits("one two three"), [])


if __name__ == "__main__":
    unittest.main()
