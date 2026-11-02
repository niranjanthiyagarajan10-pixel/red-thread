import json
import unittest

from redthread.conversation import Conversation, Turn
from redthread.model import BudgetExceeded, ModelError, Reply
from redthread import proof
from redthread.reply import check_answer

CHAT = Conversation("chat", [
    Turn(0, "Arjun", "I moved to Bengaluru in March for my job at the hospital."),
    Turn(1, "Me", "Which one?"),
    Turn(2, "Arjun", "Manipal, in cardiology. My sister Kavya still lives in Pune."),
    Turn(3, "Me", "Do you cook?"),
    Turn(4, "Arjun", "Only my mother's lemon rice, I made it on Tuesday and burnt the peanuts."),
    Turn(5, "Me", "What about the cat?"),
    Turn(6, "Arjun", "Biscuit knocked over my tea again, he sleeps on my laptop."),
    Turn(7, "Arjun", "We should walk at Cubbon Park on Saturday, I like the mornings there."),
])


def written(*questions, claims=(), name="Arjun", job="doctor", city="Bengaluru", names=()):
    return {
        "name": name, "job": job, "city": city, "names": list(names),
        "questions": [{"question": q, "quote": quote, "answer": answer} for q, quote, answer in questions],
        "claims": [{"topic": topic, "quote": quote} for topic, quote in claims],
    }


GOOD = written(
    ("What is your sister called?", "My sister Kavya still lives in Pune", "Kavya"),
    ("What went wrong when you cooked on Tuesday?", "burnt the peanuts", "burnt peanuts"),
    ("What does the cat keep knocking over?", "Biscuit knocked over my tea", "tea"),
    ("Where did you want to go on Saturday?", "walk at Cubbon Park on Saturday", "Cubbon Park"),
    ("What happened in March?", "I moved to Bengaluru in March", "moved to Bengaluru"),
    claims=[("job", "my job at the hospital"), ("family", "My sister Kavya still lives in Pune")],
    names=["Kavya", "Biscuit", "Cubbon Park", "Goa"],
)


class Scripted:
    """Answers by schema title, and records what it was sent."""

    def __init__(self, **answers):
        self.answers = answers
        self.seen = []

    def chat(self, messages, schema=None, **options):
        self.seen.append((messages, schema, options))
        answer = self.answers[schema["title"]]
        if callable(answer):
            answer = answer(messages)
        if isinstance(answer, Exception):
            raise answer
        return Reply(text=answer if isinstance(answer, str) else json.dumps(answer))


class RevealsTests(unittest.TestCase):
    def test_a_question_holding_its_own_answer_is_caught(self):
        self.assertTrue(proof.reveals_answer("Is your sister called Kavya?", "Kavya"))

    def test_sharing_common_words_is_fine(self):
        self.assertFalse(proof.reveals_answer("How old is your daughter?", "she is nine"))

    def test_an_empty_answer_reveals_nothing(self):
        self.assertFalse(proof.reveals_answer("What did you cook?", ""))


