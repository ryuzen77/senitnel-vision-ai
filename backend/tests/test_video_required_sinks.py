"""T3c0.2 — VideoService required sinks, owner leases, thread-start rollback, safe RUNNING
publication, token-bound abort and sanitized lifecycle logging.

Fake captures, fake sinks, a fake detector model and deterministic ``threading.Event``
gates only: no webcam, video file decoding, model, network or speaker. Every wait is
bounded and every helper thread is joined and asserted finished.
"""

from __future__ import annotations

import logging
import threading

import numpy as np
import pytest

from app.models.video import VideoState
from app.services.detector_service import DetectorAdmissionError, DetectorService
from app.services.private_parking_coordinator import (
    FrameSinkAdmissionError,
    FrameSinkTee,
    PrivateParkingCoordinator,
)
from app.services.video_service import (
    CAPTURE_FAILED,
    REQUIRED_START_REFUSED,
    THREAD_START_FAILED,
    StaleStartError,
    VideoService,
    VideoStartRefusedError,
)
from tests.test_private_parking_coordinator import FakeSampler, FakeTrigger
from tests.test_video_session_lifecycle import Capture, _thread, _wait_until

WAIT = 5.0
SECRET_ID, SECRET_TOKEN, SECRET_PATH = "sid-7f3a9c", "tok-5e1d2b", "C:/secret/uploads/clip-91ab.mp4"
SECRET = f"session {SECRET_ID} token {SECRET_TOKEN} path {SECRET_PATH}"


def _joined(*threads: threading.Thread) -> bool:
    for thread in threads:
        thread.join(WAIT)
    return not any(thread.is_alive() for thread in threads)


def _no_secret(text: str) -> bool:
    return not any(part in text for part in (SECRET_ID, SECRET_TOKEN, SECRET_PATH, "clip-91ab"))


class Sink:
    """A VideoService sink; ``lease_aware`` makes VideoService pass its lease."""

    def __init__(self, name: str, log: list, *, lease_aware: bool = False) -> None:
        self.name, self.log = name, log
        if lease_aware:
            self.accepts_lifecycle_lease = True
        self.active: str | None = None
        self.leases: list = []
        self.start_error: Exception | None = None
        self.end_errors: list[Exception] = []
        self.start_gate: threading.Event | None = None
        self.in_start = threading.Event()
        self.offered = 0

    def start_session(self, session_id: str, **kwargs) -> None:
        self.log.append((self.name, "start"))
        self.leases.append(("start", kwargs.get("lease")))
        self.in_start.set()
        if self.start_gate is not None:
            assert self.start_gate.wait(WAIT)
        if self.start_error is not None:
            raise self.start_error
        self.active = session_id

    def end_session(self, session_id: str, **kwargs) -> None:
        self.log.append((self.name, "end"))
        self.leases.append(("end", kwargs.get("lease")))
        if self.end_errors:
            raise self.end_errors.pop(0)
        if self.active == session_id:
            self.active = None

    def offer(self, *_args, **kwargs) -> bool:
        self.offered += 1
        return True


class Rig:
    def __init__(self, monkeypatch, *, captures=(), buffer=None, detector=None, scene=None, worker=None,
                 **service_kwargs) -> None:
        self.log: list = []
        self.captures = list(captures)
        self.opened: list = []

        def factory(*_args, **_kwargs):
            capture = self.captures.pop(0) if self.captures else Capture()
            self.opened.append(capture)
            return capture

        monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", factory)
        self.buffer = buffer if buffer is not None else Sink("buffer", self.log)
        self.detector = detector if detector is not None else Sink("detector", self.log)
        self.service = VideoService(event_frame_buffer=self.buffer, detector=self.detector,
                                    scene_classifier=scene, evaluation_worker=worker, **service_kwargs)

    def state(self) -> VideoState:
        return self.service.get_status().state

    def calls(self, name: str, kind: str) -> int:
        return self.log.count((name, kind))


@pytest.fixture
def rigs(monkeypatch):
    created: list[Rig] = []

    def make(**kwargs) -> Rig:
        rig = Rig(monkeypatch, **kwargs)
        created.append(rig)
        return rig

    yield make
    for rig in created:
        for sink in (rig.buffer, rig.detector):
            gate = getattr(sink, "start_gate", None)
            if gate is not None:
                gate.set()
        try:
            rig.service.stop()
        except Exception:
            pass


