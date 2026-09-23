"""Bounded local evidence filter for parking person/motorcycle interactions."""

from __future__ import annotations

from collections import deque
import dataclasses
import math
import threading
import uuid

from app.models.domain import DomainName, DomainSnapshot, DomainState
from app.models.parking_candidate import (
    ParkingCandidateDiagnostic,
    ParkingCandidateEvidence,
    ParkingCandidateEvent,
    ParkingCandidateState,
)
from app.models.tracking import TrackedDetection, TrackingSnapshot


@dataclasses.dataclass
class _Pair:
    person_track_id: int
    motorcycle_track_id: int
    first_close_timestamp: float
    last_seen_timestamp: float
    last_source_frame_seq: int
    proximity_observations: int
    distances: deque[float]
    sequences: deque[int]
    first_person_center: tuple[float, float]
    latest_person_center: tuple[float, float]
    first_motorcycle_center: tuple[float, float]
    latest_motorcycle_center: tuple[float, float]
    state: ParkingCandidateState = ParkingCandidateState.OBSERVING
    event: ParkingCandidateEvent | None = None
    cooldown_until: float | None = None


class ParkingCandidateTrigger:
    """Select bounded, anonymous candidate interactions without side effects.

    All timestamps are supplied by tracking observations and are expected to be
    monotonic.  This service does not make network requests or own threads.
    """

    def __init__(
        self,
        *,
        proximity_threshold: float = 0.18,
        minimum_observations: int = 4,
        minimum_dwell_seconds: float = 2.0,
        minimum_person_motion: float = 0.03,
        minimum_motorcycle_motion: float = 0.02,
        minimum_distance_range: float = 0.03,
        pair_stale_seconds: float = 3.0,
        lost_observation_grace_seconds: float = 1.5,
        pair_cooldown_seconds: float = 45.0,
        global_request_interval_seconds: float = 10.0,
        recent_sequence_limit: int = 32,
    ) -> None:
        values = {
            "proximity_threshold": proximity_threshold,
            "minimum_dwell_seconds": minimum_dwell_seconds,
            "minimum_person_motion": minimum_person_motion,
            "minimum_motorcycle_motion": minimum_motorcycle_motion,
            "minimum_distance_range": minimum_distance_range,
            "pair_stale_seconds": pair_stale_seconds,
            "lost_observation_grace_seconds": lost_observation_grace_seconds,
            "pair_cooldown_seconds": pair_cooldown_seconds,
            "global_request_interval_seconds": global_request_interval_seconds,
        }
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError("trigger thresholds must be finite and non-negative")
        if minimum_observations < 1 or recent_sequence_limit < 1:
            raise ValueError("observation and sequence limits must be positive")
        self._proximity_threshold = proximity_threshold
        self._minimum_observations = minimum_observations
        self._minimum_dwell_seconds = minimum_dwell_seconds
        self._minimum_person_motion = minimum_person_motion
        self._minimum_motorcycle_motion = minimum_motorcycle_motion
        self._minimum_distance_range = minimum_distance_range
        self._pair_stale_seconds = pair_stale_seconds
        self._lost_grace_seconds = lost_observation_grace_seconds
        self._cooldown_seconds = pair_cooldown_seconds
        self._global_interval = global_request_interval_seconds
        self._recent_limit = recent_sequence_limit
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._pairs: dict[tuple[int, int], _Pair] = {}
        self._last_sequence: int | None = None
        self._last_timestamp: float | None = None
        self._pending_event_id: str | None = None
        self._last_claim_timestamp: float | None = None
        self._closed = False

    def start_session(self, session_id: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._session_id = session_id
            self._pairs.clear()
            self._last_sequence = None
            self._last_timestamp = None
            self._pending_event_id = None
            self._last_claim_timestamp = None

    def observe(self, domain_snapshot: DomainSnapshot, tracking_snapshot: TrackingSnapshot) -> None:
        """Ingest one metadata-only tracker snapshot; never calls external code."""
        with self._lock:
            if self._closed or not self._valid_snapshot(tracking_snapshot):
                return
            if tracking_snapshot.source_session_id != self._session_id:
                return
            if not self._domain_is_active(domain_snapshot, tracking_snapshot.source_session_id):
                return
            if (
                self._last_sequence is not None and tracking_snapshot.source_frame_seq <= self._last_sequence
            ) or (
                self._last_timestamp is not None and tracking_snapshot.source_timestamp < self._last_timestamp
            ):
                return
            self._last_sequence = tracking_snapshot.source_frame_seq
            self._last_timestamp = tracking_snapshot.source_timestamp
            self._cleanup(tracking_snapshot.source_timestamp)
            diagonal = math.hypot(tracking_snapshot.frame_width, tracking_snapshot.frame_height)
            persons = [track for track in tracking_snapshot.tracks if self._eligible(track, "person")]
            motorcycles = [track for track in tracking_snapshot.tracks if self._eligible(track, "motorcycle")]
            for person in sorted(persons, key=lambda item: item.track_id):
                nearby: list[tuple[float, TrackedDetection]] = []
                for motorcycle in motorcycles:
                    try:
                        distance = self._edge_distance(person.xyxy, motorcycle.xyxy) / diagonal
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(distance) and distance <= self._proximity_threshold:
                        nearby.append((distance, motorcycle))
                if nearby:
                    distance, motorcycle = min(nearby, key=lambda item: (item[0], item[1].track_id))
                    self._observe_pair(person, motorcycle, distance, tracking_snapshot, diagonal)
            self._cleanup(tracking_snapshot.source_timestamp)

    def claim_next_candidate(self, now: float) -> ParkingCandidateEvent | None:
        if not self._valid_time(now):
            raise ValueError("now must be finite")
        with self._lock:
            if self._closed or self._pending_event_id is not None:
                return None
            if self._last_claim_timestamp is not None and now - self._last_claim_timestamp < self._global_interval:
                return None
            candidates = [pair for pair in self._pairs.values() if pair.state is ParkingCandidateState.CANDIDATE and pair.event]
            if not candidates:
                return None
            pair = min(candidates, key=self._candidate_sort_key)
            pair.state = ParkingCandidateState.API_PENDING
            event = dataclasses.replace(pair.event, state=ParkingCandidateState.API_PENDING)
            pair.event = event
            self._pending_event_id = event.event_id
            self._last_claim_timestamp = now
            return event

    def complete(self, event_id: str, now: float) -> bool:
        return self._finish(event_id, now)

    def fail(self, event_id: str, now: float) -> bool:
        return self._finish(event_id, now)

    def get_snapshot(self) -> ParkingCandidateDiagnostic:
        with self._lock:
            return ParkingCandidateDiagnostic(
                source_session_id=self._session_id,
                pending_event_id=self._pending_event_id,
                pair_states=tuple(sorted((p.person_track_id, p.motorcycle_track_id, p.state) for p in self._pairs.values())),
            )

    def end_session(self, session_id: str) -> None:
        with self._lock:
            if session_id != self._session_id:
                return
            self._session_id = None
            self._pairs.clear()
            self._last_sequence = None
            self._last_timestamp = None
            self._pending_event_id = None
            self._last_claim_timestamp = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._session_id = None
            self._pairs.clear()
            self._pending_event_id = None

    def _observe_pair(self, person: TrackedDetection, motorcycle: TrackedDetection, distance: float, snapshot: TrackingSnapshot, diagonal: float) -> None:
        key = (person.track_id, motorcycle.track_id)
        pair = self._pairs.get(key)
        if pair is not None and pair.state is ParkingCandidateState.COOLDOWN:
            return
        if pair is None or snapshot.source_timestamp - pair.last_seen_timestamp > self._lost_grace_seconds:
            pair = _Pair(
                person.track_id, motorcycle.track_id, snapshot.source_timestamp, snapshot.source_timestamp,
                snapshot.source_frame_seq - 1, 0, deque(maxlen=self._recent_limit), deque(maxlen=self._recent_limit),
                person.center, person.center, motorcycle.center, motorcycle.center,
            )
            self._pairs[key] = pair
        if snapshot.source_frame_seq <= pair.last_source_frame_seq:
            return
        pair.last_seen_timestamp = snapshot.source_timestamp
        pair.last_source_frame_seq = snapshot.source_frame_seq
        pair.proximity_observations += 1
        pair.distances.append(distance)
        pair.sequences.append(snapshot.source_frame_seq)
        pair.latest_person_center = person.center
        pair.latest_motorcycle_center = motorcycle.center
        if pair.state is ParkingCandidateState.OBSERVING and self._candidate_ready(pair, snapshot.source_timestamp, diagonal):
            evidence = self._evidence(pair, snapshot.source_timestamp, diagonal)
            pair.event = ParkingCandidateEvent(
                event_id=str(uuid.uuid4()), source_session_id=snapshot.source_session_id,
                domain=DomainName.PARKING_MOTORCYCLE, person_track_id=person.track_id,
                object_track_id=motorcycle.track_id, started_at=pair.first_close_timestamp,
                triggered_at=snapshot.source_timestamp, trigger_source_frame_seq=snapshot.source_frame_seq,
                evidence=evidence, candidate_frame_sequences=tuple(pair.sequences),
                state=ParkingCandidateState.CANDIDATE,
            )
            pair.state = ParkingCandidateState.CANDIDATE

    def _finish(self, event_id: str, now: float) -> bool:
        if not self._valid_time(now):
            raise ValueError("now must be finite")
        with self._lock:
            if self._pending_event_id != event_id:
                return False
            for pair in self._pairs.values():
                if pair.event is not None and pair.event.event_id == event_id and pair.state is ParkingCandidateState.API_PENDING:
                    pair.state = ParkingCandidateState.COOLDOWN
                    pair.cooldown_until = now + self._cooldown_seconds
                    self._pending_event_id = None
                    return True
            return False

    def _cleanup(self, timestamp: float) -> None:
        expired: list[tuple[int, int]] = []
        for key, pair in self._pairs.items():
            if pair.state is ParkingCandidateState.COOLDOWN:
                if pair.cooldown_until is not None and timestamp >= pair.cooldown_until:
                    expired.append(key)
            elif pair.state is not ParkingCandidateState.API_PENDING and timestamp - pair.last_seen_timestamp > self._pair_stale_seconds:
                pair.state = ParkingCandidateState.RELEASED
                expired.append(key)
        for key in expired:
            self._pairs.pop(key, None)

    def _candidate_ready(self, pair: _Pair, timestamp: float, diagonal: float) -> bool:
        evidence = self._evidence(pair, timestamp, diagonal)
        return (
            evidence.proximity_observations >= self._minimum_observations
            and evidence.dwell_seconds >= self._minimum_dwell_seconds
            and (
                evidence.person_motion_normalized >= self._minimum_person_motion
                or evidence.motorcycle_motion_normalized >= self._minimum_motorcycle_motion
                or evidence.pair_distance_range_normalized >= self._minimum_distance_range
            )
        )

    def _evidence(self, pair: _Pair, timestamp: float, diagonal: float) -> ParkingCandidateEvidence:
        distances = tuple(pair.distances)
        return ParkingCandidateEvidence(
            proximity_observations=pair.proximity_observations,
            dwell_seconds=max(0.0, timestamp - pair.first_close_timestamp),
            minimum_distance_normalized=min(distances),
            mean_distance_normalized=sum(distances) / len(distances),
            person_motion_normalized=self._point_distance(pair.first_person_center, pair.latest_person_center) / diagonal,
            motorcycle_motion_normalized=self._point_distance(pair.first_motorcycle_center, pair.latest_motorcycle_center) / diagonal,
            pair_distance_range_normalized=max(distances) - min(distances),
        )

    @staticmethod
    def _candidate_sort_key(pair: _Pair) -> tuple[float, float, float, int, int]:
        assert pair.event is not None
        evidence = pair.event.evidence
        return (-evidence.proximity_observations, -evidence.dwell_seconds, evidence.mean_distance_normalized, pair.person_track_id, pair.motorcycle_track_id)

    @staticmethod
    def _domain_is_active(snapshot: DomainSnapshot, session_id: str) -> bool:
        return snapshot.source_session_id == session_id and snapshot.state is DomainState.LOCKED and snapshot.domain is DomainName.PARKING_MOTORCYCLE

    @staticmethod
    def _eligible(track: TrackedDetection, expected_class: str) -> bool:
        return (
            track.confirmed and track.class_name.strip().lower() == expected_class
            and ParkingCandidateTrigger._finite(track.confidence) and track.confidence >= 0.50
            and ParkingCandidateTrigger._valid_box(track.xyxy)
            and all(ParkingCandidateTrigger._finite(value) for value in track.center)
        )

    @staticmethod
    def _valid_snapshot(snapshot: TrackingSnapshot) -> bool:
        return (
            ParkingCandidateTrigger._finite(snapshot.frame_width) and snapshot.frame_width > 0
            and ParkingCandidateTrigger._finite(snapshot.frame_height) and snapshot.frame_height > 0
            and ParkingCandidateTrigger._valid_time(snapshot.source_timestamp)
        )

    @staticmethod
    def _valid_time(value: float) -> bool:
        return ParkingCandidateTrigger._finite(value)

    @staticmethod
    def _finite(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    @staticmethod
    def _valid_box(box: tuple[float, float, float, float]) -> bool:
        return (
            isinstance(box, tuple) and len(box) == 4
            and all(ParkingCandidateTrigger._finite(value) for value in box)
            and box[2] > box[0] and box[3] > box[1]
        )

    @staticmethod
    def _edge_distance(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
        dx = max(first[0] - second[2], second[0] - first[2], 0.0)
        dy = max(first[1] - second[3], second[1] - first[3], 0.0)
        return math.hypot(dx, dy)

    @staticmethod
    def _point_distance(first: tuple[float, float], second: tuple[float, float]) -> float:
        return math.hypot(first[0] - second[0], first[1] - second[1])
