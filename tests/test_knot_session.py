import unittest

from redthread import knot
from redthread.knot_session import MAX_AUDIO_CHUNK, PHRASE_SECONDS, Knots, NotAllowed
from tests.test_knot import ANSWER, INTERESTS, PERSONAL, WRITTEN, Scripted
from tests.test_proof_call import FakeSpeech, Inline


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Base(unittest.TestCase):
    def setUp(self):
        self.model = Scripted(knot_questions=WRITTEN, knot_judge=PERSONAL, scores=lambda messages: {"scores": [{"id": item, "label": "contradicts"} for item in range(1, 10)]})
        self.speeches = []
        self.clock = Clock()

    def knots(self, model="default"):
        def speech(on_turn, on_partial, on_lost, keywords):
            self.speeches.append(FakeSpeech("me", on_turn, on_partial, on_lost, keywords))
            return self.speeches[-1]

        return Knots(self.model if model == "default" else model, speech_factory=speech, pool=Inline(), clock=self.clock)

    def seen(self, session, kind=None):
        rows, _ = session.events(0)
        return [row for row in rows if kind is None or row["type"] == kind]

    def say(self, text):
        self.speeches[-1].on_turn(text)

    def through(self, session, phrase=True, answers=(ANSWER, ANSWER, ANSWER)):
        session.begin()
        if phrase:
            self.say(f"okay {session.knot.phrase}")
        else:
            session.next()
        for answer in answers:
            self.say(answer)
            session.next()
        return session


class StartTests(Base):
    def test_questions_are_ready_before_anything_is_heard(self):
        session = self.knots().create(INTERESTS)
        self.assertEqual([item["interest"] for item in session.state()["questions"]], INTERESTS)
        self.assertEqual(self.speeches, [])

    def test_the_phrase_is_hidden_until_the_person_starts(self):
        session = self.knots().create(INTERESTS)
        self.assertNotIn("phrase", session.state())
        session.begin()
        self.assertEqual(session.state()["phrase"], session.knot.phrase)
        self.assertEqual(self.seen(session, "step")[0]["phrase"], session.knot.phrase)

    def test_the_ear_is_told_the_phrase_words_and_interests(self):
        session = self.knots().create(INTERESTS)
        session.begin()
        keywords = self.speeches[0].keywords
        self.assertTrue(set(session.knot.phrase.split()) <= set(keywords))
        self.assertIn("Football", keywords)

    def test_no_interests_is_refused(self):
        with self.assertRaises(NotAllowed):
            self.knots().create(["", "  "])


class AudioTests(Base):
    def test_audio_is_refused_before_start(self):
        session = self.knots().create(INTERESTS)
        self.assertFalse(session.audio(b"\0\0"))

    def test_odd_or_huge_chunks_are_refused(self):
        session = self.knots().create(INTERESTS)
        session.begin()
        self.assertFalse(session.audio(b"\0"))
        self.assertFalse(session.audio(b"\0" * (MAX_AUDIO_CHUNK + 2)))
        self.assertTrue(session.audio(b"\0\0"))


class PhraseTests(Base):
    def test_the_phrase_said_in_time_moves_on_by_itself(self):
        session = self.knots().create(INTERESTS)
        session.begin()
        self.say(f"um {session.knot.phrase}")
        self.assertTrue(session.knot.phrase_heard)
        self.assertEqual(session.state()["step"], 1)

    def test_the_phrase_said_too_late_does_not_count(self):
        session = self.knots().create(INTERESTS)
        session.begin()
        self.clock.now += PHRASE_SECONDS + 1
        self.say(session.knot.phrase)
        self.assertFalse(session.knot.phrase_heard)

    def test_a_typed_phrase_counts_for_people_who_cannot_speak(self):
        session = self.knots().create(INTERESTS)
        session.begin()
        session.say(session.knot.phrase)
        self.assertTrue(session.knot.phrase_heard)


class AnswerTests(Base):
    def test_each_answer_is_judged_when_the_person_moves_on(self):
        session = self.knots().create(INTERESTS)
        session.begin()
        self.say(session.knot.phrase)
        self.say("I support Kerala Blasters,")
        self.say("I watched the 2014 final at my uncle's house in Kochi with my cousin Anu.")
        session.next()
        judged = self.seen(session, "judged")
        self.assertEqual(judged[0]["question"], 1)
        self.assertEqual(judged[0]["label"], "personal")
        self.assertIn("Kerala Blasters", session.knot.results[1].said)
        self.assertEqual(session.state()["step"], 2)

    def test_a_secret_said_aloud_is_not_kept_or_shown(self):
        session = self.knots().create(INTERESTS)
        session.begin()
        self.say(session.knot.phrase)
        self.say("my otp is 482913 and I love football at Kochi with friends")
        captions = " ".join(row["text"] for row in self.seen(session, "caption"))
        self.assertNotIn("482913", captions)

    def test_the_outcome_comes_after_the_last_answer(self):
        session = self.through(self.knots().create(INTERESTS))
        outcome = self.seen(session, "outcome")[0]
        self.assertTrue(outcome["tied"])
        self.assertEqual(outcome["card"], session.knot.card_token)
        self.assertGreater(session.knot.tied_at, 0)
        self.assertTrue(self.speeches[0].closed)

    def test_skipping_the_phrase_means_not_tied(self):
        session = self.through(self.knots().create(INTERESTS), phrase=False)
        outcome = self.seen(session, "outcome")[0]
        self.assertFalse(outcome["tied"])
        self.assertIn("phrase", outcome["reason"])

    def test_without_a_model_it_still_finishes(self):
        session = self.through(self.knots(model=None).create(INTERESTS))
        outcome = self.seen(session, "outcome")[0]
        self.assertTrue(outcome["tied"])
        self.assertEqual(outcome["checked_by"], "code")


class CardTests(Base):
    def test_the_card_is_found_by_its_own_token_only(self):
        knots = self.knots()
        session = self.through(knots.create(INTERESTS))
        self.assertTrue(knots.card(session.knot.card_token)["tied"])
        self.assertIsNone(knots.card(session.knot.token))
        self.assertIsNone(knots.card("nope"))


class ReaskTests(Base):
    def test_a_later_reask_uses_one_of_their_own_details(self):
        knots = self.knots()
        session = self.through(knots.create(INTERESTS))
        later = knots.reask(session.knot.token)
        question = later.state()["question"]
        self.assertIn(question, [item.text for item in knot.reask_questions(session.knot)])

    def test_the_same_story_fits_and_is_counted_on_the_card(self):
        knots = self.knots()
        session = self.through(knots.create(INTERESTS))
        later = knots.reask(session.knot.token)
        later.begin()
        self.say("It was at my uncle's house in Kochi, with my cousin Anu.")
        later.next()
        self.assertEqual(self.seen(later, "rechecked")[0]["label"], "consistent")
        self.assertEqual(knots.card(session.knot.card_token)["rechecks"][0]["label"], "consistent")

    def test_a_different_story_is_marked(self):
        knots = self.knots()
        session = self.through(knots.create(INTERESTS))
        later = knots.reask(session.knot.token)
        later.begin()
        self.say("At a sports bar in Bengaluru with office friends.")
        later.next()
        self.assertEqual(self.seen(later, "rechecked")[0]["label"], "contradicts")

    def test_no_reask_before_the_knot_is_tied(self):
        knots = self.knots()
        session = knots.create(INTERESTS)
        with self.assertRaises(NotAllowed):
            knots.reask(session.knot.token)


if __name__ == "__main__":
    unittest.main()
