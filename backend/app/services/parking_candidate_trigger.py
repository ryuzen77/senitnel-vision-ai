"""Session-scoped, person-centered temporal parking episodes; no dispatch."""

from __future__ import annotations

from collections import deque
import dataclasses
import math
import threading
import uuid

from app.models.domain import DomainName, DomainSnapshot, DomainState
from app.models.parking_candidate import (
    ParkingCandidateDiagnostic, ParkingCandidateEvidence, ParkingCandidateEvent,
    ParkingCandidateState as State,
)
from app.models.tracking import TrackedDetection, TrackingSnapshot


@dataclasses.dataclass(frozen=True)
class _Sample:
    timestamp: float
    sequence: int
    distance: float
    person_center: tuple[float, float]
    motor_center: tuple[float, float]
    close: bool


@dataclasses.dataclass
class _Episode:
    episode_id: str
    person_id: int
    initial_motor_id: int
    motor_id: int
    started_at: float
    last_seen_at: float
    last_motor_at: float
    last_motor_center: tuple[float, float]
    history: deque[_Sample]
    sequences: deque[int]
    first_person_center: tuple[float, float] | None = None
    first_motor_center: tuple[float, float] | None = None
    state: State = State.APPROACHING
    event: ParkingCandidateEvent | None = None
    cooldown_until: float | None = None
    reassociations: int = 0
    observations: int = 0
    observations_since_first_close: int = 0
    close_observations: int = 0
    last_close_at: float | None = None
    close_streak_at: float | None = None
    minimum_distance: float = math.inf
    maximum_distance: float = 0.0
    approach_max_distance: float = 0.0
    distance_sum: float = 0.0
    person_path: float = 0.0
    motor_path: float = 0.0
    motor_after_engagement: float = 0.0
    direction_changes: int = 0
    last_direction: int = 0
    engagement_distance: float | None = None
    separation: float = 0.0


