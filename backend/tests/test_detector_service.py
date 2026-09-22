"""CPU-only concurrency tests for DetectorService."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest

from app.models.detection import Detection, DetectionResult
from app.models.video import FramePacket, VideoState
from app.services.detector_service import DetectorService
from app.services.video_service import VideoService
from tests.conftest import FakeCapture


def _packet(session_id: str, seq: int) -> FramePacket:
    frame = np.full((2, 2, 3), seq, dtype=np.uint8)
    return FramePacket(session_id=session_id, seq=seq, ts=float(seq), frame=frame, jpeg=b"jpeg")


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _detections(_: np.ndarray) -> list[Detection]:
    return [Detection(class_id=7, class_name="truck", confidence=0.9, xyxy=(1.0, 2.0, 3.0, 4.0))]


def test_model_factory_is_called_once_across_two_sessions() -> None:
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return _detections

    detector = DetectorService(model_factory=factory)
    try:
        detector.start_session("one")
        detector.offer(_packet("one", 1))
        assert _wait_for(lambda: detector.get_latest_result() is not None)
        detector.end_session("one")

        detector.start_session("two")
        detector.offer(_packet("two", 2))
        assert _wait_for(lambda: (result := detector.get_latest_result()) is not None and result.source_session_id == "two")
        assert calls == 1
    finally:
        detector.close()


def test_offer_is_nonblocking_and_latest_pending_frame_wins() -> None:
    entered = threading.Event()
    release = threading.Event()
    processed: list[int] = []

    def runner(frame: np.ndarray) -> list[Detection]:
        processed.append(int(frame[0, 0, 0]))
        entered.set()
        release.wait(timeout=2.0)
        return _detections(frame)

    detector = DetectorService(model_factory=lambda: runner)
    try:
        detector.start_session("session")
        detector.offer(_packet("session", 0))
        assert entered.wait(timeout=2.0)

        start = time.monotonic()
        detector.offer(_packet("session", 1))
        detector.offer(_packet("session", 2))
        assert time.monotonic() - start < 0.05

        release.set()
        assert _wait_for(lambda: len(processed) >= 2)
        assert processed == [0, 2]  # seq=1 was overwritten in the one-slot buffer.
        assert _wait_for(lambda: (result := detector.get_latest_result()) is not None and result.source_frame_seq == 2)
    finally:
        release.set()
        detector.close()


def test_one_frame_is_not_inferred_twice_and_result_contract_is_complete() -> None:
    calls = 0

    def runner(frame: np.ndarray) -> list[Detection]:
        nonlocal calls
        calls += 1
        frame[:, :, :] = 255  # Mutation must apply only to DetectorService's copy.
        return _detections(frame)

    packet = _packet("session", 42)
    original = packet.frame.copy()
    detector = DetectorService(model_factory=lambda: runner)
    try:
        detector.start_session("session")
        detector.offer(packet)
        assert _wait_for(lambda: detector.get_latest_result() is not None)
        time.sleep(0.05)
        result = detector.get_latest_result()
        assert result is not None
        assert calls == 1
        assert result.detection_seq == 0
        assert result.source_session_id == "session"
        assert result.source_frame_seq == 42
        assert result.source_timestamp == 42.0
        assert result.detections == [Detection(7, "truck", 0.9, (1.0, 2.0, 3.0, 4.0))]
        assert np.array_equal(packet.frame, original)
    finally:
        detector.close()


def test_old_and_post_stop_results_are_discarded() -> None:
    entered = threading.Event()
    release = threading.Event()

    def runner(frame: np.ndarray) -> list[Detection]:
        entered.set()
        release.wait(timeout=2.0)
        return _detections(frame)

    detector = DetectorService(model_factory=lambda: runner)
    try:
        detector.start_session("old")
        detector.offer(_packet("old", 1))
        assert entered.wait(timeout=2.0)
        detector.end_session("old")
        detector.start_session("new")
        release.set()
        time.sleep(0.1)
        assert detector.get_latest_result() is None
    finally:
        release.set()
        detector.close()


def test_inference_exception_does_not_change_video_state(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_runner(_: np.ndarray) -> list[Detection]:
        raise RuntimeError("fake inference failure")

    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture",
        lambda source=0, **_kwargs: FakeCapture(source, opened=True),
    )
    detector = DetectorService(model_factory=lambda: failing_runner)
    service = VideoService(detector=detector)
    try:
        assert service.start_webcam() is True
        assert _wait_for(lambda: service.get_status().state == VideoState.RUNNING)
        assert _wait_for(lambda: service.get_latest_packet() is not None)
        time.sleep(0.05)
        assert service.get_status().state == VideoState.RUNNING
        assert detector.get_latest_result() is None
    finally:
        service.stop()
        detector.close()


def test_end_session_clears_pending_and_latest_result() -> None:
    detector = DetectorService(model_factory=lambda: _detections)
    try:
        detector.start_session("session")
        detector.offer(_packet("session", 3))
        assert _wait_for(lambda: detector.get_latest_result() is not None)
        detector.end_session("session")
        assert detector.get_latest_result() is None
    finally:
        detector.close()


def test_video_stop_eof_and_error_end_detector_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every VideoService terminal path clears detector data for its session."""
    blank = np.zeros((2, 2, 3), dtype=np.uint8)

    # Explicit stop clears an already-published result.
    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture",
        lambda source=0, **_kwargs: FakeCapture(source, opened=True),
    )
    detector = DetectorService(model_factory=lambda: _detections)
    service = VideoService(detector=detector)
    try:
        service.start_webcam()
        assert _wait_for(lambda: detector.get_latest_result() is not None)
        service.stop()
        assert detector.get_latest_result() is None
    finally:
        service.stop()
        detector.close()

    # EOF and webcam read error both invoke the same VideoService cleanup path.
    eof_detector = DetectorService(model_factory=lambda: _detections)
    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture",
        lambda source=0, **_kwargs: FakeCapture(source, opened=True, reads=[(True, blank), (False, None)]),
    )
    eof_service = VideoService(detector=eof_detector)
    try:
        token = eof_service.claim_start()
        assert token is not None
        eof_service.start_file(Path("/fake/video.mp4"), token)
        assert _wait_for(lambda: eof_service.get_status().state == VideoState.IDLE)
        assert eof_detector.get_latest_result() is None
    finally:
        eof_service.stop()
        eof_detector.close()

    error_detector = DetectorService(model_factory=lambda: _detections)
    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture",
        lambda source=0, **_kwargs: FakeCapture(
            source, opened=True, reads=[(True, blank)] + [(False, None)] * 5
        ),
    )
    error_service = VideoService(detector=error_detector)
    try:
        error_service.start_webcam()
        assert _wait_for(lambda: error_service.get_status().state == VideoState.ERROR)
        assert error_detector.get_latest_result() is None
    finally:
        error_service.stop()
        error_detector.close()


