"""Behaviour of open-ended runs.

`--games 0` is meant to run for hours unattended, so the loop must survive
transient failures, stop when the window is closed, and stop rather than spin
when the session expires.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from matiks_bot.browser_bot import MatiksBrowserBot
from matiks_bot.config import load_config

IDLE_STATE = {
    "tokens": [], "question": None, "buttons": [], "bodySample": "",
    "gameOver": False, "waiting": True, "field": None, "candidates": [],
}


class FakePage:
    """Stands in for a Playwright page, scripted per test."""

    def __init__(self, script):
        self.script = script
        self.reads = 0
        self.closed = False
        self.frames = []
        self.main_frame = None

    def is_closed(self):
        return self.closed

    def evaluate(self, *_args, **_kwargs):
        self.reads += 1
        return self.script(self)

    def wait_for_timeout(self, _ms):
        pass

    def goto(self, *_args, **_kwargs):
        pass


def make_bot(script, **run_overrides):
    config = load_config(None)
    config["run"].update({"max_games": 0, "max_minutes": 0, **run_overrides})
    config["pacing"]["poll_interval_ms"] = 10
    bot = MatiksBrowserBot(config, dry_run=True, verbose=False)
    bot.page = FakePage(script)
    return bot


class TestResilience(unittest.TestCase):
    def test_stops_when_the_window_is_closed(self):
        # Closing the browser is the documented way to end an open-ended run.
        def script(page):
            if page.reads >= 4:
                page.closed = True
                raise RuntimeError("Target page, context or browser has been closed")
            return IDLE_STATE

        bot = make_bot(script)
        bot.run()
        self.assertTrue(bot.page.closed)

    def test_survives_a_transient_read_failure(self):
        # A navigation mid-evaluate raises, and must not end the run.
        def script(page):
            if page.reads == 2:
                raise RuntimeError("Execution context was destroyed")
            if page.reads >= 6:
                page.closed = True
                raise RuntimeError("browser has been closed")
            return IDLE_STATE

        bot = make_bot(script)
        bot.run()
        self.assertGreaterEqual(bot.page.reads, 6, "gave up on a recoverable error")

    def test_survives_failures_outside_the_state_read(self):
        # The real overnight crash: the browser closed during wait_for_timeout,
        # not during evaluate. Only the read path was guarded, so a run that
        # had answered 1011 questions ended in a traceback with no summary.
        class ExplodingPage(FakePage):
            def wait_for_timeout(self, _ms):
                self.closed = True
                raise RuntimeError("Target page, context or browser has been closed")

        config = load_config(None)
        config["run"].update({"max_games": 0, "max_minutes": 0})
        bot = MatiksBrowserBot(config, dry_run=True, verbose=False)
        bot.page = ExplodingPage(lambda _p: {**IDLE_STATE, "waiting": False,
                                             "buttons": ["Forfeit"]})
        stats = bot.run()          # must return, not raise
        self.assertIsNotNone(stats)

    def test_a_permanently_broken_page_stops_instead_of_spinning(self):
        # A live browser whose calls always throw must not be retried forever.
        # Without a bound the supervisor busy-loops silently until morning.
        class BrokenPage(FakePage):
            def wait_for_timeout(self, _ms):
                raise RuntimeError("page is wedged")

        config = load_config(None)
        config["run"].update({"max_games": 0, "max_minutes": 0, "max_loop_resumes": 3})
        config["pacing"]["poll_interval_ms"] = 10
        bot = MatiksBrowserBot(config, dry_run=True, verbose=False)
        bot.page = BrokenPage(lambda _p: {**IDLE_STATE, "waiting": False,
                                          "buttons": ["Forfeit"]})
        stats = bot.run()
        self.assertIsNotNone(stats)
        self.assertTrue(bot.browser_alive(), "a live browser was treated as closed")

    def test_stops_when_signed_out(self):
        # No human is watching at 3am and the bot cannot log in; spinning until
        # morning is worse than stopping.
        bot = make_bot(lambda _page: {**IDLE_STATE, "waiting": False,
                                      "buttons": ["Sign In", "Sign Up"]})
        bot.run()
        self.assertLess(bot.page.reads, 60, "kept going while signed out")

    def test_gives_up_after_a_long_idle_stretch(self):
        bot = make_bot(lambda _page: {**IDLE_STATE, "waiting": False},
                       give_up_after_idle_s=0.2)
        bot.run()
        self.assertLess(bot.stats.answered, 1)

    def test_signed_out_detection(self):
        bot = make_bot(lambda _page: IDLE_STATE)
        self.assertTrue(bot.signed_out({"buttons": ["Log In"]}))
        self.assertTrue(bot.signed_out({"buttons": ["Sign in with Google"]}))
        self.assertFalse(bot.signed_out({"buttons": ["New Game", "Forfeit"]}))
        self.assertFalse(bot.signed_out({}))

    def test_browser_alive_reports_closed_page(self):
        bot = make_bot(lambda _page: IDLE_STATE)
        self.assertTrue(bot.browser_alive())
        bot.page.closed = True
        self.assertFalse(bot.browser_alive())
        bot.page = None
        self.assertFalse(bot.browser_alive())


if __name__ == "__main__":
    unittest.main()
