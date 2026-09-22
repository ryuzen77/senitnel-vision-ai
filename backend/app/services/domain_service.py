"""Fast, session-scoped classifier for the motorcycle parking domain."""

from __future__ import annotations

from collections import deque
import math
import threading

from app.models.detection import DetectionResult
from app.models.domain import DecisionSource, DomainName, DomainSnapshot, DomainState


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
        self._decision_source: DecisionSource | None = None
        self._scene_model: str | None = None
        self._scene_sample_count: int | None = None
        self._scene_top_score: float | None = None
        self._scene_mean_margin: float | None = None
        self._scene_consistency: float | None = None

    def _clear_scene_metadata(self) -> None:
        self._decision_source = None
        self._scene_model = None
        self._scene_sample_count = None
        self._scene_top_score = None
        self._scene_mean_margin = None
        self._scene_consistency = None

    def start_session(self, session_id: str) -> None:
        """Reset classification state for a newly active capture session."""
        with self._lock:
            self._session_id = session_id
            self._state = DomainState.PROBING
            self._domain = DomainName.UNKNOWN
            self._observations.clear()
            self._last_detection_seq = None
            self._clear_scene_metadata()

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
                self._decision_source = DecisionSource.YOLO
                self._scene_model = None
                self._scene_sample_count = None
                self._scene_top_score = None
                self._scene_mean_margin = None
                self._scene_consistency = None

    def apply_scene_decision(
        self,
        session_id: str,
        domain: DomainName,
        scene_model: str,
        mean_top_score: float,
        consistency: float,
        mean_margin: float,
        sample_count: int,
    ) -> bool:
        """Atomically lock a valid active session with one CLIP scene decision."""
        allowed_domains = {DomainName.UNKNOWN, DomainName.PARKING_MOTORCYCLE, DomainName.RETAIL_STORE}
        if not isinstance(domain, DomainName) or domain not in allowed_domains:
            raise ValueError("scene domain must be UNKNOWN, PARKING_MOTORCYCLE, or RETAIL_STORE")
        if not isinstance(scene_model, str) or not scene_model.strip():
            raise ValueError("scene_model must be a non-empty string")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count <= 0:
            raise ValueError("sample_count must be a positive integer")
        values = {
            "mean_top_score": mean_top_score,
            "consistency": consistency,
            "mean_margin": mean_margin,
        }
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values.values()):
            raise ValueError("scene scores must be finite numeric values")
        if not 0.0 <= consistency <= 1.0:
            raise ValueError("consistency must be between 0.0 and 1.0")

        with self._lock:
            if self._state is not DomainState.PROBING or session_id != self._session_id:
                return False
            self._state = DomainState.LOCKED
            self._domain = domain
            self._decision_source = DecisionSource.CLIP
            self._scene_model = scene_model
            self._scene_sample_count = sample_count
            self._scene_top_score = float(mean_top_score)
            self._scene_mean_margin = float(mean_margin)
            self._scene_consistency = float(consistency)
            return True

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
            self._clear_scene_metadata()

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
                decision_source=self._decision_source,
                scene_model=self._scene_model,
                scene_sample_count=self._scene_sample_count,
                scene_top_score=self._scene_top_score,
                scene_mean_margin=self._scene_mean_margin,
                scene_consistency=self._scene_consistency,
            )


domain_service = DomainService()
