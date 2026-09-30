"""
Shared pytest fixtures for Sentinel Vision AI backend tests.

FakeCapture mimics cv2.VideoCapture without requiring a physical webcam or
real video file.  All tests monkeypatch `app.services.video_service.cv2`
so that any call to cv2.VideoCapture returns a FakeCapture instance.
"""

from __future__ import annotations

import os
import threading
from typing import Generator
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Production singletons are imported during test collection. Never let a
# developer's Langflow credentials activate external evaluation in pytest.
for _config_key in (
    "SENTINEL_LANGFLOW_URL",
    "SENTINEL_LANGFLOW_API_KEY",
    "SENTINEL_LANGFLOW_TIMEOUT_SECONDS",
    "SENTINEL_LANGFLOW_MODEL",
    "SENTINEL_LANGFLOW_COMPONENT_ID",
    "SENTINEL_CALLBACK_TOKEN",
):
    os.environ.pop(_config_key, None)

from app.services.video_service import VideoService


# ---------------------------------------------------------------------------
# FakeCapture
# ---------------------------------------------------------------------------


class FakeCapture:
    """
    Minimal stub for cv2.VideoCapture.

    Parameters
    ----------
    source
        Accepts int (webcam index) or str (file path) — stored but not used.
    opened
        Controls the return value of isOpened().
    reads
        Optional finite list of (ret, frame) tuples returned by read() in
        order.  Once the list is exhausted, subsequent read() calls return
        (False, None).  If None, read() returns an infinite stream of valid
        480×640×3 uint8 frames.
    """

    _BLANK_FRAME = np.zeros((480, 640, 3), dtype=np.uint8)

    def __init__(
        self,
        source: int | str = 0,
        *,
        opened: bool = True,
        reads: list[tuple[bool, np.ndarray | None]] | None = None,
    ) -> None:
        self._source = source
        self._opened = opened
        self._reads = reads
        self._read_index = 0
        self.release_call_count = 0

    def isOpened(self) -> bool:  # noqa: N802
        return self._opened

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._reads is not None:
            if self._read_index < len(self._reads):
                result = self._reads[self._read_index]
                self._read_index += 1
                return result
            return (False, None)
        # Infinite valid frames
        return (True, self._BLANK_FRAME.copy())

    def get(self, prop: int) -> float:  # noqa: ARG002
        # cv2.CAP_PROP_FPS == 5
        import cv2  # local import to avoid early import issues in tests

        if prop == cv2.CAP_PROP_FPS:
            return 30.0
        return 0.0

    def release(self) -> None:
        self.release_call_count += 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fresh_service(monkeypatch: pytest.MonkeyPatch) -> Generator[VideoService, None, None]:
    """
    Returns a new VideoService() with FakeCapture (opened=True, infinite reads)
    injected.  The service is stopped after the test to release any threads.
    """
    fake_instances: list[FakeCapture] = []

    def fake_cap_factory(source: int | str = 0, **_kwargs: object) -> FakeCapture:
        cap = FakeCapture(source, opened=True)
        fake_instances.append(cap)
        return cap

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", fake_cap_factory)

    service = VideoService()
    yield service
    service.stop()


@pytest.fixture()
def fake_cap_factory(monkeypatch: pytest.MonkeyPatch):
    """
    Returns a callable that, when invoked, installs a custom FakeCapture
    into a fresh VideoService.

    Usage:
        svc, cap = fake_cap_factory(opened=False)
    """
    created: list[FakeCapture] = []

    def factory(
        *,
        opened: bool = True,
        reads: list[tuple[bool, np.ndarray | None]] | None = None,
    ) -> tuple[VideoService, list[FakeCapture]]:
        def make_cap(source: int | str = 0, **_kw: object) -> FakeCapture:
            cap = FakeCapture(source, opened=opened, reads=reads)
            created.append(cap)
            return cap

        monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", make_cap)
        svc = VideoService()
        return svc, created

    yield factory

    # Cleanup: stop any running services
    # (tests own their service objects, but belt-and-suspenders)
