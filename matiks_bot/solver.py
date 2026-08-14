"""Parse and evaluate the arithmetic expressions Matiks shows.

Deliberately *not* eval(): the input comes from a screen scrape or OCR, so it can
be garbage, and eval on garbage is either a crash or a security hole. This is a
small recursive-descent parser over a fixed token set, which also gives us a
clean way to reject text that isn't a math question at all (see looks_like_math).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


class SolveError(ValueError):
    """Raised when the text isn't a solvable arithmetic expression."""


# Glyphs Matiks (and OCR) hand us that Python doesn't understand.
_TRANSLATE = {
    "×": "*",  # ×
    "⋅": "*",  # ⋅
    "∙": "*",  # ∙
    "·": "*",  # ·
    "✕": "*",  # ✕
    "✖": "*",  # ✖
    "x": "*",       # only applied between digits, see _normalize
    "÷": "/",  # ÷
    "∕": "/",  # ∕
    "⁄": "/",  # ⁄
    "−": "-",  # −
    "–": "-",  # –
    "—": "-",  # —
    "√": "√",  # √ kept as-is, handled by the parser
}

_SUPERSCRIPT = {"²": "^2", "³": "^3", "⁰": "^0", "¹": "^1"}
for _i, _ch in enumerate("⁴⁵⁶⁷⁸⁹", start=4):
    _SUPERSCRIPT[_ch] = f"^{_i}"

# Wrapper words the UI puts around the actual expression.
_STRIP_PHRASES = [
    r"^\s*what\s+is\s*",
    r"^\s*solve\s*:?\s*",
    r"^\s*calculate\s*:?\s*",
    r"^\s*answer\s*:?\s*",
    r"\s*=\s*\?*\s*$",
    r"\s*\?\s*$",
]

# The trailing \d\s*% alternative catches "25% of 80", where the operator is
# followed by a word, not a digit. Without it solvable() drops percent questions.
_MATH_HINT = re.compile(r"\d\s*(?:[-+*/^%]|×|÷|−|⋅|x)\s*\d|√\s*\d|\d+\s*!|\d\s*%")


def looks_like_math(text: str) -> bool:
    """Cheap pre-filter so we don't try to parse UI chrome like 'Play Duel'."""
    if not text or len(text) > 120:
        return False
    if not any(c.isdigit() for c in text):
        return False
    return bool(_MATH_HINT.search(text))


def _normalize(text: str) -> str:
    s = text.strip()

    # "25% of 80" -> "(25/100)*80". Do this before % becomes a bare postfix.
    s = re.sub(r"(?i)\s*%\s*of\s*", "% * ", s)
    s = re.sub(r"(?i)\bof\b", "*", s)

    for phrase in _STRIP_PHRASES:
        s = re.sub(phrase, "", s, flags=re.IGNORECASE)

    for src, dst in _SUPERSCRIPT.items():
        s = s.replace(src, dst)

    # Thousands separators: 1,234 -> 1234 (but leave "3, 4" list-ish text alone).
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)

    # 'x' as a multiplication sign only when it sits between numbers.
    s = re.sub(r"(?<=[\d)\s])[xX](?=[\s\d(])", "*", s)

    out = []
    for ch in s:
        if ch in _TRANSLATE and ch not in ("x",):
            out.append(_TRANSLATE[ch])
        else:
            out.append(ch)
    s = "".join(out)

    s = s.replace("**", "^")
    s = re.sub(r"(?i)\bsqrt\b", "√", s)
    return s.strip()


@dataclass(frozen=True)
class _Token:
    kind: str  # "num" | "op" | "lparen" | "rparen"
    value: str


_TOKEN_RE = re.compile(r"\d+\.\d+|\.\d+|\d+|[-+*/^!%()]|√|\s+|.")


