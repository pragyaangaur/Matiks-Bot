"""Clicking React Native Web controls.

Matiks renders every control as a <div>, so clicks go through a text hunt and
a coordinate click. That combination fails silently in ways a role-based click
does not, and a silent failure here stalls the bot between games — it happened
live, and a human had to press New Game.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from matiks_bot.browser_bot import MatiksBrowserBot
from matiks_bot.config import load_config

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

PAGE = """
<style>
  body { margin: 0; font-family: sans-serif; }
  .btn { padding: 14px 22px; background: #2a2a2a; color: #eee; cursor: pointer;
         display: inline-block; margin: 30px; }
  #veil { position: fixed; inset: 0; background: rgba(0,0,0,.6); }
</style>
<div class="btn" onclick="window.__clicked = 'new-game'"><div><span>New Game</span></div></div>
<div class="btn" onclick="window.__clicked = 'quit'">Quit</div>
<script>window.__clicked = null;</script>
"""


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class TestClicking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self._browser.new_page(viewport={"width": 900, "height": 600})
        self.page.set_content(PAGE)
        self.bot = MatiksBrowserBot(load_config(None), dry_run=True, verbose=False)
        self.bot.page = self.page

    def tearDown(self):
        self.page.close()

    def clicked(self):
        return self.page.evaluate("() => window.__clicked")

    def test_clicks_a_div_button(self):
        # No <button>/<a> anywhere — this is how all of Matiks is built.
        self.assertTrue(self.bot.click_any(["New Game"]))
        self.assertEqual(self.clicked(), "new-game")

    def test_click_lands_on_nested_text(self):
        # The label lives two elements deep; the handler is on the ancestor.
        self.bot.click_any(["New Game"])
        self.assertEqual(self.clicked(), "new-game")

    def test_overlay_blocks_the_click_and_verify_catches_it(self):
        # The exact silent failure seen live: the coordinates are right, the
        # click is dispatched, and the button never fires.
        self.page.evaluate("""() => {
            const veil = document.createElement('div');
            veil.id = 'veil';
            document.body.appendChild(veil);
        }""")
        ok = self.bot.click_any(["New Game"], verify=lambda: self.clicked() == "new-game",
                                settle_ms=400)
        self.assertFalse(ok, "a covered button must not be reported as clicked")
        self.assertIsNone(self.clicked())

    def test_verify_confirms_a_real_click(self):
        ok = self.bot.click_any(["New Game"], verify=lambda: self.clicked() == "new-game",
                                settle_ms=1500)
        self.assertTrue(ok)

    def test_unknown_label_reports_failure(self):
        self.assertFalse(self.bot.click_any(["Nonexistent Button"], timeout_ms=300))
        self.assertIsNone(self.clicked())

    def test_falls_back_through_label_list(self):
        # play_again is a list of candidate labels; the first may not exist.
        self.assertTrue(self.bot.click_any(["Rematch", "New Game"]))
        self.assertEqual(self.clicked(), "new-game")


if __name__ == "__main__":
    unittest.main()
