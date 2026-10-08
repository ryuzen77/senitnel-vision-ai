"""
Sentinel Vision AI — VideoService

Design constraints (Rev 4 — Corrective patch):

  * Cleanup (_run_cleanup(), one runner per attempt) never writes _state directly;
    _finalize_locked() derives the state from the attempt's result: IDLE only after a
    successful cleanup, otherwise ERROR.

  * _packet is protected exclusively by _packet_cond (threading.Condition).
    _lock is NEVER held while interacting with _packet_cond.

  * Cleanup skips thread.join() when run by the capture thread itself (self-join
    guard via threading.current_thread()) and for a thread that was never started.

  * Start operations are atomic reservations: _atomic_reserve() moves state
    IDLE → STARTING under _lock before any I/O, so two concurrent callers cannot
    both observe IDLE.

  * File playback uses a monotonic deadline loop — deadline is recorded before
    cap.read(); sleep only for the remaining time after encode.

  * Webcam enters ERROR only after WEBCAM_MAX_CONSECUTIVE_FAILURES consecutive
    False reads, not on the first failure.

  * Public wait_for_packet(after_seq, timeout) exposes Condition-based waiting
    without leaking _packet_cond to the API layer.

  * claim_start() is a thin public wrapper around _atomic_reserve() for use by
    the upload route handler (two-phase start).

  * start_webcam() returns True if the reservation was acquired (and the start
    attempt was made), False if the service was not IDLE.  The caller can use
    this to issue HTTP 409 without a separate non-atomic status check.

  * A session token (_start_token) is issued on every claim_start() /
    start_webcam() call and consumed by start_file() and the stop/upload race
    guard.  This prevents a stop() that races an upload from reviving the
    service into RUNNING.

  * On file EOF, _source, _error, and the packet slot are all cleared so the
    IDLE state is fully clean.

Session lifecycle (Rev 5 — T3a2):

  * Every reservation is a _CaptureSession with its own stop_event, resources and
    cleanup_done event. Capture threads receive their session and never read the
    shared _stop_event, so a thread that outlives a join timeout can neither label
    frames with a newer session id nor release a newer session's resources.

  * Invalidation is immediate (under _lock). The first caller to invalidate a session
    runs its cleanup; a later caller waits for that attempt outside every lock
    (bounded, logged). State is derived from the cleanup result and written only for
    the current session: IDLE only after a successful cleanup, otherwise ERROR, so a
    new session cannot start until the old one is cleaned. A capture that is still
    reading after the join timeout is never released concurrently: its release is
    deferred to the capture thread's own exit (once). Each explicit stop() retries
    failed cleanup steps once; nothing is retried automatically.

  * Sink start/end fan-outs are serialized by _lifecycle_lock with a per-call alive
    check. Lock order: _lifecycle_lock -> _lock -> service locks; _lock is never held
    while acquiring _lifecycle_lock, and neither lock is held during a join, capture
    release, file deletion, HTTP or device call.

  * A stop() that wins during a slow webcam/file open is honoured: the opener
    releases its capture and nothing is revived.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import cv2

from app.models.video import FramePacket, VideoStartResponse, VideoState, VideoStatusResponse
from app.models.event_frame import BufferedFrame
from app.services.event_frame_buffer import EventFrameBuffer

if TYPE_CHECKING:
    from app.services.detector_service import DetectorService


class FramePacketConsumer(Protocol):
    """Small nonblocking capture consumer contract used by optional workers."""

    def start_session(self, session_id: str) -> None: ...

    def offer(self, packet: FramePacket) -> None: ...

    def end_session(self, session_id: str) -> None: ...


class SessionLifecycleConsumer(Protocol):
    """Session-only consumer; never receives capture frames."""

    def start_session(self, session_id: str) -> None: ...

    def end_session(self, session_id: str) -> None: ...

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_FILE_FPS: float = 25.0
WEBCAM_MAX_CONSECUTIVE_FAILURES: int = 5

# Absolute path anchored to this file — stable regardless of CWD.
UPLOAD_DIR: Path = Path(__file__).resolve().parent.parent / "storage" / "uploads"

logger = logging.getLogger(__name__)

_STARTUP_MILESTONES = frozenset({
    "upload_handler_entered", "upload_copy_completed", "first_opencv_probe_completed",
    "state_running", "first_packet_published", "first_mjpeg_packet_yielded",
})


def _startup_timing_enabled() -> bool:
    configured = os.getenv("SENTINEL_VIDEO_STARTUP_TIMING_ENABLED", "false")
    if configured == "true":
        return True
    if configured != "false":
        try:
            logger.warning("Video startup timing disabled: invalid configuration")
        except Exception:
            pass
    return False


class _StartupTimingRecorder:
    """One upload session's metadata-only milestones; no frame or path references."""

    def __init__(self, session_id: str, token: str) -> None:
        self.session_id = session_id
        self.token = token
        self._lock = threading.Lock()
        self._origin: float | None = None
        self._seen: set[str] = set()
        self._closed = False

    def record(self, milestone: str, at: float) -> float | None:
        if milestone not in _STARTUP_MILESTONES or not math.isfinite(at):
            return None
        with self._lock:
            if self._closed or milestone in self._seen:
                return None
            if self._origin is None:
                if milestone != "upload_handler_entered":
                    return None
                self._origin = at
            elapsed_ms = max(0.0, (at - self._origin) * 1000.0)
            self._seen.add(milestone)
            return elapsed_ms

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._seen.clear()


