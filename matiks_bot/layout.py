"""Reassemble the duel question from the page's ordered prompt tokens.

Matiks lays the prompt out as stacked vertical arithmetic, the way it is
written by hand:

      4 6
    + 5 5

The digits are SVG vector outlines and the operator is text, so the expression
exists nowhere in the DOM as a string — it has to be rebuilt. DOM order is the
reading order here, so "4", "6", "+", "5", "5" concatenates straight to
"46+55" without needing any coordinates.

Geometry was tried first and was a mistake: element boxes are unreliable
(a collapsed box silently turned "46 + 55" into "46 + 5"), while document
order is exact.
"""

from __future__ import annotations

from typing import Any

from .glyphs import digit_for_path

# Sanity bounds, sized from what Matiks actually serves: up to five terms of
# three digits each ("45+443-66+12-3"), plus headroom.
#
# These were originally 8 digits total, on the assumption that a prompt is two
# operands and one operator. That silently discarded every five-term question
# with multi-digit operands — the extraction returned nothing and the bot just
# sat there, because a rejected prompt looks identical to no prompt at all.
# Counting operators is the better guard anyway: runaway chrome shows up as an
# implausible number of operators, not merely a lot of digits.
MAX_DIGITS = 24
MAX_OPERATORS = 8


def expression_from_state(state: dict[str, Any]) -> str | None:
    """Return the arithmetic expression on screen, or None if there isn't one."""
    parts: list[tuple[str, str]] = []  # (kind, char)
    for token in state.get("tokens") or []:
        if token.get("kind") == "glyph":
            digit = digit_for_path(token.get("d", ""))
            # Unknown paths are the page's icons; the digit table is the filter.
            if digit is not None:
                parts.append(("digit", digit))
        elif token.get("kind") == "op":
            parts.append(("op", token.get("text", "")))

    digit_positions = [i for i, (kind, _) in enumerate(parts) if kind == "digit"]
    if not digit_positions:
        return None

    # Trim to the span between the first and last digit, so an unrelated dash
    # elsewhere in the UI cannot be spliced onto either end of the expression.
    first, last = digit_positions[0], digit_positions[-1]
    span = parts[first : last + 1]

    operators = sum(1 for kind, _ in span if kind == "op")
    if sum(1 for kind, _ in span if kind == "digit") > MAX_DIGITS:
        return None
    if operators > MAX_OPERATORS:
        return None
    if not operators:
        return None  # no operator means this isn't a question yet

    return "".join(char for _, char in span)
