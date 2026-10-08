"""T3a2 session lifecycle of VideoService: deterministic gate tests.

Fake captures and recording sinks only; no webcam, video file decoding, model,
network or speaker. Gates (threading.Event) fix every interleaving; polling is only
used to wait for a state that a gate makes inevitable, always with a bound.
"""

from __future__ import annotations

from collections import deque
import logging
import threading
import time

import numpy as np
import pytest

from app.models.video import VideoState
from app.services.video_service import VideoService

FRAME = np.zeros((48, 64, 3), dtype=np.uint8)
WAIT = 5.0


def _wait_until(predicate, timeout: float = WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return predicate()


def _thread(target, *args) -> threading.Thread:
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


class Sink:
    """Records start/end/offer per session; optional gates and errors per method."""

    def __init__(self, name: str, log: list) -> None:
        self.name, self.log = name, log
        self.active: str | None = None
        self.offered: list[str] = []
        self.start_gate: threading.Event | None = None
        self.end_gate: threading.Event | None = None
        self.end_error: Exception | None = None
        self.fail_before_clear = False             # raise while still holding the session
        self.in_start, self.in_end = threading.Event(), threading.Event()

    def start_session(self, session_id: str) -> None:
        self.log.append((self.name, "start", session_id))
        self.in_start.set()
        if self.start_gate is not None:
            assert self.start_gate.wait(WAIT)
        self.active = session_id

    def end_session(self, session_id: str) -> None:
        self.log.append((self.name, "end", session_id))
        self.in_end.set()
        if self.end_gate is not None:
            assert self.end_gate.wait(WAIT)
        if self.end_error is not None and self.fail_before_clear:
            raise self.end_error
        if self.active == session_id:
            self.active = None
        if self.end_error is not None:
            raise self.end_error

    def offer(self, item) -> None:
        self.offered.append(getattr(item, "session_id", None) or getattr(item, "source_session_id", None))


class Capture:
    """Scripted capture. ``script`` items: a frame (ndarray), "eof", an Exception, or a
    threading.Event to block on before continuing with the next item. After the script:
    endless frames (``tail="frames"``) or EOF."""

    def __init__(self, script=(), *, tail: str = "frames", release_error: Exception | None = None,
                 opened: bool = True) -> None:
        self.opened = opened
        self.script = deque(script)
        self.tail = tail
        self.release_error = release_error
        self.release_count = 0
        self.reading = threading.Event()
        self.reading_now = False
        self.released_while_reading = False

    def isOpened(self) -> bool:  # noqa: N802
        return self.opened

    def get(self, prop: int) -> float:  # noqa: ARG002
        return 200.0                       # fast pacing for file playback

    def read(self):
        self.reading.set()
        self.reading_now = True
        try:
            return self._read()
        finally:
            self.reading_now = False

    def _read(self):
        while self.script:
            item = self.script.popleft()
            if isinstance(item, threading.Event):
                assert item.wait(WAIT)
                continue
            if isinstance(item, Exception):
                raise item
            return (False, None) if isinstance(item, str) else (True, item)   # "eof"
        if self.tail == "frames":
            time.sleep(0.002)
            return True, FRAME
        return False, None

    def release(self) -> None:
        if self.reading_now:
            self.released_while_reading = True
        self.release_count += 1
        if self.release_error is not None:
            raise self.release_error


class Rig:
    def __init__(self, monkeypatch, **service_kwargs) -> None:
        self.log: list = []
        self.buffer, self.detector = Sink("buffer", self.log), Sink("detector", self.log)
        self.scene, self.worker = Sink("scene", self.log), Sink("worker", self.log)
        self.captures: deque[Capture] = deque()
        self.opened: list[Capture] = []
        self.open_gate: threading.Event | None = None
        self.opening = threading.Event()

        def factory(_source=0, **_kwargs):
            self.opening.set()
            if self.open_gate is not None:
                assert self.open_gate.wait(WAIT)
            capture = self.captures.popleft() if self.captures else Capture()
            self.opened.append(capture)
            return capture

        monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", factory)
        self.service = VideoService(detector=self.detector, scene_classifier=self.scene,
                                    event_frame_buffer=self.buffer, evaluation_worker=self.worker,
                                    **service_kwargs)

    @property
    def sinks(self) -> tuple[Sink, ...]:
        return self.buffer, self.detector, self.scene, self.worker

    def state(self) -> VideoState:
        return self.service.get_status().state

    def ends(self, session_id: str) -> list[str]:
        return [name for name, kind, sid in self.log if kind == "end" and sid == session_id]

    def starts(self, session_id: str) -> list[str]:
        return [name for name, kind, sid in self.log if kind == "start" and sid == session_id]

    def start_file(self, tmp_path, capture: Capture, name: str = "clip.mp4"):
        self.captures.append(capture)
        upload = tmp_path / name
        upload.write_bytes(b"x")
        token = self.service.claim_start()
        assert token is not None
        session = self.service._current            # noqa: SLF001
        self.service.start_file(upload, token)
        return session, upload


@pytest.fixture()
def rigs(monkeypatch):
    created: list[Rig] = []

    def make(**kwargs) -> Rig:
        rig = Rig(monkeypatch, **kwargs)
        created.append(rig)
        return rig

    yield make
    for rig in created:
        for sink in rig.sinks:                     # never leave a test thread blocked
            for gate in (sink.start_gate, sink.end_gate):
                if gate is not None:
                    gate.set()
        if rig.open_gate is not None:
            rig.open_gate.set()
        rig.service.stop()


# ── acceptance 17: start of A held, then Stop ────────────────────────────────

def test_stop_during_a_held_start_invalidates_at_once_and_ends_every_started_sink(rigs) -> None:
    rig = rigs()
    gate = threading.Event()
    rig.detector.start_gate = gate                 # second sink in start order
    starter = _thread(rig.service.start_webcam)
    assert rig.detector.in_start.wait(WAIT)
    session = rig.service._current                 # noqa: SLF001
    stopper = _thread(rig.service.stop)

    # (a) invalidation is immediate and stop() waits on _lifecycle_lock, not on _lock
    assert _wait_until(lambda: session.released)
    assert rig.service._session_id is None         # noqa: SLF001
    assert rig.state() is VideoState.STOPPING      # get_status() takes _lock: it is free
    assert rig.service._lock.acquire(timeout=1.0)  # noqa: SLF001
    rig.service._lock.release()                    # noqa: SLF001
    stopper.join(0.2)
    assert stopper.is_alive()                      # held by the running start fan-out

    # (b) release the held start: the rest is skipped, every started sink is ended
    gate.set()
    stopper.join(WAIT)
    starter.join(WAIT)
    assert not stopper.is_alive() and not starter.is_alive()
    assert rig.starts(session.session_id) == ["buffer", "detector"]
    assert set(rig.ends(session.session_id)) == {"buffer", "detector", "scene", "worker"}
    assert all(sink.active is None for sink in rig.sinks)
    assert rig.state() is VideoState.IDLE
    assert session.thread is None                  # never revived into RUNNING
    assert [capture.release_count for capture in rig.opened] == [1]


def test_lifecycle_lock_is_never_taken_while_holding_the_state_lock(rigs) -> None:
    rig = rigs()
    original = rig.service._lifecycle_lock        # noqa: SLF001
    violations: list[str] = []

    class CheckedLock:
        def __enter__(self):
            if rig.service._lock.locked():         # noqa: SLF001
                violations.append(threading.current_thread().name)
            return original.__enter__()

        def __exit__(self, *exc):
            return original.__exit__(*exc)

    rig.service._lifecycle_lock = CheckedLock()   # noqa: SLF001
    assert rig.service.start_webcam()
    assert _wait_until(lambda: rig.detector.offered)
    rig.service.stop()
    assert violations == []


# ── Stop concurrent with EOF (both orders) ───────────────────────────────────

def test_eof_cleanup_first_then_stop_returns_only_after_that_cleanup(rigs, tmp_path) -> None:
    rig = rigs()
    read_gate, end_gate = threading.Event(), threading.Event()
    rig.worker.end_gate = end_gate                 # first sink in end order
    session, upload = rig.start_file(tmp_path, Capture([FRAME, read_gate, "eof"], tail="eof"))
    assert _wait_until(lambda: rig.detector.offered)
    read_gate.set()                                # EOF: the capture thread owns cleanup
    assert rig.worker.in_end.wait(WAIT)
    stopper = _thread(rig.service.stop)
    stopper.join(0.2)
    assert stopper.is_alive()                      # waits for the EOF cleanup to finish
    assert rig.state() is VideoState.STOPPING      # no IDLE window for a new session
    assert rig.service.claim_start() is None
    end_gate.set()
    stopper.join(WAIT)
    assert not stopper.is_alive()
    assert rig.state() is VideoState.IDLE
    assert sorted(rig.ends(session.session_id)) == ["buffer", "detector", "scene", "worker"]
    assert rig.opened[0].release_count == 1 and not upload.exists()


def test_stop_first_then_eof_never_finalizes_before_stop_cleanup_ends(rigs, tmp_path) -> None:
    rig = rigs()
    read_gate, end_gate = threading.Event(), threading.Event()
    session, _ = rig.start_file(tmp_path, Capture([FRAME, read_gate, "eof"], tail="eof"))
    assert _wait_until(lambda: rig.detector.offered)
    rig.worker.end_gate = end_gate
    stopper = _thread(rig.service.stop)
    assert _wait_until(lambda: session.released)  # stop() won the invalidation
    read_gate.set()                                # the capture thread now sees EOF
    assert rig.worker.in_end.wait(WAIT)            # stop() cleanup is ending the sinks
    session.thread.join(WAIT)
    assert not session.thread.is_alive()           # capture thread exited...
    assert rig.state() is VideoState.STOPPING      # ...without writing IDLE
    assert rig.service.claim_start() is None
    end_gate.set()
    stopper.join(WAIT)
    assert rig.state() is VideoState.IDLE
    assert rig.ends(session.session_id).count("worker") == 1


# ── A's finalization never overwrites B ──────────────────────────────────────

def test_waiter_timeout_keeps_error_and_refuses_b_until_cleanup_succeeds(rigs, tmp_path, caplog) -> None:
    rig = rigs(cleanup_wait_timeout_seconds=0.2)
    read_gate, end_gate = threading.Event(), threading.Event()
    rig.worker.end_gate = end_gate
    session_a, _ = rig.start_file(tmp_path, Capture([FRAME, read_gate, "eof"], tail="eof"))
    assert _wait_until(lambda: rig.detector.offered)
    read_gate.set()                                # A's EOF cleanup blocks in a sink
    assert rig.worker.in_end.wait(WAIT)
    with caplog.at_level(logging.WARNING):
        rig.service.stop()                         # bounded wait expires; logged
    assert "cleanup did not finish in time" in caplog.text
    status = rig.service.get_status()
    assert status.state is VideoState.ERROR and "has not finished" in status.error
    assert session_a.cleanup_ok is None            # never reported as successful
    assert rig.service.claim_start() is None       # B refused
    assert not rig.service.start_webcam()
    end_gate.set()                                 # A's cleanup completes and finalizes consistently
    assert session_a.cleanup_done.wait(WAIT)
    assert _wait_until(lambda: rig.state() is VideoState.IDLE)
    assert session_a.cleanup_ok is True
    rig.worker.end_gate = None
    assert rig.service.start_webcam()              # now B may start
    session_b = rig.service._current               # noqa: SLF001
    assert _wait_until(lambda: rig.state() is VideoState.RUNNING)
    session_a.thread.join(WAIT)
    status = rig.service.get_status()
    assert (status.state, status.source, status.error) == (VideoState.RUNNING, "webcam", None)
    assert all(sink.active == session_b.session_id for sink in rig.sinks)


# ── capture A comes back after a join timeout ────────────────────────────────

@pytest.mark.parametrize("late", ["frame", "eof", "error"])
def test_join_timeout_defers_release_to_the_capture_thread_and_refuses_b(rigs, caplog, late) -> None:
    rig = rigs(capture_join_timeout_seconds=0.1)
    release_a = threading.Event()
    outcome = {"frame": FRAME, "eof": "eof", "error": RuntimeError("late driver error")}[late]
    capture_a = Capture([FRAME, release_a, outcome], tail="eof")
    rig.captures.append(capture_a)
    assert rig.service.start_webcam()
    session_a = rig.service._current              # noqa: SLF001
    assert _wait_until(lambda: rig.detector.offered)
    assert _wait_until(lambda: capture_a.reading_now)
    mark_detector, mark_buffer = len(rig.detector.offered), len(rig.buffer.offered)
    with caplog.at_level(logging.WARNING):
        rig.service.stop()                         # A's thread is still inside read()
    assert "did not stop in time" in caplog.text and session_a.thread.is_alive()
    status = rig.service.get_status()
    assert status.state is VideoState.ERROR and "cleanup waits for it to exit" in status.error
    assert capture_a.release_count == 0            # never released under a running read
    assert session_a.cleanup_ok is None
    assert all(sink.active is None for sink in rig.sinks)   # sinks already ended
    assert rig.service.claim_start() is None       # B refused while A holds its capture
    assert rig.service.get_latest_packet() is None
    release_a.set()                                # A's read returns a late frame / EOF / error
    session_a.thread.join(WAIT)
    assert not session_a.thread.is_alive()
    assert _wait_until(lambda: rig.state() is VideoState.IDLE)   # the capture thread finished cleanup
    assert capture_a.release_count == 1 and not capture_a.released_while_reading
    assert session_a.cleanup_ok is True
    assert rig.detector.offered[mark_detector:] == [] and rig.buffer.offered[mark_buffer:] == []
    assert rig.service.get_latest_packet() is None # nothing of A was published after its stop
    capture_b = Capture()
    rig.captures.append(capture_b)
    assert rig.service.start_webcam()
    session_b = rig.service._current               # noqa: SLF001
    assert _wait_until(lambda: rig.detector.offered.count(session_b.session_id) >= 2)
    assert set(rig.detector.offered[mark_detector:]) == {session_b.session_id}
    assert rig.service.get_latest_packet().session_id == session_b.session_id
    assert capture_b.release_count == 0


# ── cleanup failure ──────────────────────────────────────────────────────────

def test_failing_capture_release_fails_cleanup_and_each_stop_retries_once(rigs, caplog) -> None:
    rig = rigs()
    capture = Capture(release_error=RuntimeError("release failed"))
    rig.captures.append(capture)
    assert rig.service.start_webcam()
    session = rig.service._current                 # noqa: SLF001
    assert _wait_until(lambda: rig.detector.offered)
    with caplog.at_level(logging.ERROR):
        rig.service.stop()
    status = rig.service.get_status()
    assert status.state is VideoState.ERROR and "cleanup failed" in status.error
    assert session.cleanup_done.is_set() and session.cleanup_ok is False   # attempt ended, not succeeded
    assert "Unable to release video capture" in caplog.text
    assert sorted(rig.ends(session.session_id)) == ["buffer", "detector", "scene", "worker"]  # others cleaned
    assert rig.service.claim_start() is None       # B refused
    rig.service.stop()                             # manual retry: the release is attempted once more
    assert capture.release_count == 2 and rig.state() is VideoState.ERROR
    assert sorted(rig.ends(session.session_id)) == ["buffer", "detector", "scene", "worker"]  # not re-ended
    capture.release_error = None
    rig.service.stop()                             # retry succeeds
    assert capture.release_count == 3 and session.cleanup_ok is True
    assert rig.state() is VideoState.IDLE
    assert rig.service.start_webcam()              # the service is usable again
    assert _wait_until(lambda: rig.state() is VideoState.RUNNING)


def test_a_sink_that_fails_before_clearing_its_session_fails_cleanup(rigs, caplog) -> None:
    rig = rigs()
    rig.detector.end_error, rig.detector.fail_before_clear = RuntimeError("end failed"), True
    assert rig.service.start_webcam()
    session = rig.service._current                 # noqa: SLF001
    assert _wait_until(lambda: rig.detector.offered)
    with caplog.at_level(logging.ERROR):
        rig.service.stop()
    assert "Unable to end detector session" in caplog.text
    status = rig.service.get_status()
    assert status.state is VideoState.ERROR and session.cleanup_ok is False
    assert rig.detector.active == session.session_id          # the sink still holds A
    assert [s.active for s in (rig.buffer, rig.scene, rig.worker)] == [None, None, None]
    assert rig.opened[0].release_count == 1                    # the capture was still released
    assert rig.service.claim_start() is None
    rig.detector.end_error = None
    rig.service.stop()                             # retry ends only the pending sink
    assert rig.ends(session.session_id).count("detector") == 2
    assert rig.ends(session.session_id).count("worker") == 1
    assert rig.detector.active is None and rig.state() is VideoState.IDLE


# ── a stop() that wins during a slow open is never revived ───────────────────

def test_stop_during_a_slow_webcam_open_is_never_revived(rigs) -> None:
    rig = rigs()
    rig.open_gate = threading.Event()
    starter = _thread(rig.service.start_webcam)
    assert rig.opening.wait(WAIT)
    session = rig.service._current                 # noqa: SLF001
    rig.service.stop()
    rig.open_gate.set()
    starter.join(WAIT)
    assert rig.state() is VideoState.IDLE and session.thread is None
    assert [capture.release_count for capture in rig.opened] == [1]
    assert rig.service.get_latest_packet() is None


def test_stop_during_a_slow_file_open_is_never_revived_and_deletes_the_upload(rigs, tmp_path) -> None:
    rig = rigs()
    rig.open_gate = threading.Event()
    upload = tmp_path / "clip.mp4"
    upload.write_bytes(b"x")
    token = rig.service.claim_start()
    session = rig.service._current                 # noqa: SLF001
    starter = _thread(rig.service.start_file, upload, token)
    assert rig.opening.wait(WAIT)
    rig.service.stop()
    rig.open_gate.set()
    starter.join(WAIT)
    assert rig.state() is VideoState.IDLE and session.thread is None
    assert [capture.release_count for capture in rig.opened] == [1] and not upload.exists()


def test_capture_error_with_a_concurrent_stop_ends_idle_with_one_cleanup(rigs) -> None:
    rig = rigs()
    error_gate = threading.Event()
    rig.captures.append(Capture([FRAME, error_gate, RuntimeError("driver error")], tail="eof"))
    assert rig.service.start_webcam()
    session = rig.service._current                 # noqa: SLF001
    assert _wait_until(lambda: rig.detector.offered)
    stopper = _thread(rig.service.stop)
    error_gate.set()
    stopper.join(WAIT)
    session.thread.join(WAIT)
    assert rig.state() is VideoState.IDLE
    assert sorted(rig.ends(session.session_id)) == ["buffer", "detector", "scene", "worker"]
    assert rig.opened[0].release_count == 1


@pytest.mark.parametrize("kwargs", [
    {"capture_join_timeout_seconds": 0}, {"capture_join_timeout_seconds": float("nan")},
    {"capture_join_timeout_seconds": True}, {"cleanup_wait_timeout_seconds": -1},
    {"cleanup_wait_timeout_seconds": float("inf")},
])
def test_invalid_lifecycle_timeouts_are_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        VideoService(**kwargs)


class HeldEvent(threading.Event):
    """cleanup_done whose waiter, after waking, is held until ``hold`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.woke, self.hold = threading.Event(), threading.Event()

    def wait(self, timeout=None):
        done = super().wait(timeout)
        self.woke.set()
        assert self.hold.wait(WAIT)
        return done


def test_a_stop_that_wakes_after_b_started_never_finalizes_b(rigs, tmp_path) -> None:
    rig = rigs()
    read_gate, end_gate = threading.Event(), threading.Event()
    rig.worker.end_gate = end_gate
    session_a, _ = rig.start_file(tmp_path, Capture([FRAME, read_gate, "eof"], tail="eof"))
    held = HeldEvent()
    session_a.cleanup_done = held
    assert _wait_until(lambda: rig.detector.offered)
    read_gate.set()                                # A's EOF wins the cleanup
    assert rig.worker.in_end.wait(WAIT)
    stopper = _thread(rig.service.stop)            # loses; waits for A's cleanup
    rig.worker.end_gate = None
    end_gate.set()
    assert held.woke.wait(WAIT)                    # stop() woke but is held before its final write
    assert _wait_until(lambda: rig.state() is VideoState.IDLE)   # A's EOF finalized A
    assert rig.service.start_webcam()              # B starts in that window
    assert _wait_until(lambda: rig.state() is VideoState.RUNNING)
    held.hold.set()
    stopper.join(WAIT)
    status = rig.service.get_status()
    assert (status.state, status.source) == (VideoState.RUNNING, "webcam")


def test_stop_during_a_slow_open_that_then_fails_is_not_turned_into_error(rigs) -> None:
    rig = rigs()
    rig.open_gate = threading.Event()
    rig.captures.append(Capture(opened=False))
    starter = _thread(rig.service.start_webcam)
    assert rig.opening.wait(WAIT)
    rig.service.stop()
    rig.open_gate.set()
    starter.join(WAIT)
    status = rig.service.get_status()
    assert (status.state, status.error) == (VideoState.IDLE, None)
    assert [capture.release_count for capture in rig.opened] == [1]


def test_a_thread_leaving_while_the_runner_still_ends_sinks_is_released_by_the_runner(rigs) -> None:
    rig = rigs(capture_join_timeout_seconds=0.1)
    release_a, end_gate = threading.Event(), threading.Event()
    capture_a = Capture([FRAME, release_a, "eof"], tail="eof")
    rig.captures.append(capture_a)
    assert rig.service.start_webcam()
    session_a = rig.service._current               # noqa: SLF001
    assert _wait_until(lambda: capture_a.reading_now and rig.detector.offered)
    rig.worker.end_gate = end_gate                 # the runner will block while ending sinks
    stopper = _thread(rig.service.stop)
    assert rig.worker.in_end.wait(WAIT)            # join timed out, release deferred, sinks ending
    assert session_a.deferred and capture_a.release_count == 0
    release_a.set()                                # the thread leaves while the runner is busy
    session_a.thread.join(WAIT)
    assert not session_a.thread.is_alive() and session_a.cleanup_running   # it could not take over
    end_gate.set()
    stopper.join(WAIT)
    assert capture_a.release_count == 1 and not capture_a.released_while_reading
    assert session_a.cleanup_ok is True and rig.state() is VideoState.IDLE


# ── handoff of a deferred release between the runner and the exiting thread ──

class HandoffLock:
    """Wraps VideoService._lock. Once armed for one thread, its next ``with`` section is
    paused: "before" the acquire, "inside" while holding the lock, or "after" the
    release. "after" is the exact window Codex reproduced on the non-atomic handoff:
    the runner has evaluated "thread left?" as False and released the lock, but has not
    yet given up ownership — the capture thread exits right there."""

    def __init__(self, inner, mode: str) -> None:
        self.inner, self.mode = inner, mode
        self.armed_for: threading.Thread | None = None
        self.paused, self.resume = threading.Event(), threading.Event()

    def _pause_if_armed(self) -> None:
        if self.armed_for is threading.current_thread():
            self.armed_for = None
            self.paused.set()
            assert self.resume.wait(WAIT)

    def __enter__(self):
        if self.mode == "before":
            self._pause_if_armed()
        self.inner.acquire()
        if self.mode == "inside":
            self._pause_if_armed()
        return self

    def __exit__(self, *exc):
        self.inner.release()
        if self.mode == "after":
            self._pause_if_armed()
        return False

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        return self.inner.acquire(blocking, timeout)

    def release(self) -> None:
        self.inner.release()

    def locked(self) -> bool:
        return self.inner.locked()


@pytest.mark.parametrize("mode", ["before", "inside", "after"])
def test_capture_leaving_at_the_runner_handoff_still_gets_exactly_one_owner(rigs, mode) -> None:
    rig = rigs(capture_join_timeout_seconds=0.1)
    release_a = threading.Event()
    capture_a = Capture([FRAME, release_a, "eof"], tail="eof")
    rig.captures.append(capture_a)
    assert rig.service.start_webcam()
    session_a = rig.service._current               # noqa: SLF001
    assert _wait_until(lambda: capture_a.reading_now and rig.detector.offered)
    lock = HandoffLock(rig.service._lock, mode)    # noqa: SLF001
    rig.service._lock = lock                       # noqa: SLF001
    original_end_sinks = rig.service._end_sinks    # noqa: SLF001

    def end_sinks_then_arm(session):
        original_end_sinks(session)
        lock.armed_for = threading.current_thread()   # the runner's next _lock use is the handoff

    rig.service._end_sinks = end_sinks_then_arm    # noqa: SLF001
    stopper = _thread(rig.service.stop)
    assert lock.paused.wait(WAIT)                  # runner at the handoff, release deferred
    assert session_a.deferred and capture_a.release_count == 0
    if mode != "inside":                           # "inside" holds _lock: never call into the service
        assert rig.service.claim_start() is None   # B refused meanwhile
    release_a.set()                                # the capture thread leaves now
    if mode != "inside":
        session_a.thread.join(WAIT)                # its exit hook ran while the runner still owned
        assert not session_a.thread.is_alive() and session_a.capture_exited
    else:
        session_a.thread.join(0.2)
        assert session_a.thread.is_alive()         # its exit hook waits for the runner's section
    lock.resume.set()
    stopper.join(WAIT)
    session_a.thread.join(WAIT)
    assert not stopper.is_alive() and not session_a.thread.is_alive()
    assert _wait_until(lambda: session_a.cleanup_ok is True)
    assert capture_a.release_count == 1 and not capture_a.released_while_reading
    assert _wait_until(lambda: rig.state() is VideoState.IDLE)
    assert rig.service.start_webcam()              # B accepted only after A's cleanup succeeded
    assert _wait_until(lambda: rig.state() is VideoState.RUNNING)


def test_stop_resumes_a_deferred_release_whose_handoff_was_lost(rigs) -> None:
    rig = rigs(capture_join_timeout_seconds=0.1)
    release_a = threading.Event()
    capture_a = Capture([FRAME, release_a, "eof"], tail="eof")
    rig.captures.append(capture_a)
    assert rig.service.start_webcam()
    session_a = rig.service._current               # noqa: SLF001

    def exit_without_takeover(session):            # simulate a lost handoff
        with rig.service._lock:                    # noqa: SLF001
            session.capture_exited = True

    rig.service._on_capture_exit = exit_without_takeover   # noqa: SLF001
    assert _wait_until(lambda: capture_a.reading_now and rig.detector.offered)
    rig.service.stop()                             # join times out: release deferred
    release_a.set()
    session_a.thread.join(WAIT)
    assert session_a.deferred and session_a.capture_exited and session_a.cleanup_ok is None
    assert rig.state() is VideoState.ERROR and rig.service.claim_start() is None
    rig.service.stop()                             # explicit stop resumes the deferred release
    assert capture_a.release_count == 1 and session_a.cleanup_ok is True
    assert rig.state() is VideoState.IDLE
    assert rig.service.start_webcam()
