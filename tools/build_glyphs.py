#!/usr/bin/env python3
"""Rebuild the digit glyph table from captured duel pages.

Matiks draws the prompt's digits as SVG outlines, so matiks_bot/glyphs.py maps
each path back to a digit. If Matiks changes its font those paths change and
the bot stops reading questions — this regenerates the table.

    python -m matiks_bot.cli play --handoff --capture-dir captures --dry-run
    python tools/build_glyphs.py captures/

It extracts every unique path, renders them to glyph_sheet.png, and asks you to
read off the digits in order. The labelling has to be human: the paths are just
outlines, with nothing in the page saying which digit each one draws.
"""

from __future__ import annotations

import collections
import pathlib
import re
import sys

SVG_PATH = re.compile(r'<svg height="(\d+)" width="[\d.]+"><path d="([^"]+)"')


def extract(capture_dir: pathlib.Path) -> list[str]:
    counts: collections.Counter[str] = collections.Counter()
    for page in sorted(capture_dir.glob("*.html")):
        for _height, path in SVG_PATH.findall(page.read_text()):
            counts[path] += 1
    return [path for path, _ in counts.most_common()]


def render_sheet(paths: list[str], out: pathlib.Path) -> None:
    from playwright.sync_api import sync_playwright

    cells = "".join(
        f'<div style="display:inline-block;text-align:center;margin:14px">'
        f'<svg height="60" width="40" viewBox="0 0 20 30"><path d="{d}" fill="black"/></svg>'
        f'<div style="font:16px monospace">idx {i}</div></div>'
        for i, d in enumerate(paths)
    )
    sheet = out.with_suffix(".html")
    sheet.write_text(f'<body style="background:white">{cells}</body>')
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 260})
        page.goto("file://" + str(sheet.resolve()))
        page.wait_for_timeout(400)
        page.screenshot(path=str(out))
        browser.close()


def emit_module(paths: list[str], labels: dict[int, str]) -> str:
    lines = ['DIGIT_BY_PATH: dict[str, str] = {']
    for index, path in enumerate(paths):
        lines.append(f"    {path!r}:\n        {labels[index]!r},")
    lines.append("}")
    return "\n".join(lines)


def main() -> int:
    capture_dir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "captures")
    if not capture_dir.is_dir():
        print(f"no such capture directory: {capture_dir}", file=sys.stderr)
        return 1

    paths = extract(capture_dir)
    print(f"found {len(paths)} unique glyph paths in {capture_dir}")
    if len(paths) != 10:
        print("warning: expected exactly 10 (one per digit). Captures may include "
              "non-duel screens, or the font changed.", file=sys.stderr)

    sheet = pathlib.Path("glyph_sheet.png")
    render_sheet(paths, sheet)
    print(f"rendered {sheet} — open it and read the digits left to right")

    answer = input(f"enter the {len(paths)} digits in idx order (e.g. 4365128790): ").strip()
    if len(answer) != len(paths) or not answer.isdigit():
        print("that isn't one digit per glyph; nothing written", file=sys.stderr)
        return 1

    print("\nReplace DIGIT_BY_PATH in matiks_bot/glyphs.py with:\n")
    print(emit_module(paths, dict(enumerate(answer))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
