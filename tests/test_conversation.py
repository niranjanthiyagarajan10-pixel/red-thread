import unittest
from datetime import datetime

from redthread.conversation import Conversation, Turn, parse_whatsapp_export

ANDROID = """12/03/2026, 21:41 - Messages and calls are end-to-end encrypted. Only people in this chat can read them.
12/03/2026, 21:41 - Riya: Hi, is this Arjun?
12/03/2026, 21:42 - Arjun: Yes. Who is this?
It is late here.
13/03/2026, 09:05 - Riya: <Media omitted>
13/03/2026, 09:06 - Riya: PTT-20260313-WA0001.opus (file attached)
13/03/2026, 09:07 - Arjun: Nice: thanks
"""

IOS = "[12/03/26, 9:41:05\u202fPM] Riya: \u200eMessages and calls are end-to-end encrypted.\n" \
      "[12/03/26, 9:41:05\u202fPM] Riya: Hello there\n" \
      "\u200e[12/03/26, 9:42:10\u202fPM] Riya: \u200eimage omitted\n" \
      "[13/03/26, 12:01:00\u202fAM] Arjun: \u200e<attached: 00000012-AUDIO-2026-03-13-00-01-00.opus>\n" \
      "[13/03/26, 12:02:00\u202fAM] Arjun: Good night\n"


class ExportParserTests(unittest.TestCase):
    def test_android_turns_and_speakers(self):
        chat = parse_whatsapp_export(ANDROID)
        self.assertEqual([t.speaker for t in chat.turns], ["Riya", "Arjun", "Riya", "Riya", "Arjun"])
        self.assertEqual(chat.speakers(), ["Riya", "Arjun"])

    def test_system_lines_are_dropped(self):
        chat = parse_whatsapp_export(ANDROID)
        self.assertNotIn("encrypted", " ".join(t.text for t in chat.turns))
        self.assertEqual(parse_whatsapp_export(IOS).turns[0].text, "Hello there")

    def test_multi_line_messages_stay_together(self):
        self.assertEqual(parse_whatsapp_export(ANDROID).turns[1].text, "Yes. Who is this?\nIt is late here.")

    def test_colons_in_messages_survive(self):
        self.assertEqual(parse_whatsapp_export(ANDROID).turns[4].text, "Nice: thanks")

    def test_attachments_are_recorded(self):
        chat = parse_whatsapp_export(ANDROID)
        self.assertEqual(chat.turns[2].attachments, ["media"])
        self.assertEqual(chat.turns[2].text, "")
        self.assertEqual(chat.turns[3].attachments, ["PTT-20260313-WA0001.opus"])
        ios = parse_whatsapp_export(IOS)
        self.assertEqual(ios.turns[1].attachments, ["image"])
        self.assertEqual(ios.turns[2].attachments, ["00000012-AUDIO-2026-03-13-00-01-00.opus"])

    def test_times(self):
        self.assertEqual(parse_whatsapp_export(ANDROID).turns[0].when, datetime(2026, 3, 12, 21, 41))
        self.assertEqual(parse_whatsapp_export(IOS).turns[0].when, datetime(2026, 3, 12, 21, 41, 5))
        self.assertEqual(parse_whatsapp_export(IOS).turns[2].when, datetime(2026, 3, 13, 0, 1))

    def test_day_first_by_default_and_month_first_when_needed(self):
        day_first = parse_whatsapp_export("03/04/2026, 10:00 - A: hi\n")
        self.assertEqual(day_first.turns[0].when.month, 4)
        month_first = parse_whatsapp_export("03/04/2026, 10:00 - A: hi\n03/25/2026, 10:00 - A: hi\n")
        self.assertEqual(month_first.turns[0].when.month, 3)

    def test_non_latin_text_and_names(self):
        chat = parse_whatsapp_export("12/03/2026, 21:41 - \u0930\u093f\u092f\u093e: \u0928\u092e\u0938\u094d\u0924\u0947\n")
        self.assertEqual(chat.turns[0].speaker, "\u0930\u093f\u092f\u093e")
        self.assertEqual(chat.turns[0].text, "\u0928\u092e\u0938\u094d\u0924\u0947")

    def test_indexes_are_positions(self):
        self.assertEqual([t.index for t in parse_whatsapp_export(ANDROID).turns], [0, 1, 2, 3, 4])

    def test_empty_or_unrecognised_text_gives_no_turns(self):
        self.assertEqual(parse_whatsapp_export("").turns, [])
        self.assertEqual(parse_whatsapp_export("not a chat export").turns, [])


class TranscriptTests(unittest.TestCase):
    def test_transcript_numbers_each_turn(self):
        chat = Conversation("x", [Turn(0, "A", "hello"), Turn(1, "B", "", attachments=["image"])])
        self.assertEqual(chat.transcript(), "[0] A: hello\n[1] B: (image)")


if __name__ == "__main__":
    unittest.main()
