"""Fake-only composition and lifecycle tests for the dual video consumers."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from app.models.detection import Detection
from app.models.video import FramePacket, VideoState
from app.services.detector_service import DetectorService
from app.services.domain_service import DomainService
from app.services.scene_domain_classifier import SceneDomainClassifier, SceneFrameScores
from app.services.video_service import VideoService
from tests.conftest import FakeCapture


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class RecordingConsumer:
    def __init__(self, fail_offer: bool = False) -> None:
        self.started: list[str] = []
        self.offered: list[FramePacket] = []
        self.ended: list[str] = []
        self.fail_offer = fail_offer

    def start_session(self, session_id: str) -> None:
        self.started.append(session_id)

    def offer(self, packet: FramePacket) -> None:
        self.offered.append(packet)
        if self.fail_offer:
            raise RuntimeError("fake scene failure")

    def end_session(self, session_id: str) -> None:
        self.ended.append(session_id)


def test_video_service_starts_offers_and_ends_both_consumers(monkeypatch) -> None:
    detector = RecordingConsumer()
    scene = RecordingConsumer()
    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture", lambda *_args, **_kwargs: FakeCapture(opened=True)
    )
    service = VideoService(detector=detector, scene_classifier=scene)
    try:
        assert service.start_webcam()
        assert _wait_for(lambda: detector.offered and scene.offered)
        assert detector.started == scene.started
        assert detector.offered[0] is scene.offered[0]
        service.stop()
        assert detector.ended == scene.ended == detector.started
    finally:
        service.stop()


def test_optional_consumers_and_failed_start_have_safe_lifecycle(monkeypatch) -> None:
    detector = RecordingConsumer()
    scene = RecordingConsumer()
    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture", lambda *_args, **_kwargs: FakeCapture(opened=False)
    )
    service = VideoService(detector=detector, scene_classifier=scene)
    assert service.start_webcam()
    assert service.get_status().state is VideoState.ERROR
    service.stop()
    assert detector.started == scene.started
    assert detector.ended == scene.ended == detector.started
    # Each optional dependency can also be absent without changing start/offer/end.
    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture", lambda *_args, **_kwargs: FakeCapture(opened=True)
    )
    detector_only = RecordingConsumer()
    detector_only_service = VideoService(detector=detector_only)
    assert detector_only_service.start_webcam()
    assert _wait_for(lambda: detector_only.offered)
    detector_only_service.stop()
    scene_only = RecordingConsumer()
    scene_only_service = VideoService(scene_classifier=scene_only)
    assert scene_only_service.start_webcam()
    assert _wait_for(lambda: scene_only.offered)
    scene_only_service.stop()
    assert detector_only.ended == detector_only.started
    assert scene_only.ended == scene_only.started


def test_scene_offer_failure_does_not_change_video_state(monkeypatch) -> None:
    detector = RecordingConsumer()
    scene = RecordingConsumer(fail_offer=True)
    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture", lambda *_args, **_kwargs: FakeCapture(opened=True)
    )
    service = VideoService(detector=detector, scene_classifier=scene)
    try:
        assert service.start_webcam()
        assert _wait_for(lambda: scene.offered)
        assert service.get_status().state is VideoState.RUNNING
    finally:
        service.stop()


def test_file_eof_ends_detector_and_scene(monkeypatch) -> None:
    detector = RecordingConsumer()
    scene = RecordingConsumer()
    monkeypatch.setattr(
        "app.services.video_service.cv2.VideoCapture",
        lambda *_args, **_kwargs: FakeCapture(opened=True, reads=[(False, None)]),
    )
    service = VideoService(detector=detector, scene_classifier=scene)
    token = service.claim_start()
    assert token is not None
    service.start_file(Path("fake.mp4"), token)
    assert _wait_for(lambda: service.get_status().state is VideoState.IDLE)
    assert detector.ended == scene.ended == detector.started


class TrackingGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def __enter__(self) -> None:
        self._lock.acquire()
        self.active += 1
        self.max_active = max(self.max_active, self.active)

    def __exit__(self, *_args) -> None:
        self.active -= 1
        self._lock.release()


def test_fake_yolo_and_clip_gpu_operations_share_gate_without_overlap() -> None:
    gate = TrackingGate()
    class CountingDomain(DomainService):
        def __init__(self) -> None:
            super().__init__()
            self.starts = 0

        def start_session(self, session_id: str) -> None:
            self.starts += 1
            super().start_session(session_id)

    domain = CountingDomain()
    yolo_started = threading.Event()
    release_yolo = threading.Event()

    def yolo_runner(_frame: np.ndarray) -> list[Detection]:
        yolo_started.set()
        release_yolo.wait(timeout=1.0)
        return []

    def clip_runner(_frames):
        # A fake production CLIP runner owns the same injected gate internally.
        with gate:
            return [SceneFrameScores(0.1, 0.4)] * 5

    detector = DetectorService(model_factory=lambda: yolo_runner, result_consumer=domain, cuda_gate=gate)
    scene = SceneDomainClassifier(domain, lambda: clip_runner, clock=lambda: 0.0)
    try:
        session = "shared"
        detector.start_session(session)  # The only DomainService start for this session.
        scene.start_session(session)
        assert domain.starts == 1
        detector.offer(FramePacket(session, 1, 1.0, np.zeros((2, 2, 3), dtype=np.uint8), b"jpeg"))
        assert yolo_started.wait(timeout=1.0)
        for sequence in range(1, 6):
            scene.offer(FramePacket(session, sequence, float(sequence), np.zeros((2, 2, 3), dtype=np.uint8), b"jpeg"))
        release_yolo.set()
        assert _wait_for(lambda: domain.get_snapshot().locked)
        assert gate.max_active == 1
    finally:
        release_yolo.set()
        scene.close()
        detector.close()


def test_production_composition_is_lazy_and_uses_one_gate() -> None:
    from app.services.runtime import detector_service, scene_domain_classifier, shared_cuda_gate, tracker_service, video_service

    assert video_service._detector is detector_service  # noqa: SLF001
    assert video_service._scene_classifier is scene_domain_classifier  # noqa: SLF001
    assert detector_service._cuda_gate is shared_cuda_gate  # noqa: SLF001
    assert detector_service._tracker is tracker_service  # noqa: SLF001
    assert scene_domain_classifier._runner is None  # noqa: SLF001
