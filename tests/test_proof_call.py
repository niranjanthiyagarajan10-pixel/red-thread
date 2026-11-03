import json
import time
import unittest
from unittest import mock

from redthread import proof, proof_demo
from redthread.conversation import Conversation, Turn
from redthread.model import ModelError, Reply
from redthread.proof_call import CONSENT_TEXT, MAX_AUDIO_CHUNK, MAX_LINE_CHECKS, Calls, NotAllowed
from tests.test_proof import CHAT, GOOD, Scripted


class Inline:
    """Runs background work at once, so tests read in order."""

    def submit(self, function, *args):
        function(*args)


class FakeSpeech:
    def __init__(self, role, on_turn, on_partial, on_lost, keywords):
        self.role, self.on_turn, self.on_partial, self.on_lost, self.keywords = role, on_turn, on_partial, on_lost, keywords
        self.audio = bytearray()
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def send(self, pcm):
        self.audio += pcm
        return True

    def close(self):
        self.closed = True


class Base(unittest.TestCase):
    def setUp(self):
        self.memory = proof.build_memory(Scripted(memory=GOOD), CHAT, "Arjun")
        self.scores = {"scores": [{"id": item, "label": "consistent"} for item in range(1, 6)]}
        self.model = Scripted(
            scores=lambda messages: self.scores,
            line=lambda messages: {"claim": self.claim},
        )
        self.claim = 0
        self.speeches = {}
        self.ended = []

    def calls(self, model="default"):
        def speech(role, on_turn, on_partial, on_lost, keywords):
            self.speeches[role] = FakeSpeech(role, on_turn, on_partial, on_lost, keywords)
            return self.speeches[role]

        return Calls(self.model if model == "default" else model, speech_factory=speech, pool=Inline(),
                     on_end=lambda call, receipt: self.ended.append((call, receipt)))

    def live(self, **options):
        call = self.calls(**options).create(self.memory)
        call.consent("asker")
        call.consent("guest")
        return call

    def seen(self, call, role="asker", kind=None):
        rows, _ = call.events(role, 0)
        return [row for row in rows if kind is None or row["type"] == kind]


class ConsentTests(Base):
    def test_nothing_is_heard_until_both_people_agree(self):
        call = self.calls().create(self.memory)
        self.assertEqual(call.phase, "waiting")
        self.assertFalse(call.audio("asker", b"\0\0"))
        call.consent("asker")
        self.assertEqual(call.phase, "waiting")
        self.assertEqual(self.speeches, {})
        self.assertFalse(call.audio("asker", b"\0\0"))
        call.consent("guest")
        self.assertEqual(call.phase, "live")
        self.assertTrue(self.speeches["asker"].started and self.speeches["guest"].started)
        self.assertTrue(call.audio("asker", b"\0\0"))

    def test_the_guest_is_told_plainly_before_agreeing(self):
        call = self.calls().create(self.memory)
        text = call.state("guest")["consent_text"]
        self.assertEqual(text, CONSENT_TEXT)
        for needed in ("AI", "compares", "Only the person who invited you", "not recorded"):
            self.assertIn(needed, text)

    def test_consent_is_only_counted_once(self):
        call = self.calls().create(self.memory)
        call.consent("guest")
        call.consent("guest")
        self.assertEqual(call.phase, "waiting")


