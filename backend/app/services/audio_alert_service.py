"""Optional local audio alert for CRITICAL AI assessments (Gate v3); no network, no TTS.

Control plane and device plane are separate:

* ``AudioAlertGate`` holds the authoritative lifecycle ``epoch``/``session``, one
  candidate slot (first-wins), and the per-attempt control. The evaluation worker
  calls ``apply_lifecycle`` and ``offer`` *inside* its own state lock, so the gate
  sees transitions in exactly the worker's order. Lock order is fixed:
  ``worker._state_lock -> gate._lock``; the gate never calls the worker.
* One device thread owns the audio device (start and stop). It and ``snapshot``
  take only ``gate._lock``. Every gate critical section is O(1): assignments,
  comparisons, ``Event.set()`` and ``Condition.notify``; never PlaySound, device
  stop, a timed wait or a join (the idle ``Condition.wait`` releases the lock).

Linearization:

* Grant: under ``gate._lock`` the device thread takes the slot, re-checks
  epoch/session/closed/fault/dedupe/cooldown and records GRANTED with a fresh,
  never-reused cancel ``Event``. Invalidation uses the same lock, so it is ordered
  either before the grant (no grant for the old generation) or after it (the
  attempt is in flight and receives the cancel signal).
* Start: still under ``gate._lock`` the device thread marks ``start_attempted``
  unless the attempt is already cancelled; the device call follows outside every
  lock. A cancel ordered before the mark means the device is never touched; one
  ordered after it means start then stop, so a short audible tail is possible.

Closure: every attempt whose start was attempted ends with a best-effort
``device.stop()`` before the attempt is cleared; the next grant requires a
cleared attempt, so the device sees start_n -> stop_n -> start_{n+1}. If that stop
fails the gate enters ``device_fault``: no further grants, later events dropped.

Lifecycle fault: if the worker cannot apply a lifecycle transition it calls
``fail_closed()``, which permanently closes this gate to grants (pending slot
dropped, active attempt cancelled). It is a short control operation that does not
depend on the state the failed update may have left half-applied.

Exactly-once alerting per event comes from the worker publishing ACCEPTED once;
the bounded recent-event set here is only an extra guard.
"""

from __future__ import annotations

from collections import OrderedDict
import dataclasses
import logging
import math
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
import wave

from app.models.evaluation import ThreatStatus

logger = logging.getLogger(__name__)

MAX_ALERT_SECONDS = 8.0
MIN_COOLDOWN_SECONDS = 10.0
MAX_COOLDOWN_SECONDS = 120.0
DEFAULT_COOLDOWN_SECONDS = 30.0
START_STALL_INDICATOR_SECONDS = 2.0
SHUTDOWN_JOIN_SECONDS = 2.0
_RECENT_EVENT_LIMIT = 64


class AudioDevice(Protocol):
    """Device operations; only the gate's device thread calls them, outside locks."""

    def start(self, path: str) -> None: ...

    def stop(self) -> None: ...


class WinsoundDevice:
    """Windows default-output playback through the stdlib, imported lazily."""

    def start(self, path: str) -> None:
        import winsound  # noqa: PLC0415 - lazy: never imported unless audio plays

        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def stop(self) -> None:
        import winsound  # noqa: PLC0415

        winsound.PlaySound(None, 0)


def probe_wav_duration(path: Path) -> float | None:
    """Return the duration of an uncompressed PCM WAV, or None if unusable."""
    try:
        with wave.open(str(path), "rb") as clip:
            if clip.getcomptype() != "NONE" or clip.getframerate() <= 0:
                return None
            duration = clip.getnframes() / clip.getframerate()
    except (OSError, EOFError, wave.Error):
        return None
    return duration if math.isfinite(duration) and duration > 0 else None


@dataclasses.dataclass(frozen=True)
class _Candidate:
    event_id: str
    epoch: int


@dataclasses.dataclass
class _Attempt:
    attempt_id: int
    event_id: str
    epoch: int
    cancel: threading.Event
    granted_at: float
    state: str = "GRANTED"            # GRANTED -> STARTING -> IN_FLIGHT
    start_attempted: bool = False
    started_at: float | None = None


@dataclasses.dataclass(frozen=True)
class AudioAlertSnapshot:
    running: bool
    closed: bool
    epoch: int
    session_short_id: str | None
    queued_event_short_id: str | None
    attempt_event_short_id: str | None
    attempt_state: str | None
    start_stalled: bool
    device_fault: bool
    lifecycle_fault: bool
    offered_critical: int
    granted: int
    completed: int
    stopped: int
    cancelled_before_start: int
    failed: int
    stop_failures: int
    dropped_not_critical: int
    dropped_stale: int
    dropped_duplicate: int
    dropped_cooldown: int
    dropped_busy: int
    dropped_device_fault: int


