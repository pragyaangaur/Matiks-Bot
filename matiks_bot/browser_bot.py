"""Primary bot: drives Matiks in a real Chromium via Playwright.

Reads the page through the DOM and answers with real key events. Note that the
question itself is *not* text — Matiks draws its digits as SVG outlines, so the
prompt is rebuilt from glyph paths (see glyphs.py and layout.py). Everything
else on screen — buttons, scores, the answer box — is ordinary text.

Still no screen capture and no OCR: the glyph paths decode exactly, so there is
nothing to calibrate and no recognition step that can be wrong.
"""

from __future__ import annotations

import os
import random
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .layout import expression_from_state
from .page_probe import READ_STATE_JS
from .solver import SolveError, looks_like_math, solve


@dataclass
class Stats:
    answered: int = 0
    games: int = 0
    solve_failures: int = 0
    started_at: float = field(default_factory=time.monotonic)
    unparsed: list[str] = field(default_factory=list)
    response_times: list[float] = field(default_factory=list)

    def elapsed_min(self) -> float:
        return (time.monotonic() - self.started_at) / 60

    def summary(self) -> str:
        minutes = self.elapsed_min()
        rate = self.answered / minutes if minutes > 0.01 else 0.0
        return (
            f"{self.answered} answered across {self.games} game(s) "
            f"in {minutes:.1f} min ({rate:.1f}/min), {self.solve_failures} unsolved"
            + (
                f", response {min(self.response_times):.2f}-{max(self.response_times):.2f}s "
                f"(avg {sum(self.response_times) / len(self.response_times):.2f}s)"
                if self.response_times else ""
            )
        )


