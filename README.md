# Matiks Bot

A bot that plays the math mode of [Matiks](https://matiks.com): it reads the
question off the screen, solves it, types the answer, and starts the next game.

Measured against live Sprint Duels: **274 questions across 5 consecutive games,
0 solve failures, ~49 answers/min**, restarting itself between games.

> **Read this before running it.** Sprint Duels are ranked and matched against
> real people. Automating them violates Matiks' terms, takes wins off human
> opponents, and pollutes the leaderboard — and a perfect score at 60 answers a
> minute is not subtle. Point it at solo or practice modes if you want to keep
> your account. This exists because reverse-engineering how a page renders its
> content is an interesting problem I wanted to demonstrate to the Matiks team, not because ruining someone else's match is.

---

## The interesting part: Matiks doesn't render its questions as text

The obvious approach is to read the DOM. That returns nothing, and it took a
while to work out why.

**The digits are SVG vector outlines.** The DOM for `46 + 55` contains no `46`
and no `55` — just a `<path d="M6.62 24.24Q...">` per digit that draws the
glyph shape. Only the operator survives as real text. No amount of
`textContent` scraping will ever see the question.

The escape hatch is that those paths are *deterministic*: one fixed path per
digit, every time. [`glyphs.py`](matiks_bot/glyphs.py) holds all ten and maps
them straight back to digits, so decoding is **exact** — no OCR, no confidence
thresholds, no misread 7s. Unknown paths are the page's icons, and discarding
them is also what keeps UI chrome out of the expression.

The prompt is laid out as stacked vertical arithmetic:

```
  4 6
+ 5 5
```

DOM order is reading order, so the tokens concatenate directly to `46+55`.
Assembling by coordinates was tried first and silently produced `46+5` when one
element's box collapsed to zero width. Order is exact; geometry is not.

Everything *else* on the page is ordinary text and is read normally — buttons,
scores, the end screen, and the `<input placeholder="Enter answer">` box.

Two other things shape the code:

- **Matiks is React Native Web**, so there are no `<button>` or `<a>` elements
  anywhere. Every control is a `<div>` with `cursor: pointer` and a generated
  class name like `css-g5y9jx r-1loqt21`. Controls are found by hit-testing
  their label text.
- **Clicks are verified, not assumed.** A click that dispatches at the right
  coordinates without firing the button looks identical to success in a log.
  That stalled the bot between games while it cheerfully reported
  `clicked 'New Game'`. Every consequential click now checks that the screen
  actually changed and falls back to another route if it didn't.

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```bash
python3 -m matiks_bot.cli play --handoff --games 0 --minutes 0
```

This opens a Chromium window and waits. You sign in and start a duel; the bot
takes over the moment it sees a question and plays until its limit.

The bot never types your credentials and never accepts terms on your behalf.
You log in once by hand; the session persists in `.browser-profile/`.

Run until you close the browser window — the intended way to leave it going:

| Command | What it does |
|---|---|
| `play` | Play games |
| `probe` | Print what the bot can see on the current page |
| `solve "128 ÷ 4"` | Run the solver alone, no browser |
| `login` | Open a window and wait for you to sign in |
| `android` | Phone fallback over adb + OCR |
| `calibrate` | Map the phone's question area and keypad |

## Pacing

Each question gets a response time sampled uniformly from a window, measured
from when the prompt appears to when the answer is submitted, with typing
inside the budget rather than added on top.

This matters more than an average rate. A rate limit still emits answers at
near-identical gaps — the mean looks human while the variance stays at zero,
which is the actual tell. Keeping typing inside the budget matters for the same
reason: otherwise a 4-digit answer takes ~180ms longer than a 1-digit one, and
answer length correlating with response time is its own signature.

```bash
python -m matiks_bot.cli play --handoff --response-min 0.6 --response-max 1.4
```

Measured live: mean 1.01s, stdev 0.11s, 98.5% inside the window.

## When it breaks

Matiks is a live app, so expect drift. In order of likelihood:

**It stops reading questions.** Matiks changed its digit font, so the glyph
paths no longer match. Recapture and rebuild the table:

```bash
python -m matiks_bot.cli play --handoff --dry-run --capture-dir captures
python tools/build_glyphs.py captures/
```

**It can't navigate between games.** Button labels changed. Run `probe` to see
what's on screen and update `navigation` in `config.yaml`. The log distinguishes
a click that failed (`did not take`) from a label that was never found.

**A question won't parse.** Every unparsed question is printed at the end of a
run. Paste one into `solve` to see the parse error, then extend `solver.py`.
Sprint Duels are DMAS only, but the solver also handles powers, roots,
percentages and factorials.

## Android fallback

An adb + OpenCV/OCR path exists for the native app, for when you need the app
rather than the web client. It requires calibration and is strictly worse than
the browser path — OCR guesses where glyph decoding is exact.

```bash
brew install android-platform-tools tesseract
python -m matiks_bot.cli calibrate
python -m matiks_bot.cli android --dry-run
```

iOS is not supported. There is no way to inject taps into an iPhone without a
jailbreak or a signed WebDriverAgent build.

## Layout

| File | What it does |
|---|---|
| [`solver.py`](matiks_bot/solver.py) | Recursive-descent arithmetic parser (deliberately not `eval`) |
| [`glyphs.py`](matiks_bot/glyphs.py) | SVG path → digit table; the heart of question reading |
| [`layout.py`](matiks_bot/layout.py) | Rebuilds the expression from ordered glyph tokens |
| [`page_probe.py`](matiks_bot/page_probe.py) | JS that reads prompt tokens, controls and game phase |
| [`browser_bot.py`](matiks_bot/browser_bot.py) | Playwright driver, clicking, pacing, game loop |
| [`android_bot.py`](matiks_bot/android_bot.py) | adb device control and the OCR loop |
| [`vision.py`](matiks_bot/vision.py) | Screenshot preprocessing and OCR |
| [`calibrate.py`](matiks_bot/calibrate.py) | Interactive region/keypad mapping for the phone |
| [`config.py`](matiks_bot/config.py) | Defaults, merged over `config.yaml` |
| [`cli.py`](matiks_bot/cli.py) | Command line entry point |
| [`tools/build_glyphs.py`](tools/build_glyphs.py) | Regenerates the glyph table from captures |

## Tests

```bash
python -m unittest discover -s tests -v
```

48 tests. Almost every one exists because something failed against the live
site first:

- `1/6` — a progress counter that parsed as division
- `Daily Challenges … 1/6` — a home-screen widget answered as a question
- `Final score 21 - 18` — an end screen answered as subtraction, forever
- a collapsed element box that turned `46 + 55` into `46 + 5`
- a covered button that reported a successful click and started no game
- percent questions silently dropped by an over-strict regex