# ---------------------------------------------------------------------------
# VideoService
# ---------------------------------------------------------------------------


CAPTURE_JOIN_TIMEOUT_SECONDS: float = 3.0
CLEANUP_WAIT_MARGIN_SECONDS: float = 2.0


def _positive_seconds(name: str, value: object) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be a finite number greater than 0")
    return float(value)


@dataclasses.dataclass(eq=False)
class _CaptureSession:
    """One reservation: identity, own stop signal, owned resources, cleanup bookkeeping.

    Every mutable field is guarded by ``VideoService._lock``, except the step results
    (``cap_released``, ``upload_removed``, ``pending_sinks``) which only the single
    cleanup runner (``cleanup_running``) writes. Resources are attached only while
    ``released`` is False, so after release they never change again.

    ``cleanup_done`` means "an attempt ended" (successfully or not); success is
    ``cleanup_ok is True``, computed from facts: capture released, upload removed, no
    sink left to end and no unexpected failure. ``cleanup_ok`` is None while an
    attempt runs or while the capture release is deferred to the capture thread's exit.
    """

    session_id: str
    token: str
    stop_event: threading.Event = dataclasses.field(default_factory=threading.Event)
    cleanup_done: threading.Event = dataclasses.field(default_factory=threading.Event)
    released: bool = False
    cap: object | None = None
    thread: threading.Thread | None = None
    upload_path: Path | None = None
    timing: _StartupTimingRecorder | None = None
    stop_requested: bool = False        # an explicit stop() asked for this session to end
    end_reason: str | None = None       # "eof" or "error" when the capture/open ended it
    capture_error: str | None = None
    capture_exited: bool = False        # the capture thread has left its loop
    cleanup_running: bool = False
    cleanup_ok: bool | None = None
    deferred: bool = False              # capture/upload release waits for the thread to exit
    unexpected_failure: bool = False
    cap_released: bool = False
    upload_removed: bool = False
    pending_sinks: set = dataclasses.field(default_factory=set)


