"""Explicitly disabled parking candidate path (T3c).

Used when ``SENTINEL_PARKING_MODE`` has an invalid value, or when private mode was
selected but its configuration is missing or invalid. It stands in the detector's
parking-trigger slot AND is the evaluation worker's trigger, so neither ``None`` nor the
legacy trigger is ever used as a silent substitute. It never produces, queues or hands
out a candidate.

``wait_for_candidate`` blocks on its own condition until ``wake_waiters()``, ``close()``
or the timeout (no busy loop) and always reports that nothing is ready. ``close()`` is
idempotent; afterwards ``start_session`` is refused. Holds no images, ids in logs or
references to other services.
"""

from __future__ import annotations

import dataclasses
import threading


class DisabledTriggerClosedError(RuntimeError):
    """A session start after close() (sanitized: no ids)."""


@dataclasses.dataclass(frozen=True)
class DisabledParkingSnapshot:
    source_session_id: str | None = None
    pending_event_id: str | None = None
    observations_ignored: int = 0
    closed: bool = False


class DisabledParkingTrigger:
    """Satisfies the worker's ``CandidateTrigger`` and the detector's
    ``ParkingCandidateConsumer`` protocols without ever producing a candidate."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._closed = False
        self._wake_generation = 0
        self._observations_ignored = 0

    # detector slot (ParkingCandidateConsumer)

    def start_session(self, session_id: str) -> None:
        with self._condition:
            if self._closed:
                raise DisabledTriggerClosedError("disabled parking trigger is closed")

    def observe(self, domain_snapshot, tracking_snapshot) -> None:
        with self._condition:
            self._observations_ignored += 1

    def end_session(self, session_id: str) -> None:
        """Nothing to clean; allowed after close()."""

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._wake_generation += 1
            self._condition.notify_all()

    # worker (CandidateTrigger)

    def wait_for_candidate(self, timeout_seconds: float) -> bool:
        with self._condition:
            if self._closed:
                return False
            generation = self._wake_generation
            self._condition.wait_for(lambda: self._closed or self._wake_generation != generation,
                                     timeout=max(0.0, float(timeout_seconds)))
            return False

    def wake_waiters(self) -> None:
        with self._condition:
            self._wake_generation += 1
            self._condition.notify_all()

    def claim_next_candidate(self, now: float):
        return None

    def complete(self, event_id: str, now: float) -> bool:
        return False

    def fail(self, event_id: str, now: float) -> bool:
        return False

    def get_snapshot(self) -> DisabledParkingSnapshot:
        with self._condition:
            return DisabledParkingSnapshot(observations_ignored=self._observations_ignored, closed=self._closed)