_COUNTERS = (
    "offered_critical", "granted", "completed", "stopped", "cancelled_before_start",
    "failed", "stop_failures", "dropped_not_critical", "dropped_stale",
    "dropped_duplicate", "dropped_cooldown", "dropped_busy", "dropped_device_fault",
)


class AudioAlertGate:
    """At most one attempt per event, first-wins single slot, cooldown, no retry."""

    def __init__(
        self, device: AudioDevice, wav_path: str, duration_seconds: float, *,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        join_timeout_seconds: float = SHUTDOWN_JOIN_SECONDS,
    ) -> None:
        if not callable(getattr(device, "start", None)) or not callable(getattr(device, "stop", None)):
            raise TypeError("device must implement start(path) and stop()")
        if not isinstance(wav_path, str) or not wav_path:
            raise ValueError("wav_path must be a non-empty string")
        if (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float))
                or not math.isfinite(duration_seconds) or not 0 < duration_seconds <= MAX_ALERT_SECONDS):
            raise ValueError(f"duration_seconds must be in (0, {MAX_ALERT_SECONDS}]")
        if (isinstance(cooldown_seconds, bool) or not isinstance(cooldown_seconds, (int, float))
                or not math.isfinite(cooldown_seconds)
                or not MIN_COOLDOWN_SECONDS <= cooldown_seconds <= MAX_COOLDOWN_SECONDS):
            raise ValueError(f"cooldown_seconds must be in [{MIN_COOLDOWN_SECONDS}, {MAX_COOLDOWN_SECONDS}]")
        if (not isinstance(join_timeout_seconds, (int, float)) or not math.isfinite(join_timeout_seconds)
                or not 0 <= join_timeout_seconds <= SHUTDOWN_JOIN_SECONDS):
            raise ValueError(f"join_timeout_seconds must be in [0, {SHUTDOWN_JOIN_SECONDS}]")
        self._device = device
        self._path = wav_path
        self._duration = float(duration_seconds)
        self._cooldown = float(cooldown_seconds)
        self._clock = clock
        self._join_timeout = float(join_timeout_seconds)
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._closed = False
        self._epoch = 0
        self._session: str | None = None
        self._slot: _Candidate | None = None
        self._attempt: _Attempt | None = None
        self._next_attempt_id = 0
        self._last_grant_at: float | None = None
        self._recent_events: OrderedDict[str, None] = OrderedDict()
        self._device_fault = False
        self._lifecycle_fault = False
        self._counts = dict.fromkeys(_COUNTERS, 0)

    # ── control plane (called by the worker inside worker._state_lock) ───────

    def apply_lifecycle(self, epoch: int, session_id: str | None) -> None:
        """Only worker lifecycle transitions change the epoch (monotonic)."""
        with self._lock:
            if self._closed or epoch <= self._epoch:
                return
            self._epoch, self._session = epoch, session_id
            self._invalidate_locked()

    def fail_closed(self) -> None:
        """Permanently revoke grants after a failed lifecycle update.

        Control only: O(1) assignments, ``Event.set()`` and ``notify``; never the
        device, a wait or a join. Written without ``_invalidate_locked`` so it does
        not rely on whatever the failed update left half-applied.
        """
        with self._lock:
            self._closed = True
            self._lifecycle_fault = True
            if self._slot is not None:
                self._slot = None
                self._counts["dropped_stale"] += 1
            if self._attempt is not None:
                self._attempt.cancel.set()
            self._wake.notify_all()

    def offer(self, epoch: int, event_id: str, session_id: str | None, status: ThreatStatus) -> None:
        """An ACCEPTED publication; never creates or changes a lifecycle."""
        with self._lock:
            if status is not ThreatStatus.CRITICAL:
                self._counts["dropped_not_critical"] += 1
                return
            self._counts["offered_critical"] += 1
            if (self._closed or epoch != self._epoch or session_id is None
                    or session_id != self._session):
                self._counts["dropped_stale"] += 1
                return
            if self._device_fault:
                self._counts["dropped_device_fault"] += 1
                return
            if event_id in self._recent_events or (self._slot is not None and self._slot.event_id == event_id):
                self._counts["dropped_duplicate"] += 1
                return
            if self._in_cooldown_locked():
                self._counts["dropped_cooldown"] += 1      # dropped, never deferred
                return
            if self._slot is not None or self._attempt is not None:
                self._counts["dropped_busy"] += 1          # first-wins
                return
            self._slot = _Candidate(event_id, epoch)
            self._wake.notify_all()

    def _invalidate_locked(self) -> None:
        if self._slot is not None:
            self._slot = None
            self._counts["dropped_stale"] += 1
        attempt = self._attempt
        if attempt is not None and (self._closed or attempt.epoch != self._epoch):
            attempt.cancel.set()                            # non-blocking; this attempt only
        self._wake.notify_all()

    def _in_cooldown_locked(self) -> bool:
        return self._last_grant_at is not None and self._clock() - self._last_grant_at < self._cooldown

    def _remember_locked(self, event_id: str) -> None:
        self._recent_events[event_id] = None
        while len(self._recent_events) > _RECENT_EVENT_LIMIT:
            self._recent_events.popitem(last=False)

    # ── device plane (only the device thread) ────────────────────────────────

    def _grant_locked(self) -> _Attempt | None:
        """Grant linearization point; shares gate._lock with invalidation."""
        candidate, self._slot = self._slot, None
        if candidate is None:
            return None
        if self._closed or candidate.epoch != self._epoch or self._session is None:
            self._counts["dropped_stale"] += 1
            return None
        if self._device_fault or self._attempt is not None:
            self._counts["dropped_device_fault" if self._device_fault else "dropped_busy"] += 1
            return None
        if candidate.event_id in self._recent_events:
            self._counts["dropped_duplicate"] += 1
            return None
        if self._in_cooldown_locked():
            self._counts["dropped_cooldown"] += 1
            return None
        self._remember_locked(candidate.event_id)          # attempt spent even if it fails
        now = self._clock()
        self._last_grant_at = now
        self._next_attempt_id += 1
        self._attempt = _Attempt(self._next_attempt_id, candidate.event_id, candidate.epoch,
                                 threading.Event(), now)
        self._counts["granted"] += 1
        return self._attempt

    def run_once(self, timeout: float | None = 0.5) -> bool:
        """One device-thread iteration; returns True when a candidate was taken."""
        with self._wake:
            if self._slot is None and not self._closed:
                self._wake.wait(timeout)
            if self._slot is None:
                return False
            attempt = self._grant_locked()
        if attempt is not None:
            self._execute(attempt)
        return True

    def _execute(self, attempt: _Attempt) -> None:
        with self._lock:
            if attempt.cancel.is_set():
                outcome = "cancelled_before_start"        # device never touched
            else:
                attempt.start_attempted = True             # recorded before device.start()
                attempt.state = "STARTING"
                outcome = None
        if outcome is None:
            try:
                self._device.start(self._path)
            except Exception:
                outcome = "failed"
                self._log("Audio alert start failed")
            else:
                with self._lock:
                    attempt.state, attempt.started_at = "IN_FLIGHT", self._clock()
                # WAV length is the playback policy bound, not proof the device finished.
                outcome = "stopped" if attempt.cancel.wait(self._duration) else "completed"
            stop_ok = True
            try:
                self._device.stop()                         # always close a started (or maybe-started) attempt
            except Exception:
                stop_ok = False
                self._log("Audio alert stop failed; audio disabled until restart")
        else:
            stop_ok = True
        with self._lock:
            if not stop_ok:
                self._device_fault = True                   # never report the device safe again
                self._counts["stop_failures"] += 1
                outcome = "failed"
            self._counts[outcome] += 1
            if self._attempt is attempt:
                self._attempt = None                        # only now may a later grant happen
            self._wake.notify_all()

    @staticmethod
    def _log(message: str) -> None:
        try:
            logger.warning(message)
        except Exception:
            pass

    def _device_loop(self) -> None:
        while True:
            with self._lock:
                if self._closed and self._slot is None and self._attempt is None:
                    return
            try:
                self.run_once(0.5)
            except Exception:
                self._log("Audio alert device loop error")
            with self._lock:
                if self._closed:
                    return

    # ── lifecycle of the gate itself ─────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._closed or self._thread is not None:
                return
            self._thread = threading.Thread(target=self._device_loop, name="audio-device", daemon=True)
            self._thread.start()

    def close(self) -> bool:
        """Idempotent; signals cancel, bounded join outside the lock.

        Every call joins (bounded) and reports whether the device thread has really
        stopped: False while it is still alive, True once it has finished.
        """
        with self._lock:
            if not self._closed:
                self._closed = True
                self._invalidate_locked()
            thread = self._thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(self._join_timeout)
        if thread.is_alive():
            self._log("Audio device thread did not stop in time")
            return False
        return True

    def get_snapshot(self) -> AudioAlertSnapshot:
        with self._lock:
            attempt = self._attempt
            stalled = (attempt is not None and attempt.state in ("GRANTED", "STARTING")
                       and self._clock() - attempt.granted_at > START_STALL_INDICATOR_SECONDS)
            short = (lambda value: value[:8] if value else None)
            return AudioAlertSnapshot(
                self._thread is not None and self._thread.is_alive(), self._closed, self._epoch,
                short(self._session), short(self._slot.event_id) if self._slot else None,
                short(attempt.event_id) if attempt else None, attempt.state if attempt else None,
                stalled, self._device_fault, self._lifecycle_fault, **self._counts,
            )