class VideoService:
    """
    Owns the complete video capture lifecycle:
      IDLE → STARTING → RUNNING → STOPPING → IDLE
                                           → ERROR → (stop) → IDLE

    Every reservation is a ``_CaptureSession``. Invalidation is immediate (under
    ``_lock``); the first caller to invalidate a session owns its cleanup and its
    final state write, and a later caller waits (bounded, outside every lock) for that
    cleanup to finish. Sink start/end fan-outs are serialized by ``_lifecycle_lock``.
    Lock order: ``_lifecycle_lock`` → ``_lock`` → service locks; ``_packet_cond`` is
    never taken while ``_lock`` is held.
    """

    def __init__(
        self,
        detector: DetectorService | None = None,
        scene_classifier: FramePacketConsumer | None = None,
        event_frame_buffer: EventFrameBuffer | None = None,
        evaluation_worker: SessionLifecycleConsumer | None = None,
        startup_timing_enabled: bool | None = None,
        *,
        capture_join_timeout_seconds: float = CAPTURE_JOIN_TIMEOUT_SECONDS,
        cleanup_wait_timeout_seconds: float | None = None,
    ) -> None:
        self._join_timeout = _positive_seconds("capture_join_timeout_seconds", capture_join_timeout_seconds)
        self._cleanup_wait = (
            self._join_timeout + CLEANUP_WAIT_MARGIN_SECONDS if cleanup_wait_timeout_seconds is None
            else _positive_seconds("cleanup_wait_timeout_seconds", cleanup_wait_timeout_seconds))
        self._lock = threading.Lock()
        # Serializes the sink start/end fan-outs; never acquired while _lock is held.
        self._lifecycle_lock = threading.Lock()
        self._detector = detector
        self._scene_classifier = scene_classifier
        self._event_frame_buffer = event_frame_buffer
        self._evaluation_worker = evaluation_worker
        self._startup_timing_enabled = (_startup_timing_enabled() if startup_timing_enabled is None
                                        else startup_timing_enabled)
        self._startup_timing: _StartupTimingRecorder | None = None

        # State fields — all guarded by _lock
        self._state: VideoState = VideoState.IDLE
        self._source: str | None = None
        self._error: str | None = None
        self._current: _CaptureSession | None = None
        # Service-level "no active session" signal for wait_for_packet(); capture
        # threads never read it (each uses its own session's stop_event).
        self._stop_event: threading.Event = threading.Event()

        # Session token — changes on every successful reservation.
        # Used to detect stop/upload races: if the token has changed by the
        # time start_file() hands off, a stop() has intervened.
        self._start_token: str | None = None
        self._session_id: str | None = None

        # Packet slot — guarded exclusively by _packet_cond
        self._packet_cond: threading.Condition = threading.Condition()
        self._packet: FramePacket | None = None

    # ------------------------------------------------------------------
    # Session lifecycle helpers
    # ------------------------------------------------------------------

    def _sinks_in_start_order(self) -> tuple[tuple[str, object], ...]:
        return tuple((label, sink) for label, sink in (
            ("event frame buffer", self._event_frame_buffer), ("detector", self._detector),
            ("scene classifier", self._scene_classifier), ("evaluation worker", self._evaluation_worker),
        ) if sink is not None)

    def _sinks_in_end_order(self) -> tuple[tuple[str, object], ...]:
        # Cancel evaluation while the parking trigger still has its pending
        # event; detector.end_session() subsequently clears that trigger.
        return tuple((label, sink) for label, sink in (
            ("evaluation worker", self._evaluation_worker), ("detector", self._detector),
            ("scene classifier", self._scene_classifier), ("event frame buffer", self._event_frame_buffer),
        ) if sink is not None)

    def _start_sinks(self, session: _CaptureSession) -> None:
        """Start every sink for ``session``; stop as soon as the session is invalidated."""
        with self._lifecycle_lock:
            for label, sink in self._sinks_in_start_order():
                with self._lock:
                    alive = self._current is session and not session.released
                if not alive:
                    return                       # a stop() already won: never start the rest
                try:
                    # In-memory slot switches only; model work stays in the sinks' workers.
                    sink.start_session(session.session_id)
                except Exception:
                    # Optional consumers must not make video start fail.
                    logger.exception("Unable to start %s session", label)

    def _end_sinks(self, session: _CaptureSession) -> None:
        """End every sink still pending for ``session``; a failing sink stays pending
        (it is retried only by an explicit stop()) and the others are still ended."""
        with self._lifecycle_lock:
            for label, sink in self._sinks_in_end_order():
                if label not in session.pending_sinks:
                    continue
                try:
                    sink.end_session(session.session_id)    # sinks ignore a non-matching id
                except Exception:
                    # Lifecycle failures never stop the remaining cleanup steps.
                    logger.exception("Unable to end %s session", label)
                else:
                    session.pending_sinks.discard(label)

    def _invalidate_locked(self, session: _CaptureSession) -> bool:
        """Caller holds _lock. Immediate, O(1); True only for the first caller, which
        becomes the cleanup runner."""
        if session.released:
            return False
        session.released = True
        session.stop_event.set()
        session.pending_sinks = {label for label, _sink in self._sinks_in_end_order()}
        session.cleanup_running = True
        session.cleanup_done.clear()
        if self._current is session:
            self._session_id = None
            self._startup_timing = None
            self._stop_event.set()
        return True

    def _release_session(self, session: _CaptureSession) -> bool:
        """Invalidate ``session`` and run its cleanup, or wait for the runner.

        Returns True when this caller ran a cleanup attempt. An explicit stop() with no
        runner active resumes the cleanup once (manual recovery; never automatic) when
        it failed, or when a release is still deferred although the capture thread has
        already left. A caller that does not run waits for the running attempt outside
        every lock, bounded by the cleanup wait (logged if exceeded). The capture thread
        never waits for its own session.
        """
        with self._lock:
            run = self._invalidate_locked(session)
            resumable = (session.cleanup_ok is False
                         or (session.deferred and session.capture_exited))
            if not run and session.stop_requested and not session.cleanup_running and resumable:
                session.cleanup_running = True
                session.cleanup_done.clear()
                run = True
            waiting = not run and session.cleanup_running
        if run:
            self._run_cleanup(session, full=True)
            return True
        if waiting and threading.current_thread() is not session.thread:
            if not session.cleanup_done.wait(self._cleanup_wait):
                self._log("Video session cleanup did not finish in time")
        return False

    def _run_cleanup(self, session: _CaptureSession, *, full: bool) -> None:
        """One cleanup attempt by the single runner; never holds _lock across a step.

        ``full`` also joins the thread, clears the packet slot and ends the sinks; a
        capture-only pass (the capture thread finishing a deferred release) releases
        the capture and the upload.

        Handoff (atomic with ``_on_capture_exit``): "has the capture thread left?" and
        releasing ``cleanup_running`` happen in ONE ``_lock`` section. If the thread left
        before it, the runner keeps ownership and does the capture-only pass itself;
        otherwise the runner releases ownership and the exiting thread, which takes the
        same lock afterwards, finds the deferred release unowned and takes it over.
        Either way exactly one owner finishes it; releases never run under ``_lock``.
        """
        passes = 0
        while True:
            try:
                if full and passes == 0:
                    self._cleanup_full_pass(session)
                else:
                    self._release_capture_and_upload(session)
            except Exception:
                session.unexpected_failure = True
                logger.exception("Video session cleanup failed")
            passes += 1
            with self._lock:
                resume = session.deferred and session.capture_exited and passes < 2
                if resume:
                    session.deferred = False     # still the owner: release now, outside the lock
                else:
                    session.cleanup_running = False
                    session.cleanup_ok = None if session.deferred else self._cleanup_complete(session)
                    self._finalize_locked(session)
            if not resume:
                break
        session.cleanup_done.set()

    def _cleanup_full_pass(self, session: _CaptureSession) -> None:
        if session.timing is not None:
            try:
                session.timing.close()
            except Exception:
                pass
        thread = session.thread
        current = threading.current_thread()
        if thread is not None and thread is not current and thread.ident is not None:
            thread.join(timeout=self._join_timeout)
        with self._lock:
            # Never release a capture another thread may still be reading.
            session.deferred = not (session.capture_exited or thread is None or thread is current
                                    or thread.ident is None)
        if session.deferred:
            self._log("Video capture thread did not stop in time; capture release deferred to its exit")
        else:
            self._release_capture_and_upload(session)
        # Notify stream consumers; only this session's packet is ever cleared.
        with self._packet_cond:
            if self._packet is not None and self._packet.session_id == session.session_id:
                self._packet = None
            self._packet_cond.notify_all()
        self._end_sinks(session)

    def _release_capture_and_upload(self, session: _CaptureSession) -> None:
        if session.cap is not None and not session.cap_released:
            try:
                session.cap.release()
            except Exception:
                logger.exception("Unable to release video capture")
            else:
                session.cap_released = True
        if session.upload_path is not None and not session.upload_removed:
            try:
                session.upload_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Unable to delete uploaded video")
            else:
                session.upload_removed = True

    @staticmethod
    def _cleanup_complete(session: _CaptureSession) -> bool:
        return ((session.cap is None or session.cap_released)
                and (session.upload_path is None or session.upload_removed)
                and not session.pending_sinks and not session.unexpected_failure)

    def _finalize_locked(self, session: _CaptureSession) -> None:
        """Caller holds _lock. Writes the state only for the current session.

        IDLE requires a successful cleanup; anything else is ERROR, so no new session
        can be reserved (reservation requires IDLE) until A's cleanup succeeded.
        """
        if self._current is not session:
            return
        if session.cleanup_running or session.cleanup_ok is None:
            self._state = VideoState.ERROR
            self._error = ("Video capture did not stop; cleanup waits for it to exit" if session.deferred
                           else "Video cleanup has not finished")
        elif not session.cleanup_ok:
            self._state = VideoState.ERROR
            self._error = "Video cleanup failed; Stop retries it"
        elif session.stop_requested or session.end_reason == "eof":
            self._state = VideoState.IDLE
            self._source = None
            self._error = None
        else:
            self._state = VideoState.ERROR
            self._error = session.capture_error

    def _on_capture_exit(self, session: _CaptureSession) -> None:
        """Last action of a capture thread: finish a deferred capture release (once)."""
        with self._lock:
            session.capture_exited = True
            run = session.deferred and not session.cleanup_running
            if run:
                session.deferred = False
                session.cleanup_running = True
                session.cleanup_done.clear()
        if run:
            self._run_cleanup(session, full=False)

    @staticmethod
    def _release_capture(cap: object) -> None:
        """Release a capture that was never attached to a session (the opener owns it)."""
        try:
            cap.release()
        except Exception:
            logger.exception("Unable to release video capture")

    @staticmethod
    def _log(message: str) -> None:
        try:
            logger.warning(message)
        except Exception:
            pass

    def _session_for_token(self, token: str) -> _CaptureSession | None:
        with self._lock:
            session = self._current
            return session if session is not None and session.token == token else None

    def _fail_start(self, session: _CaptureSession, message: str) -> None:
        """A failed open: ERROR only if this session still owns the state."""
        with self._lock:
            if self._current is not session or session.released:
                return                           # a stop() won; it owns the transition
            session.end_reason, session.capture_error = "error", message
            self._state = VideoState.STOPPING    # ERROR is written once the cleanup attempt ended
        self._release_session(session)

    def _attach(self, session: _CaptureSession, cap: object, source: str, target, args: tuple):
        """Attach an opened capture and create its thread, only for a live session."""
        with self._lock:
            if self._current is not session or session.released:
                return None                      # never revive a stopped start
            session.cap = cap
            thread = threading.Thread(target=target, args=args, kwargs={"session": session}, daemon=True)
            session.thread = thread
            self._source = source
            self._state = VideoState.RUNNING
        return thread

    def _start_capture(self, session: _CaptureSession, thread: threading.Thread) -> bool:
        with self._lock:
            if self._current is not session or session.released:
                return False                     # cleanup releases the attached capture
            thread.start()                       # under _lock: cleanup sees it started or not
            return True

    def _finish_from_capture(self, session: _CaptureSession, state: VideoState, error: str | None) -> None:
        """EOF (state IDLE) or capture error (state ERROR), called by the capture thread."""
        with self._lock:
            if session.released or self._current is not session:
                return                           # the invalidator owns the transition
            if state is VideoState.ERROR:
                session.end_reason, session.capture_error = "error", error
            else:
                session.end_reason = "eof"
            # Transitional; the cleanup attempt writes the final IDLE/ERROR when it ends.
            self._state = VideoState.STOPPING
        self._release_session(session)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _atomic_reserve(self) -> str | None:
        """
        Atomically move state IDLE → STARTING.
        Returns a new session token (str) if the reservation succeeded;
        None if state was not IDLE.
        This is the single gate for both start_webcam() and start_file().
        """
        with self._lock:
            if self._state != VideoState.IDLE:
                return None
            token = str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            session = _CaptureSession(session_id, token)
            self._current = session
            self._start_token = token
            self._session_id = session_id
            self._state = VideoState.STARTING
            self._stop_event.clear()
            if self._startup_timing_enabled:
                try:
                    self._startup_timing = _StartupTimingRecorder(session_id, token)
                except Exception:
                    self._startup_timing = None
            session.timing = self._startup_timing
        self._start_sinks(session)
        return token

    def _prepare_startup_milestone(
        self, milestone: str, *, token: str | None = None,
        session_id: str | None = None, at: float | None = None,
    ) -> tuple[str, str, float] | None:
        try:
            recorder = self._startup_timing
            if recorder is None or ((token is None) == (session_id is None)):
                return None
            if token is not None and recorder.token != token:
                return None
            if session_id is not None and recorder.session_id != session_id:
                return None
            elapsed_ms = recorder.record(milestone, time.monotonic() if at is None else at)
            if elapsed_ms is not None:
                return recorder.session_id[:8], milestone, elapsed_ms
        except Exception:
            # Observability must never alter upload, capture, or MJPEG behavior.
            pass
        return None

    @staticmethod
    def _emit_startup_milestone(entry: tuple[str, str, float] | None) -> None:
        if entry is None:
            return
        try:
            logger.info(
                "video_startup_timing session=%s milestone=%s elapsed_ms=%.3f", *entry,
            )
        except Exception:
            pass

    def record_startup_milestone(
        self, milestone: str, *, token: str | None = None,
        session_id: str | None = None, at: float | None = None,
    ) -> None:
        """Best-effort timing only; logging never runs under video or packet locks."""
        self._emit_startup_milestone(self._prepare_startup_milestone(
            milestone, token=token, session_id=session_id, at=at,
        ))

    def _packet_is_active(self, packet: FramePacket) -> bool:
        with self._lock:
            return (self._session_id == packet.session_id
                    and self._state is VideoState.RUNNING and not self._stop_event.is_set())

    def _offer_consumers(self, packet: FramePacket) -> None:
        """Keep capture alive if either optional asynchronous consumer fails."""
        if not self._packet_is_active(packet):
            return
        if self._event_frame_buffer is not None:
            try:
                height, width = packet.frame.shape[:2]
                self._event_frame_buffer.offer(BufferedFrame(
                    source_session_id=packet.session_id,
                    source_frame_seq=packet.seq,
                    source_timestamp=packet.ts,
                    frame_width=width,
                    frame_height=height,
                    jpeg=packet.jpeg,
                ))
            except Exception:
                logger.exception("Unable to offer packet to event frame buffer")
        for label, consumer in (("detector", self._detector), ("scene classifier", self._scene_classifier)):
            if consumer is None:
                continue
            if not self._packet_is_active(packet):
                return
            try:
                consumer.offer(packet)
            except Exception:
                logger.exception("Unable to offer packet to %s", label)

    # ------------------------------------------------------------------
    # Public start methods
    # ------------------------------------------------------------------

    def claim_start(self) -> str | None:
        """
        Public wrapper around _atomic_reserve() for use by the upload route.
        Returns a session token if reservation succeeded, None if not IDLE.
        The caller must call start_file(token=...) or stop() (rollback) after this.
        """
        return self._atomic_reserve()

    def start_webcam(self) -> bool:
        """
        Start capturing from webcam index 0.

        Returns True if the start attempt was made (reservation acquired),
        False if the service was not IDLE (reservation failed).
        The caller should return HTTP 409 if False is returned.

        On True return, the service will be RUNNING (or ERROR) after the call,
        unless a stop() won meanwhile: then nothing is revived.
        """
        token = self._atomic_reserve()
        if token is None:
            return False  # Not IDLE; caller should 409.
        session = self._session_for_token(token)
        if session is None:
            return True   # stopped before the open: nothing to revive

        cap: cv2.VideoCapture | None = None
        try:
            cap = cv2.VideoCapture(0)
            opened = cap.isOpened()
        except Exception as exc:
            # Constructor or isOpened() raised — release whatever we have.
            if cap is not None:
                self._release_capture(cap)
            self._fail_start(session, f"Webcam open exception: {exc}")
            return True  # Reservation was acquired; start was attempted.

        if not opened:
            self._release_capture(cap)
            self._fail_start(session, "Unable to open webcam (index 0)")
            return True  # Reservation was acquired; start was attempted.

        thread = self._attach(session, cap, "webcam", self._run_capture_webcam, (cap,))
        if thread is None:
            self._release_capture(cap)   # stop() won during the open: never revived
            return True
        self._start_capture(session, thread)
        return True

    def start_file(self, path: Path, token: str) -> None:
        """
        Start playing back a video file.
        Assumes claim_start() has already been called (service is in STARTING).

        token — the session token returned by claim_start().  If it does not
                match the current token (e.g. stop() was called between
                claim_start() and start_file()), this call is a no-op and
                raises RuntimeError so the caller can handle cleanup.
        A stop() that wins during the open is honoured too: nothing is revived.
        """
        with self._lock:
            session = self._current
            if (self._start_token != token or session is None or session.token != token
                    or session.released):
                raise RuntimeError("Session token mismatch: stop() was called before start_file()")
            session.upload_path = path

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            self._release_capture(cap)
            self._fail_start(session, f"Unable to open video file: {path.name}")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = DEFAULT_FILE_FPS

        thread = self._attach(session, cap, path.name, self._run_capture_file, (cap, fps))
        if thread is None:
            self._release_capture(cap)   # stop() won during the open; it deleted the upload
            return

        state_running_entry = self._prepare_startup_milestone(
            "state_running", session_id=session.session_id,
        )
        if self._start_capture(session, thread):
            self._emit_startup_milestone(state_running_entry)

    # ------------------------------------------------------------------
    # Stop / reset
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """
        Transition from any state back to IDLE.
        Idempotent: calling from IDLE is a no-op.
        Also acts as error recovery (ERROR → IDLE).
        Invalidates the current session token so any in-flight start_file()
        with the old token will detect the race and abort.

        Returns after the session's cleanup attempt ended, or after the bounded wait
        for another caller's attempt expired (logged). IDLE is written only when the
        cleanup succeeded; otherwise the state is ERROR and no new session can start.
        Recovery: each explicit stop() retries the failed cleanup steps once; there is
        no automatic retry. Only the current session's state is ever written.
        """
        with self._lock:
            if self._state == VideoState.IDLE:
                return
            self._state = VideoState.STOPPING
            # Invalidate the current session token
            self._start_token = None
            session = self._current
            if session is None:
                self._state = VideoState.IDLE
                self._error = None
                self._source = None
                return
            session.stop_requested = True

        self._release_session(session)

        with self._lock:
            self._finalize_locked(session)    # no-op for a session that is no longer current

    # ------------------------------------------------------------------
    # Status / frame accessors
    # ------------------------------------------------------------------

    def get_status(self) -> VideoStatusResponse:
        with self._lock:
            return VideoStatusResponse(
                state=self._state,
                source=self._source,
                error=self._error,
            )

    def get_latest_packet(self) -> FramePacket | None:
        """Return the current packet without blocking."""
        with self._packet_cond:
            return self._packet

    def wait_for_packet(self, after_seq: int, timeout: float) -> FramePacket | None:
        """
        Block until a packet with seq > after_seq is available, or timeout expires.
        Returns the packet (or None on timeout / service stopped).
        Never exposes _packet_cond to callers.

        Stop condition: _stop_event is set (service is stopping/stopped) AND
        _packet is None (cleanup cleared it).  This prevents the initial
        None state from being mistaken for a "service stopped" signal.
        """
        with self._packet_cond:
            self._packet_cond.wait_for(
                lambda: (
                    # A new frame arrived
                    (self._packet is not None and self._packet.seq > after_seq)
                    # OR the service stopped (packet slot cleared by cleanup)
                    or (self._packet is None and self._stop_event.is_set())
                ),
                timeout=timeout,
            )
            return self._packet

    # ------------------------------------------------------------------
    # Capture thread bodies
    # ------------------------------------------------------------------

    def _publish(self, session: _CaptureSession, seq: int, frame, buf) -> None:
        """Publish one encoded frame of ``session``; never after it was invalidated."""
        packet = FramePacket(
            session_id=session.session_id,
            seq=seq,
            ts=time.monotonic(),
            frame=frame,
            jpeg=bytes(buf),
        )
        with self._packet_cond:
            if session.stop_event.is_set() or self._session_id != session.session_id:
                return
            self._packet = packet
            self._packet_cond.notify_all()
        self.record_startup_milestone("first_packet_published", session_id=session.session_id)
        self._offer_consumers(packet)

    def _run_capture_webcam(self, cap: cv2.VideoCapture, *, session: _CaptureSession) -> None:
        """Capture loop for webcam source; bound to its own session."""
        try:
            self._capture_webcam_loop(cap, session)
        finally:
            self._on_capture_exit(session)

    def _capture_webcam_loop(self, cap: cv2.VideoCapture, session: _CaptureSession) -> None:
        seq = 0
        consecutive_failures = 0

        try:
            while not session.stop_event.is_set():
                ret, frame = cap.read()

                if not ret:
                    consecutive_failures += 1
                    if consecutive_failures >= WEBCAM_MAX_CONSECUTIVE_FAILURES:
                        self._finish_from_capture(
                            session, VideoState.ERROR,
                            f"Webcam read failed {consecutive_failures} consecutive times",
                        )
                        return
                    continue  # Absorb transient failure

                consecutive_failures = 0
                ok, buf = cv2.imencode(".jpg", frame)
                if not ok:
                    continue
                self._publish(session, seq, frame, buf)
                seq += 1

        except Exception as exc:
            self._finish_from_capture(session, VideoState.ERROR, str(exc))
        # Clean exit (this session's stop_event): the invalidator owns the transition.

    def _run_capture_file(self, cap: cv2.VideoCapture, fps: float, *, session: _CaptureSession) -> None:
        """Capture loop for file source with monotonic deadline pacing; bound to its session."""
        try:
            self._capture_file_loop(cap, fps, session)
        finally:
            self._on_capture_exit(session)

    def _capture_file_loop(self, cap: cv2.VideoCapture, fps: float, session: _CaptureSession) -> None:
        seq = 0
        frame_interval = 1.0 / fps

        try:
            while True:
                deadline = time.monotonic() + frame_interval

                if session.stop_event.is_set():
                    return

                ret, frame = cap.read()

                if not ret:
                    if session.stop_event.is_set():
                        return  # Stop requested right at the EOF boundary: clean.
                    # Normal EOF — transition to IDLE, not ERROR.
                    self._finish_from_capture(session, VideoState.IDLE, None)
                    return

                ok, buf = cv2.imencode(".jpg", frame)
                if ok:
                    self._publish(session, seq, frame, buf)
                    seq += 1

                # Monotonic deadline: wait only the time remaining after encode
                # (a stop of this session wakes the wait at once).
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    session.stop_event.wait(remaining)

        except Exception as exc:
            self._finish_from_capture(session, VideoState.ERROR, str(exc))


# ---------------------------------------------------------------------------
def __getattr__(name: str):  # noqa: ANN202
    """Compatibility export; production ownership lives in ``runtime``."""
    if name == "video_service":
        from app.services.runtime import video_service

        return video_service
    raise AttributeError(name)