# ── configuration ────────────────────────────────────────────────────────────

def test_required_sinks_must_be_configured_labels(monkeypatch) -> None:
    with pytest.raises(ValueError):
        VideoService(event_frame_buffer=Sink("buffer", []), required_sinks={"detector"})
    with pytest.raises(ValueError):
        VideoService(event_frame_buffer=Sink("buffer", []), required_sinks={"tee"})


# ── 7. required refusal concurrently with Stop ───────────────────────────────

def test_a_required_refusal_racing_a_stop_has_one_cleanup_runner(rigs) -> None:
    rig = rigs(required_sinks={"event frame buffer"})
    rig.service._lifecycle_lock = CountingLock()                  # noqa: SLF001
    rig.buffer.start_gate, rig.buffer.start_error = threading.Event(), RuntimeError("refused")
    errors: list = []

    def start():
        try:
            rig.service.start_webcam()
        except VideoStartRefusedError as error:
            errors.append(error)

    starter = _thread(start)
    assert rig.buffer.in_start.wait(WAIT)                         # start loop holds the lifecycle lock
    stopper = _thread(rig.service.stop)
    # deterministic: the Stop's cleanup runner is blocked on the lifecycle lock that the
    # start loop holds, BEFORE the refusal is released
    assert _wait_until(lambda: rig.service._lifecycle_lock.waiting == 1)   # noqa: SLF001
    assert rig.state() is VideoState.STOPPING
    rig.buffer.start_gate.set()
    assert _joined(starter, stopper)
    assert len(errors) == 1 and str(errors[0]) == REQUIRED_START_REFUSED
    assert rig.calls("buffer", "end") == 1 and rig.calls("detector", "end") == 1   # one runner
    assert rig.calls("detector", "start") == 0 and rig.opened == []
    assert rig.state() is VideoState.IDLE                          # the Stop owned the transition


def test_a_required_refusal_opens_no_capture_and_stays_error_until_stop(rigs) -> None:
    rig = rigs(required_sinks={"event frame buffer", "detector"})
    rig.detector.start_error = RuntimeError("refused")
    with pytest.raises(VideoStartRefusedError):
        rig.service.start_webcam()
    assert rig.opened == [] and rig.calls("buffer", "end") == 1 and rig.calls("detector", "end") == 1
    status = rig.service.get_status()
    assert (status.state, status.error) == (VideoState.ERROR, REQUIRED_START_REFUSED)
    assert rig.service.claim_start() is None                       # conflict contract: not IDLE
    assert rig.service.get_cleanup_diagnostics()["required_start_refusals"] == 1
    rig.service.stop()
    assert rig.state() is VideoState.IDLE
    rig.detector.start_error = None
    assert rig.service.start_webcam() and _wait_until(lambda: rig.state() is VideoState.RUNNING)


# ── 10. late operations with a reused session id ─────────────────────────────

def _real_rig(rigs, *, trigger_log=None):
    log: list = []
    buffer_sink = Sink("buffer-sink", log)
    tee = FrameSinkTee(buffer_sink)
    trigger = FakeTrigger(trigger_log if trigger_log is not None else [])
    detector = DetectorService(model_factory=lambda: (lambda _frame: []),
                               parking_trigger=PrivateParkingCoordinator(trigger, FakeSampler([])))
    rig = rigs(buffer=tee, detector=detector, required_sinks={"event frame buffer", "detector"},
               session_id_factory=lambda: "same")
    return rig, tee, buffer_sink, detector, trigger


