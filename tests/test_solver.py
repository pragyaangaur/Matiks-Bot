import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matiks_bot.solver import SolveError, evaluate, looks_like_math, solve


class TestSolve(unittest.TestCase):
    def test_basic_arithmetic(self):
        cases = {
            "47 + 68": "115",
            "91 - 137": "-46",
            "12 * 13": "156",
            "144 / 12": "12",
            "2 + 3 * 4": "14",
            "(2 + 3) * 4": "20",
        }
        for question, expected in cases.items():
            self.assertEqual(solve(question), expected, question)

    def test_unicode_operators(self):
        self.assertEqual(solve("12 × 13"), "156")
        self.assertEqual(solve("144 ÷ 12"), "12")
        self.assertEqual(solve("8 − 3"), "5")
        self.assertEqual(solve("7 x 6"), "42")

    def test_powers_roots_factorial(self):
        self.assertEqual(solve("2 ^ 10"), "1024")
        self.assertEqual(solve("13²"), "169")
        self.assertEqual(solve("√144"), "12")
        self.assertEqual(solve("sqrt(81)"), "9")
        self.assertEqual(solve("5!"), "120")
        self.assertEqual(solve("2 ^ 3 ^ 2"), "512")  # right-associative

    def test_percent(self):
        self.assertEqual(solve("25% of 80"), "20")
        self.assertEqual(solve("15% of 200"), "30")
        self.assertEqual(solve("50%"), "0.5")

    def test_wrapper_text_is_stripped(self):
        self.assertEqual(solve("What is 17 × 24 = ?"), "408")
        self.assertEqual(solve("Solve: 100 - 37"), "63")
        self.assertEqual(solve("1,250 + 750"), "2000")

    def test_implicit_multiplication(self):
        self.assertEqual(solve("2(3 + 4)"), "14")
        self.assertEqual(solve("3(4)(5)"), "60")

    def test_non_integer_results(self):
        self.assertEqual(solve("10 / 4"), "2.5")
        self.assertEqual(solve("1 / 3"), "0.3333")

    def test_float_precision_snaps_to_integer(self):
        # 0.1+0.2 style drift must not become "11.999999999999998" in the box.
        self.assertEqual(solve("36 / 3 + 0.1 + 0.2 - 0.3"), "12")

    def test_rejects_non_math(self):
        for junk in ["Play Duel", "", "Sprint Duels", "Waiting for opponent"]:
            self.assertFalse(looks_like_math(junk), junk)
        self.assertTrue(looks_like_math("47 + 68"))
        self.assertTrue(looks_like_math("√169"))
        # solvable() gates on this, so a miss here silently drops the whole
        # percent question type in a live duel.
        self.assertTrue(looks_like_math("25% of 80"))

    def test_errors_are_typed(self):
        for junk in ["5 / 0", "√-4", "((3 + 4)", "3 + + * 4"]:
            with self.assertRaises(SolveError, msg=junk):
                evaluate(junk)

    def test_evaluate_returns_float(self):
        self.assertTrue(math.isclose(evaluate("22 / 7"), 3.142857, rel_tol=1e-5))


if __name__ == "__main__":
    unittest.main()