class BuildMemoryTests(unittest.TestCase):
    def test_keeps_questions_whose_quote_is_real(self):
        memory = proof.build_memory(Scripted(memory=GOOD), CHAT, "Arjun")
        self.assertEqual(len(memory.questions), 5)
        self.assertFalse(memory.fallback)
        self.assertEqual(memory.questions[0].turn, 2)
        self.assertEqual([item.id for item in memory.questions], [1, 2, 3, 4, 5])

    def test_a_made_up_quote_is_dropped(self):
        answer = written(*[
            ("What is your sister called?", "My sister Meena lives in Delhi", "Meena"),
            ("Where does your cat like to sleep?", "he sleeps on my laptop", "laptop"),
            ("Where did you want to go on Saturday?", "walk at Cubbon Park on Saturday", "Cubbon Park"),
            ("What does the cat keep knocking over?", "Biscuit knocked over my tea", "tea"),
        ])
        memory = proof.build_memory(Scripted(memory=answer), CHAT, "Arjun")
        self.assertNotIn("What is your sister called?", [item.text for item in memory.questions])

    def test_a_quote_from_the_person_asking_is_dropped(self):
        answer = written(
            ("Who asked about the cat?", "What about the cat?", "you"),
            ("Where does your cat like to sleep?", "he sleeps on my laptop", "laptop"),
            ("Where did you want to go on Saturday?", "walk at Cubbon Park on Saturday", "Cubbon Park"),
            ("What does the cat keep knocking over?", "Biscuit knocked over my tea", "tea"),
        )
        memory = proof.build_memory(Scripted(memory=answer), CHAT, "Arjun")
        self.assertNotIn("Who asked about the cat?", [item.text for item in memory.questions])

    def test_a_question_giving_away_its_answer_is_dropped(self):
        answer = written(
            ("Is your cat called Biscuit?", "Biscuit knocked over my tea", "Biscuit"),
            ("Where does your cat like to sleep?", "he sleeps on my laptop", "laptop"),
            ("Where did you want to go on Saturday?", "walk at Cubbon Park on Saturday", "Cubbon Park"),
            ("What went wrong on Tuesday?", "burnt the peanuts", "burnt peanuts"),
        )
        memory = proof.build_memory(Scripted(memory=answer), CHAT, "Arjun")
        self.assertNotIn("Is your cat called Biscuit?", [item.text for item in memory.questions])

    def test_a_long_dash_from_the_model_never_reaches_a_question(self):
        answer = written(*[
            ("You mentioned the cat \u2014 what does he knock over?", "Biscuit knocked over my tea", "tea"),
            ("Where did you want to go on Saturday?", "walk at Cubbon Park on Saturday", "Cubbon Park"),
            ("What went wrong on Tuesday?", "burnt the peanuts", "burnt peanuts"),
        ])
        memory = proof.build_memory(Scripted(memory=answer), CHAT, "Arjun")
        self.assertEqual(memory.questions[0].text, "You mentioned the cat, what does he knock over?")

    def test_only_real_claims_are_kept(self):
        answer = dict(GOOD, claims=[
            {"topic": "job", "quote": "my job at the hospital"},
            {"topic": "job", "quote": "I am a pilot for an airline"},
        ])
        memory = proof.build_memory(Scripted(memory=answer), CHAT, "Arjun")
        self.assertEqual([claim.quote for claim in memory.claims], ["my job at the hospital"])

    def test_keeps_context_around_the_quote_for_scoring(self):
        memory = proof.build_memory(Scripted(memory=GOOD), CHAT, "Arjun")
        self.assertIn("Which one?", memory.questions[0].context)
        self.assertIn("My sister Kavya", memory.questions[0].context)

    def test_keywords_hold_names_for_the_ear(self):
        memory = proof.build_memory(Scripted(memory=GOOD), CHAT, "Arjun")
        for name in ("Kavya", "Biscuit", "Cubbon Park"):
            self.assertIn(name, memory.keywords)

    def test_a_name_that_is_not_in_the_chat_never_reaches_the_ear(self):
        memory = proof.build_memory(Scripted(memory=GOOD), CHAT, "Arjun")
        self.assertNotIn("Goa", memory.keywords)

    def test_model_failure_still_gives_questions_from_the_chat(self):
        for failure in (ModelError("down"), BudgetExceeded("cap"), "not json"):
            memory = proof.build_memory(Scripted(memory=failure), CHAT, "Arjun")
            self.assertTrue(memory.fallback)
            self.assertGreaterEqual(len(memory.questions), 3)
            for item in memory.questions:
                self.assertEqual(CHAT.turns[item.turn].speaker, "Arjun")

    def test_no_model_at_all_still_works(self):
        memory = proof.build_memory(None, CHAT, "Arjun")
        self.assertTrue(memory.fallback)
        self.assertTrue(memory.questions)

    def test_too_few_good_questions_are_filled_from_the_chat(self):
        answer = written(("Where does your cat like to sleep?", "he sleeps on my laptop", "laptop"))
        memory = proof.build_memory(Scripted(memory=answer), CHAT, "Arjun")
        self.assertGreaterEqual(len(memory.questions), proof.MIN_QUESTIONS)
        self.assertEqual(len({item.turn for item in memory.questions}), len(memory.questions))

    def test_chat_text_is_sent_as_data_not_instructions(self):
        model = Scripted(memory=GOOD)
        proof.build_memory(model, CHAT, "Arjun")
        messages = model.seen[0][0]
        self.assertEqual(messages[0]["role"], "developer")
        self.assertNotIn("Kavya", messages[0]["content"])
        self.assertIn("Kavya", messages[1]["content"])


