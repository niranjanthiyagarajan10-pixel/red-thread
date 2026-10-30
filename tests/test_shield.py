import unittest

from redthread.shield import check_relay, find_sensitive, scrub


class SensitiveTests(unittest.TestCase):
    def kinds(self, text):
        return {item.kind for item in find_sensitive(text)}

    def test_otp_needs_context(self):
        self.assertIn("otp", self.kinds("my OTP is 482913"))
        self.assertIn("otp", self.kinds("482913 is your one time password"))
        self.assertIn("otp", self.kinds("OTP 4829"))
        self.assertEqual(self.kinds("I paid 482913 rupees"), set())

    def test_card_uses_the_checksum(self):
        self.assertIn("card", self.kinds("card 4111 1111 1111 1111"))
        self.assertEqual(self.kinds("UTR 4111111111111112"), set())

    def test_pan(self):
        self.assertIn("pan", self.kinds("my pan is ABCPE1234F"))
        self.assertIn("pan", self.kinds("abcpe1234f"))

    def test_aadhaar_needs_context_because_utrs_are_also_twelve_digits(self):
        self.assertIn("aadhaar", self.kinds("aadhaar 2345 6789 0123"))
        self.assertEqual(self.kinds("UTR 234567890123"), set())

    def test_password_and_cvv(self):
        self.assertIn("password", self.kinds("my password is tiger123"))
        self.assertIn("cvv", self.kinds("cvv 123"))

    def test_scrub_never_keeps_the_secret_and_keeps_the_rest(self):
        result = scrub("OTP 482913 and I paid Rs 500, card 4111 1111 1111 1111")
        self.assertNotIn("482913", result.text)
        self.assertNotIn("4111", result.text)
        self.assertIn("Rs 500", result.text)
        self.assertEqual(set(result.kinds), {"otp", "card"})


class RelayTests(unittest.TestCase):
    def blocked(self, text):
        return check_relay(text).blocked

    def test_normal_talk_passes(self):
        self.assertFalse(self.blocked("I lost money too. I felt so ashamed. Did you call 1930?"))

    def test_contact_details_are_blocked(self):
        for text in ["call me on 9876543210", "pay me at helper@ybl", "see https://help.example", "mail a@b.com", "wa.me/919876543210"]:
            self.assertTrue(self.blocked(text), text)

    def test_recovery_offers_are_blocked(self):
        for text in [
            "I know a recovery agent who got my money back",
            "pay a small processing fee and you will get your money back",
            "main aapka paisa wapas dila dunga",
            "my lawyer can recover your money, just send advance",
        ]:
            self.assertTrue(self.blocked(text), text)

    def test_reason_is_given(self):
        self.assertEqual(check_relay("call 9876543210").reason, "contact_details")
        self.assertEqual(check_relay("recovery agent here").reason, "recovery_offer")

    def test_sensitive_details_are_blocked(self):
        self.assertTrue(self.blocked("my pan is ABCPE1234F"))


if __name__ == "__main__":
    unittest.main()
