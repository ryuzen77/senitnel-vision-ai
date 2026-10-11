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

Required sinks, leases and safe publication (T3c0.2):

  * required_sinks (default empty = legacy: every sink optional). A required sink whose
    start_session raises refuses the reservation: no further sink starts, and AFTER the
    start loop released _lifecycle_lock the normal cleanup runner ends every sink; the
    state is ERROR until Stop and claim_start()/start_webcam() raise
    VideoStartRefusedError. No capture is opened for a refused reservation.
  * Lease-aware sinks (accepts_lifecycle_lease) receive lease=session.token on start,
    every end attempt (Stop retries included) and, for the detector, on offer.
  * RUNNING is published in the same _lock section as a successful thread.start(); a
    failing thread.start() rolls back that session through the cleanup runner (ERROR
    until Stop). abort_start(token) rolls back only the reservation it names.
  * Single owner for every capture: the session adopts a capture the moment it is
    created; only that session's cleanup releases it (failed releases stay pending and
    each explicit Stop retries them). While the opener still uses it (``opening``) the
    release is deferred and handed over atomically when the opener is done, exactly
    like a capture thread that is still reading.
  * Lifecycle/rollback logs and public errors carry fixed labels and exception TYPE
    names only (no ids, tokens, paths or exception messages).

Prepared playback (P1b, private-parking plan §16):

  * start_prepared_file() consumes the store's handoff ticket inside the reservation
    _lock section (token, session id and a public session_ref are created there); a
    cancelled or expired ticket leaves no reservation. Not IDLE -> PlaybackRefused
    ("video_busy") without consuming.
  * The operator configuration is bound to (session_id, token) in the registry under
    _lifecycle_lock before the first sink starts and unbound with the same token after
    the sinks ended; a refused bind is a required refusal. The unbind obligation is
    recorded BEFORE bind() is called, so a bind that changed the registry and then raised
    is still rolled back; an unbind that fails stays pending (cleanup not successful, an
    explicit Stop retries it). The registry's lease check keeps another reservation's
    binding untouched.
  * The capture is created by video_decoder and adopted by the session before any call
    on it; the store owns the file (no upload_path).
  * Every cleanup attempt of a prepared session ends with the end hook, called outside
    every lock while cleanup_running is still true; the handoff is re-checked after the
    hook, and a hook that raises is a failed cleanup step that Stop retries.
  * cancel_session(token): one _lock section checks the token, records the cancel and
    invalidates the session, whatever the state.
  * Every decoded frame of a prepared session is size-checked before publication; a
    mismatch ends the session with end reason "frame_size_changed".
  * Offers go only to the current, unreleased session object.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import cv2

from app.models.video import FramePacket, VideoStartResponse, VideoState, VideoStatusResponse
from app.models.event_frame import BufferedFrame
from app.services import video_decoder
from app.services.event_frame_buffer import EventFrameBuffer
from app.services.prepared_source import PlaybackRefused

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

SINK_LABELS = ("event frame buffer", "detector", "scene classifier", "evaluation worker")
REQUIRED_START_REFUSED = "Required pipeline component refused the session"
THREAD_START_FAILED = "Capture thread could not start"
CAPTURE_FAILED = "Video capture failed"
FRAME_SIZE_CHANGED = "Video frame size changed"
_STALE = object()                           # a prepared start whose ticket could not be consumed


class VideoStartRefusedError(Exception):
    """A required sink refused the session; the reservation was already rolled back."""


class StaleStartError(RuntimeError):
    """start_file() was called with a reservation that is no longer the current one (it
    was stopped, aborted or replaced). Nothing was opened; the caller still owns its file.
    A RuntimeError subclass for existing direct callers; the upload route catches THIS
    class only, so a general RuntimeError follows the general-error path."""


class _CaptureUnavailable(Exception):
    """Internal: the capture opened without error but isOpened() is False."""


class PreparedStartFailed(RuntimeError):
    """A prepared start failed after its ticket was consumed (open, probe or thread
    start); the session was rolled back through its cleanup, which sent the end hook."""


def _frame_size(frame) -> tuple[int, int] | None:
    shape = getattr(frame, "shape", None)
    if shape is None or len(shape) < 2:
        return None
    return int(shape[1]), int(shape[0])


def _valid_size(size: object) -> bool:
    return (isinstance(size, tuple) and len(size) == 2
            and all(type(side) is int and side > 0 for side in size))