class AccessTests(Base):
    def test_each_token_opens_one_role(self):
        calls = self.calls()
        call = calls.create(self.memory)
        self.assertEqual(calls.find(call.tokens["asker"]), (call, "asker"))
        self.assertEqual(calls.find(call.tokens["guest"]), (call, "guest"))
        self.assertEqual(calls.find("wrong"), (None, None))
        self.assertNotEqual(call.tokens["asker"], call.tokens["guest"])
        self.assertGreaterEqual(len(call.tokens["guest"]), 20)

    def test_the_guest_never_sees_questions_quotes_or_captions(self):
        call = self.live()
        call.ask("asker", 1)
        self.speeches["guest"].on_turn("My sister is Meena")
        call.finish()
        state = json.dumps(call.state("guest"))
        for private in ("sister", "Kavya", "questions", "Meena"):
            self.assertNotIn(private, state)
        events = json.dumps(self.seen(call, "guest"))
        for private in ("sister", "Kavya", "Meena", "caption", "answer", "receipt"):
            self.assertNotIn(private, events)

    def test_the_asker_sees_the_questions(self):
        call = self.live()
        self.assertEqual(len(call.state("asker")["questions"]), 5)

    def test_a_guest_cannot_ask_questions(self):
        call = self.live()
        with self.assertRaises(NotAllowed):
            call.ask("guest", 1)

    def test_signals_only_reach_the_other_person(self):
        call = self.calls().create(self.memory)
        call.signal("asker", {"sdp": "offer"})
        self.assertEqual(self.seen(call, "guest", "signal")[0]["data"], {"sdp": "offer"})
        self.assertEqual(self.seen(call, "asker", "signal"), [])


