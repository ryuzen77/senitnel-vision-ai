"""Fake-only tests for DetectorService -> TrackerService fan-out."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from app.models.detection import Detection
from app.models.video import FramePacket, VideoState
from app.services.detector_service import DetectorService
from app.services.video_service import VideoService
from tests.conftest import FakeCapture


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _packet(session: str, seq: int = 7) -> FramePacket:
    return FramePacket(session, seq, 12.5, np.zeros((48, 80, 3), dtype=np.uint8), b"jpeg")


def _detections(_frame: np.ndarray) -> list[Detection]:
    return [
        Detection(0, "person", 0.9, (1.0, 2.0, 20.0, 30.0)),
        Detection(3, "motorcycle", 0.8, (30.0, 10.0, 70.0, 40.0)),
        Detection(7, "truck", 0.7, (2.0, 3.0, 4.0, 5.0)),
    ]


class FakeTracker:
    def __init__(self, gate=None, raises: bool = False) -> None:
        self.started: list[str] = []
        self.ended: list[str] = []
        self.frames = []
        self.gate = gate
        self.raises = raises

    def start_session(self, session_id: str) -> None:
        self.started.append(session_id)

    def observe(self, frame) -> tuple[()]:
        if self.gate is not None:
            assert self.gate.active == 0
        self.frames.append(frame)
        if self.raises:
            raise RuntimeError("fake tracker failure")
        return ()

    def end_session(self, session_id: str) -> None:
        self.ended.append(session_id)

    def close(self) -> None:
        pass


class FakeGate:
    def __init__(self) -> None:
        self.active = 0

    def __enter__(self) -> None:
        self.active += 1

    def __exit__(self, *_args) -> None:
        self.active -= 1


def test_tracker_receives_published_result_metadata_without_image_data() -> None:
    gate = FakeGate()
    tracker = FakeTracker(gate)
    detector = DetectorService(model_factory=lambda: _detections, tracker=tracker, cuda_gate=gate)
    try:
        detector.start_session("session")
        detector.offer(_packet("session"))
        assert _wait_for(lambda: len(tracker.frames) == 1)
        frame = tracker.frames[0]
        assert tracker.started == ["session"]
        assert (frame.source_session_id, frame.source_frame_seq, frame.source_timestamp) == ("session", 7, 12.5)
        assert (frame.frame_width, frame.frame_height) == (80, 48)
        assert [(item.class_name, item.xyxy) for item in frame.detections] == [
            ("person", (1.0, 2.0, 20.0, 30.0)),
            ("motorcycle", (30.0, 10.0, 70.0, 40.0)),
            ("truck", (2.0, 3.0, 4.0, 5.0)),
        ]
        assert not hasattr(frame, "frame") and not hasattr(frame, "jpeg")
    finally:
        detector.close()


def test_stale_post_stop_and_wrong_session_results_are_not_sent_to_tracker() -> None:
    entered = threading.Event()
    release = threading.Event()
    tracker = FakeTracker()

    def held_runner(_frame: np.ndarray) -> list[Detection]:
        entered.set()
        release.wait(timeout=1.0)
        return _detections(_frame)

    detector = DetectorService(model_factory=lambda: held_runner, tracker=tracker)
    try:
        detector.start_session("old")
        detector.offer(_packet("old"))
        assert entered.wait(timeout=1.0)
        detector.offer(_packet("wrong"))
        detector.end_session("old")
        release.set()
        time.sleep(0.05)
        assert tracker.frames == []
        assert tracker.ended == ["old"]
    finally:
        release.set()
        detector.close()


def test_tracker_exception_keeps_detection_and_video_running(monkeypatch) -> None:
    tracker = FakeTracker(raises=True)
    detector = DetectorService(model_factory=lambda: _detections, tracker=tracker)
    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture", lambda *_args, **_kwargs: FakeCapture(opened=True)
    )
    service = VideoService(detector=detector)
    try:
        assert service.start_webcam()
        assert _wait_for(lambda: detector.get_latest_result() is not None)
        assert _wait_for(lambda: tracker.frames)
        assert service.get_status().state is VideoState.RUNNING
    finally:
        service.stop()
        detector.close()


def test_video_terminal_paths_end_tracker_session(monkeypatch) -> None:
    tracker = FakeTracker()
    detector = DetectorService(model_factory=lambda: _detections, tracker=tracker)
    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture", lambda *_args, **_kwargs: FakeCapture(opened=True)
    )
    service = VideoService(detector=detector)
    try:
        assert service.start_webcam()
        assert _wait_for(lambda: tracker.frames)
        session = tracker.started[0]
        service.stop()
        assert tracker.ended == [session]
    finally:
        service.stop()
        detector.close()

    for label, reads in (
        ("eof", [(False, None)]),
        ("error", [(False, None)] * 5),
    ):
        terminal_tracker = FakeTracker()
        terminal_detector = DetectorService(model_factory=lambda: _detections, tracker=terminal_tracker)
        monkeypatch.setattr(
            "app.services.video_service.cv2.VideoCapture",
            lambda *_args, reads=reads, **_kwargs: FakeCapture(opened=True, reads=reads),
        )
        terminal_service = VideoService(detector=terminal_detector)
        try:
            if label == "eof":
                token = terminal_service.claim_start()
                assert token is not None
                terminal_service.start_file(Path("fake.mp4"), token)
                assert _wait_for(lambda: terminal_service.get_status().state is VideoState.IDLE)
            else:
                assert terminal_service.start_webcam()
                assert _wait_for(lambda: terminal_service.get_status().state is VideoState.ERROR)
            assert terminal_tracker.ended == terminal_tracker.started
        finally:
            terminal_service.stop()
            terminal_detector.close()


def test_session_replacement_ends_old_tracker_before_starting_new() -> None:
    tracker = FakeTracker()
    detector = DetectorService(model_factory=lambda: _detections, tracker=tracker)
    try:
        detector.start_session("one")
        detector.start_session("two")
        assert tracker.started == ["one", "two"]
        assert tracker.ended == ["one"]
        detector.close()
        assert tracker.ended == ["one", "two"]
    finally:
        detector.close()


def test_runtime_shutdown_closes_detector_before_tracker() -> None:
    from app.services.runtime import shutdown_services

    calls: list[str] = []

    class Service:
        def __init__(self, name: str) -> None:
            self.name = name

        def stop(self) -> None:
            calls.append(f"{self.name}.stop")

        def close(self) -> None:
            calls.append(f"{self.name}.close")

    shutdown_services(Service("video"), Service("scene"), Service("detector"), Service("tracker"))
    assert calls == ["video.stop", "scene.close", "detector.close", "tracker.close"]