def test_late_operations_of_a_never_touch_b_with_the_same_session_id(rigs, tmp_path) -> None:
    rig, tee, buffer_sink, detector, trigger = _real_rig(rigs)
    try:
        assert rig.service.start_webcam() and _wait_until(lambda: rig.state() is VideoState.RUNNING)
        token_a = rig.service._current.token                     # noqa: SLF001
        rig.service.stop()
        assert rig.state() is VideoState.IDLE
        assert rig.service.start_webcam() and _wait_until(lambda: rig.state() is VideoState.RUNNING)
        token_b = rig.service._current.token                     # noqa: SLF001
        assert token_b != token_a
        opened = len(rig.opened)
        assert not rig.service.abort_start(token_a)
        with pytest.raises(StaleStartError):
            rig.service.start_file(tmp_path / "late.mp4", token_a)
        assert len(rig.opened) == opened                           # no playback capture for A
        detector.end_session("same", lease=token_a)
        tee.end_session("same", lease=token_a)
        assert detector.get_lifecycle_diagnostics()["stale_ends"] >= 1
        assert tee.get_diagnostics()["stale_ends"] >= 1
        assert detector.get_lifecycle_diagnostics()["active_sessions"] == 1
        assert buffer_sink.active == "same" and rig.state() is VideoState.RUNNING
        assert rig.service.get_cleanup_diagnostics()["stale_aborts"] == 1
        # B's cleanup fails in the tee and in the detector: same-id starts are refused
        buffer_sink.end_errors = [RuntimeError("end")]
        trigger.end_error = RuntimeError("end")
        rig.service.stop()
        assert rig.state() is VideoState.ERROR and rig.service.claim_start() is None
        with pytest.raises(FrameSinkAdmissionError):
            tee.start_session("same", lease="other")
        with pytest.raises(DetectorAdmissionError):
            detector.start_session("same", lease="other")
        trigger.end_error = None
        rig.service.stop()                                         # retry with the SAME lease
        assert rig.state() is VideoState.IDLE
        assert tee.pending_cleanups() == 0 and detector.get_lifecycle_diagnostics()["pending_sessions"] == 0
    finally:
        rig.service.stop()
        detector.close()


# ── 11. thread.start() fails ─────────────────────────────────────────────────

class _FailingThread:
    def __init__(self, *args, **kwargs) -> None:
        self.ident = None

    def start(self) -> None:
        raise RuntimeError(SECRET)

    def is_alive(self) -> bool:
        return False

    def join(self, timeout=None) -> None:
        pass


@pytest.mark.parametrize("source", ["webcam", "file"])
def test_a_failing_thread_start_rolls_back_only_that_session(rigs, tmp_path, caplog, source) -> None:
    factories = [_FailingThread]
    rig = rigs(thread_factory=lambda *a, **k: (factories.pop(0) if factories else threading.Thread)(*a, **k))
    rig.detector.end_errors = [RuntimeError(SECRET)]
    transitions: list = []
    upload = tmp_path / "upload.mp4"
    upload.write_bytes(b"x")
    with caplog.at_level(logging.WARNING):
        if source == "webcam":
            assert rig.service.start_webcam()
        else:
            token = rig.service.claim_start()
            rig.service.start_file(upload, token)
        transitions.append(rig.state())
    status = rig.service.get_status()
    assert status.state is VideoState.ERROR and "cleanup failed" in status.error   # detector end failed
    assert rig.opened[0].release_count == 1 and rig.calls("buffer", "end") == 1
    assert rig.service.get_cleanup_diagnostics()["pending_sinks"] == ["detector"]
    assert rig.service.get_cleanup_diagnostics()["thread_start_failures"] == 1
    if source == "file":
        assert not upload.exists()                                 # the runner deleted this upload
    assert _no_secret(caplog.text) and "Unable to start the capture thread (RuntimeError)" in caplog.text
    rig.service.stop()                                             # retry: only the detector
    assert rig.calls("detector", "end") == 2 and rig.calls("buffer", "end") == 1
    status = rig.service.get_status()
    assert status.state is VideoState.IDLE
    assert rig.service.start_webcam() and _wait_until(lambda: rig.state() is VideoState.RUNNING)


def test_a_thread_start_failure_reports_a_fixed_error(rigs) -> None:
    rig = rigs(thread_factory=_FailingThread)
    assert rig.service.start_webcam()
    status = rig.service.get_status()
    assert (status.state, status.error) == (VideoState.ERROR, THREAD_START_FAILED)
    rig.service.stop()
    assert rig.state() is VideoState.IDLE


# ── 12. immediate EOF / immediate capture error ──────────────────────────────

class _StateLog(VideoService):
    """Records every state write with the number of the _lock section it happened in."""

    def __setattr__(self, name, value):
        if name == "_state" and hasattr(self, "transitions"):
            self.transitions.append((value, getattr(self._lock, "sections", None)))
        super().__setattr__(name, value)


