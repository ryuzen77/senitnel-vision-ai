"""Fast, session-scoped classifier for the motorcycle parking domain."""

from __future__ import annotations

from collections import deque
import threading

from app.models.detection import DetectionResult
from app.models.domain import DomainName, DomainSnapshot, DomainState


class DomainService:
    """Classify recent detector observations without inference or I/O."""

    _WINDOW_SIZE = 5
    _HITS_TO_LOCK = 3
    _MIN_CONFIDENCE = 0.50

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._state = DomainState.IDLE
        self._domain = DomainName.UNKNOWN
        self._observations: deque[bool] = deque(maxlen=self._WINDOW_SIZE)
        self._last_detection_seq: int | None = None

    def start_session(self, session_id: str) -> None:
        """Reset classification state for a newly active capture session."""
        with self._lock:
            self._session_id = session_id
            self._state = DomainState.PROBING
            self._domain = DomainName.UNKNOWN
            self._observations.clear()
            self._last_detection_seq = None

    def observe(self, detection_result: DetectionResult) -> None:
        """Record one valid detector result, ignoring stale or foreign results."""
        with self._lock:
            if (
                self._state is not DomainState.PROBING
                or detection_result.source_session_id != self._session_id
                or (
                    self._last_detection_seq is not None
                    and detection_result.detection_seq <= self._last_detection_seq
                )
            ):
                return

            self._last_detection_seq = detection_result.detection_seq
            hit = any(
                detection.class_name.strip().lower() == "motorcycle"
                and detection.confidence >= self._MIN_CONFIDENCE
                for detection in detection_result.detections
            )
            self._observations.append(hit)
            if sum(self._observations) >= self._HITS_TO_LOCK:
                self._state = DomainState.LOCKED
                self._domain = DomainName.PARKING_MOTORCYCLE

    def end_session(self, session_id: str) -> None:
        """Clear only the matching active session."""
        with self._lock:
            if session_id != self._session_id:
                return
            self._session_id = None
            self._state = DomainState.IDLE
            self._domain = DomainName.UNKNOWN
            self._observations.clear()
            self._last_detection_seq = None

    def get_snapshot(self) -> DomainSnapshot:
        with self._lock:
            observation_count = len(self._observations)
            evidence_hits = sum(self._observations)
            return DomainSnapshot(
                source_session_id=self._session_id,
                state=self._state,
                domain=self._domain,
                observation_count=observation_count,
                evidence_hits=evidence_hits,
                confidence=evidence_hits / observation_count if observation_count else 0.0,
                locked=self._state is DomainState.LOCKED,
                last_detection_seq=self._last_detection_seq,
            )


domain_service = DomainService()