def test_close_stops_worker_cleanly() -> None:
    detector = DetectorService(model_factory=lambda: _detections)
    detector.close()
    assert not detector._worker.is_alive()  # noqa: SLF001


def test_result_consumer_receives_only_published_active_session_results() -> None:
    class Consumer:
        def __init__(self) -> None:
            self.started: list[str] = []
            self.ended: list[str] = []
            self.results: list[DetectionResult] = []

        def start_session(self, session_id: str) -> None:
            self.started.append(session_id)

        def observe(self, result: DetectionResult) -> None:
            self.results.append(result)

        def end_session(self, session_id: str) -> None:
            self.ended.append(session_id)

    entered = threading.Event()
    release = threading.Event()

    def runner(frame: np.ndarray) -> list[Detection]:
        if int(frame[0, 0, 0]) == 2:
            entered.set()
            release.wait(timeout=2.0)
        return _detections(frame)

    consumer = Consumer()
    detector = DetectorService(model_factory=lambda: runner, result_consumer=consumer)
    try:
        detector.start_session("valid")
        detector.offer(_packet("valid", 1))
        assert _wait_for(lambda: len(consumer.results) == 1)
        assert consumer.results[0].source_session_id == "valid"

        detector.start_session("stale")
        detector.offer(_packet("stale", 2))
        assert entered.wait(timeout=2.0)
        detector.end_session("stale")
        release.set()
        time.sleep(0.05)
        assert len(consumer.results) == 1
        assert consumer.started == ["valid", "stale"]
        assert consumer.ended == ["stale"]
    finally:
        release.set()
        detector.close()
