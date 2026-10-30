import unittest

from redthread.rules import during_call, money_flags, money_has_moved


class DuringCallTests(unittest.TestCase):
    def test_pressure_on_a_live_call_fires(self):
        cases = {
            "A CBI officer is on the phone and says digital arrest": "fake_authority",
            "police on video call, says do not disconnect": "fake_authority",
            "caller is asking for my OTP right now": "code_request",
            "he wants me to install anydesk": "remote_app",
            "they said move money to a safe account": "safe_account",
            "फोन पर पुलिस है, डिजिटल अरेस्ट बोल रहे हैं": "fake_authority",
        }
        for text, kind in cases.items():
            self.assertEqual(during_call(text), kind, text)

    def test_ordinary_messages_do_not_fire(self):
        self.assertIsNone(during_call("my friend is a police officer in Pune"))
        self.assertIsNone(during_call("is this message a scam?"))


class MoneyFlagTests(unittest.TestCase):
    def names(self, text):
        return [flag.name for flag in money_flags(text)]

    def test_broker_claim_without_a_validated_handle(self):
        self.assertIn("broker_without_valid_handle", self.names("I am a SEBI registered broker, pay to rahul.trade@ybl"))

    def test_validated_handle_passes(self):
        self.assertNotIn("broker_without_valid_handle", self.names("SEBI registered broker, pay abc.brk@validhdfc"))

    def test_no_broker_claim_no_flag(self):
        self.assertNotIn("broker_without_valid_handle", self.names("pay my share of dinner to ravi@ybl"))

    def test_pay_to_withdraw(self):
        self.assertIn("pay_to_withdraw", self.names("To withdraw your profit you must first pay 10% tax"))

    def test_guaranteed_returns(self):
        self.assertIn("guaranteed_returns", self.names("guaranteed 30% returns every month"))

    def test_flag_carries_a_source(self):
        flag = [f for f in money_flags("SEBI registered broker, pay rahul@ybl") if f.name.startswith("broker")][0]
        self.assertIn("SEBI", flag.source)


class MoneyMovedTests(unittest.TestCase):
    def test_says_paid(self):
        for text in ["I already paid", "I sent him 40000", "paise bhej diye", "money got debited", "पैसे भेज दिए", "transfer panniten"]:
            self.assertTrue(money_has_moved(text), text)

    def test_has_not_paid(self):
        for text in ["should I pay?", "he asked me to send money", "I did not pay", "not paid yet"]:
            self.assertFalse(money_has_moved(text), text)


if __name__ == "__main__":
    unittest.main()