class CountingLock:
    """Test instrumentation: counts ``with`` sections and threads currently blocked
    trying to enter one (``waiting``), so a test can prove a thread is contending."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._meta = threading.Lock()
        self.sections = 0
        self.waiting = 0

    def __enter__(self):
        with self._meta:
            self.waiting += 1
        self._lock.acquire()
        with self._meta:
            self.waiting -= 1
        self.sections += 1
        return self

    def __exit__(self, *exc) -> None:
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()


def _state_log_service(monkeypatch, captures, **kwargs):
    log: list = []
    queue = list(captures)
    opened: list = []

    def factory(*_a, **_k):
        capture = queue.pop(0) if queue else Capture()
        opened.append(capture)
        return capture

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", factory)
    service = _StateLog(event_frame_buffer=Sink("buffer", log), detector=Sink("detector", log), **kwargs)
    service._lock = CountingLock()                                 # noqa: SLF001
    service.transitions = []
    return service, log, opened


def _running_after_final(transitions) -> bool:
    final = next((i for i, (state, _s) in enumerate(transitions)
                  if state in (VideoState.IDLE, VideoState.ERROR)), None)
    return final is not None and any(state is VideoState.RUNNING for state, _s in transitions[final:])


def test_immediate_eof_and_immediate_error_follow_the_running_publication(monkeypatch, tmp_path, caplog) -> None:
    service, log, _opened = _state_log_service(monkeypatch, [Capture(["eof"], tail="eof")])
    upload = tmp_path / "eof.mp4"
    upload.write_bytes(b"x")
    service.start_file(upload, service.claim_start())
    assert _wait_until(lambda: service.get_status().state is VideoState.IDLE)
    states = [state for state, _s in service.transitions]
    assert states.index(VideoState.RUNNING) < states.index(VideoState.STOPPING)
    assert not _running_after_final(service.transitions)
    assert log.count(("buffer", "end")) == 1 and log.count(("detector", "end")) == 1

    service, log, _opened = _state_log_service(monkeypatch, [Capture([RuntimeError(SECRET)])])
    with caplog.at_level(logging.WARNING):
        assert service.start_webcam()
        assert _wait_until(lambda: service.get_status().state is VideoState.ERROR)
    status = service.get_status()
    assert status.error == CAPTURE_FAILED and _no_secret(caplog.text)
    states = [state for state, _s in service.transitions]
    assert states.index(VideoState.RUNNING) < states.index(VideoState.STOPPING)
    assert not _running_after_final(service.transitions)
    service.stop()


# ── 13. Stop at the thread.start / RUNNING boundary ──────────────────────────

def test_stop_before_the_running_section_never_starts_the_thread(monkeypatch) -> None:
    service, _log, opened = _state_log_service(monkeypatch, [Capture()])
    original = service._start_capture                               # noqa: SLF001
    entered, gate, results = threading.Event(), threading.Event(), []

    def gated(session, thread):
        entered.set()
        assert gate.wait(WAIT)
        results.append(original(session, thread))
        return results[-1]

    service._start_capture = gated                                  # noqa: SLF001
    starter = _thread(service.start_webcam)
    assert entered.wait(WAIT)
    service.stop()                                                  # wins before the section
    assert service.get_status().state is VideoState.IDLE
    gate.set()
    assert _joined(starter)
    assert results == [False] and opened[0].release_count == 1
    assert VideoState.RUNNING not in [state for state, _s in service.transitions]


def test_stop_during_thread_start_waits_and_ends_the_running_session(monkeypatch) -> None:
    gate, entered = threading.Event(), threading.Event()

    class GatedThread(threading.Thread):
        def start(self):
            entered.set()
            assert gate.wait(WAIT)
            super().start()

    service, _log, _opened = _state_log_service(monkeypatch, [Capture()], thread_factory=GatedThread)
    starter = _thread(service.start_webcam)
    assert entered.wait(WAIT)                                       # thread.start() under _lock
    assert service._lock.locked()                                   # noqa: SLF001
    stopper = _thread(service.stop)
    # deterministic: the Stop is blocked entering _lock (the RUNNING publication section)
    # before thread.start() is released
    assert _wait_until(lambda: service._lock.waiting == 1)          # noqa: SLF001
    assert VideoState.RUNNING not in [state for state, _s in service.transitions]
    gate.set()
    assert _joined(starter, stopper)
    states = [state for state, _s in service.transitions]
    assert VideoState.RUNNING in states
    assert states.index(VideoState.RUNNING) < len(states) - 1 and states[-1] is VideoState.IDLE
    assert not _running_after_final(service.transitions)


# ── 14. RUNNING is published in the same _lock section as thread.start() ─────

def test_running_is_published_in_the_thread_start_section(monkeypatch) -> None:
    sections: list = []

    class RecordingThread(threading.Thread):
        def start(self):
            sections.append(("thread.start", service._lock.locked(), service._lock.sections))   # noqa: SLF001
            super().start()

    service, _log, _opened = _state_log_service(monkeypatch, [Capture()], thread_factory=RecordingThread)
    assert service.start_webcam() and _wait_until(lambda: service.get_status().state is VideoState.RUNNING)
    ((_name, locked, start_section),) = sections
    running = [section for state, section in service.transitions if state is VideoState.RUNNING]
    assert locked and running == [start_section]
    service.stop()


# ── 15. IDLE/admission only after every obligation ───────────────────────────

def test_idle_and_admission_only_after_the_coordinator_really_ended(rigs) -> None:
    trigger_log: list = []
    rig, _tee, _sink, detector, trigger = _real_rig(rigs, trigger_log=trigger_log)
    try:
        assert rig.service.start_webcam() and _wait_until(lambda: rig.state() is VideoState.RUNNING)
        trigger.end_error = RuntimeError("end")
        rig.service.stop()
        assert rig.state() is VideoState.ERROR and rig.service.claim_start() is None
        assert rig.service.get_cleanup_diagnostics()["pending_sinks"] == ["detector"]
        trigger.end_error = None
        trigger_log.clear()
        rig.service.stop()                                         # reaches the coordinator again
        assert trigger_log == [("trigger.end", "same")]
        assert rig.state() is VideoState.IDLE
        assert rig.service.start_webcam()
    finally:
        rig.service.stop()
        detector.close()


# ── 16/17. legacy behaviour and sanitization ─────────────────────────────────

def test_legacy_optional_sinks_still_let_the_video_run(rigs) -> None:
    rig = rigs()
    rig.buffer.start_error = RuntimeError("optional")
    rig.detector.start_error = RuntimeError("optional")
    assert rig.service.start_webcam() and _wait_until(lambda: rig.state() is VideoState.RUNNING)
    closed_tee = FrameSinkTee(Sink("inner", []))
    closed_tee.close()
    legacy = rigs(buffer=closed_tee)
    assert legacy.service.start_webcam() and _wait_until(lambda: legacy.state() is VideoState.RUNNING)


def test_same_lease_on_start_every_end_attempt_and_detector_offer(rigs) -> None:
    log: list = []
    buffer = Sink("buffer", log, lease_aware=True)
    detector = Sink("detector", log, lease_aware=True)
    scene = Sink("scene", log)                                     # id-only
    rig = rigs(buffer=buffer, detector=detector, scene=scene)
    detector.offer_leases = []
    detector.offer = lambda packet, **kwargs: detector.offer_leases.append(kwargs.get("lease"))
    assert rig.service.start_webcam() and _wait_until(lambda: detector.offer_leases)
    token = rig.service._current.token                             # noqa: SLF001
    buffer.end_errors = [RuntimeError("end")]
    rig.service.stop()
    rig.service.stop()                                             # retry
    assert buffer.leases == [("start", token), ("end", token), ("end", token)]
    assert detector.leases == [("start", token), ("end", token)]
    assert scene.leases == [("start", None), ("end", None)]
    assert set(detector.offer_leases) == {token}


def test_lifecycle_failures_never_leak_ids_tokens_paths_or_messages(rigs, caplog) -> None:
    rig = rigs(captures=[Capture(release_error=RuntimeError(SECRET))])
    rig.buffer.start_error = RuntimeError(SECRET)                  # optional: logged
    rig.detector.end_errors = [RuntimeError(SECRET)]

    class SecretPath:
        name = "clip-91ab.mp4"

        def __str__(self) -> str:
            return SECRET_PATH

        def unlink(self, missing_ok=False) -> None:
            raise OSError(SECRET)

    with caplog.at_level(logging.DEBUG):
        token = rig.service.claim_start()
        rig.service.start_file(SecretPath(), token)
        assert _wait_until(lambda: rig.state() is VideoState.RUNNING)
        rig.service.stop()
    status = rig.service.get_status()
    assert status.state is VideoState.ERROR
    assert _no_secret(caplog.text) and _no_secret(status.error or "")
    assert _no_secret(str(rig.service.get_cleanup_diagnostics()))
    for text in ("Unable to start event frame buffer session (RuntimeError)",
                 "Unable to end detector session (RuntimeError)",
                 "Unable to release video capture (RuntimeError)",
                 "Unable to delete uploaded video (OSError)"):
        assert text in caplog.text


def test_an_old_abort_never_rolls_back_b_while_b_is_still_starting(rigs) -> None:
    rig = rigs()
    token_a = rig.service.claim_start()
    rig.service.stop()                                             # A ends before its handler aborts
    token_b = rig.service.claim_start()                            # B reserved, still STARTING
    ends = rig.calls("buffer", "end")
    assert not rig.service.abort_start(token_a)                    # A's late rollback
    assert rig.state() is VideoState.STARTING and rig.calls("buffer", "end") == ends
    assert rig.service.abort_start(token_b)                        # B's own rollback works
    assert rig.state() is VideoState.IDLE and rig.calls("buffer", "end") == ends + 1
    assert not rig.service.abort_start(token_b)                    # once only


# ── delta 1: capture ownership before attach, error classification ──────────

class _FlakyCapture(Capture):
    def __init__(self, *, fail: str) -> None:
        super().__init__()
        self.fail = fail

    def isOpened(self) -> bool:  # noqa: N802
        if self.fail == "isOpened":
            raise RuntimeError(SECRET)
        return super().isOpened()

    def get(self, prop: int) -> float:
        if self.fail == "get":
            raise RuntimeError(SECRET)
        return super().get(prop)


def _broken_thread_factory(*_args, **_kwargs):
    raise RuntimeError(SECRET)


@pytest.mark.parametrize("source,fail", [("file", "isOpened"), ("file", "get"), ("file", "thread"),
                                         ("webcam", "isOpened"), ("webcam", "thread")])
def test_a_capture_failing_before_attach_is_released_once_and_rolled_back(rigs, tmp_path, caplog, source,
                                                                          fail) -> None:
    capture = _FlakyCapture(fail=fail)
    kwargs = {"thread_factory": _broken_thread_factory} if fail == "thread" else {}
    rig = rigs(captures=[capture], **kwargs)
    upload = tmp_path / "upload.mp4"
    upload.write_bytes(b"x")
    with caplog.at_level(logging.DEBUG):
        if source == "file":
            token = rig.service.claim_start()
            rig.service.start_file(upload, token)                  # returns normally (open failure)
        else:
            assert rig.service.start_webcam()
    assert capture.release_count == 1                              # released exactly once
    status = rig.service.get_status()
    expected = "Unable to open video file" if source == "file" else "Webcam open failed"
    assert (status.state, status.error) == (VideoState.ERROR, expected)
    assert rig.calls("buffer", "end") == 1 and rig.calls("detector", "end") == 1
    if source == "file":
        assert not upload.exists()                                 # this reservation's upload
        assert not rig.service.abort_start(token)                  # nothing left to roll back
    assert _no_secret(caplog.text) and _no_secret(status.error)
    rig.service.stop()
    assert rig.state() is VideoState.IDLE and capture.release_count == 1
    rig.service._thread_factory = threading.Thread                 # noqa: SLF001
    assert rig.service.start_webcam() and _wait_until(lambda: rig.state() is VideoState.RUNNING)


def test_a_failed_open_of_a_blocks_b_until_its_capture_is_released(rigs, tmp_path, monkeypatch) -> None:
    rig = rigs()
    gate, entered = threading.Event(), threading.Event()
    capture_a, capture_b = _FlakyCapture(fail="isOpened"), Capture()
    opened: list = []

    def factory(*_args, **_kwargs):
        if not opened:
            opened.append(capture_a)
            entered.set()
            assert gate.wait(WAIT)                                 # A is inside its open
            return capture_a
        opened.append(capture_b)
        return capture_b

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", factory)
    upload = tmp_path / "a.mp4"
    upload.write_bytes(b"x")
    token_a = rig.service.claim_start()
    a = _thread(rig.service.start_file, upload, token_a)
    assert entered.wait(WAIT)
    rig.service.stop()                                             # A stopped during its open
    status = rig.service.get_status()
    assert status.state is VideoState.ERROR and "still opening" in status.error
    assert not rig.service.start_webcam()                          # B refused while A's open is pending
    gate.set()
    assert _joined(a)
    assert capture_a.release_count == 1 and rig.state() is VideoState.IDLE and not upload.exists()
    assert rig.service.start_webcam() and _wait_until(lambda: rig.state() is VideoState.RUNNING)
    assert capture_b.release_count == 0


def test_a_stale_reservation_is_distinguished_from_a_general_error(rigs, tmp_path) -> None:
    rig = rigs()
    token = rig.service.claim_start()
    rig.service.stop()
    with pytest.raises(StaleStartError):
        rig.service.start_file(tmp_path / "late.mp4", token)       # stale: nothing opened
    assert rig.opened == []
    general = rigs(captures=[_FlakyCapture(fail="isOpened")])
    token = general.service.claim_start()
    general.service.start_file(tmp_path / "late.mp4", token)       # general: open-failure contract
    assert general.service.get_status().state is VideoState.ERROR


# ── delta 2: single-owner capture cleanup with retry ─────────────────────────

class _OwnedCapture(Capture):
    """Counts release ATTEMPTS and successful releases; can fail a number of releases,
    hold the opener inside isOpened(), and flags a release while the opener uses it."""

    def __init__(self, *, fail: str | None = None, release_failures: int = 0,
                 open_gate: threading.Event | None = None) -> None:
        super().__init__()
        self.fail, self.release_failures, self.open_gate = fail, release_failures, open_gate
        self.in_open = threading.Event()
        self.using = False
        self.released_while_used = False
        self.release_attempts = 0

    def isOpened(self) -> bool:  # noqa: N802
        self.using = True
        try:
            self.in_open.set()
            if self.open_gate is not None:
                assert self.open_gate.wait(WAIT)
            if self.fail == "isOpened":
                raise RuntimeError(SECRET)
            return self.fail != "closed"
        finally:
            self.using = False

    def get(self, prop: int) -> float:
        self.using = True
        try:
            if self.fail == "get":
                raise RuntimeError(SECRET)
            return 200.0
        finally:
            self.using = False

    def release(self) -> None:
        self.release_attempts += 1
        if self.using:
            self.released_while_used = True
        if self.release_failures:
            self.release_failures -= 1
            raise RuntimeError(SECRET)
        self.release_count += 1


def _start(rig, source: str, tmp_path):
    if source == "file":
        upload = tmp_path / "owned.mp4"
        upload.write_bytes(b"x")
        rig.service.start_file(upload, rig.service.claim_start())
        return upload
    assert rig.service.start_webcam()
    return None


PRE_ATTACH = [("file", "get", {}), ("file", "isOpened", {}), ("webcam", "closed", {}),
              ("webcam", "thread", {"thread_factory": _broken_thread_factory}),
              ("file", "start", {"thread_factory": _FailingThread})]


@pytest.mark.parametrize("source,fail,kwargs", PRE_ATTACH)
def test_a_failed_release_stays_pending_blocks_admission_and_stop_retries_it(rigs, tmp_path, caplog, source,
                                                                           fail, kwargs) -> None:
    capture = _OwnedCapture(fail=None if fail in ("thread", "start") else fail, release_failures=1)
    rig = rigs(captures=[capture], **kwargs)
    with caplog.at_level(logging.DEBUG):
        upload = _start(rig, source, tmp_path)
    # 1. release failed once -> ERROR, the capture obligation is still recorded
    status = rig.service.get_status()
    assert status.state is VideoState.ERROR and "cleanup failed" in status.error
    diagnostics = rig.service.get_cleanup_diagnostics()
    assert diagnostics["capture_pending"] == 1 and diagnostics["pending_sinks"] == []
    assert (capture.release_attempts, capture.release_count) == (1, 0)
    if upload is not None:
        assert not upload.exists() and diagnostics["upload_pending"] == 0
    # 2. admission is refused before the retry succeeded
    assert rig.service.claim_start() is None and not rig.service.start_webcam()
    ends = (rig.calls("buffer", "end"), rig.calls("detector", "end"))
    # 3. Stop retries the capture (only what is still pending) -> IDLE, admission opens
    rig.service.stop()
    assert (capture.release_attempts, capture.release_count) == (2, 1)
    assert (rig.calls("buffer", "end"), rig.calls("detector", "end")) == ends == (1, 1)
    assert rig.state() is VideoState.IDLE and rig.service.get_cleanup_diagnostics()["capture_pending"] == 0
    assert _no_secret(caplog.text)
    rig.service._thread_factory = threading.Thread                 # noqa: SLF001
    assert rig.service.start_webcam() and _wait_until(lambda: rig.state() is VideoState.RUNNING)
    assert capture.release_attempts == 2                           # a cleaned capture is never retried


def test_a_release_failing_repeatedly_keeps_the_obligation(rigs, tmp_path) -> None:
    capture = _OwnedCapture(fail="get", release_failures=3)
    rig = rigs(captures=[capture])
    _start(rig, "file", tmp_path)
    for attempt in (2, 3):
        rig.service.stop()
        assert rig.state() is VideoState.ERROR and capture.release_attempts == attempt
        assert rig.service.get_cleanup_diagnostics()["capture_pending"] == 1
        assert rig.service.claim_start() is None
    rig.service.stop()
    assert (capture.release_attempts, capture.release_count) == (4, 1) and rig.state() is VideoState.IDLE


@pytest.mark.parametrize("fail", [None, "isOpened"])
def test_a_stop_during_the_open_never_releases_the_capture_while_the_opener_uses_it(rigs, tmp_path,
                                                                                  fail) -> None:
    gate = threading.Event()
    capture = _OwnedCapture(fail=fail, open_gate=gate)
    rig = rigs(captures=[capture])
    upload = tmp_path / "held.mp4"
    upload.write_bytes(b"x")
    opener = _thread(rig.service.start_file, upload, rig.service.claim_start())
    assert capture.in_open.wait(WAIT)                              # the opener is inside isOpened()
    rig.service.stop()                                             # the rollback (Stop) runs now
    assert capture.release_attempts == 0                           # deferred to the opener
    assert "still opening" in rig.service.get_status().error and upload.exists()
    gate.set()
    assert _joined(opener)
    assert (capture.release_attempts, capture.release_count) == (1, 1)
    assert not capture.released_while_used and not upload.exists()
    assert rig.state() is VideoState.IDLE


def test_an_old_cleanup_never_touches_the_new_session_or_its_capture(rigs, tmp_path) -> None:
    capture_a, capture_b = _OwnedCapture(fail="get", release_failures=1), _OwnedCapture()
    rig = rigs(captures=[capture_a, capture_b])
    _start(rig, "file", tmp_path)
    session_a = rig.service._current                               # noqa: SLF001
    rig.service.stop()                                             # A's retry succeeds
    assert rig.state() is VideoState.IDLE
    assert rig.service.start_webcam() and _wait_until(lambda: rig.state() is VideoState.RUNNING)
    ends = (rig.calls("buffer", "end"), rig.calls("detector", "end"))
    rig.service._release_session(session_a)                        # noqa: SLF001  late retry of A
    rig.service._end_open(session_a)                               # noqa: SLF001
    rig.service._fail_start(session_a, "late")                     # noqa: SLF001
    assert capture_b.release_attempts == 0 and rig.state() is VideoState.RUNNING
    assert capture_a.release_attempts == 2                         # already cleaned: not retried
    assert (rig.calls("buffer", "end"), rig.calls("detector", "end")) == ends


def test_a_release_that_succeeds_at_once_happens_exactly_once(rigs, tmp_path) -> None:
    capture = _OwnedCapture(fail="get")
    rig = rigs(captures=[capture])
    _start(rig, "file", tmp_path)
    assert (capture.release_attempts, capture.release_count) == (1, 1)
    rig.service.stop()
    rig.service.stop()
    assert capture.release_attempts == 1 and rig.state() is VideoState.IDLE


def test_a_stop_retry_never_releases_a_capture_that_was_already_released(rigs, tmp_path) -> None:
    capture = _OwnedCapture(fail="get")
    rig = rigs(captures=[capture])
    rig.detector.end_errors = [RuntimeError("end")]                # another obligation fails
    upload = _start(rig, "file", tmp_path)
    assert rig.state() is VideoState.ERROR and rig.service.get_cleanup_diagnostics()["pending_sinks"] == ["detector"]
    assert (capture.release_attempts, capture.release_count) == (1, 1) and not upload.exists()
    rig.service.stop()                                             # retries only the detector
    assert capture.release_attempts == 1 and rig.calls("detector", "end") == 2
    assert rig.state() is VideoState.IDLE
