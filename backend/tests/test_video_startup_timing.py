"""Synthetic upload-to-preview timing tests; no real video, model, or network."""

from __future__ import annotations

import asyncio
import io
import re
import threading
import time

import numpy as np
import pytest
from starlette.datastructures import Headers, UploadFile

from app.api import video as video_api
from app.models.video import VideoState
from app.services.video_service import VideoService
from tests.conftest import FakeCapture


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.005)
    return predicate()


def test_milestones_are_once_per_session_and_reset_on_stop(monkeypatch) -> None:
    service = VideoService(startup_timing_enabled=True)
    records: list[str] = []

    def record_log(message, *values) -> None:
        assert service._lock.acquire(blocking=False)
        service._lock.release()
        assert service._packet_cond.acquire(blocking=False)
        service._packet_cond.release()
        recorder = service._startup_timing
        assert recorder is not None and recorder._lock.acquire(blocking=False)
        recorder._lock.release()
        records.append(message % values)

    monkeypatch.setattr("app.services.video_service.logger.info", record_log)
    first = service.claim_start()
    assert first is not None
    first_session = service._session_id
    service.record_startup_milestone("upload_handler_entered", token=first, at=100)
    service.record_startup_milestone("upload_copy_completed", token=first, at=101)
    service.record_startup_milestone("upload_copy_completed", token=first, at=102)
    service.record_startup_milestone("first_packet_published", session_id="wrong", at=103)
    assert len(records) == 2
    assert "elapsed_ms=0.000" in records[0]
    assert "elapsed_ms=1000.000" in records[1]
    assert all(re.fullmatch(
        r"video_startup_timing session=[0-9a-f]{8} milestone=[a-z_]+ elapsed_ms=\d+\.\d{3}",
        item,
    ) for item in records)
    service.stop()
    assert service._startup_timing is None
    service.record_startup_milestone("first_packet_published", session_id=first_session, at=104)
    assert len(records) == 2

    second = service.claim_start()
    assert second is not None and service._session_id != first_session
    service.record_startup_milestone("upload_handler_entered", token=second, at=200)
    service.record_startup_milestone("upload_copy_completed", token=first, at=201)
    assert len(records) == 3 and "elapsed_ms=0.000" in records[-1]
    assert records[-1].split("session=", 1)[1][:8] != records[0].split("session=", 1)[1][:8]
    service.stop()
    assert service._startup_timing is None


def test_synthetic_upload_to_first_mjpeg_packet_logs_six_safe_milestones(monkeypatch, tmp_path) -> None:
    service = VideoService(startup_timing_enabled=True)
    monkeypatch.setattr(video_api, "video_service", service)
    monkeypatch.setattr(video_api, "UPLOAD_DIR", tmp_path)
    records: list[str] = []
    monkeypatch.setattr(
        "app.services.video_service.logger.info",
        lambda message, *values: records.append(message % values),
    )
    release = threading.Event()
    opened = []

    class SyntheticCapture(FakeCapture):
        def __init__(self, source):
            super().__init__(source, opened=True)
            self._read_once = False

        def read(self):
            if not self._read_once:
                self._read_once = True
                return True, np.zeros((24, 32, 3), dtype=np.uint8)
            release.wait(2)
            return False, None

    def open_capture(source):
        cap = SyntheticCapture(source)
        opened.append(cap)
        return cap

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", open_capture)
    monkeypatch.setattr(
        "app.services.video_service.cv2.imencode",
        lambda *_args: (True, np.frombuffer(b"\xff\xd8synthetic\xff\xd9", dtype=np.uint8)),
    )

    async def exercise() -> None:
        upload = UploadFile(
            file=io.BytesIO(b"private-upload-content"), filename="private-file.mp4",
            headers=Headers({"content-type": "video/mp4"}),
        )
        result = await video_api.upload_video(upload)
        assert result.state is VideoState.RUNNING
        for _ in range(100):
            if service.get_latest_packet() is not None and len(records) >= 5:
                break
            await asyncio.sleep(.005)
        assert service.get_latest_packet() is not None
        response = await video_api.stream_video()
        first_chunk = await anext(response.body_iterator)
        assert first_chunk.startswith(b"--frame\r\n")
        await response.body_iterator.aclose()

    try:
        asyncio.run(exercise())
        assert len(opened) == 2  # Probe and playback still open separately.
        milestones = [item.split("milestone=", 1)[1].split()[0] for item in records]
        assert len(records) == 6 and set(milestones) == {
            "upload_handler_entered", "upload_copy_completed", "first_opencv_probe_completed",
            "state_running", "first_packet_published", "first_mjpeg_packet_yielded",
        }
        assert all(secret not in "\n".join(records) for secret in (
            "private-file", "private-upload", str(tmp_path), "synthetic", "token",
        ))
    finally:
        release.set()
        service.stop()
    assert service._startup_timing is None


@pytest.mark.parametrize("outcome", ["eof", "error", "failed_start"])
def test_terminal_paths_discard_timing_state(monkeypatch, tmp_path, outcome) -> None:
    service = VideoService(startup_timing_enabled=True)

    class TerminalCapture(FakeCapture):
        def __init__(self, source):
            super().__init__(source, opened=outcome != "failed_start")

        def read(self):
            if outcome == "error":
                raise RuntimeError("synthetic read failure")
            return False, None

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", TerminalCapture)
    token = service.claim_start()
    assert token is not None
    service.record_startup_milestone("upload_handler_entered", token=token)
    service.start_file(tmp_path / "synthetic.mp4", token)
    expected = VideoState.ERROR if outcome != "eof" else VideoState.IDLE
    assert _wait_for(lambda: service.get_status().state is expected)
    assert service._startup_timing is None
    service.stop()
    assert service._startup_timing is None


@pytest.mark.parametrize("configured,enabled", [
    ("true", True), ("false", False), ("1", False), ("TRUE", False),
    ("invalid-private-value", False),
])
def test_feature_flag_is_strict_and_invalid_value_is_not_logged(
    monkeypatch, caplog, configured, enabled,
) -> None:
    monkeypatch.setenv("SENTINEL_VIDEO_STARTUP_TIMING_ENABLED", configured)
    service = VideoService()
    assert service._startup_timing_enabled is enabled
    if configured not in ("true", "false"):
        assert "Video startup timing disabled: invalid configuration" in caplog.text
    if configured == "invalid-private-value":
        assert configured not in caplog.text


def test_timing_logger_failure_does_not_change_video_session(monkeypatch) -> None:
    service = VideoService(startup_timing_enabled=True)
    monkeypatch.setattr(
        "app.services.video_service.logger.info",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic logging failure")),
    )
    token = service.claim_start()
    assert token is not None
    service.record_startup_milestone("upload_handler_entered", token=token)
    assert service.get_status().state is VideoState.STARTING
    service.stop()
    assert service.get_status().state is VideoState.IDLE
