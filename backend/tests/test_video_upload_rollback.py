"""T3c0.2 — video routes: refusal before write/probe (503), token-bound upload rollback
(an old upload never stops a newer session) and sanitized route errors.

The route coroutines are called directly with a fake upload; the handler PROBE
(``app.api.video.cv2``) and the PLAYBACK capture (``app.services.video_service.cv2``)
are counted separately. No server, real file decoding, webcam or network.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import cv2 as real_cv2
import pytest
from fastapi import HTTPException

import app.api.video as video_api
from app.models.video import VideoState
from app.services.video_service import REQUIRED_START_REFUSED, VideoService
from tests.test_video_required_sinks import SECRET, Sink, _joined, _no_secret
from tests.test_video_session_lifecycle import Capture, _thread, _wait_until

WAIT = 5.0


class ProbeCv2:
    """Stands in for the route's cv2 module: counts PROBE opens only."""

    def __init__(self, *, opened: bool = True, error: Exception | None = None) -> None:
        self.opened, self.error, self.calls = opened, error, 0

    def VideoCapture(self, *_args, **_kwargs):  # noqa: N802
        self.calls += 1
        if self.error is not None:
            raise self.error
        probe = Capture(opened=self.opened)
        return probe

    def __getattr__(self, name):
        return getattr(real_cv2, name)


class FakeUpload:
    def __init__(self, data: bytes = b"video", *, gate: threading.Event | None = None,
                 content_type: str = "video/mp4", filename: str = "clip.mp4") -> None:
        self._chunks = [data, b""]
        self.gate, self.in_read = gate, threading.Event()
        self.content_type, self.filename = content_type, filename

    async def read(self, _size: int) -> bytes:
        self.in_read.set()
        if self.gate is not None:
            gate, self.gate = self.gate, None
            assert gate.wait(WAIT)                    # holds this request only (its own loop)
        return self._chunks.pop(0) if self._chunks else b""


class Env:
    def __init__(self, monkeypatch, tmp_path, *, required=frozenset(), probe: ProbeCv2 | None = None) -> None:
        self.log: list = []
        self.playback = 0

        def playback_factory(*_args, **_kwargs):
            self.playback += 1
            return Capture()

        monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", playback_factory)
        self.probe = probe or ProbeCv2()
        monkeypatch.setattr(video_api, "cv2", self.probe)
        self.upload_dir = tmp_path / "uploads"
        self.upload_dir.mkdir()
        monkeypatch.setattr(video_api, "UPLOAD_DIR", self.upload_dir)
        self.buffer, self.detector = Sink("buffer", self.log), Sink("detector", self.log)
        self.service = VideoService(event_frame_buffer=self.buffer, detector=self.detector,
                                    required_sinks=required)
        monkeypatch.setattr(video_api, "video_service", self.service)

    def files(self) -> list:
        return list(self.upload_dir.iterdir())


@pytest.fixture
def env(monkeypatch, tmp_path):
    created: list[Env] = []

    def make(**kwargs) -> Env:
        made = Env(monkeypatch, tmp_path / str(len(created)), **kwargs) if created else Env(
            monkeypatch, tmp_path, **kwargs)
        created.append(made)
        return made

    yield make
    for made in created:
        made.service.stop()


def _upload(upload: FakeUpload):
    return asyncio.run(video_api.upload_video(upload))


# ── 8. refusal before write, probe and playback ─────────────────────────────

def test_upload_refusal_answers_503_before_any_write_or_probe(env) -> None:
    made = env(required={"event frame buffer"})
    made.buffer.start_error = RuntimeError("refused")
    with pytest.raises(HTTPException) as raised:
        _upload(FakeUpload())
    assert (raised.value.status_code, raised.value.detail) == (503, "Required pipeline component is unavailable")
    assert made.probe.calls == 0 and made.playback == 0 and made.files() == []
    status = made.service.get_status()
    assert (status.state, status.error) == (VideoState.ERROR, REQUIRED_START_REFUSED)
    with pytest.raises(HTTPException) as conflict:
        _upload(FakeUpload())                                       # ERROR until Stop: 409
    assert conflict.value.status_code == 409
    made.service.stop()
    assert made.service.get_status().state is VideoState.IDLE


def test_webcam_refusal_answers_503_without_opening_the_camera(env) -> None:
    made = env(required={"detector"})
    made.detector.start_error = RuntimeError("refused")
    with pytest.raises(HTTPException) as raised:
        asyncio.run(video_api.start_webcam())
    assert raised.value.status_code == 503 and made.playback == 0
    assert made.service.get_status().state is VideoState.ERROR
    made.service.stop()
    made.detector.start_error = None
    response = asyncio.run(video_api.start_webcam())
    assert response.state in (VideoState.RUNNING, VideoState.STARTING) and made.playback == 1


# ── 9. an old upload's rollback never stops a newer session ─────────────────