class ParkingCandidateTrigger:
    """Select review candidates from bounded metadata, never image data.

    These constructor defaults are prototype baselines, not a theft classifier.
    All times come from monotonic tracking observations.
    """

    def __init__(
        self, *, proximity_threshold: float = 0.18,
        minimum_observations: int = 4, minimum_dwell_seconds: float = 2.0,
        minimum_person_motion: float = 0.03, minimum_motorcycle_motion: float = 0.02,
        minimum_distance_range: float = 0.03, close_ratio_threshold: float = 0.75,
        engagement_observations: int = 2, separation_threshold: float = 0.03,
        replacement_center_threshold: float = 0.06,
        pair_stale_seconds: float = 3.0, lost_observation_grace_seconds: float = 1.5,
        pair_cooldown_seconds: float = 45.0, global_request_interval_seconds: float = 10.0,
        recent_sequence_limit: int = 32, history_limit: int = 64,
        history_horizon_seconds: float = 10.0,
    ) -> None:
        numeric = dict(
            proximity_threshold=proximity_threshold, minimum_dwell_seconds=minimum_dwell_seconds,
            minimum_person_motion=minimum_person_motion, minimum_motorcycle_motion=minimum_motorcycle_motion,
            minimum_distance_range=minimum_distance_range, close_ratio_threshold=close_ratio_threshold,
            separation_threshold=separation_threshold, replacement_center_threshold=replacement_center_threshold,
            pair_stale_seconds=pair_stale_seconds, lost_observation_grace_seconds=lost_observation_grace_seconds,
            pair_cooldown_seconds=pair_cooldown_seconds,
            global_request_interval_seconds=global_request_interval_seconds,
            history_horizon_seconds=history_horizon_seconds,
        )
        if any(not math.isfinite(v) or v < 0 for v in numeric.values()) or close_ratio_threshold > 1:
            raise ValueError("episode thresholds must be finite and in range")
        if min(minimum_observations, engagement_observations, recent_sequence_limit, history_limit) < 1:
            raise ValueError("episode count limits must be positive")
        self._proximity_threshold = proximity_threshold
        self._minimum_observations = minimum_observations
        self._minimum_dwell_seconds = minimum_dwell_seconds
        self._minimum_person_motion = minimum_person_motion
        self._minimum_motorcycle_motion = minimum_motorcycle_motion
        self._minimum_distance_range = minimum_distance_range
        self._close_ratio_threshold = close_ratio_threshold
        self._engagement_observations = engagement_observations
        self._separation_threshold = separation_threshold
        self._replacement_threshold = replacement_center_threshold
        self._stale_seconds = pair_stale_seconds
        self._grace_seconds = lost_observation_grace_seconds
        self._cooldown_seconds = pair_cooldown_seconds
        self._global_interval = global_request_interval_seconds
        self._sequence_limit = recent_sequence_limit
        self._history_limit = history_limit
        self._history_horizon = history_horizon_seconds
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._episodes: dict[int, _Episode] = {}
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
            self._episodes.clear()
            self._last_sequence = self._last_timestamp = self._last_claim_timestamp = None
            self._pending_event_id = None

    def observe(self, domain_snapshot: DomainSnapshot, tracking_snapshot: TrackingSnapshot) -> None:
        with self._lock:
            if (self._closed or not self._valid_snapshot(tracking_snapshot)
                    or tracking_snapshot.source_session_id != self._session_id
                    or not self._domain_is_active(domain_snapshot, tracking_snapshot.source_session_id)):
                return
            seq, now = tracking_snapshot.source_frame_seq, tracking_snapshot.source_timestamp
            if ((self._last_sequence is not None and seq <= self._last_sequence)
                    or (self._last_timestamp is not None and now < self._last_timestamp)):
                return
            self._last_sequence, self._last_timestamp = seq, now
            self._cleanup(now)
            diagonal = math.hypot(tracking_snapshot.frame_width, tracking_snapshot.frame_height)
            persons = sorted((t for t in tracking_snapshot.tracks if self._eligible(t, "person")), key=lambda t: t.track_id)
            motors = {t.track_id: t for t in tracking_snapshot.tracks if self._eligible(t, "motorcycle")}
            for person in persons:
                episode = self._episodes.get(person.track_id)
                if episode is not None and episode.state is State.RELEASED:
                    if self._near_any(person, motors.values(), diagonal):
                        episode.last_seen_at = now  # retain tombstone while engagement continues
                    else:
                        self._episodes.pop(person.track_id)
                    continue
                if episode is None:
                    choices = sorted(
                        ((self._edge_distance(person.xyxy, m.xyxy) / diagonal, m.track_id, m) for m in motors.values()),
                        key=lambda item: (item[0], item[1]),
                    )
                    if not choices or choices[0][0] > self._proximity_threshold * 2:
                        continue
                    motor = choices[0][2]
                    episode = _Episode(
                        str(uuid.uuid4()), person.track_id, motor.track_id, motor.track_id,
                        now, now, now, motor.center,
                        deque(maxlen=self._history_limit), deque(maxlen=self._sequence_limit),
                    )
                    self._episodes[person.track_id] = episode
                motor = motors.get(episode.motor_id)
                if motor is None:
                    motor = self._replacement(episode, motors.values(), now, diagonal)
                    if motor is None:
                        continue
                    episode.motor_id = motor.track_id
                    episode.reassociations += 1
                distance = self._edge_distance(person.xyxy, motor.xyxy) / diagonal
                self._record(episode, person, motor, distance, now, seq, diagonal)
            self._cleanup(now)

    def claim_next_candidate(self, now: float) -> ParkingCandidateEvent | None:
        if not self._valid_time(now):
            raise ValueError("now must be finite")
        with self._lock:
            if (self._closed or self._pending_event_id is not None
                    or (self._last_claim_timestamp is not None and now - self._last_claim_timestamp < self._global_interval)):
                return None
            candidates = [e for e in self._episodes.values() if e.state is State.CANDIDATE and e.event]
            if not candidates:
                return None
            episode = min(candidates, key=self._candidate_sort_key)
            episode.state = State.API_PENDING
            episode.event = dataclasses.replace(episode.event, state=State.API_PENDING)
            self._pending_event_id = episode.event.event_id
            self._last_claim_timestamp = now
            return episode.event

    def complete(self, event_id: str, now: float) -> bool:
        return self._finish(event_id, now)

    def fail(self, event_id: str, now: float) -> bool:
        return self._finish(event_id, now)

    def get_snapshot(self) -> ParkingCandidateDiagnostic:
        with self._lock:
            current = tuple(sorted(self._episodes.values(), key=lambda e: e.person_id))
            return ParkingCandidateDiagnostic(
                self._session_id, self._pending_event_id,
                tuple((e.person_id, e.motor_id, e.state) for e in current if e.state is not State.RELEASED),
                tuple((e.person_id, e.episode_id, e.initial_motor_id, e.motor_id,
                       e.reassociations, e.state) for e in current),
            )

    def end_session(self, session_id: str) -> None:
        with self._lock:
            if session_id != self._session_id:
                return
            self._session_id = None
            self._episodes.clear()
            self._last_sequence = self._last_timestamp = self._last_claim_timestamp = None
            self._pending_event_id = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._session_id = None
            self._episodes.clear()
            self._pending_event_id = None

    def _record(self, e: _Episode, person: TrackedDetection, motor: TrackedDetection,
                distance: float, now: float, seq: int, diagonal: float) -> None:
        previous = e.history[-1] if e.history else None
        if e.first_person_center is None:
            e.first_person_center = person.center
            e.first_motor_center = motor.center
        close = distance <= self._proximity_threshold
        if previous is not None:
            e.person_path += self._point_distance(previous.person_center, person.center) / diagonal
            motor_step = self._point_distance(previous.motor_center, motor.center) / diagonal
            e.motor_path += motor_step
            if e.state in (State.ENGAGED, State.TRANSITION):
                e.motor_after_engagement += motor_step
            delta = distance - previous.distance
            direction = 1 if delta > 0.003 else (-1 if delta < -0.003 else 0)
            if direction and e.last_direction and direction != e.last_direction:
                e.direction_changes += 1
            if direction:
                e.last_direction = direction
        e.observations += 1
        if close:
            if e.last_close_at is None or now - e.last_close_at > self._grace_seconds:
                e.close_streak_at = now
            e.last_close_at = now
            e.close_observations += 1
            e.sequences.append(seq)
        elif e.last_close_at is not None and now - e.last_close_at > self._grace_seconds:
            e.close_streak_at = None
        if e.last_close_at is not None:
            # Earlier approach frames do not dilute a stable close engagement.
            e.observations_since_first_close += 1
        e.minimum_distance = min(e.minimum_distance, distance)
        e.maximum_distance = max(e.maximum_distance, distance)
        e.distance_sum += distance
        if e.state is State.APPROACHING:
            e.approach_max_distance = max(e.approach_max_distance, distance)
        if e.engagement_distance is not None:
            e.separation = max(e.separation, distance - e.engagement_distance)
        e.history.append(_Sample(now, seq, distance, person.center, motor.center, close))
        while e.history and now - e.history[0].timestamp > self._history_horizon:
            e.history.popleft()
        e.last_seen_at, e.last_motor_at, e.last_motor_center = now, now, motor.center
        if (not close and e.last_close_at is not None
                and now - e.last_close_at > self._grace_seconds
                and e.state not in (State.API_PENDING, State.COOLDOWN)):
            e.state = State.RELEASED
            self._episodes.pop(e.person_id, None)
            return
        if e.state in (State.CANDIDATE, State.API_PENDING, State.COOLDOWN):
            return  # one event per episode
        if e.state is State.APPROACHING and e.close_observations >= self._engagement_observations:
            e.state = State.ENGAGED
            e.engagement_distance = distance
        if e.state in (State.ENGAGED, State.TRANSITION):
            if e.motor_after_engagement >= self._minimum_motorcycle_motion or e.separation >= self._separation_threshold:
                e.state = State.TRANSITION
            evidence = self._evidence(e, now, diagonal)
            baseline = (
                evidence.proximity_observations >= self._minimum_observations
                and evidence.continuous_close_duration >= self._minimum_dwell_seconds
                and evidence.close_observation_ratio >= self._close_ratio_threshold
            )
            activity = (
                evidence.cumulative_person_path_normalized >= self._minimum_person_motion
                or evidence.cumulative_motorcycle_path_normalized >= self._minimum_motorcycle_motion
                or evidence.pair_distance_range_normalized >= self._minimum_distance_range
                or evidence.distance_direction_changes >= 2
                or evidence.motor_motion_after_engagement >= self._minimum_motorcycle_motion
            )
            if baseline and (activity or (e.state is State.TRANSITION and e.separation >= self._separation_threshold)):
                self._make_candidate(e, evidence, now, seq)

    def _make_candidate(self, e: _Episode, evidence: ParkingCandidateEvidence, now: float, seq: int) -> None:
        e.event = ParkingCandidateEvent(
            str(uuid.uuid4()), self._session_id, DomainName.PARKING_MOTORCYCLE,
            e.person_id, e.motor_id, e.started_at, now, seq,
            evidence, tuple(e.sequences), State.CANDIDATE,
            episode_id=e.episode_id, initial_object_track_id=e.initial_motor_id,
            active_object_track_id=e.motor_id, reassociation_count=e.reassociations,
            approach_distance_drop=evidence.approach_distance_drop,
            close_observation_ratio=evidence.close_observation_ratio,
            continuous_close_duration=evidence.continuous_close_duration,
            cumulative_person_path_normalized=evidence.cumulative_person_path_normalized,
            cumulative_motorcycle_path_normalized=evidence.cumulative_motorcycle_path_normalized,
            distance_direction_changes=evidence.distance_direction_changes,
            motor_motion_after_engagement=evidence.motor_motion_after_engagement,
            separation_after_engagement=evidence.separation_after_engagement,
            episode_state_at_trigger=evidence.episode_state_at_trigger,
        )
        e.state = State.CANDIDATE

    def _evidence(self, e: _Episode, now: float, diagonal: float) -> ParkingCandidateEvidence:
        last = e.history[-1]
        assert e.first_person_center is not None and e.first_motor_center is not None
        person_net = self._point_distance(e.first_person_center, last.person_center) / diagonal
        motor_net = self._point_distance(e.first_motor_center, last.motor_center) / diagonal
        duration = max(0.0, now - (e.close_streak_at if e.close_streak_at is not None else now))
        return ParkingCandidateEvidence(
            e.close_observations, duration, e.minimum_distance, e.distance_sum / e.observations,
            person_net, motor_net, e.maximum_distance - e.minimum_distance,
            episode_id=e.episode_id, initial_object_track_id=e.initial_motor_id,
            active_object_track_id=e.motor_id, reassociation_count=e.reassociations,
            approach_distance_drop=max(0.0, e.approach_max_distance - e.minimum_distance),
            close_observation_ratio=e.close_observations / e.observations_since_first_close,
            continuous_close_duration=duration,
            cumulative_person_path_normalized=e.person_path,
            cumulative_motorcycle_path_normalized=e.motor_path,
            distance_direction_changes=e.direction_changes,
            motor_motion_after_engagement=e.motor_after_engagement,
            separation_after_engagement=e.separation,
            episode_state_at_trigger=e.state,
        )

    def _replacement(self, e: _Episode, motors: object, now: float, diagonal: float) -> TrackedDetection | None:
        if now - e.last_motor_at > self._grace_seconds:
            return None
        nearby = sorted(
            ((self._point_distance(m.center, e.last_motor_center) / diagonal, m.track_id, m) for m in motors),
            key=lambda item: (item[0], item[1]),
        )
        plausible = [item for item in nearby if item[0] <= self._replacement_threshold]
        return plausible[0][2] if len(plausible) == 1 else None

    def _finish(self, event_id: str, now: float) -> bool:
        if not self._valid_time(now):
            raise ValueError("now must be finite")
        with self._lock:
            if event_id != self._pending_event_id:
                return False
            for e in self._episodes.values():
                if e.event and e.event.event_id == event_id and e.state is State.API_PENDING:
                    e.state = State.COOLDOWN
                    e.cooldown_until = now + self._cooldown_seconds
                    self._pending_event_id = None
                    return True
            return False

    def _cleanup(self, now: float) -> None:
        for person_id, e in tuple(self._episodes.items()):
            if e.state is State.COOLDOWN:
                if e.cooldown_until is not None and now >= e.cooldown_until:
                    e.state = State.RELEASED
                continue
            if e.state is State.API_PENDING:
                continue
            if e.state is State.RELEASED:
                if now - e.last_seen_at > self._stale_seconds:
                    self._episodes.pop(person_id)
                continue
            if now - e.last_seen_at > self._stale_seconds or now - e.last_motor_at > self._stale_seconds:
                e.state = State.RELEASED
                self._episodes.pop(person_id)

    @staticmethod
    def _candidate_sort_key(e: _Episode) -> tuple[float, float, float, float, int, int]:
        assert e.event is not None
        v = e.event.evidence
        return (-v.proximity_observations, -v.dwell_seconds, v.mean_distance_normalized,
                e.event.triggered_at, e.person_id, e.motor_id)

    def _near_any(self, person: TrackedDetection, motors: object, diagonal: float) -> bool:
        return any(self._edge_distance(person.xyxy, m.xyxy) / diagonal <= self._proximity_threshold for m in motors)

    @staticmethod
    def _domain_is_active(snapshot: DomainSnapshot, session_id: str) -> bool:
        return snapshot.source_session_id == session_id and snapshot.state is DomainState.LOCKED and snapshot.domain is DomainName.PARKING_MOTORCYCLE

    @staticmethod
    def _eligible(track: TrackedDetection, label: str) -> bool:
        return (track.confirmed and track.class_name.strip().lower() == label
                and ParkingCandidateTrigger._finite(track.confidence) and track.confidence >= 0.50
                and ParkingCandidateTrigger._valid_box(track.xyxy)
                and len(track.center) == 2 and all(ParkingCandidateTrigger._finite(v) for v in track.center))

    @staticmethod
    def _valid_snapshot(snapshot: TrackingSnapshot) -> bool:
        return (ParkingCandidateTrigger._finite(snapshot.frame_width) and snapshot.frame_width > 0
                and ParkingCandidateTrigger._finite(snapshot.frame_height) and snapshot.frame_height > 0
                and ParkingCandidateTrigger._valid_time(snapshot.source_timestamp))

    @staticmethod
    def _valid_time(value: object) -> bool:
        return ParkingCandidateTrigger._finite(value)

    @staticmethod
    def _finite(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    @staticmethod
    def _valid_box(box: object) -> bool:
        return (isinstance(box, tuple) and len(box) == 4
                and all(ParkingCandidateTrigger._finite(v) for v in box)
                and box[2] > box[0] and box[3] > box[1])

    @staticmethod
    def _edge_distance(first: tuple[float, ...], second: tuple[float, ...]) -> float:
        dx = max(first[0] - second[2], second[0] - first[2], 0.0)
        dy = max(first[1] - second[3], second[1] - first[3], 0.0)
        return math.hypot(dx, dy)

    @staticmethod
    def _point_distance(first: tuple[float, float], second: tuple[float, float]) -> float:
        return math.hypot(first[0] - second[0], first[1] - second[1])
