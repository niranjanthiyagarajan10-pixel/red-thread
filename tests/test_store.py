import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from redthread.engine import Engine, Incoming
from redthread.network import Network
from redthread.store import Store

NOW = datetime(2026, 11, 1, 10, 0)


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "store.sqlite"
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def test_round_trip_keeps_money_dates_and_sets_exact(self):
        data = {"amount": Decimal("38000.50"), "when": NOW, "seen": {"a", "b"}, "nested": [{"held": Decimal(5)}], "none": None}
        self.store.save("u", data)
        self.assertEqual(self.store.load("u"), data)

    def test_missing_user_is_none_and_delete_removes(self):
        self.assertIsNone(self.store.load("u"))
        self.store.save("u", {"a": 1})
        self.store.delete("u")
        self.assertIsNone(self.store.load("u"))

    def test_a_message_id_counts_as_new_only_once(self):
        self.assertTrue(self.store.mark_seen("wamid.1"))
        self.assertFalse(self.store.mark_seen("wamid.1"))

    def test_many_threads_can_write(self):
        def write(index):
            self.store.save(f"u{index}", {"n": index})

        threads = [threading.Thread(target=write, args=(index,)) for index in range(30)]
        [thread.start() for thread in threads]
        [thread.join() for thread in threads]
        self.assertEqual(self.store.load("u29"), {"n": 29})


class EngineRestartTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def engine(self):
        network = Network(self.folder / "network.sqlite", salt=b"s")
        store = Store(self.folder / "store.sqlite")
        self.addCleanup(network.close)
        self.addCleanup(store.close)
        return Engine(network=network, store=store)

    def test_a_case_survives_a_restart(self):
        first = self.engine()
        first.handle("u", Incoming(voice_text="I already paid Rs 38,000", screenshot_text="Paid Rs 38,000 to scam.one@ybl UTR 533827652689", now=NOW))
        first.handle("u", Incoming(text="Acknowledgement No 30000000000012", now=NOW))
        second = self.engine()
        session = second.session("u")
        self.assertEqual(session.case.state, "helpline_ack")
        self.assertEqual(session.case.file_by, NOW + timedelta(hours=24))
        self.assertEqual(session.facts["amount"], Decimal(38000))
        self.assertEqual(session.asking, "story")
        second.handle("u", Incoming(text="Rs 38,000 is eligible for restoration on the Money Restoration portal", now=NOW))
        self.assertEqual(second.session("u").case.expected, Decimal(38000))

    def test_the_other_persons_chat_is_never_saved_to_disk(self):
        engine = self.engine()
        export = "01/03/2026, 21:00 - Riya: You should invest on this secretplatform today\n01/03/2026, 21:05 - Arjun: ok\n"
        engine.handle("u", Incoming(chat_export=export, now=NOW))
        engine.handle("u", Incoming(button="Arjun", now=NOW))
        self.assertNotIn(b"secretplatform", (self.folder / "store.sqlite").read_bytes())

    def test_the_chat_is_dropped_from_memory_after_an_hour(self):
        engine = self.engine()
        export = "01/03/2026, 21:00 - Riya: You should invest on this platform today\n01/03/2026, 21:05 - Arjun: ok\n"
        engine.handle("u", Incoming(chat_export=export, now=NOW))
        engine.handle("u", Incoming(text="hi", now=NOW + timedelta(hours=2)))
        self.assertIsNone(engine.session("u").chat)

    def test_delete_my_data_removes_everything_including_the_network(self):
        engine = self.engine()
        engine.handle("u", Incoming(voice_text="I paid Rs 500", screenshot_text="Paid Rs 500 to scam@ybl", now=NOW))
        out = engine.handle("u", Incoming(text="Delete my data", now=NOW))
        self.assertIn("deleted", out.answer.headline.lower())
        self.assertIsNone(engine.store.load("u"))
        self.assertEqual(engine.network.report_count("v", {"upi": ["scam@ybl"]}), 0)
        self.assertIsNone(engine.session("u").case)

    def test_an_unexpected_crash_still_gives_an_answer(self):
        engine = self.engine()

        def broken(*_):
            raise RuntimeError("boom")

        engine._route = broken
        import contextlib
        import io

        with contextlib.redirect_stderr(io.StringIO()):
            out = engine.handle("u", Incoming(text="hi", now=NOW))
        self.assertTrue(out.answer.next_step)

    def test_time_defaults_to_now_when_a_channel_gives_none(self):
        engine = self.engine()
        engine.handle("u", Incoming(text="Acknowledgement No 30000000000012"))
        self.assertIsNotNone(engine.session("u").case.file_by)


class NetworkThreadTests(unittest.TestCase):
    def test_forgetting_a_case_removes_its_rows(self):
        network = Network(":memory:", salt=b"s")
        self.addCleanup(network.close)
        script = "My uncle runs a private trading desk and he showed me how to double my savings in a month."
        network.add_case("a", {"upi": ["x@ybl"]}, script_text=script)
        network.opt_in("a", "b")
        network.forget_case("a")
        self.assertEqual(network.matches("b", {"upi": ["x@ybl"]}, script_text=script), [])
        self.assertFalse(network.linked("a", "b"))


if __name__ == "__main__":
    unittest.main()
