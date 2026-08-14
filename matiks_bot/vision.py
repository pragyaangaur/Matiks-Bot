"""OCR path — only used by the Android fallback.

This exists because reading pixels is strictly worse than reading the DOM: it
needs calibration, it misreads glyphs, and it costs ~100ms per frame. Use
browser_bot unless you specifically need the phone.
"""

from __future__ import annotations

import re

import cv2
import numpy as np

# Tesseract happily turns "7" into "?" unless you constrain the alphabet.
_WHITELIST = "0123456789+-*/^()=?.%!xX×÷√"
_TESS_CONFIG = f"--psm 7 -c tessedit_char_whitelist={_WHITELIST}"

# Common OCR confusions in this narrow alphabet, applied only where a digit or
# operator is required by context.
_FIXUPS = [
    (re.compile(r"(?<=\d)[oO](?=\d)"), "0"),
    (re.compile(r"(?<=\d)[lI|](?=\d)"), "1"),
    (re.compile(r"[—–−]"), "-"),
    (re.compile(r"\s{2,}"), " "),
]


def crop(image: np.ndarray, region: list[int] | None) -> np.ndarray:
    if not region:
        return image
    x, y, w, h = region
    return image[max(y, 0) : y + h, max(x, 0) : x + w]


def preprocess(image: np.ndarray, upscale: int = 3) -> np.ndarray:
    """Normalize to dark text on white, big enough for Tesseract to be happy."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray = cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.bilateralFilter(gray, 7, 50, 50)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Matiks renders light text on a dark card; Tesseract wants the inverse.
    if np.mean(binary) < 127:
        binary = cv2.bitwise_not(binary)
    return cv2.copyMakeBorder(binary, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)


def read_text(image: np.ndarray) -> str:
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError(
            "The Android path needs OCR:\n"
            "  brew install tesseract\n"
            "  pip install pytesseract"
        ) from exc

    raw = pytesseract.image_to_string(preprocess(image), config=_TESS_CONFIG)
    text = raw.strip().replace("\n", " ")
    for pattern, replacement in _FIXUPS:
        text = pattern.sub(replacement, text)
    return text.strip()


def frame_changed(previous: np.ndarray | None, current: np.ndarray, threshold: float = 3.0) -> bool:
    """Skip OCR when the question region hasn't visibly changed."""
    if previous is None or previous.shape != current.shape:
        return True
    return float(np.mean(cv2.absdiff(previous, current))) > threshold
