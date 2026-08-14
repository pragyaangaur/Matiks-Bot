"""Interactive calibration for the Android fallback.

Opens a live screenshot of the phone. You drag a box around the question, then
click each keypad button as prompted. Results are written back to config.yaml.
"""

from __future__ import annotations

from typing import Any

import cv2

from .android_bot import AndroidDevice
from .config import save_config

KEY_ORDER = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "minus", "dot", "submit"]
OPTIONAL_KEYS = {"minus", "dot", "submit"}


def _select_region(image) -> list[int]:
    print("\nDrag a box around the QUESTION text, then press ENTER (or 'c' to cancel).")
    x, y, w, h = cv2.selectROI("matiks calibrate", image, showCrosshair=True)
    cv2.destroyAllWindows()
    if w == 0 or h == 0:
        raise SystemExit("no region selected")
    return [int(x), int(y), int(w), int(h)]


def _collect_keys(image) -> dict[str, list[int]]:
    keys: dict[str, list[int]] = {}
    clicked: list[tuple[int, int]] = []

    def on_mouse(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked.append((x, y))

    window = "matiks calibrate — click each key"
    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_mouse)

    print("\nClick the on-screen key for each prompt. Press 's' to skip an optional key, 'q' to finish.")
    for key in KEY_ORDER:
        hint = " (optional — 's' to skip)" if key in OPTIONAL_KEYS else ""
        print(f"  click: {key}{hint}", flush=True)
        clicked.clear()
        while True:
            preview = image.copy()
            for name, (kx, ky) in keys.items():
                cv2.circle(preview, (kx, ky), 12, (0, 200, 0), 2)
                cv2.putText(preview, name, (kx - 10, ky - 16),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)
            cv2.putText(preview, f"click: {key}", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
            cv2.imshow(window, preview)
            pressed = cv2.waitKey(30) & 0xFF
            if clicked:
                keys[key] = [int(clicked[-1][0]), int(clicked[-1][1])]
                break
            if pressed == ord("s") and key in OPTIONAL_KEYS:
                break
            if pressed == ord("q"):
                cv2.destroyAllWindows()
                return keys
    cv2.destroyAllWindows()
    return keys


def calibrate(config: dict[str, Any], config_path: str, serial: str | None = None) -> dict[str, Any]:
    device = AndroidDevice(serial)
    print("Open Matiks on the phone and get to a screen showing a question + keypad.")
    input("Press ENTER when the screen is ready… ")

    image = device.screenshot()
    config["android"]["question_region"] = _select_region(image)
    config["android"]["keys"] = _collect_keys(image)

    save_config(config, config_path)
    print(f"\nSaved region and {len(config['android']['keys'])} keys to {config_path}")
    return config
