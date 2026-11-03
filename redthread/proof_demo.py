"""An invented chat and an invented person for the judge page. Everything here is made up and labelled as simulated.

A judge can call Rohan and ask about the chat. The person who answers is either the one who wrote it, or a stand-in
who never saw it. The judge is told which only at the end. Written questions mean the demo never waits on a model to start.
"""
import random
import wave
from dataclasses import dataclass, field
from pathlib import Path

from redthread.conversation import Conversation, Turn
from redthread.proof import Claim, Memory, Question
from redthread.quotes import find_quote

OTHER = "Rohan"
PEOPLE = ("writer", "stand_in")
MONEY_LINE = "By the way, my cousin runs this trading app. You should invest with me today, send me 5000 rupees and I will set it up."
WRITER_JOB_LINE = "Still the product job at the software firm in Bengaluru. Too many meetings, honestly."
JOB_LINE = "My job is at a bank in Dubai, I have been working there for six years and I have never lived in Bengaluru."
_TURNS = [
    ("Rohan", "Hey! Your profile made me laugh, the bit about arguing with autorickshaw meters."),
    ("Me", "Ha, someone had to say it. What do you do?"),
    ("Rohan", "I work in product at a software firm in Bengaluru. Mostly meetings, a little bit of building things."),
    ("Me", "Are you from there?"),
    ("Rohan", "Grew up in Chennai. My paati taught me filter coffee in Mylapore and she still scolds me for the ratio."),
    ("Me", "That is very sweet. Pets?"),
    ("Rohan", "A dog called Pepper. He hides under the bed every time the pressure cooker whistles."),
    ("Me", "Poor thing! How do you get around Bengaluru?"),
    ("Rohan", "My old scooter Bijli, when she agrees to start. She broke down near Silk Board again on Monday."),
    ("Me", "Did you do anything fun this weekend?"),
    ("Rohan", "Went up to Nandi Hills with friends and dropped my phone in a cup of chai. It still smells of ginger."),
    ("Me", "No way. Is it working?"),
    ("Rohan", "Mostly! The speaker sounds like it is underwater now."),
    ("Me", "We should meet some time."),
    ("Rohan", "I would really like that. I will bring you banganapalli mangoes from my uncle's farm in Salem."),
    ("Me", "That is a very good opening offer."),
    ("Rohan", "It is mango season only in my heart right now, but I will find some."),
]
_QUESTIONS = [
    ("Who taught you to make filter coffee?", "My paati taught me filter coffee in Mylapore", "paati"),
    ("What makes your dog run and hide?", "He hides under the bed every time the pressure cooker whistles", "pressure cooker"),
    ("What do you call that scooter of yours?", "My old scooter Bijli", "Bijli"),
    ("What happened to your phone up at the hills?", "dropped my phone in a cup of chai", "fell in chai"),
    ("What did you say you would bring when we meet?", "I will bring you banganapalli mangoes", "mangoes"),
]
_CLAIMS = [
    ("job", "I work in product at a software firm in Bengaluru"),
    ("home", "Grew up in Chennai"),
    ("pet", "A dog called Pepper"),
]
_ANSWERS = {
    "writer": [
        "My paati, back in Mylapore. She is still very strict about it.",
        "The pressure cooker. One whistle and he is under the bed.",
        "Oh, Bijli. She is dying slowly, honestly.",
        "Do not remind me. It fell in my chai and now it smells of ginger.",
        "Mangoes, those banganapalli ones from my uncle's farm.",
    ],
    "stand_in": [
        "My mom. She makes the best coffee at home.",
        "Fireworks. Diwali is really hard for him.",
        "I call it Rocket, because it is so fast.",
        "I think I cracked the screen on a rock.",
        "Probably some flowers, I am not sure.",
    ],
}
_LABELS = {
    "writer": ["consistent"] * 5,
    "stand_in": ["contradicts", "contradicts", "contradicts", "contradicts", "contradicts"],
}
CLIPS = Path(__file__).parent / "demo_clips"


def chat():
    return Conversation("demo", [Turn(index, speaker, text) for index, (speaker, text) in enumerate(_TURNS)])


def memory():
    conversation = chat()
    questions = []
    for number, (text, quote, answer) in enumerate(_QUESTIONS, 1):
        turn = find_quote(conversation, quote, speaker=OTHER)
        context = "\n".join(f"{item.speaker}: {item.text}" for item in conversation.turns[max(0, turn - 2):turn + 2])
        questions.append(Question(number, text, quote, turn, answer, context))
    claims = [Claim(number, topic, quote, find_quote(conversation, quote, speaker=OTHER)) for number, (topic, quote) in enumerate(_CLAIMS, 1)]
    keywords = ["Rohan", "Mylapore", "Pepper", "Bijli", "Silk Board", "Nandi Hills", "banganapalli", "Salem", "paati"]
    return Memory(OTHER, questions, claims, keywords, {"name": OTHER, "job": "product", "city": "Bengaluru"})


def _read_pcm(path):
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getframerate() != 16000 or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                return None
            return audio.readframes(audio.getnframes())
    except (OSError, wave.Error, EOFError):
        return None


@dataclass
class Person:
    kind: str
    answers: dict
    labels: dict
    clips_dir: Path = field(default=CLIPS)

    def clip_path(self, question_id):
        return Path(self.clips_dir) / f"{self.kind}_{question_id}.wav"

    def clip(self, question_id):
        """Real spoken audio when it has been recorded. The call works without it."""
        return _read_pcm(self.clip_path(question_id))


def person(kind=None, clips_dir=CLIPS):
    kind = kind or random.choice(PEOPLE)
    answers = dict(enumerate(_ANSWERS[kind], 1))
    labels = dict(enumerate(_LABELS[kind], 1))
    return Person(kind, answers, labels, clips_dir)
