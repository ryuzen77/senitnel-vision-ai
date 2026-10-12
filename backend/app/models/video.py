"""
Sentinel Vision AI — Video domain models.

FramePacket is a pure-Python dataclass used internally by VideoService.
It is never serialised to JSON directly.

Pydantic models (VideoStatusResponse, VideoStartResponse) are the wire types
returned by the API layer.
"""

from __future__ import annotations

import dataclasses
import enum

import numpy as np
from pydantic import BaseModel


class VideoState(str, enum.Enum):
    """Lifecycle states for the video capture service."""

    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


@dataclasses.dataclass
class FramePacket:
    """
    A single captured video frame together with its metadata.

    seq   : Monotonically increasing counter; starts at 0, increments every
            frame within a session.  Never resets mid-session.
    ts    : time.monotonic() timestamp recorded at the moment of capture.
    frame : Raw BGR numpy array as returned by cv2.VideoCapture.read().
            Retained for future processing hooks (e.g. YOLO, tamper monitor).
    jpeg  : JPEG-encoded bytes of the frame ready for MJPEG delivery.
    """

    session_id: str
    seq: int
    ts: float
    frame: np.ndarray
    jpeg: bytes


# ---------------------------------------------------------------------------
# Pydantic wire models
# ---------------------------------------------------------------------------


class VideoStatusResponse(BaseModel):
    state: VideoState
    source: str | None = None
    error: str | None = None
    # Additive (P1c, D6): the current reservation's public name and session id, read in
    # the same lock section as ``state``; null without a reservation or once IDLE.
    session_ref: str | None = None
    session_id: str | None = None


class VideoStartResponse(BaseModel):
    state: VideoState
    message: str