class GuessTests(unittest.TestCase):
    def memory(self):
        return proof.build_memory(Scripted(memory=GOOD), CHAT, "Arjun")

    def bluff(self, ids):
        return {"answers": [{"id": item, "spoken": f"guess {item}"} for item in ids]}

    def test_questions_spark_guesses_blind_go_last_and_are_dropped_when_there_are_enough(self):
        labels = {"scores": [{"id": 1, "label": "consistent"}, {"id": 2, "label": "contradicts"}, {"id": 3, "label": "no_answer"},
                             {"id": 4, "label": "contradicts"}, {"id": 5, "label": "contradicts"}]}
        memory = proof.drop_guessable(Scripted(bluff=self.bluff([1, 2, 3, 4, 5]), scores=labels), self.memory())
        self.assertNotIn("What is your sister called?", [item.text for item in memory.questions])
        self.assertEqual(len(memory.questions), 4)

    def test_never_falls_below_the_minimum(self):
        labels = {"scores": [{"id": item, "label": "consistent"} for item in range(1, 6)]}
        memory = proof.drop_guessable(Scripted(bluff=self.bluff([1, 2, 3, 4, 5]), scores=labels), self.memory())
        self.assertEqual(len(memory.questions), proof.MIN_QUESTIONS)

    def test_failure_keeps_every_question(self):
        memory = proof.drop_guessable(Scripted(bluff=ModelError("down"), scores=ModelError("down")), self.memory())
        self.assertEqual(len(memory.questions), 5)

    def test_the_bluff_never_sees_the_chat(self):
        model = Scripted(bluff=self.bluff([1, 2, 3, 4, 5]), scores={"scores": []})
        proof.drop_guessable(model, self.memory())
        bluff_prompt = json.dumps(model.seen[0][0])
        self.assertNotIn("burnt the peanuts", bluff_prompt)
        self.assertIn("doctor", bluff_prompt)


class SpareQuestionTests(unittest.TestCase):
    def long_chat(self):
        turns = []
        for index in range(0, 20, 2):
            turns.append(Turn(index, "Me", "Tell me more?"))
            turns.append(Turn(index + 1, "Arjun", f"Detail number {index} is about the blue kettle at Lalbagh."))
        return Conversation("chat", turns)

    def many(self):
        return written(*[(f"Question {index} please?", f"Detail number {index} is", f"thing {index}") for index in range(0, 20, 2)])

    def test_it_keeps_spare_questions_so_the_guess_check_can_drop_some(self):
        memory = proof.build_memory(Scripted(memory=self.many()), self.long_chat(), "Arjun")
        self.assertEqual(len(memory.questions), proof.ASKED_FOR_QUESTIONS)

    def test_after_the_guess_check_only_the_wanted_number_is_asked(self):
        memory = proof.build_memory(Scripted(memory=self.many()), self.long_chat(), "Arjun")
        model = Scripted(bluff={"answers": []}, scores={"scores": []})
        self.assertEqual(len(proof.drop_guessable(model, memory).questions), proof.WANTED_QUESTIONS)

    def test_a_failed_guess_check_still_asks_only_the_wanted_number(self):
        memory = proof.build_memory(Scripted(memory=self.many()), self.long_chat(), "Arjun")
        model = Scripted(bluff=ModelError("down"), scores=ModelError("down"))
        self.assertEqual(len(proof.drop_guessable(model, memory).questions), proof.WANTED_QUESTIONS)


