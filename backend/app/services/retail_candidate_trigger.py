"""Session-scoped retail display-zone episodes; metadata only, no dispatch.

Core only: nothing here is wired into runtime, the detector or the worker yet.
The public surface mirrors what ``EvaluationWorker`` uses on the parking trigger
(observe, wait_for_candidate, wake_waiters, claim_next_candidate, complete, fail,
get_snapshot with source_session_id/pending_event_id, session lifecycle, close).

Signals: only confirmed ``person`` tracks and owner-configured display zones
(ROI). There is no merchandise, hand, pose, concealment or payment signal, so a
candidate means "a person stayed in a display zone", never "an item was taken".

Two monotonic timelines; no rule ever compares a value from one with the other:

* Observation timeline (``TrackingSnapshot.source_timestamp``): dwell, gaps,
  continuity, relinking, identity retention, ``started_at`` and
  ``triggered_at``. Dwell is the sum of *observed* intervals of at most
  ``max_sample_gap_seconds`` between two in-zone observations; it is not
  continuous presence. A larger gap adds nothing (not even a clamped part). An
  episode unseen for more than ``lost_tolerance_seconds`` ends; the same track
  ID returning later starts a new episode from zero dwell.
* Control timeline (the injected ``clock``): candidate TTL deadline
  (``clock() + candidate_ttl_seconds`` at promotion), zone cooldowns and the
  global claim interval. A candidate is claimable while ``clock() < deadline``
  and expires at ``clock() >= deadline``. Expiry runs on observe, claim,
  complete/fail, wait_for_candidate and get_snapshot, so it progresses without
  new frames; get_snapshot may therefore change state (expire candidates).

The ``now`` arguments of claim/complete/fail are validated (finite, not bool)
for interface parity only; every decision uses the injected clock. An
API_PENDING (claimed) episode is held until complete/fail for its event, or
until the session ends or the trigger closes; no timer releases it.

Relinking: a new track may continue a lost episode only when its ID is not in
the recent-identity history: track IDs seen as eligible persons within the
last ``lost_tolerance_seconds`` of observation time (bounded by
``max_known_tracks``, least recently seen evicted first). Such an ID is a
person known to exist elsewhere, never a continuation. If an eviction leaves
the history incomplete, relinking is suppressed until every evicted ID would
have aged out (one tolerance window), so a known person is never mistaken for
a fresh ID. Track ID order is not assumed.

Zone reservation is atomic per snapshot: promotions are processed in a
deterministic order under one lock and the first one reserves the zone, so two
subjects crossing the threshold together create one zone candidate. An expired
(unclaimed) candidate ends its episode for good (no retry) and puts the zone in
cooldown from the TTL deadline. Thresholds are provisional, not calibrated.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable
import dataclasses
import math
import threading
import time
import uuid

from app.models.domain import DomainName, DomainSnapshot, DomainState
from app.models.retail_candidate import (
    RetailCandidateDiagnostic, RetailCandidateEvent, RetailCandidateEvidence,
    RetailCandidateState as State, RetailEpisodeView, RetailZone, RetailZoneView,
    validate_retail_zones,
)
from app.models.tracking import TrackedDetection, TrackingSnapshot

_EDGE_MARGIN = 0.002            # normalized distance that counts as touching the frame edge
_RELINK_REFERENCE_SECONDS = 1.0
_PROTECTED = (State.CANDIDATE, State.API_PENDING)


def _is_number(value: object) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _number(name: str, value: object, low: float, high: float, *,
            low_open: bool = False) -> float:
    if not _is_number(value):
        raise ValueError(f"{name} must be a finite number")
    if (value <= low if low_open else value < low) or value > high:
        raise ValueError(f"{name} is out of range")
    return float(value)


def _integer(name: str, value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


@dataclasses.dataclass
class _Episode:
    episode_id: str
    zone_index: int
    track_id: int
    initial_track_id: int
    started_at: float
    last_observed_at: float
    sequences: deque[int]
    state: State = State.ENGAGING
    lost: bool = False
    inside: bool = False
    dwell: float = 0.0
    consecutive_enter: int = 0
    engage_start: float = 0.0
    relinks: int = 0
    observations: int = 0
    max_gap: float = 0.0
    overlap_sum: float = 0.0
    overlap_max: float = 0.0
    height_min: float = math.inf
    height_max: float = 0.0
    width_min: float = math.inf
    width_max: float = 0.0
    path: float = 0.0
    last_center: tuple[float, float] = (0.0, 0.0)
    last_area: float = 0.0
    last_edge: bool = False
    edge_observations: int = 0
    conflict_counted: bool = False
    ttl_deadline: float | None = None
    event: RetailCandidateEvent | None = None


@dataclasses.dataclass
class _ZoneState:
    reserved_episode_id: str | None = None
    cooldown_until: float | None = None


class RetailCandidateTrigger:
    """Select retail review candidates from bounded metadata, never image data."""

    def __init__(
        self, zones: object, *, clock: Callable[[], float] = time.monotonic,
        enter_overlap: float = 0.20, exit_overlap: float = 0.10,
        engage_observations: int = 2, engage_seconds: float = 0.5,
        trigger_dwell_seconds: float = 3.0, max_sample_gap_seconds: float = 0.5,
        lost_tolerance_seconds: float = 1.5, relink_distance: float = 0.08,
        relink_area_ratio: float = 2.0, max_relinks: int = 3,
        candidate_ttl_seconds: float = 1.0, zone_cooldown_seconds: float = 60.0,
        global_request_interval_seconds: float = 10.0, max_episodes: int = 32,
        sequence_limit: int = 32, min_person_confidence: float = 0.50,
        max_known_tracks: int = 64,
    ) -> None:
        # Provisional defaults (uncalibrated); every value is validated here.
        self._zones: tuple[RetailZone, ...] = validate_retail_zones(zones)
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._enter = _number("enter_overlap", enter_overlap, 0.0, 1.0, low_open=True)
        self._exit = _number("exit_overlap", exit_overlap, 0.0, 1.0, low_open=True)
        if not self._exit < self._enter:
            raise ValueError("exit_overlap must be lower than enter_overlap")
        self._engage_observations = _integer("engage_observations", engage_observations, 1, 100)
        self._engage_seconds = _number("engage_seconds", engage_seconds, 0.0, 60.0)
        self._dwell = _number("trigger_dwell_seconds", trigger_dwell_seconds, 0.0, 600.0, low_open=True)
        if self._engage_seconds > self._dwell:
            raise ValueError("engage_seconds must not exceed trigger_dwell_seconds")
        self._sample_gap = _number("max_sample_gap_seconds", max_sample_gap_seconds, 0.0, 10.0, low_open=True)
        self._tolerance = _number("lost_tolerance_seconds", lost_tolerance_seconds, 0.0, 10.0, low_open=True)
        if self._sample_gap > self._tolerance:
            raise ValueError("max_sample_gap_seconds must not exceed lost_tolerance_seconds")
        self._relink_distance = _number("relink_distance", relink_distance, 0.0, 1.0, low_open=True)
        self._area_ratio = _number("relink_area_ratio", relink_area_ratio, 1.0, 10.0)
        self._max_relinks = _integer("max_relinks", max_relinks, 0, 20)
        self._ttl = _number("candidate_ttl_seconds", candidate_ttl_seconds, 0.0, 10.0, low_open=True)
        self._zone_cooldown = _number("zone_cooldown_seconds", zone_cooldown_seconds, 0.0, 3600.0)
        self._global_interval = _number(
            "global_request_interval_seconds", global_request_interval_seconds, 0.0, 3600.0)
        self._max_episodes = _integer("max_episodes", max_episodes, 1, 256)
        self._sequence_limit = _integer("sequence_limit", sequence_limit, 1, 256)
        self._min_confidence = _number("min_person_confidence", min_person_confidence, 0.0, 1.0)
        self._max_known_tracks = _integer("max_known_tracks", max_known_tracks, 1, 1024)
        self._clock = clock
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._wake_generation = 0
        self._closed = False
        self._session_id: str | None = None
        with self._lock:
            self._reset_locked(None)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _reset_locked(self, session_id: str | None) -> None:
        self._session_id = session_id
        self._episodes: dict[str, _Episode] = {}
        self._by_track: dict[int, _Episode] = {}
        self._zone_states = [_ZoneState() for _ in self._zones]
        self._pending_event_id: str | None = None
        self._last_claim_at: float | None = None
        self._last_sequence: int | None = None
        self._last_timestamp: float | None = None
        # Recent-identity history: track_id -> last observation time (bounded).
        self._known_tracks: OrderedDict[int, float] = OrderedDict()
        self._relink_suppressed_until: float | None = None
        self._counts = dict.fromkeys(
            ("candidates_created", "candidates_expired", "relinks", "ambiguous_relinks",
             "gap_resets", "dropped_capacity", "zone_reservation_conflicts",
             "identity_evictions", "relinks_suppressed"), 0)
        self._wake_generation += 1
        self._condition.notify_all()

    def start_session(self, session_id: str) -> None:
        with self._lock:
            if not self._closed:
                self._reset_locked(session_id)

    def end_session(self, session_id: str) -> None:
        with self._lock:
            if not self._closed and session_id == self._session_id:
                self._reset_locked(None)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._reset_locked(None)

    # ── observation timeline ─────────────────────────────────────────────────

    def observe(self, domain_snapshot: DomainSnapshot, tracking_snapshot: TrackingSnapshot) -> None:
        with self._lock:
            if (self._closed or self._session_id is None
                    or not self._valid_snapshot(tracking_snapshot)
                    or tracking_snapshot.source_session_id != self._session_id
                    or not self._domain_is_active(domain_snapshot, self._session_id)):
                return
            seq, at = tracking_snapshot.source_frame_seq, tracking_snapshot.source_timestamp
            if ((self._last_sequence is not None and seq <= self._last_sequence)
                    or (self._last_timestamp is not None and at < self._last_timestamp)):
                return
            self._last_sequence, self._last_timestamp = seq, at
            width, height = tracking_snapshot.frame_width, tracking_snapshot.frame_height
            now = self._clock()                          # control timeline, read once
            self._expire_locked(now)
            # Episodes unseen for longer than the tolerance end (the same ID
            # returning later starts over). Reserved/claimed ones are kept
            # until TTL or complete/fail resolves them.
            for episode in tuple(self._episodes.values()):
                if episode.state not in _PROTECTED and at - episode.last_observed_at > self._tolerance:
                    self._discard_locked(episode)
                    self._counts["gap_resets"] += 1
            persons: dict[int, TrackedDetection] = {}
            for track in sorted(tracking_snapshot.tracks, key=lambda item: item.track_id):
                if self._eligible(track) and track.track_id not in persons:
                    persons[track.track_id] = track
            known_before = self._recent_identities_locked(at)
            unbound: list[TrackedDetection] = []
            for track_id, track in persons.items():
                episode = self._by_track.get(track_id)
                if episode is None:
                    unbound.append(track)
                elif not self._record_locked(episode, track, at, seq, width, height):
                    self._discard_locked(episode)        # left its zone
                    unbound.append(track)
            for episode in self._episodes.values():
                episode.lost = episode.track_id not in persons
            unbound = self._relink_locked(unbound, known_before, at, seq, width, height)
            for track in unbound:
                self._start_episode_locked(track, at, seq, width, height)
            self._remember_identities_locked(persons, at)
            if self._promote_locked(at, seq, now):
                self._condition.notify_all()

    def _record_locked(self, episode: _Episode, track: TrackedDetection, at: float, seq: int,
                       width: int, height: int) -> bool:
        """Apply one observation; False when the track left its episode zone."""
        overlap = self._overlap(track.xyxy, self._zones[episode.zone_index], width, height)
        gap = at - episode.last_observed_at
        box = track.xyxy
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        area = (box[2] - box[0]) * (box[3] - box[1])
        edge = self._touches_edge(box, width, height)
        if overlap < self._exit:
            if episode.state not in _PROTECTED:
                return False
            episode.inside = False                      # reservation kept until TTL/claim
        else:
            if gap <= self._sample_gap and episode.inside:
                episode.dwell += gap
                episode.path += math.dist(center, episode.last_center) / math.hypot(width, height)
            elif gap > self._sample_gap:
                episode.consecutive_enter = 0           # presence must be re-established
            if overlap >= self._enter:
                if episode.consecutive_enter == 0:
                    episode.engage_start = at
                episode.consecutive_enter += 1
            else:
                episode.consecutive_enter = 0           # hysteresis band: kept, not engaging
            if (episode.state is State.ENGAGING
                    and episode.consecutive_enter >= self._engage_observations
                    and at - episode.engage_start >= self._engage_seconds):
                episode.state = State.ENGAGED
            episode.inside = True
            episode.observations += 1
            episode.overlap_sum += overlap
            episode.overlap_max = max(episode.overlap_max, overlap)
            episode.height_min = min(episode.height_min, (box[3] - box[1]) / height)
            episode.height_max = max(episode.height_max, (box[3] - box[1]) / height)
            episode.width_min = min(episode.width_min, (box[2] - box[0]) / width)
            episode.width_max = max(episode.width_max, (box[2] - box[0]) / width)
            episode.edge_observations += edge
            episode.sequences.append(seq)
        episode.max_gap = max(episode.max_gap, gap)
        episode.last_observed_at = at
        episode.last_center, episode.last_area, episode.last_edge = center, area, edge
        episode.lost = False
        return True

    def _recent_identities_locked(self, at: float) -> frozenset[int]:
        """Track IDs seen as eligible persons within the tolerance window before ``at``."""
        while self._known_tracks:
            track_id, seen_at = next(iter(self._known_tracks.items()))
            if at - seen_at <= self._tolerance:
                break
            del self._known_tracks[track_id]               # aged out (least recent first)
        return frozenset(self._known_tracks)

    def _remember_identities_locked(self, persons: dict[int, TrackedDetection], at: float) -> None:
        for track_id in persons:
            self._known_tracks[track_id] = at
            self._known_tracks.move_to_end(track_id)
        while len(self._known_tracks) > self._max_known_tracks:
            self._known_tracks.popitem(last=False)
            self._counts["identity_evictions"] += 1
            # An evicted ID could still be alive; until it would have aged out,
            # the history is incomplete and relinking is not safe.
            self._relink_suppressed_until = at + self._tolerance

    def _relink_locked(self, unbound: list[TrackedDetection], known_before: frozenset[int],
                       at: float, seq: int, width: int, height: int) -> list[TrackedDetection]:
        """Link a track absent from recent history to one lost episode, only when unambiguous."""
        diagonal = math.hypot(width, height)
        fresh = [track for track in unbound if track.track_id not in known_before]
        if fresh and self._relink_suppressed_until is not None and at <= self._relink_suppressed_until:
            self._counts["relinks_suppressed"] += 1
            return unbound
        lost = [episode for episode in self._episodes.values()
                if episode.lost and at - episode.last_observed_at <= self._tolerance
                and episode.relinks < self._max_relinks]
        matches: dict[int, list[_Episode]] = {}
        claims: dict[str, int] = {}
        for track in fresh:
            box = track.xyxy
            center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            area = (box[2] - box[0]) * (box[3] - box[1])
            for episode in lost:
                if self._overlap(box, self._zones[episode.zone_index], width, height) < self._enter:
                    continue
                gap = at - episode.last_observed_at
                allowed = self._relink_distance * max(1.0, gap / _RELINK_REFERENCE_SECONDS)
                if math.dist(center, episode.last_center) / diagonal > allowed:
                    continue
                truncated = episode.last_edge or self._touches_edge(box, width, height)
                if not truncated and max(area, episode.last_area) > self._area_ratio * min(area, episode.last_area):
                    continue
                matches.setdefault(track.track_id, []).append(episode)
                claims[episode.episode_id] = claims.get(episode.episode_id, 0) + 1
        linked: set[int] = set()
        for track in fresh:
            candidates = matches.get(track.track_id, [])
            if not candidates:
                continue
            if len(candidates) != 1 or claims[candidates[0].episode_id] != 1:
                self._counts["ambiguous_relinks"] += 1
                continue
            episode = candidates[0]
            del self._by_track[episode.track_id]
            episode.track_id = track.track_id
            episode.relinks += 1
            self._by_track[track.track_id] = episode
            self._counts["relinks"] += 1
            self._record_locked(episode, track, at, seq, width, height)
            linked.add(track.track_id)
        return [track for track in unbound if track.track_id not in linked]

    def _start_episode_locked(self, track: TrackedDetection, at: float, seq: int,
                              width: int, height: int) -> None:
        best: tuple[float, int] | None = None
        for index, zone in enumerate(self._zones):      # max overlap, then config order
            overlap = self._overlap(track.xyxy, zone, width, height)
            if overlap >= self._enter and (best is None or overlap > best[0]):
                best = (overlap, index)
        if best is None:
            return
        if len(self._episodes) >= self._max_episodes:
            self._counts["dropped_capacity"] += 1
            return
        episode = _Episode(str(uuid.uuid4()), best[1], track.track_id, track.track_id, at, at,
                           deque(maxlen=self._sequence_limit))
        self._episodes[episode.episode_id] = episode
        self._by_track[track.track_id] = episode
        self._record_locked(episode, track, at, seq, width, height)

    def _promote_locked(self, at: float, seq: int, now: float) -> bool:
        """Atomic per snapshot: the first ready episode reserves its zone."""
        ready = sorted(
            (episode for episode in self._episodes.values()
             if episode.state is State.ENGAGED and not episode.lost and episode.inside
             and episode.last_observed_at == at and episode.dwell >= self._dwell),
            key=lambda episode: (-episode.dwell, episode.zone_index, episode.track_id),
        )
        created = False
        for episode in ready:
            zone_state = self._zone_states[episode.zone_index]
            if (zone_state.reserved_episode_id is not None
                    or (zone_state.cooldown_until is not None and now < zone_state.cooldown_until)):
                if not episode.conflict_counted:
                    episode.conflict_counted = True
                    self._counts["zone_reservation_conflicts"] += 1
                continue
            zone_state.reserved_episode_id = episode.episode_id
            concurrent = sum(
                1 for other in self._episodes.values()
                if other.zone_index == episode.zone_index and not other.lost and other.inside
                and other.last_observed_at == at
            )
            episode.state = State.CANDIDATE
            episode.ttl_deadline = now + self._ttl             # control timeline
            episode.event = RetailCandidateEvent(
                str(uuid.uuid4()), self._session_id, DomainName.RETAIL_STORE, episode.track_id,
                episode.started_at, at, seq, self._evidence(episode, concurrent),
                tuple(episode.sequences), State.CANDIDATE,
            )
            self._counts["candidates_created"] += 1
            created = True
        return created

    def _evidence(self, episode: _Episode, concurrent: int) -> RetailCandidateEvidence:
        return RetailCandidateEvidence(
            episode.episode_id, self._zones[episode.zone_index].name, episode.zone_index,
            episode.initial_track_id, episode.track_id, episode.relinks, episode.dwell,
            episode.observations, episode.max_gap, episode.overlap_sum / episode.observations,
            episode.overlap_max, episode.height_max - episode.height_min,
            episode.width_max - episode.width_min, episode.path, episode.edge_observations, concurrent,
        )

    def _discard_locked(self, episode: _Episode) -> None:
        self._episodes.pop(episode.episode_id, None)
        if self._by_track.get(episode.track_id) is episode:
            del self._by_track[episode.track_id]

    # ── control timeline ─────────────────────────────────────────────────────

    def _expire_locked(self, now: float) -> None:
        for episode in self._episodes.values():
            if episode.state is State.CANDIDATE and now >= episode.ttl_deadline:
                episode.state = State.DONE                # no retry for this episode
                zone_state = self._zone_states[episode.zone_index]
                zone_state.reserved_episode_id = None
                zone_state.cooldown_until = episode.ttl_deadline + self._zone_cooldown
                self._counts["candidates_expired"] += 1

    def wait_for_candidate(self, timeout_seconds: float) -> bool:
        """Wait without claiming; session changes and close interrupt the wait."""
        if not _is_number(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            generation = self._wake_generation
            while True:
                if self._closed or self._session_id is None or generation != self._wake_generation:
                    return False
                self._expire_locked(self._clock())
                if self._pending_event_id is None and any(
                        episode.state is State.CANDIDATE for episode in self._episodes.values()):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)

    def wake_waiters(self) -> None:
        with self._condition:
            self._wake_generation += 1
            self._condition.notify_all()

    def claim_next_candidate(self, now: float) -> RetailCandidateEvent | None:
        self._require_time(now)
        with self._lock:
            current = self._clock()
            self._expire_locked(current)
            if (self._closed or self._session_id is None or self._pending_event_id is not None
                    or (self._last_claim_at is not None
                        and current - self._last_claim_at < self._global_interval)):
                return None
            candidates = [episode for episode in self._episodes.values()
                          if episode.state is State.CANDIDATE and episode.event is not None]
            if not candidates:
                return None
            episode = min(candidates, key=lambda item: (
                -item.dwell, item.event.triggered_at, item.zone_index, item.track_id))
            episode.state = State.API_PENDING
            episode.event = dataclasses.replace(episode.event, state=State.API_PENDING)
            self._pending_event_id = episode.event.event_id
            self._last_claim_at = current
            return episode.event

    def complete(self, event_id: str, now: float) -> bool:
        return self._finish(event_id, now)

    def fail(self, event_id: str, now: float) -> bool:
        return self._finish(event_id, now)

    def _finish(self, event_id: str, now: float) -> bool:
        self._require_time(now)
        with self._lock:
            if self._pending_event_id is None or event_id != self._pending_event_id:
                return False
            for episode in self._episodes.values():
                if (episode.state is State.API_PENDING and episode.event is not None
                        and episode.event.event_id == event_id):
                    episode.state = State.DONE
                    zone_state = self._zone_states[episode.zone_index]
                    zone_state.reserved_episode_id = None
                    zone_state.cooldown_until = self._clock() + self._zone_cooldown
                    self._pending_event_id = None
                    return True
            return False

    def get_snapshot(self) -> RetailCandidateDiagnostic:
        with self._lock:
            self._expire_locked(self._clock())
            episodes = tuple(
                RetailEpisodeView(e.episode_id, self._zones[e.zone_index].name, e.track_id,
                                  e.state, e.dwell, e.lost, e.relinks)
                for e in sorted(self._episodes.values(), key=lambda e: (e.zone_index, e.track_id))
            )
            zones = tuple(
                RetailZoneView(zone.name, state.reserved_episode_id, state.cooldown_until)
                for zone, state in zip(self._zones, self._zone_states)
            )
            return RetailCandidateDiagnostic(
                self._session_id, self._pending_event_id, episodes, zones, **self._counts)

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _require_time(now: object) -> None:
        if not _is_number(now):
            raise ValueError("now must be finite")

    def _eligible(self, track: TrackedDetection) -> bool:
        box = track.xyxy
        return (type(track.track_id) is int and track.confirmed
                and isinstance(track.class_name, str) and track.class_name.strip().lower() == "person"
                and _is_number(track.confidence) and track.confidence >= self._min_confidence
                and len(box) == 4 and all(_is_number(value) for value in box)
                and box[2] > box[0] and box[3] > box[1])

    @staticmethod
    def _valid_snapshot(snapshot: TrackingSnapshot) -> bool:
        return (isinstance(snapshot, TrackingSnapshot) and type(snapshot.source_frame_seq) is int
                and _is_number(snapshot.source_timestamp)
                and type(snapshot.frame_width) is int and snapshot.frame_width > 0
                and type(snapshot.frame_height) is int and snapshot.frame_height > 0)

    @staticmethod
    def _domain_is_active(snapshot: DomainSnapshot, session_id: str) -> bool:
        return (isinstance(snapshot, DomainSnapshot) and snapshot.source_session_id == session_id
                and snapshot.state is DomainState.LOCKED and snapshot.domain is DomainName.RETAIL_STORE)

    @staticmethod
    def _overlap(box: tuple[float, float, float, float], zone: RetailZone,
                 width: int, height: int) -> float:
        """Fraction of the visible person box that lies inside the zone."""
        left, top = max(box[0], zone.x1 * width), max(box[1], zone.y1 * height)
        right, bottom = min(box[2], zone.x2 * width), min(box[3], zone.y2 * height)
        intersection = max(0.0, right - left) * max(0.0, bottom - top)
        return intersection / ((box[2] - box[0]) * (box[3] - box[1]))

    @staticmethod
    def _touches_edge(box: tuple[float, float, float, float], width: int, height: int) -> bool:
        return (box[0] <= _EDGE_MARGIN * width or box[1] <= _EDGE_MARGIN * height
                or box[2] >= (1 - _EDGE_MARGIN) * width or box[3] >= (1 - _EDGE_MARGIN) * height)
