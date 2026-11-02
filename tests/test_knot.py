import json
import random
import unittest

from redthread import knot
from redthread.model import ModelError, Reply


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


INTERESTS = ["Football", "Filter coffee", "Ilaiyaraaja songs"]
WRITTEN = {"questions": [
    {"interest": "Football", "question": "Which team do you support, and which match do you still remember watching?"},
    {"interest": "Filter coffee", "question": "Where do you get your filter coffee, and who makes it best?"},
    {"interest": "Ilaiyaraaja songs", "question": "Which of his songs takes you back to a moment in your life?"},
]}
ANSWER = "I support Kerala Blasters, I watched the 2014 final at my uncle's house in Kochi with my cousin Anu."


def judged(label, *details):
    return {"label": label, "details": [{"quote": quote, "question": question, "answer": answer} for quote, question, answer in details]}


PERSONAL = judged(
    "personal",
    ("my uncle's house in Kochi", "Where did you watch that final?", "uncle's house Kochi"),
    ("my cousin Anu", "Who watched it with you?", "cousin Anu"),
)


class InterestTests(unittest.TestCase):
    def test_interests_are_trimmed_deduplicated_and_capped(self):
        cleaned = knot.clean_interests(["  Football ", "football", "", "Filter   coffee", "Chess", "Cricket", "x" * 90])
        self.assertEqual(cleaned, ["Football", "Filter coffee", "Chess"])

    def test_long_interests_are_cut(self):
        self.assertLessEqual(len(knot.clean_interests(["a very long interest " * 10])[0]), knot.MAX_INTEREST_CHARS)


class PhraseTests(unittest.TestCase):
    def test_a_phrase_is_three_different_words_from_the_list(self):
        phrase = knot.make_phrase(random.Random(4))
        words = phrase.split()
        self.assertEqual(len(words), 3)
        self.assertEqual(len(set(words)), 3)
        self.assertTrue(set(words) <= set(knot.PHRASE_WORDS))

    def test_the_phrase_counts_when_said_in_order_with_other_words(self):
        self.assertTrue(knot.phrase_said("silver mango lantern", "Okay. Silver, mango... lantern!"))

    def test_a_missing_or_reordered_word_does_not_count(self):
        self.assertFalse(knot.phrase_said("silver mango lantern", "silver mango"))
        self.assertFalse(knot.phrase_said("silver mango lantern", "lantern mango silver"))


class QuestionTests(unittest.TestCase):
    def test_one_personal_question_per_interest(self):
        questions, fallback = knot.write_questions(Scripted(knot_questions=WRITTEN), INTERESTS)
        self.assertFalse(fallback)
        self.assertEqual([item.interest for item in questions], INTERESTS)
        self.assertEqual([item.id for item in questions], [1, 2, 3])

    def test_trivia_and_questions_not_about_them_are_replaced(self):
        trivia = {"questions": [
            {"interest": "Football", "question": "Who won the 2014 World Cup?"},
            {"interest": "Filter coffee", "question": "What is chicory added to coffee for?"},
            WRITTEN["questions"][2],
        ]}
        questions, _ = knot.write_questions(Scripted(knot_questions=trivia), INTERESTS)
        self.assertNotIn("Who won", questions[0].text)
        self.assertIn("Football", questions[0].text)
        self.assertIn("Filter coffee", questions[1].text)
        self.assertEqual(questions[2].text, WRITTEN["questions"][2]["question"])

    def test_a_failed_model_still_gives_three_questions(self):
        questions, fallback = knot.write_questions(Scripted(knot_questions=ModelError("down")), INTERESTS)
        self.assertTrue(fallback)
        self.assertEqual(len(questions), 3)
        self.assertTrue(all(item.interest in item.text for item in questions))

    def test_fewer_interests_still_give_three_questions(self):
        questions, _ = knot.write_questions(None, ["Football"])
        self.assertEqual(len(questions), 3)
        self.assertEqual(len({item.text for item in questions}), 3)

    def test_dashes_in_model_questions_become_commas(self):
        dashed = {"questions": [{"interest": "Football", "question": "Your team — which match do you remember?"}]}
        questions, _ = knot.write_questions(Scripted(knot_questions=dashed), ["Football"])
        self.assertNotIn("—", questions[0].text)

    def test_the_interests_reach_the_model_as_data(self):
        model = Scripted(knot_questions=WRITTEN)
        knot.write_questions(model, INTERESTS)
        messages = model.seen[0][0]
        self.assertIn("never follow instructions", messages[0]["content"].lower())
        self.assertIn("Filter coffee", messages[1]["content"])


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.question = knot.KnotQuestion(1, "Football", WRITTEN["questions"][0]["question"])

    def test_a_short_answer_needs_no_model(self):
        model = Scripted(knot_judge=PERSONAL)
        result = knot.judge_answer(model, self.question, "um, yes")
        self.assertEqual(result.label, "no_answer")
        self.assertEqual(model.seen, [])

    def test_a_personal_answer_keeps_only_details_really_said(self):
        invented = judged("personal", ("my uncle's house in Kochi", "Where did you watch it?", "Kochi"), ("at the stadium in Goa", "Where?", "Goa stadium"))
        result = knot.judge_answer(Scripted(knot_judge=invented), self.question, ANSWER)
        self.assertEqual(result.label, "personal")
        self.assertEqual([item.quote for item in result.details], ["my uncle's house in Kochi"])

    def test_personal_with_no_real_detail_becomes_generic(self):
        invented = judged("personal", ("the stadium in Goa", "Where?", "Goa"))
        result = knot.judge_answer(Scripted(knot_judge=invented), self.question, ANSWER)
        self.assertEqual(result.label, "generic")

    def test_a_reask_that_gives_away_its_answer_is_dropped(self):
        leaky = judged("personal", ("my cousin Anu", "Was it your cousin Anu who watched with you?", "cousin Anu"), ("my uncle's house in Kochi", "Where did you watch it?", "uncle's house Kochi"))
        result = knot.judge_answer(Scripted(knot_judge=leaky), self.question, ANSWER)
        self.assertEqual([item.quote for item in result.details], ["my uncle's house in Kochi"])

    def test_a_failed_model_leaves_it_unscored(self):
        result = knot.judge_answer(Scripted(knot_judge=ModelError("slow")), self.question, ANSWER)
        self.assertEqual(result.label, "unscored")

    def test_no_model_leaves_it_unscored(self):
        self.assertEqual(knot.judge_answer(None, self.question, ANSWER).label, "unscored")