class MatiksBrowserBot:
    def __init__(self, config: dict[str, Any], dry_run: bool = False, verbose: bool = True,
                 debug_detect: bool = False, capture_dir: str | None = None):
        self.config = config
        self.dry_run = dry_run
        self.verbose = verbose
        self.debug_detect = debug_detect
        self.capture_dir = capture_dir
        self._captures = 0
        self._last_capture = 0.0
        self.stats = Stats()
        self._last_question: str | None = None
        self._pending_question: str | None = None
        self._seen_question_this_game = False
        self._question_seen_at: float | None = None
        self.restart_timeout_s = 120
        self._consecutive_errors = 0
        self._recoveries = 0
        self._last_progress_at = time.monotonic()
        self.page = None
        self._context = None
        self._playwright = None

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is missing. Run:\n"
                "  pip install -r requirements.txt\n"
                "  python -m playwright install chromium"
            ) from exc

        cfg = self.config["browser"]
        profile = Path(cfg["profile_dir"]).expanduser().resolve()
        profile.mkdir(parents=True, exist_ok=True)

        self._release_profile_lock(profile)
        self._playwright = sync_playwright().start()
        # Persistent context = your Matiks login survives between runs, so you
        # log in by hand once and never hand credentials to this script.
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=cfg["headless"],
            viewport=cfg["viewport"],
            slow_mo=cfg["slow_mo_ms"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self.page.goto(cfg["url"], wait_until="domcontentloaded")
        self.log(f"opened {cfg['url']}")

    def _release_profile_lock(self, profile: Path) -> None:
        """Clear orphaned Chromiums still holding this profile directory.

        Killing the bot process does not always take its browser with it, and
        the next launch then dies with "profile is already in use" before any
        window appears. These are our own leftovers — the profile is created by
        this tool and used for nothing else — so it is safe to clear them.
        """
        try:
            found = subprocess.run(
                ["pgrep", "-f", f"user-data-dir={profile}"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            return
        pids = [int(line) for line in found.stdout.split() if line.isdigit()]
        if not pids:
            return
        self.log(f"clearing {len(pids)} stale browser process(es) holding the profile")
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        for _ in range(20):  # up to ~4s for them to go away
            time.sleep(0.2)
            still = subprocess.run(
                ["pgrep", "-f", f"user-data-dir={profile}"],
                capture_output=True, text=True,
            )
            if not still.stdout.strip():
                return
        self.log("some browser processes would not exit; launch may still fail")

    def stop(self) -> None:
        for closer in (self._context, self._playwright):
            try:
                closer.close() if closer else None
            except Exception:
                pass

    def log(self, message: str) -> None:
        if self.verbose:
            print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    # ------------------------------------------------------------- page state
    def read_state(self, debug: bool = False) -> dict[str, Any]:
        detect = self.config["detect"]
        payload = {
            "gameOverText": detect["game_over_text"],
            "waitingText": detect["waiting_text"],
            "minQuestionFontPx": detect.get("min_question_font_px", 22),
            "debug": debug,
        }
        state = self.page.evaluate(READ_STATE_JS, payload)
        if state.get("question"):
            return state

        # page.evaluate only sees the main frame. If the game is mounted in an
        # iframe, the main frame is empty and every poll would come back blank.
        for frame in self.page.frames:
            if frame is self.page.main_frame:
                continue
            try:
                sub = frame.evaluate(READ_STATE_JS, payload)
            except Exception:
                continue
            if sub.get("question"):
                return sub
        return state

    # Returns every plausible target for a label, best first.
    #
    # Clicking the smallest matching element and assuming it worked was wrong:
    # the log said "clicked New Game" while the button never fired, because the
    # element's centre can be covered by an overlay, or the smallest match can
    # be a stale node from a screen that is animating out. elementFromPoint
    # hit-tests each centre so we know the click will actually reach it.
    _FIND_CLICK_TARGETS_JS = r"""
    (label) => {
      const want = label.trim().toLowerCase();
      const out = [];
      for (const el of document.querySelectorAll('body *')) {
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        if (parseFloat(style.opacity || '1') < 0.5) continue;
        if (style.pointerEvents === 'none') continue;
        const rect = el.getBoundingClientRect();
        if (rect.width < 6 || rect.height < 6) continue;
        const text = (el.innerText || el.textContent || '').trim().toLowerCase();
        if (!text || text.length > 60) continue;
        if (text !== want && !text.includes(want)) continue;

        const cx = rect.x + rect.width / 2;
        const cy = rect.y + rect.height / 2;
        const onScreen = cy >= 0 && cy <= window.innerHeight;
        let hittable = false;
        if (onScreen) {
          const hit = document.elementFromPoint(cx, cy);
          hittable = !!hit && (el.contains(hit) || hit.contains(el));
        }
        out.push({
          x: cx, y: cy, top: rect.top, area: rect.width * rect.height,
          hittable, onScreen, exact: text === want,
        });
      }
      // Reachable first, then exact label, then smallest box.
      out.sort((a, b) =>
        (b.hittable - a.hittable) || (b.exact - a.exact) || (a.area - b.area));
      return out.slice(0, 6);
    }
    """

    def click_any(self, labels: list[str], timeout_ms: int = 1200,
                  verify=None, settle_ms: int = 2500) -> bool:
        """Click a control by its visible label.

        With `verify`, each candidate is clicked and then given `settle_ms` for
        that callable to return True; if it doesn't, the next candidate is
        tried. Without it, the first reachable candidate is clicked blind —
        fine for navigation, not for anything whose failure is silent.
        """
        for label in labels:
            for locator in (
                self.page.get_by_role("button", name=label, exact=False),
                self.page.get_by_role("link", name=label, exact=False),
            ):
                try:
                    element = locator.first
                    element.wait_for(state="visible", timeout=min(timeout_ms, 400))
                    element.click(timeout=timeout_ms)
                    if verify is None or self._settled(verify, settle_ms):
                        self.log(f"clicked {label!r}")
                        return True
                except Exception:
                    continue

            try:
                targets = self.page.evaluate(self._FIND_CLICK_TARGETS_JS, label)
            except Exception:
                continue

            for index, target in enumerate(targets):
                try:
                    if not target["onScreen"]:
                        self.page.evaluate(
                            "(y) => window.scrollBy({top: y, behavior: 'instant'})",
                            target["top"] - 200,
                        )
                        self.page.wait_for_timeout(300)
                        refreshed = self.page.evaluate(self._FIND_CLICK_TARGETS_JS, label)
                        if not refreshed:
                            break
                        target = refreshed[0]

                    self.page.mouse.click(target["x"], target["y"])
                    if verify is None or self._settled(verify, settle_ms):
                        self.log(f"clicked {label!r}" + (f" (candidate {index + 1})" if index else ""))
                        return True
                    self.log(f"clicked {label!r} but nothing changed; trying next candidate")
                except Exception:
                    continue
        return False

    def browser_alive(self) -> bool:
        """False once the window is gone — the signal to stop an open-ended run."""
        try:
            return self.page is not None and not self.page.is_closed()
        except Exception:
            return False

    def signed_out(self, state: dict[str, Any]) -> bool:
        """A session that expired mid-run cannot be recovered without a human."""
        labels = " ".join(state.get("buttons") or []).lower()
        return any(word in labels for word in ("sign in", "log in", "sign up"))

    def recover(self) -> bool:
        """Try to get back to a duel from an unrecognised screen.

        Escalates: walk the menu, then reload the page, then walk it again.
        An open-ended run must not die on one unfamiliar screen, but it also
        must not click blindly forever.
        """
        self._recoveries += 1
        self.log(f"recovering (attempt {self._recoveries})")

        if self.navigate_to_game() and self.wait_for_game(timeout_s=45):
            return True

        self.log("menu walk failed; reloading the page")
        try:
            self.page.goto(self.config["browser"]["url"], wait_until="domcontentloaded")
            self.page.wait_for_timeout(4000)
        except Exception as exc:
            self.log(f"reload failed: {exc}")
            return False

        return self.navigate_to_game() and self.wait_for_game(timeout_s=60)

    def _settled(self, verify, timeout_ms: int) -> bool:
        """Poll `verify` until it returns True or the budget runs out."""
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            try:
                if verify():
                    return True
            except Exception:
                pass
            self.page.wait_for_timeout(150)
        return False

    def wait_for_login(self, timeout_s: float = 300) -> None:
        """Block until a human has signed in.

        The bot never types credentials. If the page still looks logged out,
        it waits for you to do it in the window it opened.
        """
        deadline = time.monotonic() + timeout_s
        ticks = 0
        while time.monotonic() < deadline:
            try:
                state = self.read_state()
            except Exception:
                time.sleep(2)  # mid-navigation during an OAuth redirect
                continue
            text = " ".join(state["buttons"]).lower()
            if not any(word in text for word in ("sign in", "log in", "login", "sign up")):
                return
            if ticks % 5 == 0:  # every ~15s, not every 3
                remaining = int((deadline - time.monotonic()) / 60)
                self.log(f"waiting for sign-in in the browser window… ({remaining} min left)")
            ticks += 1
            time.sleep(3)
        raise TimeoutError("still signed out after waiting; sign in and rerun")

    # ------------------------------------------------------------- navigation
    def navigate_to_game(self) -> bool:
        for step in self.config["navigation"]["to_game"]:
            if not self.click_any(step):
                self.log(f"could not find any of {step} — already past this step?")
            self.page.wait_for_timeout(700)
        # Success = a question is on screen within a few seconds.
        for _ in range(40):
            if self.read_state()["question"]:
                return True
            self.page.wait_for_timeout(250)
        return False

    # A duel always shows a countdown; the menus don't. Cheap way to know we
    # are on the screen worth capturing.
    _DUEL_TIMER = re.compile(r"\b\d{1,2}:\d{2}\b")

    def capture_snapshot(self, tag: str = "duel") -> None:
        """Dump the live DOM and a screenshot for offline analysis.

        Leaf-text summaries were not enough to work out how the prompt is
        built — the expression is split across sibling nodes. This grabs the
        real tree so the structure can be read directly instead of guessed.
        """
        if not self.capture_dir or self._captures >= 40:
            return
        now = time.monotonic()
        if now - self._last_capture < 1.5:
            return
        self._last_capture = now
        self._captures += 1
        stamp = time.strftime("%H%M%S")
        base = Path(self.capture_dir) / f"{tag}-{stamp}-{self._captures:02d}"
        try:
            base.with_suffix(".html").write_text(self.page.content(), encoding="utf-8")
            self.page.screenshot(path=str(base.with_suffix(".png")))
            self.log(f"captured {base.name}")
        except Exception as exc:
            self.log(f"capture failed: {exc}")

    def wait_for_game(self, timeout_s: float = 900) -> bool:
        """Hold until a question is on screen, however it got there.

        This is the handoff path. Matiks gates its web client behind a terms
        checkbox and a login, and its menus are React Native Web divs with no
        stable labels — so instead of guessing the route, the bot waits for a
        human to reach a duel and starts the moment it sees arithmetic.
        """
        deadline = time.monotonic() + timeout_s
        ticks = 0
        while time.monotonic() < deadline:
            show_debug = self.debug_detect and ticks % 8 == 0
            try:
                state = self.read_state(debug=show_debug)
            except Exception:
                time.sleep(1)
                continue
            question = self.question_text(state)
            answer = None if state.get("gameOver") else self.solvable(question)
            if answer is not None:
                self.log(f"question detected ({question!r} = {answer}) — taking over")
                return True
            if self.capture_dir and self._DUEL_TIMER.search(state.get("bodySample") or ""):
                self.capture_snapshot()

            if show_debug and state.get("debugTexts"):
                biggest = ", ".join(
                    f"{item['text']!r}@{item['size']}px" for item in state["debugTexts"][:8]
                )
                self.log(f"  visible: {biggest}")
            elif ticks % 10 == 0:
                remaining = int((deadline - time.monotonic()) / 60)
                self.log(f"waiting for you to start a duel… ({remaining} min left)")
            ticks += 1
            time.sleep(1)
        return False

    # ----------------------------------------------------------------- answer
    def _pace(self, answer: str) -> float:
        """Hold until this question has taken a human-plausible time to answer.

        Budgeted as total time-to-answer, sampled per question, measured from
        the moment the prompt appeared — not as an average rate. A rate limit
        still produces near-identical gaps every single time, which is the
        thing that looks mechanical; sampling per question is what actually
        varies. Typing is inside the budget, not added to it.
        """
        pacing = self.config["pacing"]
        target = random.uniform(pacing["response_min_s"], pacing["response_max_s"])

        # Keystrokes and the focus click happen after this sleep, so subtract
        # them or every answer lands late by the length of the number.
        typing_s = len(answer) * pacing["typing_delay_ms"] / 1000 + 0.06
        elapsed = time.monotonic() - (self._question_seen_at or time.monotonic())

        delay = target - elapsed - typing_s
        if delay > 0:
            time.sleep(delay)
        return target

    def submit_answer(self, answer: str) -> None:
        run = self.config["run"]
        selector = run.get("answer_selector")

        # Matiks gives the answer box a stable placeholder, which beats relying
        # on whatever happens to hold focus. Falls back to typing at the page
        # if the selector ever stops matching.
        target = None
        if selector:
            try:
                candidate = self.page.locator(selector).first
                candidate.wait_for(state="visible", timeout=800)
                target = candidate
            except Exception:
                self.log(f"answer box {selector!r} not found; typing at the page")

        if target is not None:
            # The first answer of every duel used to vanish: the box exists and
            # is clickable while the round is still starting, so the keystrokes
            # went nowhere and the bot only noticed 2s later via re-submit.
            # Confirming focus before typing is what makes the opening answer
            # land like the rest.
            for attempt in range(3):
                try:
                    target.click(timeout=800)
                except Exception:
                    pass
                try:
                    if target.evaluate("el => el === document.activeElement"):
                        break
                except Exception:
                    break
                if attempt < 2:
                    self.page.wait_for_timeout(80)

            if run["clear_before_typing"]:
                self.page.keyboard.press("ControlOrMeta+A")
            self.page.keyboard.type(answer, delay=self.config["pacing"]["typing_delay_ms"])
        else:
            if run["clear_before_typing"]:
                self.page.keyboard.press("ControlOrMeta+A")
            self.page.keyboard.type(answer, delay=self.config["pacing"]["typing_delay_ms"])

        if run["submit_key"]:
            self.page.keyboard.press(run["submit_key"])

    def question_text(self, state: dict[str, Any]) -> str | None:
        """The prompt on screen, glyphs first.

        In a duel the digits are SVG outlines, so the glyph tokens are the only
        source. The text scan still covers any screen that renders its prompt
        as real text.
        """
        return expression_from_state(state) or (state.get("question") or {}).get("text")

    def solvable(self, question: str | None) -> str | None:
        """Return the answer, or None if this text isn't really a question.

        Detection means solvable, not merely math-shaped. An earlier build
        treated any arithmetic-looking text as a prompt, latched onto the home
        screen's "Daily Challenges … 1/6" widget, and re-submitted eleven times
        against a page that had no answer box at all. Each distinct failure is
        logged once, not once per poll.
        """
        if not question or not looks_like_math(question):
            return None
        try:
            return solve(question)
        except SolveError as exc:
            if question not in self.stats.unparsed:
                self.stats.unparsed.append(question)
                self.stats.solve_failures += 1
                self.log(f"ignoring unsolvable text {question!r}: {exc}")
            return None

    def handle_question(self, question: str, answer: str) -> None:
        self._consecutive_errors = 0
        self._pace(answer)
        if self.dry_run:
            self.log(f"[dry-run] {question}  ->  {answer}")
        else:
            self.submit_answer(answer)
        took = time.monotonic() - (self._question_seen_at or time.monotonic())
        self.stats.response_times.append(took)
        if not self.dry_run:
            self.log(f"{question}  ->  {answer}   ({took:.2f}s)")
        self.stats.answered += 1

    # -------------------------------------------------------------- main loop
    def run(self) -> Stats:
        """Play until a limit is hit or the window is closed.

        Supervises the loop rather than being it. Any Playwright call can throw
        when the browser goes away — not just the state read — and an
        unguarded click or keystroke used to end an overnight run with a
        traceback and no summary after a thousand answered questions.
        """
        # Start the clock here, not at construction: time spent waiting for a
        # human to log in and open a duel is not play time, and counting it
        # burned the whole --minutes budget before the first question.
        self.stats.started_at = time.monotonic()

        # Resuming is for transient faults. If the page throws every time, the
        # supervisor would otherwise retry forever — busy, silent, and useless.
        max_resumes = self.config["run"].get("max_loop_resumes", 10)
        resumes = 0
        answered_at_last_error = -1

        while True:
            try:
                return self._loop()
            except Exception as exc:
                if not self.browser_alive():
                    self.log(f"browser window closed — stopping. {self.stats.summary()}")
                    return self.stats

                # Progress since the last fault means the fault was transient,
                # so the budget resets; repeated faults with nothing answered
                # in between are the ones that count.
                if self.stats.answered > answered_at_last_error:
                    resumes = 0
                answered_at_last_error = self.stats.answered

                resumes += 1
                if resumes > max_resumes:
                    self.log(f"{resumes} errors with no progress — stopping. {self.stats.summary()}")
                    return self.stats
                self.log(f"loop error ({type(exc).__name__}: {exc}); resuming [{resumes}/{max_resumes}]")
                time.sleep(1)

    def _loop(self) -> Stats:
        run_cfg = self.config["run"]
        poll_s = self.config["pacing"]["poll_interval_ms"] / 1000
        max_games = run_cfg["max_games"]
        max_minutes = run_cfg["max_minutes"]
        idle_ticks = 0

        give_up_after = run_cfg.get("give_up_after_idle_s", 1800)

        while True:
            # Closing the browser is how an open-ended run is meant to end.
            if not self.browser_alive():
                self.log("browser window closed — stopping")
                break
            if max_games and self.stats.games >= max_games:
                self.log(f"reached max_games={max_games}")
                break
            if max_minutes and self.stats.elapsed_min() >= max_minutes:
                self.log(f"reached max_minutes={max_minutes}")
                break
            if self._consecutive_errors >= run_cfg["stop_after_consecutive_errors"]:
                self.log("too many consecutive unsolved questions — stopping so it can be diagnosed")
                break

            try:
                state = self.read_state()
            except Exception as exc:  # page navigated mid-evaluate, etc.
                if not self.browser_alive():
                    self.log("browser window closed — stopping")
                    break
                self.log(f"state read failed ({exc}); retrying")
                time.sleep(0.5)
                continue

            question = self.question_text(state)

            # Game-over is checked first on purpose. End screens show things
            # like "Final score 21 - 18", which parses as arithmetic; if the
            # question branch won, the bot would answer the scoreboard forever
            # and never click Play Again.
            if state.get("gameOver"):
                # Count on the edge, not the level: the end screen sits there
                # for several polls, and counting each one inflated a single
                # game into two before anything had actually restarted.
                if self._seen_question_this_game:
                    self.stats.games += 1
                    self.log(f"game over — {self.stats.summary()}")
                self._seen_question_this_game = False
                self._last_question = None
                self._pending_question = None
                idle_ticks = 0

                # Check the limits before starting another duel. Clicking
                # "New Game" and then exiting drops a real opponent into a
                # match against nobody.
                if max_games and self.stats.games >= max_games:
                    self.log(f"reached max_games={max_games} — not starting another duel")
                    break
                if max_minutes and self.stats.elapsed_min() >= max_minutes:
                    self.log(f"reached max_minutes={max_minutes} — not starting another duel")
                    break

                # Verify the end screen actually goes away. Clicking blind
                # here is what left duels un-started: the log claimed success
                # while the button had not fired, and a human had to click it.
                def left_end_screen() -> bool:
                    return not self.read_state().get("gameOver")

                if not self.click_any(self.config["navigation"]["play_again"],
                                      timeout_ms=3000, verify=left_end_screen,
                                      settle_ms=3000):
                    self.log("play-again did not take; walking the menu again")
                    self.navigate_to_game()
                # Matchmaking takes a while. Waiting for the next question to
                # appear is what makes the restart reliable; the old fixed
                # 1.5s pause just fell through to "idle" and quit.
                if not self.wait_for_game(timeout_s=self.restart_timeout_s):
                    # Matchmaking can stall, or Matiks can show a screen we
                    # have never seen. Neither should end an overnight run.
                    if not self.recover():
                        self.log("could not get back into a duel; will keep trying")
                continue

            answer = self.solvable(question)
            # Questions cross-fade, and mid-transition both the outgoing and
            # incoming prompts can sit in the DOM at once — which would
            # concatenate into a plausible-looking but wrong expression. One
            # extra poll (~60ms) of agreement costs nothing and rules that out.
            confirmed = question is not None and question == self._pending_question
            if question is not None and question != self._pending_question:
                self._question_seen_at = time.monotonic()
            self._pending_question = question
            if answer is not None and confirmed:
                if question != self._last_question:
                    self._last_question = question
                    idle_ticks = 0
                    self._seen_question_this_game = True
                    self._last_progress_at = time.monotonic()
                    self._recoveries = 0
                    self.handle_question(question, answer)
                else:
                    # Same text still showing: either a repeat question or our
                    # answer hasn't registered. Give it a beat, then re-answer.
                    idle_ticks += 1
                    if idle_ticks > int(1.2 / poll_s):
                        self.log("question unchanged — re-submitting")
                        # Time the retry from now; measuring from the original
                        # sighting reported 5.42s for what was a 0.9s answer.
                        self._question_seen_at = time.monotonic()
                        self._last_question = None
                        idle_ticks = 0
                time.sleep(poll_s)
                continue

            if state.get("waiting"):
                time.sleep(0.4)
                continue

            # Nothing recognizable on screen: try to get back into a game, but
            # only every couple of seconds so we don't spam clicks.
            if self.signed_out(state):
                self.log("signed out — the session expired and I cannot log in for you; stopping")
                break

            idle_ticks += 1
            if idle_ticks > int(2 / poll_s):
                idle_ticks = 0
                self.click_any(self.config["navigation"]["dismiss"], timeout_ms=400)
                self.recover()

            idle_for = time.monotonic() - self._last_progress_at
            if give_up_after and idle_for > give_up_after:
                self.log(f"no questions answered for {idle_for / 60:.0f} min — stopping")
                break
            time.sleep(poll_s)

        return self.stats
