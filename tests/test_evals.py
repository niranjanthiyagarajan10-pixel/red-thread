import unittest

from redthread.evals import DEV, FRESH, render, run_all


class EvalTests(unittest.TestCase):
    def assert_clean(self, batch):
        groups = run_all(batch)
        self.assertEqual([item for group in groups for item in group.misses + group.false_alarms], [], render(groups))

    def test_dev_plants_stay_caught(self):
        self.assert_clean(DEV)

    def test_fresh_plants_stay_caught(self):
        self.assert_clean(FRESH)


if __name__ == "__main__":
    unittest.main()