def _log_failure(what: str, error: BaseException) -> None:
    """Sanitized: a fixed description and the exception TYPE only (ERROR level, as the
    logger.exception calls it replaces)."""
    try:
        logger.error("%s (%s)", what, type(error).__name__)
    except Exception:
        pass


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
    cleanup runner (``cleanup_running``) writes. The capture thread and the upload are
    attached only while ``released`` is False. One exception for the capture: an opener
    that is still active (``opening``) when the session is invalidated adopts the capture
    it just created into THIS (old) context, solely so that this context's cleanup
    releases it (deferred and handed over by ``_end_open``); such a capture never
    continues the session (no thread is created for a released session) and never moves
    to another session.

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
    start_refused: bool = False         # a required sink refused this reservation
    opening: bool = False               # the opener may still create/use the capture
    # Prepared playback (P1b). ``on_finished`` is set only for a consumed ticket.
    session_ref: str | None = None      # public, never reused name of this reservation
    ticket_id: int | None = None
    on_finished: Callable | None = None
    config: object | None = None        # operator configuration bound for this session
    expected_size: tuple[int, int] | None = None
    binding_pending: bool = False       # bound in the registry and not yet unbound
    cancel_requested: bool = False
    hook_reason: str | None = None      # end reason, fixed once when the end is decided
    hook_pending: bool = False          # the last end-hook delivery raised


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
        required_sinks: frozenset[str] | set[str] = frozenset(),
        session_id_factory: Callable[[], str] | None = None,
        thread_factory: Callable[..., threading.Thread] | None = None,
        session_config_registry: object | None = None,
        decoder: object | None = None,
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
        configured = {label for label, sink in zip(SINK_LABELS, (
            event_frame_buffer, detector, scene_classifier, evaluation_worker)) if sink is not None}
        required = frozenset(required_sinks)
        if not required <= configured:
            raise ValueError("every required sink must be one of the configured sinks")
        self._required_sinks = required
        # Tests only: force a reused session id / a failing thread start.
        self._session_id_factory = session_id_factory or (lambda: str(uuid.uuid4()))
        self._thread_factory = thread_factory or threading.Thread
        # Prepared playback: lease-bound configuration registry and the one decoder.
        self._registry = session_config_registry
        self._decoder = decoder if decoder is not None else video_decoder
        self._counts = dict.fromkeys(("required_start_refusals", "thread_start_failures",
                                      "aborted_starts", "stale_aborts", "stale_cancels",
                                      "hook_failures"), 0)
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

    @staticmethod
    def _lease_kwargs(sink: object, session: _CaptureSession) -> dict:
        """The owner's lease, only for sinks that declare they check it."""
        return {"lease": session.token} if getattr(sink, "accepts_lifecycle_lease", False) is True else {}

    def _start_sinks(self, session: _CaptureSession) -> str | None:
        """Start every sink for ``session``; stop as soon as the session is invalidated.

        Returns None (all started or a stop() won) or the label of a REQUIRED sink that
        refused. Never starts a cleanup itself: the caller rolls back after this returned
        and released _lifecycle_lock (non-reentrant; the cleanup runner takes it).
        """
        with self._lifecycle_lock:
            if session.config is not None:       # prepared: bind before the first sink
                with self._lock:
                    alive = self._current is session and not session.released
                if not alive:
                    return None                  # cancelled/stopped: nothing bound, nothing started
                # Rollback obligation first: a bind may change the registry and still raise.
                session.binding_pending = True
                try:
                    self._registry.bind(session.session_id, session.token, session.config)
                except Exception as error:
                    _log_failure("Unable to bind the session configuration", error)
                    return "configuration binding"   # a required refusal: no sink starts
            for label, sink in self._sinks_in_start_order():
                with self._lock:
                    alive = self._current is session and not session.released
                if not alive:
                    return None                  # a stop() already won: never start the rest
                try:
                    # In-memory slot switches only; model work stays in the sinks' workers.
                    sink.start_session(session.session_id, **self._lease_kwargs(sink, session))
                except Exception as error:
                    _log_failure(f"Unable to start {label} session", error)
                    if label in self._required_sinks:
                        return label             # no further sink starts
                    # Optional consumers must not make video start fail.
        return None

    def _end_sinks(self, session: _CaptureSession) -> None:
        """End every sink still pending for ``session``; a failing sink stays pending
        (it is retried only by an explicit stop()) and the others are still ended."""
        with self._lifecycle_lock:
            for label, sink in self._sinks_in_end_order():
                if label not in session.pending_sinks:
                    continue
                try:
                    # Same lease on every attempt (Stop retries included); sinks ignore a
                    # non-matching id or lease.
                    sink.end_session(session.session_id, **self._lease_kwargs(sink, session))
                except Exception as error:
                    # Lifecycle failures never stop the remaining cleanup steps.
                    _log_failure(f"Unable to end {label} session", error)
                else:
                    session.pending_sinks.discard(label)
            if session.binding_pending:          # same token as the bind; idempotent on retries
                try:
                    # Lease-checked: removes only this token's entry; False = nothing of ours.
                    self._registry.unbind(session.session_id, session.token)
                except Exception as error:
                    _log_failure("Unable to unbind the session configuration", error)   # stays pending
                else:
                    session.binding_pending = False

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
                         or (session.deferred
                             and not self._capture_in_use_locked(session, threading.current_thread())))
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

        Prepared session (consumed ticket): the end hook is part of the attempt. Its facts
        are computed while ``cleanup_running`` is still true (the state is not IDLE, so
        nothing new can be reserved); the hook runs outside every lock; then ONE ``_lock``
        section records the delivery and re-checks the handoff before ownership is given
        up: a capture that stopped being in use while the hook ran (its exit path saw
        ``cleanup_running`` and did not take over) is released by this runner, followed by
        the final hook. A hook that raises makes the attempt a failed cleanup (ERROR; an
        explicit Stop resumes it and sends the hook again).
        """
        passes = 0
        current = threading.current_thread()
        while True:
            try:
                if full and passes == 0:
                    self._cleanup_full_pass(session)
                else:
                    self._release_capture_and_upload(session)
            except Exception as error:
                session.unexpected_failure = True
                _log_failure("Video session cleanup failed", error)
            passes += 1
            hook = None
            with self._lock:
                resume = (session.deferred and passes < 2
                          and not self._capture_in_use_locked(session, current))
                if resume:
                    session.deferred = False     # still the owner: release now, outside the lock
                else:
                    ok = None if session.deferred else self._cleanup_complete(session)
                    if session.on_finished is None:
                        session.cleanup_running = False
                        session.cleanup_ok = ok
                        self._finalize_locked(session)
                    else:
                        hook = (session.on_finished, session.ticket_id, session.token,
                                self._hook_reason_locked(session), ok)
            if resume:
                continue
            if hook is None:
                break
            delivered = self._deliver_hook(hook)
            with self._lock:
                session.hook_pending = not delivered
                again = (session.deferred and passes < 3
                         and not self._capture_in_use_locked(session, current))
                if again:
                    session.deferred = False     # released by its user while the hook ran
                else:
                    session.cleanup_running = False
                    session.cleanup_ok = ok if delivered else False
                    self._finalize_locked(session)
            if not again:
                break
        session.cleanup_done.set()

    def _deliver_hook(self, hook: tuple) -> bool:
        """Outside every lock: the store's end hook; False if it raised (type logged)."""
        on_finished, ticket_id, token, reason, ok = hook
        try:
            on_finished(ticket_id, token, reason, ok)
        except Exception as error:
            with self._lock:
                self._counts["hook_failures"] += 1
            _log_failure("Prepared session end hook failed", error)
            return False
        return True

    @staticmethod
    def _hook_reason_locked(session: _CaptureSession) -> str:
        return session.hook_reason or ("stop" if session.stop_requested else "error")

    @staticmethod
    def _decide_end_locked(session: _CaptureSession, reason: str) -> None:
        """Caller holds _lock. The end reason is fixed once, by the first decision."""
        if session.hook_reason is None:
            session.hook_reason = reason

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
            # Never release a capture the opener or the capture thread may still use.
            session.deferred = self._capture_in_use_locked(session, current)
            opening = session.opening
        if session.deferred:
            self._log("Video source is still opening; capture release deferred to the opener" if opening
                      else "Video capture thread did not stop in time; capture release deferred to its exit")
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
            except Exception as error:
                _log_failure("Unable to release video capture", error)
            else:
                session.cap_released = True
        if session.upload_path is not None and not session.upload_removed:
            try:
                session.upload_path.unlink(missing_ok=True)
            except OSError as error:
                _log_failure("Unable to delete uploaded video", error)
            else:
                session.upload_removed = True

    @staticmethod
    def _capture_in_use_locked(session: _CaptureSession, current: threading.Thread) -> bool:
        """Caller holds _lock: True while the opener or the capture thread may still use
        the capture, so its release must be deferred to them (single owner handoff)."""
        if session.opening:
            return True
        thread = session.thread
        return not (session.capture_exited or thread is None or thread is current or thread.ident is None)

    @staticmethod
    def _cleanup_complete(session: _CaptureSession) -> bool:
        return ((session.cap is None or session.cap_released)
                and (session.upload_path is None or session.upload_removed)
                and not session.pending_sinks and not session.binding_pending
                and not session.unexpected_failure)

    def _finalize_locked(self, session: _CaptureSession) -> None:
        """Caller holds _lock. Writes the state only for the current session.

        IDLE requires a successful cleanup; anything else is ERROR, so no new session
        can be reserved (reservation requires IDLE) until A's cleanup succeeded.
        """
        if self._current is not session:
            return
        if session.cleanup_running or session.cleanup_ok is None:
            self._state = VideoState.ERROR
            if session.deferred and session.opening:
                self._error = "Video source is still opening; cleanup waits for it"
            elif session.deferred:
                self._error = "Video capture did not stop; cleanup waits for it to exit"
            else:
                self._error = "Video cleanup has not finished"
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
            self._decide_end_locked(session, "error")
            self._state = VideoState.STOPPING    # ERROR is written once the cleanup attempt ended
        self._release_session(session)

    def _begin_open_locked(self, session: _CaptureSession) -> bool:
        """Caller holds _lock. Mark the opener active (defers any capture release to it)."""
        if self._current is not session or session.released:
            return False
        session.opening = True
        return True

    def _adopt_capture(self, session: _CaptureSession, cap: object) -> None:
        """From its creation on, the capture belongs to the session's cleanup (single
        owner): released once, a failed release stays pending and Stop retries it."""
        with self._lock:
            session.cap = cap

    def _end_open(self, session: _CaptureSession) -> None:
        """The opener no longer uses the capture. If a stop() already deferred the release
        to it, hand the release to exactly one owner (atomic, as _on_capture_exit)."""
        with self._lock:
            if not session.opening:
                return
            session.opening = False
            run = session.released and session.deferred and not session.cleanup_running
            if run:
                session.deferred = False
                session.cleanup_running = True
                session.cleanup_done.clear()
        if run:
            self._run_cleanup(session, full=False)

    def _attach(self, session: _CaptureSession, source: str, target, args: tuple):
        """Create the capture thread for a live session (the capture is already the
        session's). The state stays STARTING: RUNNING is published by _start_capture."""
        with self._lock:
            if self._current is not session or session.released:
                return None                      # never revive a stopped start
            thread = self._thread_factory(target=target, args=args, kwargs={"session": session}, daemon=True)
            session.thread = thread
            self._source = source
            session.opening = False              # the opener is done with the capture
        return thread

    def _start_capture(self, session: _CaptureSession, thread: threading.Thread) -> bool:
        """One _lock section: re-check, thread.start(), then publish RUNNING. The capture
        thread reports EOF/errors through _finish_from_capture (needs _lock), so it always
        observes RUNNING; a released/finalized session is never made RUNNING again. If
        thread.start() raises, this session is rolled back (ERROR until Stop)."""
        with self._lock:
            if self._current is not session or session.released:
                return False                     # cleanup releases the attached capture
            try:
                thread.start()                   # under _lock: cleanup sees it started or not
            except Exception as error:
                failure = error
                session.thread = None            # never started: cleanup must not join it
                session.end_reason, session.capture_error = "error", THREAD_START_FAILED
                self._decide_end_locked(session, "error")
                self._counts["thread_start_failures"] += 1
                self._state = VideoState.STOPPING    # ERROR is written once the cleanup ended
            else:
                self._state = VideoState.RUNNING
                return True
        _log_failure("Unable to start the capture thread", failure)
        self._release_session(session)           # outside _lock; this session's runner
        return False

    def _finish_from_capture(self, session: _CaptureSession, state: VideoState, error: str | None,
                             *, reason: str | None = None) -> None:
        """EOF (state IDLE) or capture error (state ERROR), called by the capture thread.
        ``reason`` names a specific end for the hook (``frame_size_changed``)."""
        with self._lock:
            if session.released or self._current is not session:
                return                           # the invalidator owns the transition
            if state is VideoState.ERROR:
                session.end_reason, session.capture_error = "error", error
                self._decide_end_locked(session, reason or "error")
            else:
                session.end_reason = "eof"
                self._decide_end_locked(session, "eof")
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
        reserved = self._reserve()
        return None if reserved is None else reserved[0]

    def _reserve(self, prepared: dict | None = None):
        """The reservation section. ``prepared`` (a handoff ticket and its session data):
        not IDLE raises PlaybackRefused("video_busy") without consuming; the ticket is
        consumed in this same _lock section before any state change, and a failed
        consume returns ``_STALE`` with nothing reserved. Returns (token, session)."""
        with self._lock:
            if self._state != VideoState.IDLE:
                if prepared is not None:
                    raise PlaybackRefused("video_busy")
                return None
            token = str(uuid.uuid4())
            session_id = self._session_id_factory()
            session_ref = str(uuid.uuid4())
            if prepared is not None and not prepared["ticket"].try_consume(token, session_ref, session_id):
                return _STALE                    # cancelled or past its deadline: no reservation
            session = _CaptureSession(session_id, token, session_ref=session_ref)
            if prepared is not None:
                session.ticket_id = prepared["ticket"].ticket_id
                session.on_finished = prepared["on_finished"]
                session.config = prepared["config"]
                session.expected_size = prepared["expected_size"]
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
        refused = self._start_sinks(session)     # _lifecycle_lock released on return
        if refused is None:
            return token, session
        with self._lock:
            run = self._current is session and not session.released
            if run:                              # otherwise a stop()/abort won and owns cleanup
                session.end_reason, session.capture_error = "error", REQUIRED_START_REFUSED
                session.start_refused = True
                self._decide_end_locked(session, "refused")
                self._counts["required_start_refusals"] += 1
                self._start_token = None
                self._state = VideoState.STOPPING    # ERROR (until Stop) once the cleanup ended
        if run:
            self._release_session(session)       # ends every sink; never under a held lock
        raise VideoStartRefusedError(REQUIRED_START_REFUSED)

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

    def _packet_is_active(self, packet: FramePacket, session: _CaptureSession | None = None) -> bool:
        """``session`` (the publishing session object) narrows the check to that object:
        a frame of a released session never reaches a newer one that reuses its id."""
        with self._lock:
            if session is not None and (self._current is not session or session.released):
                return False
            return (self._session_id == packet.session_id
                    and self._state is VideoState.RUNNING and not self._stop_event.is_set())

    def _offer_consumers(self, packet: FramePacket, *, lease: str | None = None,
                         session: _CaptureSession | None = None) -> None:
        """Keep capture alive if either optional asynchronous consumer fails. ``lease``
        (the capture session's token) is passed to a lease-aware detector only."""
        if not self._packet_is_active(packet, session):
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
            except Exception as error:
                _log_failure("Unable to offer packet to event frame buffer", error)
        for label, consumer in (("detector", self._detector), ("scene classifier", self._scene_classifier)):
            if consumer is None:
                continue
            if not self._packet_is_active(packet, session):
                return
            try:
                if lease is not None and getattr(consumer, "accepts_lifecycle_lease", False) is True:
                    consumer.offer(packet, lease=lease)
                else:
                    consumer.offer(packet)
            except Exception as error:
                _log_failure(f"Unable to offer packet to {label}", error)

    # ------------------------------------------------------------------
    # Public start methods
    # ------------------------------------------------------------------

    def claim_start(self) -> str | None:
        """
        Public wrapper around _atomic_reserve() for use by the upload route.
        Returns a session token if reservation succeeded, None if not IDLE; raises
        VideoStartRefusedError when a required sink refused (already rolled back, ERROR
        until Stop). The caller must call start_file(token=...) or abort_start(token).
        """
        return self._atomic_reserve()

    def abort_start(self, token: str) -> bool:
        """Roll back the reservation ``token`` names, and nothing else.

        Acts only while that reservation is current, not released and still STARTING
        (not handed to a capture thread); its own cleanup runner releases its capture,
        upload and sinks; IDLE after a successful cleanup, ERROR (Stop retries) otherwise.
        Any other token is a no-op returning False, so an old upload never stops B.
        """
        with self._lock:
            session = self._current
            if (session is None or session.token != token or session.released
                    or self._state is not VideoState.STARTING):
                self._counts["stale_aborts"] += 1
                return False
            session.stop_requested = True        # a rollback by the reservation's owner
            self._start_token = None
            self._state = VideoState.STOPPING
            self._counts["aborted_starts"] += 1
        self._release_session(session)
        with self._lock:
            self._finalize_locked(session)
        return True

    def get_cleanup_diagnostics(self) -> dict:
        """Labels and counts only (no ids, tokens or paths)."""
        with self._lock:
            session = self._current
            released = session is not None and session.released
            pending = sorted(session.pending_sinks) if released else []
            capture_pending = released and session.cap is not None and not session.cap_released
            upload_pending = released and session.upload_path is not None and not session.upload_removed
            return dict(self._counts, state=self._state.value, pending_sinks=pending,
                        capture_pending=int(capture_pending), upload_pending=int(upload_pending),
                        required_sinks=sorted(self._required_sinks))

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
        with self._lock:
            opening = session is not None and self._begin_open_locked(session)
        if not opening:
            return True   # stopped before the open: nothing to revive

        # Single owner: the session adopts the capture at creation; only its cleanup
        # releases it (deferred while this opener still uses it, see _end_open).
        try:
            cap = cv2.VideoCapture(0)
            self._adopt_capture(session, cap)
            if not cap.isOpened():
                raise _CaptureUnavailable()
            thread = self._attach(session, "webcam", self._run_capture_webcam, (cap,))
        except Exception as error:
            unavailable = isinstance(error, _CaptureUnavailable)
            if not unavailable:
                _log_failure("Webcam open failed", error)
            self._end_open(session)              # hands a deferred release over (a stop() won)
            self._fail_start(session, "Unable to open webcam (index 0)" if unavailable else "Webcam open failed")
            return True  # Reservation was acquired; start was attempted.
        if thread is None:
            self._end_open(session)              # stop() won during the open: its cleanup releases
            return True
        self._start_capture(session, thread)
        return True

    def start_file(self, path: Path, token: str) -> None:
        """
        Start playing back a video file.
        Assumes claim_start() has already been called (service is in STARTING).

        token — the session token returned by claim_start().  If it is no longer
                the current reservation (stopped, aborted or replaced), nothing is
                opened and StaleStartError is raised; the caller still owns its file.
        Any failure while opening/preparing the capture (constructor, isOpened(),
        get(), thread creation) follows the open-failure contract: the capture is
        released exactly once, this reservation's cleanup deletes the upload, and the
        state is ERROR "Unable to open video file" until Stop (returns normally).
        A stop() that wins during the open is honoured too: nothing is revived.
        """
        with self._lock:
            session = self._current
            if (self._start_token != token or session is None or session.token != token
                    or session.released):
                raise StaleStartError("Session token mismatch: the upload reservation is no longer current")
            session.upload_path = path
            self._begin_open_locked(session)

        # Single owner as in start_webcam(): the session adopts the capture at creation.
        try:
            cap = cv2.VideoCapture(str(path))
            self._adopt_capture(session, cap)
            if not cap.isOpened():
                raise _CaptureUnavailable()
            fps = cap.get(cv2.CAP_PROP_FPS)
            if not fps > 0:
                fps = DEFAULT_FILE_FPS
            thread = self._attach(session, path.name, self._run_capture_file, (cap, fps))
        except Exception as error:
            if not isinstance(error, _CaptureUnavailable):
                _log_failure("Video file open failed", error)
            self._end_open(session)              # hands a deferred release over (a stop() won)
            self._fail_start(session, "Unable to open video file")
            return
        if thread is None:
            self._end_open(session)              # stop() won during the open: its cleanup releases
            return

        state_running_entry = self._prepare_startup_milestone(
            "state_running", session_id=session.session_id,
        )
        if self._start_capture(session, thread):
            self._emit_startup_milestone(state_running_entry)

    def start_prepared_file(self, path: Path, *, ticket, session_config, expected_size,
                            on_finished) -> bool:
        """Start analysing a store-owned prepared file (P1 plan §7.5, §16.2).

        Returns True once RUNNING was published for this session; False when the ticket
        was cancelled or expired before the consume (nothing reserved). Raises
        PlaybackRefused for "configuration_unavailable" (before the consume),
        "video_busy" (not IDLE; not consumed), "required_component_refused" (rolled
        back) and "stopped" (a Stop or cancel won after the consume); raises
        PreparedStartFailed when the open, probe or thread start failed after the
        consume. Every consumed start ends through this session's cleanup and hook.
        """
        if (self._registry is None or session_config is None or not callable(on_finished)
                or not _valid_size(expected_size)):
            raise PlaybackRefused("configuration_unavailable")
        try:
            reserved = self._reserve({"ticket": ticket, "config": session_config,
                                      "expected_size": expected_size, "on_finished": on_finished})
        except VideoStartRefusedError:
            raise PlaybackRefused("required_component_refused") from None
        if reserved is _STALE:
            return False
        _token, session = reserved
        with self._lock:
            opening = self._begin_open_locked(session)
        if not opening:
            raise PlaybackRefused("stopped")     # its cleanup already sent the hook
        try:
            cap = self._decoder.create_capture(path)
        except Exception as error:
            _log_failure("Prepared video open failed", error)
            self._end_open(session)
            self._fail_start(session, "Unable to open video file")
            raise self._prepared_failure(session) from None
        self._adopt_capture(session, cap)        # owner before any call on the capture
        try:
            fps = self._decoder.probe(cap)
            thread = self._attach(session, "prepared file", self._run_capture_file, (cap, fps))
        except Exception as error:
            if not isinstance(error, video_decoder.CaptureUnavailable):
                _log_failure("Prepared video probe failed", error)
            self._end_open(session)
            self._fail_start(session, "Unable to open video file")
            raise self._prepared_failure(session) from None
        if thread is None:
            self._end_open(session)              # a stop won during the open: its cleanup releases
            raise PlaybackRefused("stopped")
        if self._start_capture(session, thread):
            return True
        raise self._prepared_failure(session)

    def _prepared_failure(self, session: _CaptureSession) -> Exception:
        with self._lock:
            stopped = session.hook_reason in ("stop", "cancelled")
        return PlaybackRefused("stopped") if stopped else PreparedStartFailed("prepared start failed")

    def cancel_session(self, lease: str) -> bool:
        """End the reservation whose token is ``lease``, whatever its state.

        One _lock section is the linearization point: the token check, the cancel
        record and the invalidation (released, stop_event) happen together, so no
        STARTING -> RUNNING transition can slip between a check and the action; every
        later step of that reservation re-checks ``released`` and stops. Another or an
        already released token: False (counted), nothing changes. The caller then runs
        this session's cleanup (with its end hook)."""
        with self._lock:
            session = self._current
            if session is None or session.token != lease or session.released:
                self._counts["stale_cancels"] += 1
                return False
            session.cancel_requested = True
            session.stop_requested = True
            self._decide_end_locked(session, "cancelled")
            self._start_token = None
            self._invalidate_locked(session)
            self._state = VideoState.STOPPING
        self._run_cleanup(session, full=True)
        with self._lock:
            self._finalize_locked(session)
        return True

    def get_session_identity(self) -> tuple[VideoState, str | None, str | None]:
        """(state, session_ref, session_id) of the current reservation in one _lock
        section; the ids stay visible until the state is IDLE."""
        with self._lock:
            session = self._current
            if session is None or self._state is VideoState.IDLE:
                return self._state, None, None
            return self._state, session.session_ref, session.session_id

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
            self._decide_end_locked(session, "stop")

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
        self._offer_consumers(packet, lease=session.token, session=session)

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

        except Exception as error:
            _log_failure(CAPTURE_FAILED, error)
            self._finish_from_capture(session, VideoState.ERROR, CAPTURE_FAILED)
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

                # Prepared: every frame must have the configured size BEFORE publication;
                # a mismatching frame reaches no packet slot, buffer, tee or model.
                if session.expected_size is not None and _frame_size(frame) != session.expected_size:
                    self._finish_from_capture(session, VideoState.ERROR, FRAME_SIZE_CHANGED,
                                              reason="frame_size_changed")
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

        except Exception as error:
            _log_failure(CAPTURE_FAILED, error)
            self._finish_from_capture(session, VideoState.ERROR, CAPTURE_FAILED)


# ---------------------------------------------------------------------------
def __getattr__(name: str):  # noqa: ANN202
    """Compatibility export; production ownership lives in ``runtime``."""
    if name == "video_service":
        from app.services.runtime import video_service

        return video_service
    raise AttributeError(name)
