"""
Sentinel Vision AI — VideoService

Design constraints (Rev 4 — Corrective patch):

  * _release_resources() is a pure resource-cleanup helper; it NEVER sets _state.
    Every caller is responsible for setting the state appropriate to its outcome.

  * _packet is protected exclusively by _packet_cond (threading.Condition).
    _lock is NEVER held while interacting with _packet_cond.

  * _release_resources() skips thread.join() when called from the capture thread
    itself (self-join guard via threading.current_thread()).

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
"""

from __future__ import annotations

import logging
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


# ---------------------------------------------------------------------------
# VideoService
# ---------------------------------------------------------------------------


class VideoService:
    """
    Owns the complete video capture lifecycle:
      IDLE → STARTING → RUNNING → STOPPING → IDLE
                                           → ERROR → (stop) → IDLE
    """

    def __init__(
        self,
        detector: DetectorService | None = None,
        scene_classifier: FramePacketConsumer | None = None,
        event_frame_buffer: EventFrameBuffer | None = None,
        evaluation_worker: SessionLifecycleConsumer | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._detector = detector
        self._scene_classifier = scene_classifier
        self._event_frame_buffer = event_frame_buffer
        self._evaluation_worker = evaluation_worker

        # State fields — all guarded by _lock
        self._state: VideoState = VideoState.IDLE
        self._source: str | None = None
        self._error: str | None = None
        self._cap: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event = threading.Event()
        self._upload_path: Path | None = None
        self._released: bool = True  # starts True; set False on each new start

        # Session token — changes on every successful reservation.
        # Used to detect stop/upload races: if the token has changed by the
        # time start_file() hands off, a stop() has intervened.
        self._start_token: str | None = None
        self._session_id: str | None = None

        # Packet slot — guarded exclusively by _packet_cond
        self._packet_cond: threading.Condition = threading.Condition()
        self._packet: FramePacket | None = None

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
            self._start_token = token
            self._session_id = session_id
            self._state = VideoState.STARTING
            self._stop_event.clear()
            self._released = False
        if self._event_frame_buffer is not None:
            try:
                self._event_frame_buffer.start_session(session_id)
            except Exception:
                logger.exception("Unable to start event frame buffer session")
        # This only switches a small in-memory slot; model work remains in its
        # worker and capture never waits for it.
        if self._detector is not None:
            try:
                self._detector.start_session(session_id)
            except Exception:
                logger.exception("Unable to start detector session")
        if self._scene_classifier is not None:
            try:
                self._scene_classifier.start_session(session_id)
            except Exception:
                # Scene sampling is optional and must not make video start fail.
                logger.exception("Unable to start scene classifier session")
        if self._evaluation_worker is not None:
            try:
                self._evaluation_worker.start_session(session_id)
                # stop() can race the out-of-lock reservation fan-out. Do not
                # revive a worker session after its cleanup has already run.
                with self._lock:
                    still_active = self._session_id == session_id and not self._released
                if not still_active:
                    self._evaluation_worker.end_session(session_id)
            except Exception:
                logger.exception("Unable to start evaluation worker session")
        return token

    def _release_resources(self) -> None:
        """
        Idempotent resource-cleanup helper.

        Releases: cap, thread (if not self), upload file, packet slot.
        NEVER sets _state — every caller owns its own state transition.
        """
        with self._lock:
            if self._released:
                return
            self._released = True
            cap = self._cap
            thread = self._thread
            upload_path = self._upload_path
            self._cap = None
            self._thread = None
            self._upload_path = None
            session_id = self._session_id
            self._session_id = None
            # Signal the capture thread to stop (idempotent)
            self._stop_event.set()
        # --- Lock released before join to avoid deadlock ---

        # Self-join guard: if this IS the capture thread, skip join.
        current = threading.current_thread()
        if thread is not None and thread is not current:
            thread.join(timeout=3.0)

        if cap is not None:
            cap.release()

        if upload_path is not None:
            try:
                upload_path.unlink(missing_ok=True)
            except OSError:
                pass

        # Notify stream consumers that the packet slot is gone.
        with self._packet_cond:
            self._packet = None
            self._packet_cond.notify_all()

        # Cancel evaluation while the parking trigger still has its pending
        # event; detector.end_session() subsequently clears that trigger.
        if session_id is not None and self._evaluation_worker is not None:
            try:
                self._evaluation_worker.end_session(session_id)
            except Exception:
                logger.exception("Unable to end evaluation worker session")
        if session_id is not None and self._detector is not None:
            try:
                self._detector.end_session(session_id)
            except Exception:
                # Detection is optional: lifecycle failures must not break video cleanup.
                logger.exception("Unable to end detector session")
        if session_id is not None and self._scene_classifier is not None:
            try:
                self._scene_classifier.end_session(session_id)
            except Exception:
                logger.exception("Unable to end scene classifier session")
        if session_id is not None and self._event_frame_buffer is not None:
            try:
                self._event_frame_buffer.end_session(session_id)
            except Exception:
                logger.exception("Unable to end event frame buffer session")

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

        On True return, the service will be RUNNING (or ERROR) after the call.
        """
        token = self._atomic_reserve()
        if token is None:
            return False  # Not IDLE; caller should 409.

        cap: cv2.VideoCapture | None = None
        try:
            cap = cv2.VideoCapture(0)
            opened = cap.isOpened()
        except Exception as exc:
            # Constructor or isOpened() raised — release whatever we have.
            if cap is not None:
                cap.release()
            with self._lock:
                self._state = VideoState.ERROR
                self._error = f"Webcam open exception: {exc}"
            self._release_resources()
            return True  # Reservation was acquired; start was attempted.

        if not opened:
            cap.release()
            with self._lock:
                self._state = VideoState.ERROR
                self._error = "Unable to open webcam (index 0)"
            self._release_resources()
            return True  # Reservation was acquired; start was attempted.

        with self._lock:
            self._cap = cap
            self._source = "webcam"
            t = threading.Thread(
                target=self._run_capture_webcam,
                args=(cap,),
                daemon=True,
            )
            self._thread = t
            self._state = VideoState.RUNNING

        t.start()
        return True

    def start_file(self, path: Path, token: str) -> None:
        """
        Start playing back a video file.
        Assumes claim_start() has already been called (service is in STARTING).

        token — the session token returned by claim_start().  If it does not
                match the current token (e.g. stop() was called between
                claim_start() and start_file()), this call is a no-op and
                raises RuntimeError so the caller can handle cleanup.
        """
        with self._lock:
            if self._start_token != token:
                raise RuntimeError("Session token mismatch: stop() was called before start_file()")
            self._upload_path = path

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            cap.release()
            with self._lock:
                self._state = VideoState.ERROR
                self._error = f"Unable to open video file: {path.name}"
            self._release_resources()
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = DEFAULT_FILE_FPS

        with self._lock:
            self._cap = cap
            self._source = path.name
            t = threading.Thread(
                target=self._run_capture_file,
                args=(cap, fps),
                daemon=True,
            )
            self._thread = t
            self._state = VideoState.RUNNING

        t.start()

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
        """
        with self._lock:
            if self._state == VideoState.IDLE:
                return
            self._state = VideoState.STOPPING
            # Invalidate the current session token
            self._start_token = None

        self._release_resources()

        with self._lock:
            self._state = VideoState.IDLE
            self._error = None
            self._source = None

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

    def _run_capture_webcam(self, cap: cv2.VideoCapture) -> None:
        """Capture loop for webcam source."""
        seq = 0
        consecutive_failures = 0

        try:
            while True:
                if self._stop_event.is_set():
                    break

                ret, frame = cap.read()

                if not ret:
                    consecutive_failures += 1
                    if consecutive_failures >= WEBCAM_MAX_CONSECUTIVE_FAILURES:
                        with self._lock:
                            self._state = VideoState.ERROR
                            self._error = (
                                f"Webcam read failed {consecutive_failures} "
                                "consecutive times"
                            )
                        self._release_resources()
                        return
                    continue  # Absorb transient failure

                consecutive_failures = 0
                ok, buf = cv2.imencode(".jpg", frame)
                if not ok:
                    continue

                with self._lock:
                    session_id = self._session_id
                if session_id is None:
                    continue
                packet = FramePacket(
                    session_id=session_id,
                    seq=seq,
                    ts=time.monotonic(),
                    frame=frame,
                    jpeg=bytes(buf),
                )
                seq += 1

                with self._packet_cond:
                    if self._stop_event.is_set() or self._session_id != packet.session_id:
                        continue
                    self._packet = packet
                    self._packet_cond.notify_all()
                self._offer_consumers(packet)

        except Exception as exc:
            with self._lock:
                self._state = VideoState.ERROR
                self._error = str(exc)
            self._release_resources()
            return

        # Clean stop (stop_event was set)
        with self._lock:
            self._state = VideoState.STOPPING
        self._release_resources()
        with self._lock:
            self._state = VideoState.IDLE
            self._source = None
            self._error = None

    def _run_capture_file(self, cap: cv2.VideoCapture, fps: float) -> None:
        """Capture loop for file source with monotonic deadline pacing."""
        seq = 0
        frame_interval = 1.0 / fps

        try:
            while True:
                deadline = time.monotonic() + frame_interval

                if self._stop_event.is_set():
                    break

                ret, frame = cap.read()

                if not ret and self._stop_event.is_set():
                    # Stop was requested right at the EOF boundary — treat as clean.
                    break

                if not ret:
                    # Normal EOF — transition to IDLE, not ERROR.
                    with self._lock:
                        self._state = VideoState.STOPPING
                    self._release_resources()
                    with self._lock:
                        self._state = VideoState.IDLE
                        self._source = None
                        self._error = None
                    return

                ok, buf = cv2.imencode(".jpg", frame)
                if not ok:
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    continue

                with self._lock:
                    session_id = self._session_id
                if session_id is None:
                    continue
                packet = FramePacket(
                    session_id=session_id,
                    seq=seq,
                    ts=time.monotonic(),
                    frame=frame,
                    jpeg=bytes(buf),
                )
                seq += 1

                with self._packet_cond:
                    if self._stop_event.is_set() or self._session_id != packet.session_id:
                        continue
                    self._packet = packet
                    self._packet_cond.notify_all()
                self._offer_consumers(packet)

                # Monotonic deadline: sleep only the time remaining after encode.
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)

        except Exception as exc:
            with self._lock:
                self._state = VideoState.ERROR
                self._error = str(exc)
            self._release_resources()
            return

        # Clean stop (stop_event was set)
        with self._lock:
            self._state = VideoState.STOPPING
        self._release_resources()
        with self._lock:
            self._state = VideoState.IDLE
            self._source = None
            self._error = None


# ---------------------------------------------------------------------------
def __getattr__(name: str):  # noqa: ANN202
    """Compatibility export; production ownership lives in ``runtime``."""
    if name == "video_service":
        from app.services.runtime import video_service

        return video_service
    raise AttributeError(name)
