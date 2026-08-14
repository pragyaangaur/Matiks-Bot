"""Fallback bot: phone over USB via adb, screen read with OpenCV + OCR.

Only worth using if you genuinely need the app rather than the web client.
Requires: Android device, USB debugging on, `adb` and `tesseract` installed.
iOS is not supported — Apple provides no input-injection path without a
jailbreak or a signed WebDriverAgent build, so there is no honest way to make
this work on an iPhone.
"""

from __future__ import annotations

import random
import subprocess
import time
from typing import Any

import cv2
import numpy as np

from . import vision
from .browser_bot import Stats
from .solver import SolveError, looks_like_math, solve


class AdbError(RuntimeError):
    pass


class AndroidDevice:
    def __init__(self, serial: str | None = None):
        self.serial = serial
        self._check()

    def _cmd(self, *args: str) -> list[str]:
        base = ["adb"]
        if self.serial:
            base += ["-s", self.serial]
        return base + list(args)

    def _check(self) -> None:
        try:
            result = subprocess.run(
                self._cmd("get-state"), capture_output=True, text=True, timeout=10
            )
        except FileNotFoundError as exc:
            raise AdbError("adb not found — install with: brew install android-platform-tools") from exc
        if result.returncode != 0 or "device" not in result.stdout:
            raise AdbError(
                f"no device ready (adb said: {result.stdout.strip() or result.stderr.strip()}). "
                "Plug in over USB, enable USB debugging, accept the prompt on the phone."
            )

    def screenshot(self) -> np.ndarray:
        result = subprocess.run(self._cmd("exec-out", "screencap", "-p"), capture_output=True, timeout=15)
        if result.returncode != 0 or not result.stdout:
            raise AdbError(f"screencap failed: {result.stderr.decode(errors='replace')}")
        image = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise AdbError("could not decode the screenshot")
        return image

    def tap(self, x: int, y: int) -> None:
        subprocess.run(self._cmd("shell", "input", "tap", str(int(x)), str(int(y))), timeout=10)


class MatiksAndroidBot:
    def __init__(self, config: dict[str, Any], serial: str | None = None,
                 dry_run: bool = False, verbose: bool = True):
        self.config = config
        self.android = config["android"]
        self.dry_run = dry_run
        self.verbose = verbose
        self.device = AndroidDevice(serial)
        self.stats = Stats()
        self._last_question: str | None = None
        self._last_region: np.ndarray | None = None
        self._last_answer_at = 0.0

        if not self.android.get("question_region"):
            raise RuntimeError("Run `python -m matiks_bot.cli calibrate` first — no question region set.")
        if not self.android.get("keys"):
            raise RuntimeError("Run `python -m matiks_bot.cli calibrate` first — no keypad mapped.")

        tess = self.android.get("tesseract_cmd")
        if tess:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = tess

    def log(self, message: str) -> None:
        if self.verbose:
            print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def type_answer(self, answer: str) -> None:
        keys = self.android["keys"]
        for char in answer:
            key = "minus" if char == "-" else ("dot" if char == "." else char)
            position = keys.get(key)
            if not position:
                self.log(f"no calibrated key for {char!r}; skipping this answer")
                return
            self.device.tap(*position)
            time.sleep(random.uniform(0.03, 0.08))
        if submit := keys.get("submit"):
            self.device.tap(*submit)
        self._last_answer_at = time.monotonic()

    def _pace(self) -> None:
        pacing = self.config["pacing"]
        min_gap = 60.0 / max(pacing["target_answers_per_min"], 1)
        since = time.monotonic() - self._last_answer_at
        delay = max(min_gap - since, pacing["min_reaction_ms"] / 1000)
        time.sleep(delay + random.uniform(0, pacing["jitter_ms"] / 1000))

    def run(self) -> Stats:
        region = self.android["question_region"]
        poll_s = self.config["pacing"]["poll_interval_ms"] / 1000
        max_minutes = self.config["run"]["max_minutes"]

        self.log("running — Ctrl-C to stop")
        while True:
            if max_minutes and self.stats.elapsed_min() >= max_minutes:
                break

            patch = vision.crop(self.device.screenshot(), region)
            if not vision.frame_changed(self._last_region, patch):
                time.sleep(poll_s)
                continue
            self._last_region = patch

            text = vision.read_text(patch)
            if not looks_like_math(text) or text == self._last_question:
                time.sleep(poll_s)
                continue

            self._last_question = text
            try:
                answer = solve(text)
            except SolveError as exc:
                self.stats.solve_failures += 1
                self.log(f"UNSOLVED (OCR read {text!r}): {exc}")
                continue

            self._pace()
            if self.dry_run:
                self.log(f"[dry-run] {text}  ->  {answer}")
            else:
                self.type_answer(answer)
                self.log(f"{text}  ->  {answer}")
            self.stats.answered += 1

        return self.stats