class ScoreTests(unittest.TestCase):
    def question(self):
        return proof.build_memory(Scripted(memory=GOOD), CHAT, "Arjun").questions[0]

    def test_returns_the_label(self):
        model = Scripted(scores={"scores": [{"id": 1, "label": "contradicts"}]})
        self.assertEqual(proof.score_answer(model, self.question(), "her name is Meena"), "contradicts")

    def test_scorer_sees_the_turns_around_the_quote(self):
        model = Scripted(scores={"scores": [{"id": 1, "label": "consistent"}]})
        proof.score_answer(model, self.question(), "I think it was her")
        self.assertIn("Which one?", json.dumps(model.seen[0][0]))

    def test_words_holding_the_fact_settle_it_with_no_model(self):
        model = Scripted(scores=ModelError("should not be called"))
        self.assertEqual(proof.score_answer(model, self.question(), "oh she is called Kavya"), "consistent")
        self.assertEqual(proof.score_answer(None, self.question(), "Kavya"), "consistent")
        self.assertEqual(model.seen, [])

    def test_a_denial_is_not_settled_by_code(self):
        model = Scripted(scores={"scores": [{"id": 1, "label": "contradicts"}]})
        self.assertEqual(proof.score_answer(model, self.question(), "no it is not Kavya"), "contradicts")

    def test_failure_or_no_model_is_unscored_and_never_a_guess(self):
        self.assertEqual(proof.score_answer(None, self.question(), "I think it was her"), "unscored")
        self.assertEqual(proof.score_answer(Scripted(scores=ModelError("x")), self.question(), "I think it was her"), "unscored")
        self.assertEqual(proof.score_answer(Scripted(scores={"scores": [{"id": 1, "label": "maybe"}]}), self.question(), "I think it was her"), "unscored")

    def test_live_calls_are_quick_and_do_not_retry(self):
        model = Scripted(scores={"scores": [{"id": 1, "label": "consistent"}]})
        proof.score_answer(model, self.question(), "I think it was her")
        options = model.seen[0][2]
        self.assertEqual(options["attempts"], 1)
        self.assertLessEqual(options["timeout"], 15)


class LineTests(unittest.TestCase):
    def memory(self):
        return proof.build_memory(Scripted(memory=GOOD), CHAT, "Arjun")

    def test_a_line_that_touches_a_claim_is_worth_checking(self):
        self.assertTrue(proof.worth_checking(self.memory(), "actually my job is at a bank in Dubai"))

    def test_small_talk_is_not_checked(self):
        self.assertFalse(proof.worth_checking(self.memory(), "haha yeah"))
        self.assertFalse(proof.worth_checking(self.memory(), "can you hear me okay now"))

    def test_a_claim_it_points_to_comes_back_with_the_checked_quote(self):
        found = proof.check_line(Scripted(line={"claim": 1}), self.memory(), "I work at a bank in Dubai")
        self.assertEqual(found.quote, "my job at the hospital")
        self.assertEqual(found.said, "I work at a bank in Dubai")

    def test_no_contradiction_or_an_invented_claim_gives_nothing(self):
        for claim in (0, 99, -1):
            self.assertIsNone(proof.check_line(Scripted(line={"claim": claim}), self.memory(), "I work at a bank"))

    def test_failure_gives_nothing(self):
        self.assertIsNone(proof.check_line(Scripted(line=ModelError("x")), self.memory(), "I work at a bank"))
        self.assertIsNone(proof.check_line(None, self.memory(), "I work at a bank"))


