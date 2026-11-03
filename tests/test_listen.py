import json
import unittest

from redthread.listen import MAX_AUDIO_CHUNK, Listens
from tests.test_proof_call import FakeSpeech


class Base(unittest.TestCase):
    def setUp(self):
        self.speeches = []

        def speech(on_turn, on_partial, on_lost, keywords):
            fake = FakeSpeech("room", on_turn, on_partial, on_lost, keywords)
            self.speeches.append(fake)
            return fake

        self.listens = Listens(speech_factory=speech)
        self.addCleanup(self.listens.close)

    def started(self):
        session = self.listens.create()
        session.start()
        return session, self.speeches[0]

    def seen(self, session, kind=None):
        rows, _ = session.events(0)
        return [row for row in rows if kind is None or row["type"] == kind]


class ListenTests(Base):
    def test_nothing_is_heard_before_start(self):
        session = self.listens.create()
        self.assertFalse(session.audio(b"\0\0"))
        self.assertEqual(self.speeches, [])

    def test_audio_reaches_the_ear_once_started(self):
        session, ear = self.started()
        self.assertTrue(session.audio(b"\x01\x00"))
        self.assertEqual(bytes(ear.audio), b"\x01\x00")
        self.assertFalse(session.audio(b"\x01"))
        self.assertFalse(session.audio(b"\0\0" * (MAX_AUDIO_CHUNK // 2 + 1)))

    def test_the_script_sets_off_an_alarm_at_once_with_no_model(self):
        session, ear = self.started()
        ear.on_turn("This is the CBI. You are under digital arrest. Do not tell anyone.")
        alarm = self.seen(session, "alarm")[0]
        self.assertEqual(alarm["kind"], "fake_authority")
        self.assertIn("1930", alarm["next_step"])

    def test_each_kind_of_alarm_fires_once(self):
        session, ear = self.started()
        ear.on_turn("You are under digital arrest")
        ear.on_turn("Stay on video, you are under digital arrest")
        ear.on_turn("Move your savings to the safe account now")
        self.assertEqual([row["kind"] for row in self.seen(session, "alarm")], ["fake_authority", "safe_account"])

    def test_a_money_ask_fires(self):
        session, ear = self.started()
        ear.on_turn("Transfer 2 lakh rupees to this account today")
        self.assertEqual(self.seen(session, "alarm")[0]["kind"], "money_ask")

    def test_everyday_talk_stays_quiet(self):
        session, ear = self.started()
        ear.on_turn("Hi beta, are you coming home for dinner tonight?")
        self.assertEqual(self.seen(session, "alarm"), [])

    def test_captions_never_keep_a_secret(self):
        session, ear = self.started()
        ear.on_partial("my otp is 4")
        ear.on_turn("the card is 4111 1111 1111 1111")
        self.assertNotIn("4111", json.dumps(self.seen(session)))

    def test_stop_closes_the_ear_and_hands_back_what_was_said(self):
        session, ear = self.started()
        ear.on_turn("This is the police calling about a parcel with drugs")
        said = session.stop()
        self.assertTrue(ear.closed)
        self.assertIn("parcel", said)
        self.assertFalse(session.audio(b"\0\0"))
        self.assertEqual(self.seen(session, "ended")[0]["type"], "ended")

    def test_names_from_the_script_help_the_ear(self):
        self.started()
        for word in ("CBI", "digital arrest", "safe account"):
            self.assertIn(word, self.speeches[0].keywords)

    def test_lost_captions_are_reported(self):
        session, ear = self.started()
        ear.on_lost()
        self.assertEqual(len(self.seen(session, "captions_off")), 1)

    def test_no_model_means_no_ear_but_no_crash(self):
        listens = Listens(speech_factory=None)
        session = listens.create()
        session.start()
        self.assertFalse(session.audio(b"\0\0"))
        self.assertEqual(self.seen(session, "captions_off")[0]["type"], "captions_off")

    def test_sessions_are_found_by_token_and_swept(self):
        session = self.listens.create()
        self.assertIs(self.listens.find(session.token), session)
        session.created -= 10_000
        self.listens.sweep()
        self.assertIsNone(self.listens.find(session.token))


if __name__ == "__main__":
    unittest.main()
