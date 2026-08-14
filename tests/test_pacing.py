"""Per-question response pacing.

The bot answers far faster than any person, so each question is held to a
sampled time-to-answer. These tests check the budget is honoured and that
typing time is inside it rather than added on top — otherwise every answer
lands late by the length of the number.
"""

import pathlib
import statistics
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from matiks_bot.browser_bot import MatiksBrowserBot
from matiks_bot.config import load_config


def make_bot(low: float, high: float) -> MatiksBrowserBot:
    config = load_config(None)
    config["pacing"]["response_min_s"] = low
    config["pacing"]["response_max_s"] = high
    return MatiksBrowserBot(config, dry_run=True, verbose=False)


class TestPacing(unittest.TestCase):
    def total_for(self, bot, answer: str, already_elapsed: float = 0.1) -> float:
        typing = len(answer) * bot.config["pacing"]["typing_delay_ms"] / 1000 + 0.06
        bot._question_seen_at = time.monotonic() - already_elapsed
        start = bot._question_seen_at
        bot._pace(answer)
        return (time.monotonic() - start) + typing

    def test_lands_inside_the_window(self):
        bot = make_bot(0.30, 0.45)
        for answer in ["7", "42", "1024"]:
            with self.subTest(answer=answer):
                total = self.total_for(bot, answer)
                self.assertGreaterEqual(total, 0.30 - 0.05, total)
                self.assertLessEqual(total, 0.45 + 0.05, total)

    def test_longer_answers_do_not_run_over(self):
        # Typing is subtracted from the budget, so a 4-digit answer must not
        # take meaningfully longer than a 1-digit one.
        bot = make_bot(0.40, 0.40)
        short = statistics.mean(self.total_for(bot, "7") for _ in range(5))
        long = statistics.mean(self.total_for(bot, "1024") for _ in range(5))
        self.assertLess(abs(long - short), 0.08, f"short={short:.3f} long={long:.3f}")

    def test_actually_varies(self):
        # A fixed rate would give near-zero spread; that is the tell we are
        # trying to avoid.
        bot = make_bot(0.20, 0.60)
        samples = [self.total_for(bot, "42") for _ in range(12)]
        self.assertGreater(statistics.stdev(samples), 0.03)

    def test_slow_detection_shrinks_the_wait(self):
        # If the prompt was spotted late, the remaining wait shrinks to
        # compensate. Measured as added sleep, not total: once detection plus
        # typing already exceeds the budget there is no way back under it, and
        # the correct behaviour is simply to add nothing.
        bot = make_bot(0.30, 0.30)
        bot._question_seen_at = time.monotonic() - 0.28
        start = time.monotonic()
        bot._pace("42")
        self.assertLess(time.monotonic() - start, 0.03)

    def test_early_detection_waits_the_difference(self):
        bot = make_bot(0.50, 0.50)
        bot._question_seen_at = time.monotonic()
        start = time.monotonic()
        bot._pace("7")
        slept = time.monotonic() - start
        self.assertGreater(slept, 0.30)
        self.assertLess(slept, 0.50)

    def test_never_sleeps_when_already_over_budget(self):
        bot = make_bot(0.10, 0.10)
        start = time.monotonic()
        bot._question_seen_at = start - 5.0  # detection took way too long
        bot._pace("42")
        self.assertLess(time.monotonic() - start, 0.05)


if __name__ == "__main__":
    unittest.main()
