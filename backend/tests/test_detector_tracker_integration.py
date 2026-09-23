"""Fake-only tests for DetectorService -> TrackerService fan-out."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from app.models.detection import Detection
from app.models.domain import DomainName, DomainSnapshot, DomainState
from app.models.tracking import TrackedDetection
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


class FakeTrigger:
    def __init__(self, gate=None, raises: bool = False) -> None:
        self.started: list[str] = []
        self.ended: list[str] = []
        self.observations = []
        self.gate = gate
        self.raises = raises
        self.claim_called = False

    def start_session(self, session_id: str) -> None:
        self.started.append(session_id)

    def observe(self, domain, snapshot) -> None:
        if self.gate is not None:
            assert self.gate.active == 0
        self.observations.append((domain, snapshot))
        if self.raises:
            raise RuntimeError("fake trigger failure")

    def claim_next_candidate(self, *_args) -> None:
        self.claim_called = True
        raise AssertionError("must not be dispatched during integration")

    def end_session(self, session_id: str) -> None:
        self.ended.append(session_id)

    def close(self) -> None:
        pass


class SnapshotTracker(FakeTracker):
    def observe(self, frame):
        super().observe(frame)
        return tuple(
            TrackedDetection(
                track_id=index,
                class_id=item.class_id,
                class_name=item.class_name,
                confidence=item.confidence,
                xyxy=item.xyxy,
                center=((item.xyxy[0] + item.xyxy[2]) / 2, (item.xyxy[1] + item.xyxy[3]) / 2),
                source_session_id=frame.source_session_id,
                source_frame_seq=frame.source_frame_seq,
                source_timestamp=frame.source_timestamp,
                confirmed=True,
                hit_count=2,
            )
            for index, item in enumerate(frame.detections)
        )


def _parking_snapshot(session: str, state: DomainState = DomainState.LOCKED, domain: DomainName = DomainName.PARKING_MOTORCYCLE) -> DomainSnapshot:
    return DomainSnapshot(session, state, domain, 3, 3, 1.0, state is DomainState.LOCKED, 2)


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


def test_trigger_receives_post_domain_post_tracker_metadata_without_images() -> None:
    gate = FakeGate()
    tracker = SnapshotTracker(gate)
    trigger = FakeTrigger(gate)
    order: list[str] = []

    class Consumer:
        def start_session(self, _session: str) -> None: pass
        def end_session(self, _session: str) -> None: pass
        def observe(self, _result) -> None: order.append("domain-observe")

    def provider() -> DomainSnapshot:
        assert order == ["domain-observe"]
        order.append("snapshot-read")
        return _parking_snapshot("session")

    detector = DetectorService(
        model_factory=lambda: _detections,
        result_consumer=Consumer(),
        tracker=tracker,
        parking_trigger=trigger,
        domain_snapshot_provider=provider,
        cuda_gate=gate,
    )
    try:
        detector.start_session("session")
        detector.offer(_packet("session"))
        assert _wait_for(lambda: len(trigger.observations) == 1)
        domain, snapshot = trigger.observations[0]
        assert trigger.started == ["session"]
        assert domain.domain is DomainName.PARKING_MOTORCYCLE
        assert (snapshot.source_session_id, snapshot.source_frame_seq, snapshot.source_timestamp) == ("session", 7, 12.5)
        assert (snapshot.frame_width, snapshot.frame_height) == (80, 48)
        assert [track.class_name for track in snapshot.tracks] == ["person", "motorcycle", "truck"]
        assert not hasattr(snapshot, "frame") and not hasattr(snapshot, "jpeg")
        assert order == ["domain-observe", "snapshot-read"]
        assert not trigger.claim_called
    finally:
        detector.close()


def test_same_result_yolo_domain_lock_is_visible_to_trigger() -> None:
    from app.services.domain_service import DomainService

    domain = DomainService()
    tracker = SnapshotTracker()
    trigger = FakeTrigger()
    detector = DetectorService(
        model_factory=lambda: lambda _frame: [Detection(3, "motorcycle", 0.9, (1, 1, 20, 20))],
        result_consumer=domain,
        tracker=tracker,
        parking_trigger=trigger,
        domain_snapshot_provider=domain.get_snapshot,
    )
    try:
        detector.start_session("session")
        for seq in range(3):
            detector.offer(_packet("session", seq))
            assert _wait_for(lambda: len(trigger.observations) > seq)
        assert trigger.observations[-1][0].domain is DomainName.PARKING_MOTORCYCLE
        assert trigger.observations[-1][0].state is DomainState.LOCKED
    finally:
        detector.close()


def test_trigger_failure_and_provider_failure_do_not_break_detection_or_tracker() -> None:
    tracker = SnapshotTracker()
    trigger = FakeTrigger(raises=True)
    detector = DetectorService(
        model_factory=lambda: _detections,
        tracker=tracker,
        parking_trigger=trigger,
        domain_snapshot_provider=lambda: _parking_snapshot("session"),
    )
    try:
        detector.start_session("session")
        detector.offer(_packet("session"))
        assert _wait_for(lambda: detector.get_latest_result() is not None)
        assert _wait_for(lambda: tracker.frames and trigger.observations)
        assert detector.get_latest_result() is not None
    finally:
        detector.close()

    provider_tracker = SnapshotTracker()
    provider_trigger = FakeTrigger()
    provider_detector = DetectorService(
        model_factory=lambda: _detections,
        tracker=provider_tracker,
        parking_trigger=provider_trigger,
        domain_snapshot_provider=lambda: (_ for _ in ()).throw(RuntimeError("provider failure")),
    )
    try:
        provider_detector.start_session("session")
        provider_detector.offer(_packet("session"))
        assert _wait_for(lambda: provider_detector.get_latest_result() is not None)
        assert _wait_for(lambda: provider_tracker.frames)
        assert provider_trigger.observations == []
    finally:
        provider_detector.close()


def test_trigger_lifecycle_follows_replacement_and_failed_video_start(monkeypatch) -> None:
    tracker = SnapshotTracker()
    trigger = FakeTrigger()
    detector = DetectorService(model_factory=lambda: _detections, tracker=tracker, parking_trigger=trigger)
    try:
        detector.start_session("one")
        detector.start_session("two")
        assert trigger.started == ["one", "two"]
        assert trigger.ended == ["one"]
        detector.end_session("two")
        assert trigger.ended == ["one", "two"]
    finally:
        detector.close()

    failed_tracker = SnapshotTracker()
    failed_trigger = FakeTrigger()
    failed_detector = DetectorService(model_factory=lambda: _detections, tracker=failed_tracker, parking_trigger=failed_trigger)
    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", lambda *_args, **_kwargs: FakeCapture(opened=False))
    failed_video = VideoService(detector=failed_detector)
    try:
        assert failed_video.start_webcam()
        assert failed_trigger.ended == failed_trigger.started
    finally:
        failed_video.stop()
        failed_detector.close()


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


def test_runtime_shutdown_closes_detector_before_trigger_and_tracker() -> None:
    from app.services.runtime import shutdown_services

    calls: list[str] = []

    class Service:
        def __init__(self, name: str) -> None:
            self.name = name

        def stop(self) -> None:
            calls.append(f"{self.name}.stop")

        def close(self) -> None:
            calls.append(f"{self.name}.close")

    shutdown_services(Service("video"), Service("scene"), Service("detector"), Service("trigger"), Service("tracker"))
    assert calls == ["video.stop", "scene.close", "detector.close", "trigger.close", "tracker.close"]
