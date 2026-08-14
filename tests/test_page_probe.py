"""DOM-extraction tests, run against a real browser.

Every case here is a false positive that actually happened against live Matiks
or an earlier fixture. Skipped if Playwright isn't installed.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from matiks_bot.page_probe import READ_STATE_JS

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

CONFIG = {
    "gameOverText": ["play again", "final score", "game over"],
    "waitingText": ["finding opponent"],
    "minQuestionFontPx": 22,
}
FIXTURE = "file://" + str(pathlib.Path(__file__).parent / "fixtures" / "fake_game.html")


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class TestPageProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self._browser.new_page(viewport={"width": 1280, "height": 900})
        self.page.goto(FIXTURE)

    def tearDown(self):
        self.page.close()

    def probe(self):
        return self.page.evaluate(READ_STATE_JS, CONFIG)

    def test_reads_question_split_across_spans(self):
        # The prompt is three sibling <span>s; naive textContent on a wrapper
        # would swallow the whole screen instead.
        state = self.probe()
        self.assertEqual(state["question"]["text"], "47 + 68")
        self.assertFalse(state["gameOver"])

    def test_ignores_scoreboard_timer_and_history(self):
        # "Score 12 - 9", "0:47" and "prev: 12 × 13 = 156" all parse as math.
        self.assertEqual(self.probe()["question"]["text"], "47 + 68")

    def test_progress_counter_is_not_a_question(self):
        # "1/6" means question 1 of 6. Live Matiks served exactly this and an
        # earlier build would have answered 0.1667.
        self.page.evaluate("() => document.querySelector('.q').remove()")
        self.assertIsNone(self.probe()["question"])

    def test_game_over_screen_yields_no_question(self):
        # "Final score 21 - 18" parses as subtraction; if it won, the bot would
        # answer the scoreboard forever instead of clicking Play Again.
        self.page.evaluate(
            """() => { document.querySelector('.q').remove();
                 const d = document.createElement('div');
                 d.innerHTML = '<h1>Game Over</h1><p>Final score 21 - 18</p>'
                             + '<button>Play Again</button>';
                 document.body.appendChild(d); }"""
        )
        state = self.probe()
        self.assertIsNone(state["question"])
        self.assertTrue(state["gameOver"])
        self.assertIn("Play Again", state["buttons"])

    def test_deeply_nested_question_with_small_wrapper_font(self):
        # How React Native Web actually renders: several wrapper layers, the
        # wrapper inheriting 16px while the digits inside are 56px. The first
        # build required all children to be leaves and sized off the wrapper,
        # so it saw nothing at all on the real site.
        self.page.evaluate(
            """() => {
                 document.querySelector('.q').remove();
                 const outer = document.createElement('div');
                 outer.style.fontSize = '16px';
                 outer.innerHTML =
                   '<div><div><span style="font-size:56px">128</span>'
                 + '<span style="font-size:56px"> \\u00f7 </span>'
                 + '<span style="font-size:56px">4</span></div></div>';
                 document.body.appendChild(outer);
               }"""
        )
        state = self.probe()
        self.assertIsNotNone(state["question"], "nested question was not found")
        self.assertEqual(state["question"]["text"], "128 ÷ 4")
        self.assertGreaterEqual(state["question"]["fontSize"], 56)

    def test_daily_challenges_widget_is_not_a_question(self):
        # Verbatim from live Matiks' logged-in home screen. It contains "1/6",
        # and an earlier build latched onto it and re-submitted 11 times.
        self.page.evaluate(
            """() => {
                 document.querySelector('.q').remove();
                 const d = document.createElement('div');
                 d.style.fontSize = '28px';
                 d.innerHTML = '<span>Daily Challenges</span>'
                             + '<span>Complete to earn rewards</span><span>1/6</span>';
                 document.body.appendChild(d);
               }"""
        )
        self.assertIsNone(self.probe()["question"])

    def test_prose_containing_arithmetic_is_rejected(self):
        for junk in ["You won 3 - 1 against Alex", "Level 2 of 10 complete",
                     "Streak 5 - keep going", "Answer within 2 - 3 seconds"]:
            with self.subTest(junk=junk):
                self.page.evaluate(
                    """(t) => {
                         const old = document.querySelector('.q');
                         if (old) old.remove();
                         const d = document.createElement('div');
                         d.className = 'q';
                         d.style.fontSize = '48px';
                         d.textContent = t;
                         document.body.appendChild(d);
                       }""",
                    junk,
                )
                self.assertIsNone(self.probe()["question"], junk)

    def test_wrapper_words_still_allowed(self):
        # "25% of 80" and "What is 47 + 68" are real question phrasings and
        # must survive the math-only filter.
        for phrasing in ["25% of 80", "What is 47 + 68"]:
            with self.subTest(phrasing=phrasing):
                self.page.evaluate(
                    """(t) => { document.querySelector('.q').textContent = t; }""",
                    phrasing,
                )
                self.assertEqual(self.probe()["question"]["text"], phrasing)

    def test_finds_div_based_controls(self):
        # Matiks is React Native Web: controls are cursor:pointer <div>s.
        self.page.evaluate(
            """() => { const d = document.createElement('div');
                 d.textContent = 'PLAY ON BROWSER';
                 d.style.cursor = 'pointer';
                 document.body.appendChild(d); }"""
        )
        self.assertIn("PLAY ON BROWSER", self.probe()["buttons"])


if __name__ == "__main__":
    unittest.main()