def _tokenize(s: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _TOKEN_RE.finditer(s):
        text = match.group()
        if text.isspace():
            continue
        if text[0].isdigit() or text[0] == ".":
            tokens.append(_Token("num", text))
        elif text == "(":
            tokens.append(_Token("lparen", text))
        elif text == ")":
            tokens.append(_Token("rparen", text))
        elif text in "-+*/^!%√":
            tokens.append(_Token("op", text))
        else:
            raise SolveError(f"unexpected character {text!r} in {s!r}")
    if not tokens:
        raise SolveError(f"nothing to parse in {s!r}")
    return tokens


class _Parser:
    """expr := term (('+'|'-') term)*
    term    := unary (('*'|'/') unary | implicit-mul unary)*
    unary   := ('-'|'+') unary | '√' unary | power
    power   := postfix ('^' unary)?          # right-associative
    postfix := primary ('!' | '%')*
    primary := number | '(' expr ')'
    """

    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> _Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> _Token:
        tok = self.peek()
        if tok is None:
            raise SolveError("expression ended early")
        self.pos += 1
        return tok

    def parse(self) -> float:
        value = self.expr()
        if self.pos != len(self.tokens):
            raise SolveError(f"trailing tokens at {self.pos}")
        return value

    def expr(self) -> float:
        value = self.term()
        while (tok := self.peek()) and tok.kind == "op" and tok.value in "+-":
            self.next()
            rhs = self.term()
            value = value + rhs if tok.value == "+" else value - rhs
        return value

    def term(self) -> float:
        value = self.unary()
        while tok := self.peek():
            if tok.kind == "op" and tok.value in "*/":
                self.next()
                rhs = self.unary()
                if tok.value == "*":
                    value *= rhs
                else:
                    if rhs == 0:
                        raise SolveError("division by zero")
                    value /= rhs
            elif tok.kind in ("num", "lparen") or (tok.kind == "op" and tok.value == "√"):
                # Implicit multiplication: 2(3+4), 3√4.
                value *= self.unary()
            else:
                break
        return value

    def unary(self) -> float:
        tok = self.peek()
        if tok and tok.kind == "op" and tok.value in "+-":
            self.next()
            value = self.unary()
            return -value if tok.value == "-" else value
        if tok and tok.kind == "op" and tok.value == "√":
            self.next()
            operand = self.unary()
            if operand < 0:
                raise SolveError("square root of a negative number")
            return math.sqrt(operand)
        return self.power()

    def power(self) -> float:
        base = self.postfix()
        tok = self.peek()
        if tok and tok.kind == "op" and tok.value == "^":
            self.next()
            return base ** self.unary()
        return base

    def postfix(self) -> float:
        value = self.primary()
        while (tok := self.peek()) and tok.kind == "op" and tok.value in "!%":
            self.next()
            if tok.value == "!":
                if value < 0 or value != int(value) or value > 170:
                    raise SolveError(f"cannot take factorial of {value}")
                value = float(math.factorial(int(value)))
            else:
                value /= 100.0
        return value

    def primary(self) -> float:
        tok = self.next()
        if tok.kind == "num":
            return float(tok.value)
        if tok.kind == "lparen":
            value = self.expr()
            closing = self.peek()
            if closing is None or closing.kind != "rparen":
                raise SolveError("unbalanced parentheses")
            self.next()
            return value
        raise SolveError(f"unexpected token {tok.value!r}")


def evaluate(text: str) -> float:
    """Evaluate a Matiks question and return the raw float result."""
    normalized = _normalize(text)
    if not normalized:
        raise SolveError(f"empty expression from {text!r}")
    value = _Parser(_tokenize(normalized)).parse()
    if not math.isfinite(value):
        raise SolveError(f"non-finite result for {text!r}")
    return value


def format_answer(value: float, max_decimals: int = 4) -> str:
    """Render the result the way the answer box expects it.

    Matiks answers are integers in the overwhelming majority of question types,
    so we snap near-integers rather than typing '11.999999999999998'.
    """
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    text = f"{value:.{max_decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def solve(text: str) -> str:
    """Question text in, keystrokes out."""
    return format_answer(evaluate(text))