def test_a_held_upload_a_rolls_back_only_itself_after_stop_and_start_b(env) -> None:
    made = env(probe=ProbeCv2(opened=False))                        # A will fail its probe
    gate = threading.Event()
    upload = FakeUpload(gate=gate)
    outcome: list = []

    def run_a():
        try:
            _upload(upload)
        except HTTPException as error:
            outcome.append(error.status_code)

    a = _thread(run_a)
    assert upload.in_read.wait(WAIT)                                # A holds its reservation
    made.service.stop()                                             # the user stops A
    assert made.service.get_status().state is VideoState.IDLE
    assert made.service.start_webcam()                              # B
    assert _wait_until(lambda: made.service.get_status().state is VideoState.RUNNING)
    ends_before = made.log.count(("buffer", "end")) + made.log.count(("detector", "end"))
    gate.set()
    assert _joined(a)
    assert outcome == [422]
    assert made.service.get_status().state is VideoState.RUNNING     # B survived A's rollback
    assert made.log.count(("buffer", "end")) + made.log.count(("detector", "end")) == ends_before
    assert made.service.get_cleanup_diagnostics()["stale_aborts"] == 1
    assert made.files() == []                                       # A's file deleted by the handler
    assert made.playback == 1                                       # only B's camera


def test_a_validation_failure_rolls_back_its_own_reservation_to_idle(env) -> None:
    made = env()
    with pytest.raises(HTTPException) as raised:
        _upload(FakeUpload(content_type="text/plain"))
    assert raised.value.status_code == 422
    assert made.service.get_status().state is VideoState.IDLE
    assert made.service.get_cleanup_diagnostics()["aborted_starts"] == 1
    assert made.log.count(("buffer", "end")) == 1 and made.files() == []


# ── delta 1: stale reservation versus general errors in the route ─────────

def test_the_route_maps_a_stale_reservation_to_409_without_claiming_a_stop(env, monkeypatch) -> None:
    made = env()
    original = made.service.start_file

    def stale(path, token):
        made.service.abort_start(token)                            # the reservation goes away
        return original(path, token)

    monkeypatch.setattr(made.service, "start_file", stale)
    with pytest.raises(HTTPException) as raised:
        _upload(FakeUpload())
    assert (raised.value.status_code, raised.value.detail) == (409, "Upload aborted: the reservation is no longer active")
    assert "stop" not in raised.value.detail.lower()
    assert made.files() == [] and made.playback == 0
    assert made.service.get_status().state is VideoState.IDLE


def test_a_general_hand_off_error_is_a_500_and_rolls_back_only_this_reservation(env, monkeypatch, caplog) -> None:
    made = env()

    def broken(_path, _token):
        raise RuntimeError(SECRET)                                 # NOT a stale reservation

    monkeypatch.setattr(made.service, "start_file", broken)
    with caplog.at_level(logging.DEBUG), pytest.raises(HTTPException) as raised:
        _upload(FakeUpload())
    assert (raised.value.status_code, raised.value.detail) == (500, "Service start error")
    assert raised.value.__suppress_context__ and _no_secret(caplog.text)
    assert "Upload hand-off failed (RuntimeError)" in caplog.text
    assert made.service.get_cleanup_diagnostics()["aborted_starts"] == 1
    assert made.service.get_status().state is VideoState.IDLE and made.files() == []


def test_a_playback_open_failure_follows_the_open_failure_contract(env) -> None:
    made = env()

    def raising_playback(*_args, **_kwargs):
        made.playback += 1
        capture = Capture()
        capture.isOpened = lambda: (_ for _ in ()).throw(RuntimeError(SECRET))
        made.last = capture
        return capture

    import app.services.video_service as service_module
    original = service_module.cv2.VideoCapture
    service_module.cv2.VideoCapture = raising_playback
    try:
        response = _upload(FakeUpload())
    finally:
        service_module.cv2.VideoCapture = original
    assert response.state is VideoState.ERROR and response.message == "Start failed: Unable to open video file"
    assert made.probe.calls == 1 and made.playback == 1 and made.last.release_count == 1
    assert made.files() == []                                      # the reservation's cleanup deleted it


# ── 17. sanitized route errors ──────────────────────────────────────────────

def test_route_errors_and_logs_carry_no_ids_tokens_paths_or_messages(env, caplog) -> None:
    made = env(probe=ProbeCv2(error=RuntimeError(SECRET)))
    with caplog.at_level(logging.DEBUG), pytest.raises(HTTPException) as probe_error:
        _upload(FakeUpload())
    assert probe_error.value.status_code == 500 and _no_secret(str(probe_error.value.detail))
    assert made.files() == [] and made.service.get_status().state is VideoState.IDLE

    class Exploding(FakeUpload):
        @property
        def filename(self):
            raise RuntimeError(SECRET)

        @filename.setter
        def filename(self, _value):
            pass

    with caplog.at_level(logging.DEBUG), pytest.raises(HTTPException) as unexpected:
        _upload(Exploding())
    assert (unexpected.value.status_code, unexpected.value.detail) == (500, "Internal server error")
    assert unexpected.value.__suppress_context__
    assert "Unexpected error in upload_video (RuntimeError)" in caplog.text
    assert _no_secret(caplog.text)
    assert made.service.get_status().state is VideoState.IDLE
