"""One decoder for prepared files (P1 plan §6, §16.2 item 4 and 11).

These functions never own a capture: ``create_capture`` only constructs it and returns
it, so the caller gives it an owner (VideoService's session; for the P1c preview the
store) before any other call; ``probe`` and ``read_first_frame`` only use a capture
their caller already owns and never release it. No resize, crop or rotation here: the
analysed frames and the preview are the decoded frames.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2

DEFAULT_FILE_FPS: float = 25.0


class CaptureUnavailable(Exception):
    """The capture was created but did not open (``isOpened()`` is False)."""


def create_capture(path: Path):
    """Construct the capture for ``path``; nothing else is called on it here."""
    return cv2.VideoCapture(str(path))


def probe(capture) -> float:
    """Raise ``CaptureUnavailable`` unless the capture opened; return its frame rate
    (``DEFAULT_FILE_FPS`` when the container reports none)."""
    if not capture.isOpened():
        raise CaptureUnavailable()
    fps = capture.get(cv2.CAP_PROP_FPS)
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        return DEFAULT_FILE_FPS
    return float(fps)


def read_first_frame(capture):
    """The first decodable frame of an owned, opened capture, or None."""
    ok, frame = capture.read()
    return frame if ok else None