class AlarmTests(unittest.TestCase):
    def test_asking_for_a_code_or_money_fires_with_no_model(self):
        self.assertEqual(proof.live_alarm("just tell me the OTP you received").kind, "code_request")
        self.assertEqual(proof.live_alarm("can you send me 5000 rupees today").kind, "money_ask")
        self.assertEqual(proof.live_alarm("install anydesk so I can help").kind, "remote_app")
        self.assertEqual(proof.live_alarm("you should invest in this app with me").kind, "money_ask")

    def test_ordinary_talk_does_not_fire(self):
        for line in ("I paid for dinner last time", "my sister sent me a photo", "the rice was good", "my job pays okay"):
            self.assertIsNone(proof.live_alarm(line), line)

    def test_alarm_wording_passes_our_own_rules(self):
        for line in ("tell me the OTP you received", "send me 5000 rupees", "install anydesk"):
            alarm = proof.live_alarm(line)
            self.assertEqual(check_answer(alarm.answer), [])


class AskedTests(unittest.TestCase):
    def memory(self):
        return proof.build_memory(Scripted(memory=GOOD), CHAT, "Arjun")

    def test_saying_a_question_aloud_is_noticed(self):
        found = proof.question_said(self.memory(), "um so what is your sister called again")
        self.assertEqual(found.text, "What is your sister called?")

    def test_other_talk_is_not_a_question(self):
        self.assertIsNone(proof.question_said(self.memory(), "that sounds really nice honestly"))


class ReceiptTests(unittest.TestCase):
    def receipt(self, labels, joined=True):
        memory = proof.build_memory(Scripted(memory=GOOD), CHAT, "Arjun")
        results = [proof.Result(question, label, f"said {question.id}") for question, label in zip(memory.questions, labels)]
        return proof.Receipt(results=results, differs=[], joined=joined, not_asked=len(memory.questions) - len(labels))

    def test_one_question_reads_properly(self):
        self.assertIn("Of 1 question,", proof.receipt_answer(self.receipt(["consistent"])).headline)

    def test_a_quote_with_a_banned_word_does_not_lose_the_receipt(self):
        receipt = self.receipt(["contradicts"])
        receipt.results[0].said = "this is not fake, trust me"
        self.assertEqual(check_answer(proof.receipt_answer(receipt)), [])

    def test_counts_come_from_code(self):
        receipt = self.receipt(["consistent", "contradicts", "no_answer"])
        answer = proof.receipt_answer(receipt)
        self.assertIn("1", answer.headline)
        self.assertEqual(check_answer(answer), [])

    def test_shows_old_words_beside_new_words_for_what_differed(self):
        answer = proof.receipt_answer(self.receipt(["contradicts"]))
        lines = " ".join(answer.proof)
        self.assertIn("My sister Kavya", lines)
        self.assertIn("said 1", lines)

    def test_never_gives_a_verdict_on_the_person(self):
        for labels in (["consistent"] * 5, ["contradicts"] * 5, ["no_answer"] * 5, []):
            answer = proof.receipt_answer(self.receipt(labels))
            text = " ".join([answer.headline, *answer.proof, answer.next_step]).casefold()
            for word in ("real", "verified", "genuine", "fake", "safe to", "liar", "lying"):
                self.assertNotIn(word, text)
            self.assertEqual(check_answer(answer), [])

    def test_not_joining_is_reported_and_not_judged(self):
        answer = proof.receipt_answer(self.receipt([], joined=False))
        self.assertIn("did not join", answer.headline)
        self.assertIn("proves nothing", " ".join([*answer.proof, answer.next_step]))

    def test_long_quotes_still_fit_in_one_whatsapp_message(self):
        receipt = self.receipt(["contradicts"] * 3)
        for result in receipt.results:
            result.said = "words " * 300
            result.question.quote = "quote " * 300
        self.assertEqual(check_answer(proof.receipt_answer(receipt)), [])


if __name__ == "__main__":
    unittest.main()