class AudioTests(Base):
    def test_audio_goes_only_to_that_persons_own_stream(self):
        call = self.live()
        call.audio("guest", b"\x01\x00")
        self.assertEqual(bytes(self.speeches["guest"].audio), b"\x01\x00")
        self.assertEqual(bytes(self.speeches["asker"].audio), b"")

    def test_odd_or_huge_chunks_are_refused(self):
        call = self.live()
        self.assertFalse(call.audio("guest", b"\x01"))
        self.assertFalse(call.audio("guest", b"\0\0" * (MAX_AUDIO_CHUNK // 2 + 1)))

    def test_names_from_the_chat_reach_the_ear(self):
        self.live()
        self.assertIn("Kavya", self.speeches["guest"].keywords)


class LiveTests(Base):
    def test_captions_reach_the_asker_with_who_said_it(self):
        call = self.live()
        self.speeches["guest"].on_partial("hello th")
        self.speeches["guest"].on_turn("hello there")
        self.speeches["asker"].on_turn("hi")
        self.assertEqual(self.seen(call, kind="partial")[0], {"type": "partial", "who": "them", "text": "hello th", "seq": self.seen(call, kind="partial")[0]["seq"]})
        captions = self.seen(call, kind="caption")
        self.assertEqual([(row["who"], row["text"]) for row in captions], [("them", "hello there"), ("you", "hi")])

    def test_a_secret_said_aloud_is_not_kept(self):
        call = self.live()
        self.speeches["asker"].on_turn("my card is 4111 1111 1111 1111")
        self.assertNotIn("4111", json.dumps(self.seen(call)))

    def test_a_money_ask_fires_at_once_with_no_model(self):
        call = self.live(model=None)
        self.speeches["guest"].on_turn("can you send me 5000 rupees today")
        alarm = self.seen(call, kind="alarm")[0]
        self.assertEqual(alarm["kind"], "money_ask")
        self.assertTrue(alarm["headline"])

    def test_the_same_alarm_does_not_repeat(self):
        call = self.live()
        self.speeches["guest"].on_turn("send me 5000 rupees")
        self.speeches["guest"].on_turn("please send me 5000 rupees")
        self.assertEqual(len(self.seen(call, kind="alarm")), 1)

    def test_what_the_asker_says_never_sets_off_an_alarm(self):
        call = self.live()
        self.speeches["asker"].on_turn("should I send you 5000 rupees")
        self.assertEqual(self.seen(call, kind="alarm"), [])


class QuestionTests(Base):
    def test_old_words_appear_beside_new_words(self):
        call = self.live()
        call.ask("asker", 1)
        self.speeches["guest"].on_turn("her name is Kavya")
        card = self.seen(call, kind="answer")[0]
        self.assertEqual(card["quote"], "My sister Kavya still lives in Pune")
        self.assertEqual(card["said"], "her name is Kavya")
        self.assertEqual(card["label"], "consistent")

    def test_saying_the_question_aloud_is_the_same_as_tapping(self):
        call = self.live()
        self.speeches["asker"].on_turn("so what is your sister called again")
        self.assertEqual(self.seen(call, kind="asked")[0]["question"], 1)
        self.speeches["guest"].on_turn("Kavya")
        self.assertEqual(self.seen(call, kind="answer")[0]["question"], 1)

    def test_a_no_answer_gets_one_more_turn_to_finish_the_thought(self):
        call = self.live()
        self.scores = {"scores": [{"id": 1, "label": "no_answer"}]}
        call.ask("asker", 1)
        self.speeches["guest"].on_turn("oh let me think")
        self.scores = {"scores": [{"id": 1, "label": "consistent"}]}
        self.speeches["guest"].on_turn("it is Kavya")
        cards = self.seen(call, kind="answer")
        self.assertEqual(cards[-1]["label"], "consistent")
        self.assertIn("it is Kavya", cards[-1]["said"])
        self.assertIn("let me think", cards[-1]["said"])

    def test_one_answer_split_into_two_quick_turns_stays_together(self):
        call = self.live()
        now = [100.0]
        call.clock = lambda: now[0]
        call.ask("asker", 1)
        self.speeches["guest"].on_turn("Oh, Kavya.")
        now[0] += 1
        self.speeches["guest"].on_turn("She still lives in Pune.")
        cards = self.seen(call, kind="answer")
        self.assertEqual(cards[-1]["said"], "Oh, Kavya. She still lives in Pune.")

    def test_a_settled_answer_stops_listening_once_they_move_on(self):
        call = self.live()
        now = [100.0]
        call.clock = lambda: now[0]
        call.ask("asker", 1)
        self.speeches["guest"].on_turn("Kavya")
        now[0] += 20
        self.speeches["guest"].on_turn("anyway the weather is nice")
        self.assertEqual(len(self.seen(call, kind="answer")), 1)

    def test_a_money_ask_is_never_counted_as_an_answer(self):
        call = self.live()
        call.ask("asker", 1)
        self.speeches["guest"].on_turn("send me 5000 rupees today")
        self.assertEqual(self.seen(call, kind="answer"), [])
        self.assertEqual(len(self.seen(call, kind="alarm")), 1)

    def test_model_failure_still_shows_both_sides_with_no_label(self):
        call = self.live(model=None)
        call.ask("asker", 1)
        self.speeches["guest"].on_turn("I think it was her")
        card = self.seen(call, kind="answer")[0]
        self.assertEqual(card["label"], "unscored")
        self.assertEqual(card["quote"], "My sister Kavya still lives in Pune")

    def test_an_unknown_question_is_refused(self):
        call = self.live()
        with self.assertRaises(NotAllowed):
            call.ask("asker", 99)


class LineCheckTests(Base):
    def test_a_line_touching_a_claim_can_raise_a_card(self):
        call = self.live()
        self.claim = 1
        self.speeches["guest"].on_turn("actually my job is at a bank in Dubai")
        card = self.seen(call, kind="differs")[0]
        self.assertEqual(card["quote"], "my job at the hospital")
        self.assertEqual(card["said"], "actually my job is at a bank in Dubai")

    def test_small_talk_costs_no_model_call(self):
        call = self.live()
        self.speeches["guest"].on_turn("haha yeah")
        self.assertEqual(self.model.seen, [])

    def test_checks_per_call_are_capped(self):
        call = self.live()
        for _ in range(MAX_LINE_CHECKS + 5):
            self.speeches["guest"].on_turn("my job is really tiring these days")
        self.assertEqual(len(self.model.seen), MAX_LINE_CHECKS)

    def test_lines_during_a_question_are_scored_and_not_line_checked(self):
        call = self.live()
        call.ask("asker", 1)
        self.speeches["guest"].on_turn("my job is tiring but she is great")
        self.assertEqual([seen[1]["title"] for seen in self.model.seen], ["scores"])


class FinishTests(Base):
    def test_receipt_counts_each_question_and_closes_the_streams(self):
        call = self.live()
        call.ask("asker", 1)
        self.speeches["guest"].on_turn("Kavya")
        call.ask("asker", 2)
        call.finish()
        self.assertTrue(self.speeches["asker"].closed and self.speeches["guest"].closed)
        self.assertEqual(call.phase, "ended")
        receipt = self.ended[0][1]
        self.assertEqual([item.label for item in receipt.results], ["consistent", "no_answer"])
        self.assertEqual(receipt.not_asked, 3)
        self.assertTrue(receipt.joined)

    def test_the_asker_gets_the_receipt_as_three_lines(self):
        call = self.live()
        call.ask("asker", 1)
        self.speeches["guest"].on_turn("Kavya")
        call.finish()
        receipt = self.seen(call, kind="receipt")[0]
        self.assertIn("Of 1 question,", receipt["answer"]["headline"])

    def test_a_guest_who_never_joined_is_reported_as_that(self):
        call = self.calls().create(self.memory)
        call.consent("asker")
        call.finish()
        self.assertFalse(self.ended[0][1].joined)

    def test_finishing_twice_sends_one_receipt(self):
        call = self.live()
        call.finish()
        call.finish()
        self.assertEqual(len(self.ended), 1)

    def test_audio_stops_after_the_call_ends(self):
        call = self.live()
        call.finish()
        self.assertFalse(call.audio("asker", b"\0\0"))

    def test_nothing_private_stays_after_the_call_is_swept(self):
        calls = self.calls()
        call = calls.create(self.memory)
        call.created = time.monotonic() - 100000
        calls.sweep()
        self.assertEqual(calls.find(call.tokens["asker"]), (None, None))
        self.assertEqual(call.memory, None)

    def test_a_crash_in_the_receipt_callback_never_reaches_the_call(self):
        calls = Calls(None, speech_factory=lambda *args: FakeSpeech(*args), pool=Inline(), on_end=lambda *args: 1 / 0)
        call = calls.create(self.memory)
        call.finish()
        self.assertEqual(call.phase, "ended")


class PhraseTests(Base):
    def test_the_phrase_is_shown_only_once_the_call_is_live(self):
        call = self.calls().create(self.memory)
        self.assertNotIn("phrase", call.state("guest"))
        call.consent("asker")
        call.consent("guest")
        phrase = call.state("guest")["phrase"]
        self.assertEqual(len(phrase.split()), 3)
        self.assertEqual(call.state("asker")["phrase"], phrase)
        self.assertFalse(call.state("asker")["phrase_heard"])

    def test_the_guest_saying_it_is_noticed_and_reported(self):
        call = self.live()
        self.speeches["guest"].on_turn(f"Okay, um, {call.phrase}.")
        self.assertEqual(len(self.seen(call, kind="phrase_ok")), 1)
        self.assertTrue(call.state("asker")["phrase_heard"])
        self.speeches["guest"].on_turn(call.phrase)
        self.assertEqual(len(self.seen(call, kind="phrase_ok")), 1)
        call.finish()
        self.assertTrue(self.ended[0][1].phrase_heard)

    def test_the_asker_saying_it_does_not_count(self):
        call = self.live()
        self.speeches["asker"].on_turn(f"Can you say {call.phrase}?")
        self.assertEqual(self.seen(call, kind="phrase_ok"), [])

    def test_a_call_where_it_was_never_said_is_reported(self):
        call = self.live()
        call.finish()
        self.assertFalse(self.ended[0][1].phrase_heard)
        self.assertIn("never said the phrase", " ".join(self.seen(call, kind="receipt")[0]["answer"]["proof"]))

    def test_a_money_ask_stays_on_the_receipt(self):
        call = self.live()
        self.speeches["guest"].on_turn("send me 5000 rupees today")
        call.finish()
        self.assertEqual(self.ended[0][1].alarms, ["money_ask"])
        self.assertIn("asked for money", " ".join(self.seen(call, kind="receipt")[0]["answer"]["proof"]))

    def test_a_simulated_person_says_it_through_the_same_path(self):
        calls = Calls(None, speech_factory=None, pool=Inline(), on_end=None)
        call = calls.create(proof_demo.memory(), person=proof_demo.person("stand_in"))
        call.consent("asker")
        captions = self.seen(call, kind="caption")
        self.assertIn(call.phrase, captions[0]["text"].lower())
        self.assertTrue(call.phrase_heard)

    def test_guarding_a_live_call_drops_what_was_guessed_and_says_so(self):
        call = self.live()
        call.ask("asker", 2)
        labels = {"scores": [{"id": 1, "label": "consistent"}, {"id": 2, "label": "consistent"}] + [{"id": item, "label": "no_answer"} for item in range(3, 6)]}
        bluffer = Scripted(bluff={"answers": [{"id": item, "spoken": "a guess"} for item in range(1, 6)]})
        call.guard(Scripted(scores=labels), bluffer)
        ids = [item["id"] for item in call.state("asker")["questions"]]
        self.assertNotIn(1, ids)
        self.assertIn(2, ids, "a question already asked stays")
        event = self.seen(call, kind="guarded")[0]
        self.assertEqual(len(event["guard"]["guessed"]), 2)
        self.assertEqual([item["id"] for item in event["questions"]], ids)

    def test_a_failed_guard_changes_nothing(self):
        call = self.live()
        call.guard(Scripted(scores=ModelError("down")), Scripted(bluff=ModelError("down")))
        self.assertEqual(len(call.state("asker")["questions"]), 5)
        self.assertEqual(self.seen(call, kind="guarded"), [])
        self.assertEqual(len(self.seen(call, kind="unguarded")), 1)
        self.assertFalse(call.state("asker")["guarding"])

    def test_only_the_asker_of_a_real_call_gets_the_guest_link(self):
        call = self.calls().create(self.memory)
        self.assertEqual(call.state("asker")["guest"], call.tokens["guest"])
        self.assertNotIn(call.tokens["guest"], json.dumps(call.state("guest")))
        self.assertNotIn(call.tokens["asker"], json.dumps(call.state("guest")))

    def test_the_guard_reaches_the_asker_only(self):
        self.memory.guard = {"by": "muse-wonder-1.0", "tried": 5, "guessed": [{"question": "What is your sister called?", "spoken": "Kavya"}]}
        call = self.live()
        self.assertEqual(call.state("asker")["guard"]["tried"], 5)
        self.assertNotIn("guard", call.state("guest"))


class WaitTests(Base):
    def test_events_can_be_read_from_where_you_left_off(self):
        call = self.live()
        _, seen = call.events("asker", 0)
        self.speeches["guest"].on_turn("hello there")
        rows, _ = call.events("asker", seen)
        self.assertEqual([row["type"] for row in rows], ["caption"])

    def test_waiting_returns_quickly_when_nothing_happens(self):
        call = self.live()
        _, seen = call.events("asker", 0)
        started = time.monotonic()
        rows, _ = call.events("asker", seen, wait=0.05)
        self.assertEqual(rows, [])
        self.assertLess(time.monotonic() - started, 1)


class DemoTests(Base):
    def test_every_demo_quote_is_really_in_the_demo_chat(self):
        chat = proof_demo.chat()
        memory = proof_demo.memory()
        from redthread.quotes import find_quote

        for item in memory.questions:
            self.assertEqual(find_quote(chat, item.quote, speaker=memory.other), item.turn)
            self.assertFalse(proof.reveals_answer(item.text, item.answer), item.text)
        for claim in memory.claims:
            self.assertEqual(find_quote(chat, claim.quote, speaker=memory.other), claim.turn)

    def test_both_demo_people_can_answer_every_question(self):
        memory = proof_demo.memory()
        for kind in proof_demo.PEOPLE:
            person = proof_demo.person(kind)
            self.assertEqual(set(person.answers), {item.id for item in memory.questions})

    def test_the_demo_labels_match_who_answered(self):
        self.assertEqual(set(proof_demo.person("writer").labels.values()), {"consistent"})
        self.assertIn("contradicts", proof_demo.person("stand_in").labels.values())

    def test_a_simulated_call_works_with_no_model_and_no_audio(self):
        calls = Calls(None, speech_factory=None, pool=Inline(), on_end=None)
        call = calls.create(proof_demo.memory(), person=proof_demo.person("stand_in"))
        call.consent("asker")
        self.assertEqual(call.phase, "live")
        call.ask("asker", 1)
        call.play_guest(1)
        card = self.seen(call, kind="answer")[0]
        self.assertEqual(card["said"], proof_demo.person("stand_in").answers[1])
        self.assertEqual(card["label"], proof_demo.person("stand_in").labels[1])

    def test_a_simulated_call_is_labelled_and_reveals_who_only_at_the_end(self):
        calls = Calls(None, speech_factory=None, pool=Inline(), on_end=None)
        call = calls.create(proof_demo.memory(), person=proof_demo.person("writer"))
        call.consent("asker")
        state = call.state("asker")
        self.assertTrue(state["simulated"])
        self.assertNotIn("writer", json.dumps(state))
        call.finish()
        self.assertEqual(self.seen(call, kind="receipt")[0]["person"], "writer")

    def test_a_simulated_call_opens_no_speech_stream_until_there_is_audio(self):
        opened = []

        def speech(role, on_turn, on_partial, on_lost, keywords):
            opened.append(role)
            return FakeSpeech(role, on_turn, on_partial, on_lost, keywords)

        calls = Calls(None, speech_factory=speech, pool=Inline(), on_end=None)
        call = calls.create(proof_demo.memory(), person=proof_demo.person("writer"))
        call.consent("asker")
        self.assertEqual(opened, [])
        self.assertTrue(call.audio("asker", b"\0\0"))
        self.assertEqual(opened, ["asker"])

    def simulated_with_clip(self, heard):
        def speech(role, on_turn, on_partial, on_lost, keywords):
            fake = FakeSpeech(role, on_turn, on_partial, on_lost, keywords)
            if heard:
                sent = fake.send

                def send(pcm):
                    if any(pcm) and not fake.said:
                        fake.said = True
                        on_turn(heard)
                    return sent(pcm)

                fake.said = False
                fake.send = send
            self.speeches[role] = fake
            return fake

        person = proof_demo.person("writer")
        person.clip = lambda question_id: b"\x01\x00" * 800
        calls = Calls(None, speech_factory=speech, pool=Inline(), on_end=None)
        call = calls.create(proof_demo.memory(), person=person)
        call.consent("asker")
        self.addCleanup(call.finish)
        return call

    def wait_for_card(self, call):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (self.seen(call, kind="answer") and not call.guest_busy):
            time.sleep(0.02)
        return self.seen(call, kind="answer")

    def test_a_clip_goes_through_the_live_path_when_speech_hears_it(self):
        call = self.simulated_with_clip(heard="my paati taught me")
        call.play_guest(1)
        self.assertEqual(self.wait_for_card(call)[-1]["said"], "my paati taught me")
        self.assertTrue(any(self.speeches["guest"].audio))

    def test_if_speech_hears_nothing_the_words_still_arrive(self):
        with mock.patch("redthread.proof_call._LAST_WORDS_WAIT", 0.1):
            call = self.simulated_with_clip(heard=None)
            call.play_guest(1)
            self.assertEqual(self.wait_for_card(call)[-1]["said"], proof_demo.person("writer").answers[1])

    def test_lines_play_one_at_a_time_and_land_on_their_own_questions(self):
        with mock.patch("redthread.proof_call._LAST_WORDS_WAIT", 0.1):
            call = self.simulated_with_clip(heard=None)
            call.play_guest(1)
            call.play_guest(2)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and len(self.seen(call, kind="answer")) < 2:
                time.sleep(0.02)
            cards = self.seen(call, kind="answer")
            self.assertEqual([card["question"] for card in cards], [1, 2])
            self.assertEqual(cards[1]["said"], proof_demo.person("writer").answers[2])

    def test_only_a_simulated_call_can_play_a_guest(self):
        call = self.live()
        with self.assertRaises(NotAllowed):
            call.play_guest(1)

    def test_demo_lines_set_off_the_real_rules(self):
        self.assertEqual(proof.live_alarm(proof_demo.MONEY_LINE).kind, "money_ask")


if __name__ == "__main__":
    unittest.main()
