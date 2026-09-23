"""Fake-only VideoService/JPEG-buffer integration tests; no real video or GPU."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from app.models.event_frame import BufferedFrame
from app.models.video import FramePacket, VideoState
from app.services.event_frame_buffer import EventFrameBuffer
from app.services.video_service import VideoService
from tests.conftest import FakeCapture


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.005)
    return False


class RecordingBuffer:
    def __init__(self, events: list[tuple[str, object]] | None = None, fail: bool = False) -> None:
        self.started: list[str] = []
        self.offered: list[BufferedFrame] = []
        self.ended: list[str] = []
        self.events = events if events is not None else []
        self.fail = fail
        self.service: VideoService | None = None
        self.gate: threading.Lock | None = None

    def start_session(self, session_id: str) -> None:
        self.started.append(session_id)

    def offer(self, frame: BufferedFrame) -> bool:
        self.events.append(("buffer", frame.source_frame_seq))
        self.offered.append(frame)
        if self.service is not None:
            published = self.service.get_latest_packet()
            assert published is not None and published.jpeg is frame.jpeg
            assert self.service._lock.acquire(blocking=False)  # noqa: SLF001
            self.service._lock.release()  # noqa: SLF001
            assert self.service._packet_cond.acquire(blocking=False)  # noqa: SLF001
            self.service._packet_cond.release()  # noqa: SLF001
        if self.gate is not None:
            assert self.gate.acquire(blocking=False)
            self.gate.release()
        if self.fail:
            raise RuntimeError("fake buffer failure")
        return True

    def end_session(self, session_id: str) -> None:
        self.ended.append(session_id)


class RecordingConsumer:
    def __init__(self, name: str, events: list[tuple[str, object]]) -> None:
        self.name = name
        self.events = events
        self.started: list[str] = []
        self.offered: list[FramePacket] = []
        self.ended: list[str] = []

    def start_session(self, session_id: str) -> None:
        self.started.append(session_id)

    def offer(self, packet: FramePacket) -> None:
        self.events.append((self.name, packet.seq))
        self.offered.append(packet)

    def end_session(self, session_id: str) -> None:
        self.ended.append(session_id)


def test_production_owns_one_injected_buffer() -> None:
    from app.services.runtime import event_frame_buffer, video_service
    assert isinstance(event_frame_buffer, EventFrameBuffer)
    assert video_service._event_frame_buffer is event_frame_buffer  # noqa: SLF001


def test_active_packet_order_metadata_jpeg_identity_and_no_locks() -> None:
    events: list[tuple[str, object]] = []
    buffer = RecordingBuffer(events)
    detector = RecordingConsumer("detector", events)
    scene = RecordingConsumer("scene", events)
    service = VideoService(detector=detector, scene_classifier=scene, event_frame_buffer=buffer)
    buffer.service = service
    buffer.gate = threading.Lock()
    token = service.claim_start()
    assert token is not None
    with service._lock:  # noqa: SLF001
        service._state = VideoState.RUNNING  # noqa: SLF001
        session_id = service._session_id  # noqa: SLF001
    frame = np.zeros((37, 53, 3), dtype=np.uint8)
    jpeg = b"already-encoded-jpeg"
    packet = FramePacket(session_id, 7, 123.5, frame, jpeg)
    try:
        with service._packet_cond:  # noqa: SLF001
            service._packet = packet  # noqa: SLF001
        service._offer_consumers(packet)  # noqa: SLF001
        assert events == [("buffer", 7), ("detector", 7), ("scene", 7)]
        item = buffer.offered[0]
        assert (item.source_session_id, item.source_frame_seq, item.source_timestamp) == (session_id, 7, 123.5)
        assert (item.frame_width, item.frame_height, item.mime_type) == (53, 37, "image/jpeg")
        assert item.jpeg is packet.jpeg
        assert detector.offered == scene.offered == [packet]
        assert service.get_latest_packet() is packet
        assert buffer.started == detector.started == scene.started == [session_id]
    finally:
        service.stop()
    assert buffer.ended == detector.ended == scene.ended == [session_id]


def test_stale_wrong_session_and_post_stop_packet_never_fan_out() -> None:
    events = []
    buffer = RecordingBuffer(events)
    detector = RecordingConsumer("detector", events)
    service = VideoService(detector=detector, event_frame_buffer=buffer)
    service.claim_start()
    with service._lock:  # noqa: SLF001
        service._state = VideoState.RUNNING  # noqa: SLF001
        session = service._session_id  # noqa: SLF001
    wrong = FramePacket("wrong", 1, 1.0, np.zeros((2, 3, 3), dtype=np.uint8), b"jpeg")
    service._offer_consumers(wrong)  # noqa: SLF001
    assert events == []
    service.stop()
    service._offer_consumers(FramePacket(session, 2, 2.0, wrong.frame, b"jpeg"))  # noqa: SLF001
    assert events == []


def test_buffer_exception_does_not_break_detector_scene_or_packet_slot() -> None:
    events = []
    buffer = RecordingBuffer(events, fail=True)
    detector = RecordingConsumer("detector", events)
    scene = RecordingConsumer("scene", events)
    service = VideoService(detector=detector, scene_classifier=scene, event_frame_buffer=buffer)
    service.claim_start()
    with service._lock:  # noqa: SLF001
        service._state = VideoState.RUNNING  # noqa: SLF001
        session = service._session_id  # noqa: SLF001
    packet = FramePacket(session, 1, 1.0, np.zeros((2, 2, 3), dtype=np.uint8), b"jpeg")
    try:
        with service._packet_cond:  # noqa: SLF001
            service._packet = packet  # noqa: SLF001
        service._offer_consumers(packet)  # noqa: SLF001
        assert events == [("buffer", 1), ("detector", 1), ("scene", 1)]
        assert service.get_latest_packet() is packet
        assert service.get_status().state is VideoState.RUNNING
    finally:
        service.stop()


def test_capture_success_uses_one_existing_encode_and_offer(monkeypatch) -> None:
    frame = np.zeros((13, 17, 3), dtype=np.uint8)
    jpeg_array = np.frombuffer(b"jpeg-once", dtype=np.uint8)
    encode_calls = []
    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_a, **_kw: FakeCapture(opened=True, reads=[(True, frame), (False, None)]))
    def fake_encode(ext, image):
        encode_calls.append((ext, image))
        return True, jpeg_array
    monkeypatch.setattr("app.services.video_service.cv2.imencode", fake_encode)
    buffer = RecordingBuffer()
    detector = RecordingConsumer("detector", [])
    service = VideoService(detector=detector, event_frame_buffer=buffer)
    buffer.service = service
    token = service.claim_start()
    assert token is not None
    service.start_file(Path("nonexistent-fake.mp4"), token)
    assert _wait_for(lambda: service.get_status().state is VideoState.IDLE)
    assert len(encode_calls) == len(buffer.offered) == len(detector.offered) == 1
    assert encode_calls[0][0] == ".jpg" and encode_calls[0][1] is frame
    assert buffer.offered[0].jpeg is detector.offered[0].jpeg
    assert (buffer.offered[0].frame_width, buffer.offered[0].frame_height) == (17, 13)
    assert buffer.started == buffer.ended == detector.started == detector.ended


def test_failed_read_and_failed_encode_never_offer(monkeypatch) -> None:
    for reads, encode_ok in (([(False, None)], True),
                             ([(True, np.zeros((2, 2, 3), dtype=np.uint8)), (False, None)], False)):
        monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                            lambda *_a, **_kw: FakeCapture(opened=True, reads=reads))
        monkeypatch.setattr("app.services.video_service.cv2.imencode",
                            lambda *_a, **_kw: (encode_ok, np.frombuffer(b"jpeg", dtype=np.uint8)))
        buffer = RecordingBuffer()
        service = VideoService(event_frame_buffer=buffer)
        token = service.claim_start()
        service.start_file(Path("nonexistent-fake.mp4"), token)
        assert _wait_for(lambda: service.get_status().state is VideoState.IDLE)
        assert buffer.offered == [] and buffer.ended == buffer.started


def test_stop_eof_webcam_error_failed_start_and_new_session_cleanup(monkeypatch) -> None:
    real_buffer = EventFrameBuffer()
    service = VideoService(event_frame_buffer=real_buffer)
    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", lambda *_a, **_kw: FakeCapture(opened=False))
    assert service.start_webcam()
    assert service.get_status().state is VideoState.ERROR
    assert real_buffer.get_snapshot().source_session_id is None
    service.stop()
    assert service.get_status().state is VideoState.IDLE
    token = service.claim_start()
    service.start_file(Path("nonexistent-fake.mp4"), token)
    assert real_buffer.get_snapshot().source_session_id is None
    service.stop()
    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_a, **_kw: FakeCapture(opened=True, reads=[(False, None)]))
    token = service.claim_start()
    service.start_file(Path("nonexistent-fake.mp4"), token)
    assert _wait_for(lambda: service.get_status().state is VideoState.IDLE)
    assert real_buffer.get_snapshot().total_jpeg_bytes == 0
    # Rollback from STARTING clears even a JPEG retained by the current session.
    token = service.claim_start()
    session = real_buffer.get_snapshot().source_session_id
    assert token is not None and session is not None
    assert real_buffer.offer(BufferedFrame(session, 1, 1.0, 2, 2, b"jpeg"))
    service.stop()
    assert real_buffer.get_snapshot().total_jpeg_bytes == 0
    assert service.claim_start() is not None
    assert real_buffer.get_snapshot().frame_count == 0
    service.stop()


def test_capture_exception_ends_buffer_without_losing_error_state(monkeypatch) -> None:
    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_a, **_kw: FakeCapture(opened=True, reads=[
                            (True, np.zeros((2, 2, 3), dtype=np.uint8)),
                        ]))
    def raise_encode(*_args):
        raise RuntimeError("fake encode failure")
    monkeypatch.setattr("app.services.video_service.cv2.imencode", raise_encode)
    buffer = RecordingBuffer()
    service = VideoService(event_frame_buffer=buffer)
    token = service.claim_start()
    service.start_file(Path("nonexistent-fake.mp4"), token)
    assert _wait_for(lambda: service.get_status().state is VideoState.ERROR)
    assert _wait_for(lambda: buffer.ended == buffer.started)
    assert buffer.started == buffer.ended and buffer.offered == []
    service.stop()


def test_webcam_read_error_ends_buffer(monkeypatch) -> None:
    buffer = RecordingBuffer()
    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_a, **_kw: FakeCapture(opened=True, reads=[(False, None)] * 5))
    service = VideoService(event_frame_buffer=buffer)
    try:
        assert service.start_webcam()
        assert _wait_for(lambda: service.get_status().state is VideoState.ERROR)
        assert buffer.started == buffer.ended
    finally:
        service.stop()


def test_shutdown_closes_buffer_after_video_and_workers() -> None:
    from app.services.runtime import shutdown_services
    events = []
    class Service:
        def __init__(self, name): self.name = name
        def stop(self): events.append("video.stop")
        def close(self): events.append(self.name + ".close")
    shutdown_services(Service("video"), Service("scene"), Service("detector"),
                      Service("trigger"), Service("tracker"), Service("buffer"))
    assert events == ["video.stop", "scene.close", "detector.close", "trigger.close", "tracker.close", "buffer.close"]