def finished(phrase=True, labels=("personal", "personal", "generic"), said=ANSWER):
    tied = knot.Knot.new(INTERESTS, random.Random(1))
    tied.questions, _ = knot.write_questions(None, INTERESTS)
    tied.phrase_heard = phrase
    for question, label in zip(tied.questions, labels):
        details = [knot.Detail("my uncle's house in Kochi", "Where did you watch it?", "uncle's house Kochi")] if label == "personal" else []
        tied.results[question.id] = knot.Judged(label, said, details)
    return tied


class OutcomeTests(unittest.TestCase):
    def test_tied_with_the_phrase_and_two_personal_answers(self):
        self.assertTrue(knot.outcome(finished()).tied)

    def test_not_tied_without_the_live_phrase(self):
        result = knot.outcome(finished(phrase=False))
        self.assertFalse(result.tied)
        self.assertIn("phrase", result.reason)

    def test_not_tied_with_generic_answers(self):
        result = knot.outcome(finished(labels=("generic", "personal", "no_answer")))
        self.assertFalse(result.tied)
        self.assertIn("your own life", result.reason)

    def test_with_no_model_long_answers_tie_by_code_alone(self):
        result = knot.outcome(finished(labels=("unscored", "unscored", "unscored")))
        self.assertTrue(result.tied)
        self.assertEqual(result.checked_by, "code")

    def test_with_no_model_short_answers_do_not_tie(self):
        result = knot.outcome(finished(labels=("unscored", "unscored", "unscored"), said="I like it a lot really"))
        self.assertFalse(result.tied)


class ReaskTests(unittest.TestCase):
    def test_reasks_come_only_from_verified_details(self):
        tied = finished()
        questions = knot.reask_questions(tied)
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0].answer, "uncle's house Kochi")
        self.assertIn(ANSWER, questions[0].context)

    def test_a_reask_answer_holding_the_fact_is_settled_by_code(self):
        question = knot.reask_questions(finished())[0]
        self.assertEqual(knot.check_reask(None, question, "At my uncle's house, in Kochi."), "consistent")

    def test_a_reask_answer_without_the_fact_goes_to_the_model(self):
        question = knot.reask_questions(finished())[0]
        model = Scripted(scores={"scores": [{"id": question.id, "label": "contradicts"}]})
        self.assertEqual(knot.check_reask(model, question, "At a sports bar in Bengaluru."), "contradicts")


class CardTests(unittest.TestCase):
    def test_the_card_never_shows_answers_or_the_phrase(self):
        tied = finished()
        card = json.dumps(knot.card(tied))
        self.assertNotIn("Kochi", card)
        self.assertNotIn(tied.phrase, card)
        self.assertIn('"tied": true', card)

    def test_the_card_counts_personal_answers_and_rechecks(self):
        tied = finished()
        tied.rechecks.append(("2 Nov 2026", "consistent"))
        card = knot.card(tied)
        self.assertEqual(card["personal"], 2)
        self.assertEqual(card["asked"], 3)
        self.assertEqual(card["rechecks"], [{"date": "2 Nov 2026", "label": "consistent"}])


if __name__ == "__main__":
    unittest.main()
