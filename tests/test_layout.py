"""Rebuilding the duel expression from SVG glyph tokens.

Matiks renders the prompt's digits as SVG outlines, so these tests use the real
path data lifted from live captures (see matiks_bot/glyphs.py).
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from matiks_bot.glyphs import DIGIT_BY_PATH, digit_for_path
from matiks_bot.layout import expression_from_state
from matiks_bot.solver import solve

PATH_FOR_DIGIT = {digit: path for path, digit in DIGIT_BY_PATH.items()}


def glyph(digit: str) -> dict:
    return {"kind": "glyph", "d": PATH_FOR_DIGIT[digit]}


def op(symbol: str) -> dict:
    return {"kind": "op", "text": symbol}


def state(*tokens) -> dict:
    return {"tokens": list(tokens)}


class TestGlyphTable(unittest.TestCase):
    def test_all_ten_digits_present(self):
        self.assertEqual(sorted(DIGIT_BY_PATH.values()), list("0123456789"))

    def test_unknown_path_is_not_a_digit(self):
        # Icons share the page with the prompt; this is what excludes them.
        self.assertIsNone(digit_for_path("M0 0L10 10Z"))
        self.assertIsNone(digit_for_path(""))

    def test_matching_is_scale_invariant(self):
        import re
        original = PATH_FOR_DIGIT["7"]
        doubled = re.sub(r"\d+\.?\d*", lambda m: str(round(float(m.group()) * 2, 2)), original)
        self.assertEqual(digit_for_path(doubled), "7")


class TestExpressionFromState(unittest.TestCase):
    def test_stacked_addition(self):
        # The layout that live Matiks actually shows:  46 over +55
        expr = expression_from_state(state(glyph("4"), glyph("6"), op("+"), glyph("5"), glyph("5")))
        self.assertEqual(expr, "46+55")
        self.assertEqual(solve(expr), "101")

    def test_each_operator(self):
        for symbol, expected in [("+", "56"), ("−", "40"), ("×", "384"), ("÷", "6")]:
            expr = expression_from_state(
                state(glyph("4"), glyph("8"), op(symbol), glyph("8"))
            )
            self.assertEqual(solve(expr), expected, symbol)

    def test_icons_between_digits_are_dropped(self):
        expr = expression_from_state(
            state(glyph("2"), {"kind": "glyph", "d": "M1 1L9 9Z"}, glyph("8"), op("÷"), glyph("7"))
        )
        self.assertEqual(expr, "28÷7")

    def test_stray_operator_outside_the_prompt_is_trimmed(self):
        # A dash elsewhere in the UI must not be glued onto either end.
        expr = expression_from_state(
            state(op("-"), glyph("9"), op("×"), glyph("3"), op("-"))
        )
        self.assertEqual(expr, "9×3")

    def test_digits_with_no_operator_is_not_a_question(self):
        self.assertIsNone(expression_from_state(state(glyph("1"), glyph("2"))))

    def test_no_glyphs_at_all(self):
        self.assertIsNone(expression_from_state(state()))
        self.assertIsNone(expression_from_state({}))

    def test_five_terms(self):
        # Five-term prompts are a real Matiks format. An 8-digit cap silently
        # discarded every one with multi-digit operands, and a discarded
        # prompt is indistinguishable from no prompt: the bot just waited.
        for expr, expected in [
            ("12+34+56+78+90", "270"),
            ("45+443-66+12-3", "431"),
            ("9+8+7+6+5", "35"),
            ("100-20-30-10-5", "35"),
        ]:
            with self.subTest(expr=expr):
                tokens = [glyph(c) if c.isdigit() else op(c) for c in expr]
                built = expression_from_state(state(*tokens))
                self.assertEqual(built, expr)
                self.assertEqual(solve(built), expected)

    def test_long_operand_run_still_allowed(self):
        # Three digits per term across five terms is the realistic ceiling.
        expr = "123+456+789+321+654"
        tokens = [glyph(c) if c.isdigit() else op(c) for c in expr]
        self.assertEqual(expression_from_state(state(*tokens)), expr)

    def test_absurdly_long_run_is_rejected(self):
        # Guards against sweeping in page chrome and "solving" nonsense.
        tokens = [glyph("1") for _ in range(30)]
        tokens.insert(6, op("+"))
        self.assertIsNone(expression_from_state(state(*tokens)))

    def test_too_many_operators_is_rejected(self):
        tokens = []
        for _ in range(10):
            tokens += [glyph("1"), op("+")]
        self.assertIsNone(expression_from_state(state(*tokens)))


if __name__ == "__main__":
    unittest.main()
