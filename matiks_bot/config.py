"""Config loading with defaults, so a missing key never crashes mid-run."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - surfaced with a real message in cli.py
    yaml = None

DEFAULTS: dict[str, Any] = {
    "browser": {
        "url": "https://matiks.com",
        "profile_dir": ".browser-profile",
        "headless": False,
        "viewport": {"width": 1280, "height": 900},
        "slow_mo_ms": 0,
    },
    "navigation": {
        # Each entry is a list of candidate labels, tried in order. The bot
        # walks this sequence from the home page to a live game.
        "to_game": [
            ["Math", "Maths", "Math Section"],
            ["Sprint Duels", "Sprint Duel", "Duels"],
            ["Play Duel", "Play", "Start Duel"],
        ],
        "play_again": ["Play Again", "New Game", "Rematch", "Play Duel", "Next Game"],
        "dismiss": ["Close", "Skip", "Continue", "OK"],
    },
    "detect": {
        "game_over_text": ["play again", "rematch", "final score", "you won", "you lost", "game over"],
        "waiting_text": ["finding opponent", "waiting for opponent", "matching"],
        # Prompts are big type; timers and "1/6" progress counters are not.
        "min_question_font_px": 22,
    },
    "pacing": {
        # Time-to-answer per question, sampled uniformly from this window and
        # measured from when the prompt appears. A per-question window varies
        # in a way an average rate does not — a rate limit still produces
        # near-identical gaps every time.
        "response_min_s": 0.8,
        "response_max_s": 1.2,
        # Android fallback still paces by rate; it has no per-question signal.
        "target_answers_per_min": 45,
        "min_reaction_ms": 450,
        "jitter_ms": 250,
        "poll_interval_ms": 60,
        "typing_delay_ms": 45,
    },
    "run": {
        "max_games": 0,          # 0 = unlimited
        "max_minutes": 0,        # 0 = unlimited
        "submit_key": "Enter",
        # Matiks' answer box, identified by its placeholder text.
        "answer_selector": 'input[placeholder="Enter answer"]',
        "clear_before_typing": True,
        "stop_after_consecutive_errors": 15,
        # Open-ended runs stop after this long with nothing answered,
        # so a wedged overnight session does not spin until morning.
        "give_up_after_idle_s": 1800,
        # Consecutive loop faults with nothing answered in between
        # before giving up, so a broken page cannot spin all night.
        "max_loop_resumes": 10,
    },
    "android": {
        "question_region": None,   # [x, y, w, h] from calibrate.py
        "keys": {},                # {"0": [x, y], ..., "submit": [x, y]}
        "screencap_scale": 1.0,
        "tesseract_cmd": None,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return copy.deepcopy(DEFAULTS)
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    if yaml is None:
        raise RuntimeError("pyyaml is not installed; run: pip install -r requirements.txt")
    with path.open() as handle:
        loaded = yaml.safe_load(handle) or {}
    return _deep_merge(DEFAULTS, loaded)


def save_config(config: dict[str, Any], path: str | Path) -> None:
    if yaml is None:
        raise RuntimeError("pyyaml is not installed; run: pip install -r requirements.txt")
    with Path(path).open("w") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
