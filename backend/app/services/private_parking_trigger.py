"""Private-parking access trigger core (a mode of PARKING_MOTORCYCLE); metadata only.

Not wired into runtime yet (T1). The public surface matches what
``EvaluationWorker`` calls on a trigger (wait_for_candidate, wake_waiters,
claim_next_candidate, complete, fail, get_snapshot with source_session_id and
pending_event_id) plus observe/session lifecycle and ``pop_alerts`` for the
future local warning path.

Flow per anonymous person track:
  first seen inside            -> origin UNKNOWN_INSIDE: no episode, no event.
                                  A coverage limitation, NOT "considered safe".
  outside -> inside through the configured segment, within the crossing gap
                               -> episode ENTERED (SIAGA)
  valid gesture signal         -> AUTHORIZED (this episode only; never inherited)
  ENTERED + confirmed approach to a parked motorcycle in a motor zone
                               -> TRIGGERED: one local alert + one candidate,
                                  same event_id; never re-triggers
  inside -> outside / track lost past tolerance / max age / Stop / new session
                               -> episode ends (an aged-out track stays
                                  UNKNOWN_INSIDE until it leaves)

Track identity is never inferred spatially: a new track ID is a new track, so a
lost-and-reappeared person inside the area becomes UNKNOWN_INSIDE (no event).

Timelines: observation timestamps drive sides, crossing, gaps, pairing and
approach confirmation; the injected control clock drives episode max age,
candidate TTL and alert TTL, so they expire even when observations stop.

Overflow policy (counted, never silent): a full alert queue drops the NEW alert
(``alerts_dropped_queue_full``) while the event and its candidate still issue
once; a full candidate store drops the NEW candidate (``candidates_dropped_full``,
outcome ``REJECTED_FULL``) while the alert still issues. Neither is retried. All
thresholds are provisional and uncalibrated.

Candidate preparation (T3a1): an issued candidate is PREPARING and never
claimable. It carries a ``CandidateToken`` (session generation + a serial that is
never reused) that the alert also carries. Only ``publish_candidate(token)``,
called after the evidence seal succeeded, makes it claimable, and only within
its deadline (control clock, read inside the lock); ``abandon_candidate(token)``
closes it after an evidence failure. A closed preparing candidate leaves a
bounded tombstone, so a late publish reports EXPIRED/ABANDONED/REJECTED_FULL
while the tombstone exists and UNKNOWN after eviction; an expired candidate is
never published. Nothing is retried.

Evidence markers: every episode/candidate transition queues an ``EpisodeMarker``
(sequence per session generation). ``pop_markers`` drains them atomically. If the
bounded queue overflows, it is dropped and the next ``pop_markers`` returns an
authoritative ``EvidenceView`` instead (episodes that still need working samples,
including triggered episodes whose candidate still waits for its seal).

Expiry runs only inside method calls (observe, pop_*, publish/abandon, wait,
claim, snapshot); there is no timer thread. A candidate past its deadline is
never published or claimed, whenever the next call happens.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
import dataclasses
import math
import threading
import time
import uuid

from app.models.domain import DomainName, DomainSnapshot, DomainState
from app.models.private_parking import (
    AuthorizationSignal, CandidateOutcome, CandidateToken, EpisodeMarker, EpisodeOrigin, EpisodeState,
    EpisodeView, EvidenceView, LocalProximityAlert, MarkerBatch, MarkerKind, PrivateParkingCandidate,
    PrivateParkingConfig, PrivateParkingDiagnostic, ProximityEvidence,
)
from app.models.tracking import TrackedDetection, TrackingSnapshot
from app.services.private_parking_authorization import AuthorizationSignalSource

_COUNTERS = (
    "snapshots_ignored", "tracks_capacity_dropped", "unknown_inside_tracks",
    "crossings_rejected_gap", "crossings_rejected_outside_segment", "episodes_started",
    "episodes_capacity_dropped", "episodes_exited", "episodes_lost", "episodes_aged_out",
    "escort_paired", "escort_ambiguous", "authorizations_applied", "authorizations_rejected",
    "authorizations_after_trigger", "authorization_source_errors", "events_issued",
    "alerts_dropped_queue_full", "alerts_expired", "alerts_popped", "candidates_dropped_full",
    "candidates_expired", "candidates_claimed", "candidates_finished", "candidates_published",
    "candidates_prepare_expired", "candidates_abandoned", "publish_rejected", "abandon_rejected",
    "outcomes_evicted", "markers_emitted", "markers_popped", "marker_overflows",
    "markers_dropped_overflow", "resyncs_issued", "finish_rejected", "episode_id_collisions",
)
_ID_ATTEMPTS = 4      # fresh ids tried when a minted episode id is already live
# Tombstones of closed candidates kept per session: a multiple of max_candidates.
_OUTCOME_FACTOR = 4


def _is_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


@dataclasses.dataclass
class _Track:
    track_id: int
    kind: str                                    # "person" | "motorcycle"
    last_ts: float
    side: str | None = None                      # "inside" | "outside" | None (band/unknown)
    last_outside: tuple[float, float, float] | None = None   # x, y, ts
    origin: EpisodeOrigin | None = None
    episode_id: str | None = None
    crossed_in_at: float | None = None
    foot: tuple[float, float] = (0.0, 0.0)
    center: tuple[float, float] = (0.0, 0.0)
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    centers: deque = dataclasses.field(default_factory=deque)


@dataclasses.dataclass
class _Episode:
    episode_id: str
    person_track_id: int
    crossed_in_at: float
    started_control: float
    state: EpisodeState = EpisodeState.ENTERED
    escorted_motor: int | None = None
    pairing_open: bool = True
    pairing_counts: dict = dataclasses.field(default_factory=dict)
    approach_count: int = 0
    approach_target: int | None = None       # motor the current confirmation run is bound to
    min_edge: float = math.inf
    revision: int = 1                        # bumped on every state change (markers, views)
    instance: int = 0                        # unique per episode instance in the process (ids may repeat)


@dataclasses.dataclass
class _Candidate:
    candidate: PrivateParkingCandidate
    deadline: float                          # control clock
    token: CandidateToken
    episode_id: str
    revision: int
    crossed_in_at: float


class PrivateParkingTrigger:
    def __init__(self, config: PrivateParkingConfig, *, clock: Callable[[], float] = time.monotonic,
                 authorization: AuthorizationSignalSource | None = None) -> None:
        if not isinstance(config, PrivateParkingConfig):
            raise ValueError("a validated PrivateParkingConfig is required")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._config = config
        self._t = config.thresholds
        self._clock = clock
        self._authorization = authorization
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._wake_generation = 0
        self._session_generation = 0
        self._serial = 0                       # token serials: never reset, never reused
        self._closed = False
        with self._lock:
            self._reset_locked(None)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _reset_locked(self, session_id: str | None) -> None:
        self._session_generation += 1          # invalidates any in-flight adapter call
        self._session_id = session_id
        self._tracks: OrderedDict[int, _Track] = OrderedDict()
        self._episodes: dict[str, _Episode] = {}
        self._alerts: deque[LocalProximityAlert] = deque()
        self._preparing: OrderedDict[CandidateToken, _Candidate] = OrderedDict()
        self._candidates: deque[_Candidate] = deque()          # published: claimable or claimed
        self._outcomes: OrderedDict[CandidateToken, CandidateOutcome] = OrderedDict()
        self._markers: deque[EpisodeMarker] = deque()
        self._marker_seq = 0
        self._resync_required = False
        self._pending_event_id: str | None = None
        self._pending_token: CandidateToken | None = None      # identity of the claimed candidate
        self._last_seq: int | None = None
        self._last_ts: float | None = None
        self._counts = dict.fromkeys(_COUNTERS, 0)
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
            if not self._closed:
                self._closed = True
                self._reset_locked(None)

    # ── observation ──────────────────────────────────────────────────────────

    def observe(self, domain_snapshot: DomainSnapshot, tracking_snapshot: TrackingSnapshot) -> None:
        with self._lock:
            session, generation = self._session_id, self._session_generation
            if self._closed or session is None:
                return
        signals: Sequence[AuthorizationSignal] = ()
        source_failed = False
        if self._authorization is not None:
            try:                                      # outside the lock: may block
                signals = tuple(self._authorization.signals(session))
            except Exception:
                signals, source_failed = (), True
        with self._lock:
            if self._closed or self._session_generation != generation:
                return                                # session changed during the call: touch nothing
            if source_failed:
                self._counts["authorization_source_errors"] += 1
            if (not self._valid_snapshot(tracking_snapshot)
                    or tracking_snapshot.source_session_id != session
                    or not self._domain_is_active(domain_snapshot, session)):
                self._counts["snapshots_ignored"] += 1
                self._counts["authorizations_rejected"] += len(signals)
                return
            seq, ts = tracking_snapshot.source_frame_seq, tracking_snapshot.source_timestamp
            if ((self._last_seq is not None and seq <= self._last_seq)
                    or (self._last_ts is not None and ts < self._last_ts)):
                self._counts["snapshots_ignored"] += 1
                self._counts["authorizations_rejected"] += len(signals)
                return
            self._last_seq, self._last_ts = seq, ts
            now = self._clock()
            self._expire_locked(now)
            self._purge_lost_locked(ts)
            width, height = tracking_snapshot.frame_width, tracking_snapshot.frame_height
            seen: set[int] = set()
            for track in sorted(tracking_snapshot.tracks, key=lambda item: item.track_id):
                kind = self._kind(track)
                if kind is None or track.track_id in seen:
                    continue
                seen.add(track.track_id)
                self._update_track_locked(track, kind, ts, width, height, now)
            self._pair_locked(ts)
            for signal in signals:                    # re-validated here, under the lock
                self._apply_signal_locked(signal, ts)
            if self._approach_locked(ts, now):
                self._condition.notify_all()

    def _update_track_locked(self, track: TrackedDetection, kind: str, ts: float, width: int,
                             height: int, now: float) -> None:
        x1, y1, x2, y2 = (track.xyxy[0] / width, track.xyxy[1] / height,
                          track.xyxy[2] / width, track.xyxy[3] / height)
        state = self._tracks.get(track.track_id)
        if state is None:
            if len(self._tracks) >= self._t.max_tracks:
                self._counts["tracks_capacity_dropped"] += 1
                return
            state = _Track(track.track_id, kind, ts, centers=deque(maxlen=self._t.parked_history))
            self._tracks[track.track_id] = state
        elif state.kind != kind:
            return                                    # a track never changes class
        state.last_ts = ts
        state.box = (x1, y1, x2, y2)
        state.foot = ((x1 + x2) / 2, y2)
        state.center = ((x1 + x2) / 2, (y1 + y2) / 2)
        state.centers.append((ts, state.center))
        line, hysteresis = self._config.line, self._t.line_hysteresis
        distance = line.signed_distance(*state.foot)
        if distance <= -hysteresis:                   # outside
            if state.side == "inside" and state.episode_id is not None:
                self._end_episode_locked(state.episode_id, "episodes_exited")
            if state.origin is EpisodeOrigin.UNKNOWN_INSIDE:
                state.origin = None                   # now observed outside: may cross in later
            state.side = "outside"
            state.last_outside = (state.foot[0], state.foot[1], ts)
        elif distance >= hysteresis:                  # inside
            if state.side == "outside" and state.last_outside is not None:
                self._crossing_locked(state, ts, now)
            elif state.side is None and state.origin is None:
                state.origin = EpisodeOrigin.UNKNOWN_INSIDE
                if kind == "person":
                    self._counts["unknown_inside_tracks"] += 1
            state.side = "inside"
            state.last_outside = None
        # inside the hysteresis band: keep the previous side

    def _crossing_locked(self, state: _Track, ts: float, now: float) -> None:
        ax, ay, ats = state.last_outside
        line = self._config.line
        if ts - ats > self._t.max_crossing_gap_seconds:
            self._counts["crossings_rejected_gap"] += 1
            state.origin = EpisodeOrigin.UNKNOWN_INSIDE
            return
        da, db = line.signed_distance(ax, ay), line.signed_distance(*state.foot)
        u = da / (da - db)
        qx, qy = ax + u * (state.foot[0] - ax), ay + u * (state.foot[1] - ay)
        position = line.segment_parameter(qx, qy)
        margin = self._t.segment_margin
        if not -margin <= position <= 1 + margin:
            self._counts["crossings_rejected_outside_segment"] += 1
            state.origin = EpisodeOrigin.UNKNOWN_INSIDE
            return
        state.origin = EpisodeOrigin.CROSSED_IN
        if state.kind == "motorcycle":
            state.crossed_in_at = ts
            return
        if len(self._episodes) >= self._t.max_episodes:
            self._counts["episodes_capacity_dropped"] += 1
            state.origin = EpisodeOrigin.UNKNOWN_INSIDE
            return
        episode_id = uuid.uuid4().hex
        for _attempt in range(_ID_ATTEMPTS):          # a live episode never shares its id
            if episode_id not in self._episodes:
                break
            self._counts["episode_id_collisions"] += 1
            episode_id = uuid.uuid4().hex
        else:
            self._counts["episodes_capacity_dropped"] += 1
            state.origin = EpisodeOrigin.UNKNOWN_INSIDE
            return
        self._serial += 1                         # instances share the never-reused serial counter
        episode = _Episode(episode_id, state.track_id, ts, now, instance=self._serial)
        self._episodes[episode.episode_id] = episode
        state.episode_id = episode.episode_id
        self._counts["episodes_started"] += 1
        self._mark_locked(MarkerKind.EPISODE_ENTERED, episode.episode_id, episode.revision, ts,
                          instance=episode.instance)

    def _pair_locked(self, ts: float) -> None:
        """Escort exemption only for one unambiguous motorcycle that crossed in and moved with the person."""
        escorted_elsewhere = {e.escorted_motor for e in self._episodes.values() if e.escorted_motor is not None}
        for episode in self._episodes.values():
            if not episode.pairing_open:
                continue
            person = self._tracks.get(episode.person_track_id)
            if ts - episode.crossed_in_at > self._t.pairing_window_seconds:
                episode.pairing_open = False
                continue
            if person is None or person.last_ts != ts:
                continue
            qualifying = []
            for motor in self._tracks.values():
                if (motor.kind != "motorcycle" or motor.crossed_in_at is None or motor.last_ts != ts
                        or abs(motor.crossed_in_at - episode.crossed_in_at) > self._t.co_cross_window_seconds):
                    continue
                close = math.dist(person.center, motor.center) <= self._t.co_move_distance
                count = episode.pairing_counts.get(motor.track_id, 0) + 1 if close else 0
                episode.pairing_counts[motor.track_id] = count
                if count >= self._t.co_move_observations:
                    qualifying.append(motor.track_id)
            if not qualifying:
                continue
            episode.pairing_open = False
            if len(qualifying) == 1 and qualifying[0] not in escorted_elsewhere:
                episode.escorted_motor = qualifying[0]
                escorted_elsewhere.add(qualifying[0])
                self._counts["escort_paired"] += 1
            else:
                self._counts["escort_ambiguous"] += 1

    def _apply_signal_locked(self, signal: object, ts: float) -> None:
        rejected = True
        if (isinstance(signal, AuthorizationSignal) and signal.session_id == self._session_id
                and type(signal.track_id) is int and _is_number(signal.observed_at)):
            track = self._tracks.get(signal.track_id)
            episode = self._episodes.get(track.episode_id) if track and track.episode_id else None
            if (episode is not None and track.kind == "person"
                    and (signal.episode_id is None or signal.episode_id == episode.episode_id)
                    and episode.crossed_in_at <= signal.observed_at <= ts
                    and ts - signal.observed_at <= self._t.signal_max_age_seconds):
                rejected = False
                if episode.state is EpisodeState.TRIGGERED:
                    self._counts["authorizations_after_trigger"] += 1
                elif episode.state is EpisodeState.ENTERED:
                    episode.state = EpisodeState.AUTHORIZED
                    episode.revision += 1
                    self._counts["authorizations_applied"] += 1
                    self._mark_locked(MarkerKind.EPISODE_AUTHORIZED, episode.episode_id,
                                      episode.revision, episode.crossed_in_at, instance=episode.instance)
        if rejected:
            self._counts["authorizations_rejected"] += 1

    def _approach_locked(self, ts: float, now: float) -> bool:
        issued = False
        for episode in list(self._episodes.values()):
            if episode.state is not EpisodeState.ENTERED:
                continue
            person = self._tracks.get(episode.person_track_id)
            if person is None or person.last_ts != ts:
                continue
            best: tuple[float, int, str] | None = None
            for zone in self._config.zones:
                if not zone.contains(*person.foot):
                    continue
                for motor in self._tracks.values():
                    if (motor.kind != "motorcycle" or motor.last_ts != ts
                            or motor.track_id == episode.escorted_motor
                            or not zone.contains(*motor.foot) or not self._parked(motor)):
                        continue
                    edge = self._edge_distance(person.box, motor.box)
                    if edge <= self._t.approach_distance and (best is None or edge < best[0]):
                        best = (edge, motor.track_id, zone.name)
            if best is None:                          # association lost or not eligible: reset
                episode.approach_count, episode.approach_target, episode.min_edge = 0, None, math.inf
                continue
            if best[1] != episode.approach_target:    # target changed: start a new run for it
                episode.approach_count, episode.approach_target, episode.min_edge = 0, best[1], math.inf
            episode.approach_count += 1
            episode.min_edge = min(episode.min_edge, best[0])
            if episode.approach_count >= self._t.approach_observations:
                self._issue_locked(episode, best[1], best[2], ts, now)
                issued = True
        return issued

    def _issue_locked(self, episode: _Episode, motor_id: int, zone_name: str, ts: float, now: float) -> None:
        """Atomic: state change, alert and PREPARING candidate share one event_id and
        one token; issued exactly once. The candidate is not claimable until published."""
        episode.state = EpisodeState.TRIGGERED
        episode.revision += 1
        self._mark_locked(MarkerKind.EPISODE_TRIGGERED, episode.episode_id, episode.revision,
                          episode.crossed_in_at, instance=episode.instance)
        event_id = uuid.uuid4().hex
        self._serial += 1
        token = CandidateToken(self._session_generation, self._serial, episode.instance)
        self._counts["events_issued"] += 1
        if len(self._alerts) >= self._t.alert_queue_limit:
            self._counts["alerts_dropped_queue_full"] += 1
        else:
            self._alerts.append(LocalProximityAlert(
                event_id, self._session_id, episode.episode_id, episode.person_track_id, motor_id,
                zone_name, ts, now, token))
        if len(self._preparing) + len(self._candidates) >= self._t.max_candidates:
            self._counts["candidates_dropped_full"] += 1
            self._remember_outcome_locked(token, CandidateOutcome.REJECTED_FULL)
            self._mark_locked(MarkerKind.CANDIDATE_CLOSED, episode.episode_id, episode.revision,
                              episode.crossed_in_at, token)
            return
        evidence = ProximityEvidence(episode.episode_id, zone_name, episode.crossed_in_at,
                                     episode.approach_count, episode.min_edge, episode.escorted_motor)
        self._preparing[token] = _Candidate(PrivateParkingCandidate(
            event_id, self._session_id, DomainName.PARKING_MOTORCYCLE, episode.person_track_id,
            motor_id, episode.crossed_in_at, ts, evidence, token),
            now + self._t.candidate_ttl_seconds, token, episode.episode_id, episode.revision,
            episode.crossed_in_at)

    def _end_episode_locked(self, episode_id: str, counter: str) -> None:
        episode = self._episodes.pop(episode_id, None)
        if episode is None:
            return
        self._counts[counter] += 1
        episode.revision += 1
        waiting_seal = any(entry.token.episode_instance == episode.instance for entry in self._preparing.values())
        self._mark_locked(MarkerKind.EPISODE_ENDED, episode_id, episode.revision, episode.crossed_in_at,
                          keep_samples=waiting_seal, instance=episode.instance)
        track = self._tracks.get(episode.person_track_id)
        if track is not None and track.episode_id == episode_id:
            track.episode_id = None
            if counter != "episodes_exited":
                track.origin = EpisodeOrigin.UNKNOWN_INSIDE   # no new episode until it leaves

    def _purge_lost_locked(self, ts: float) -> None:
        for track_id, track in list(self._tracks.items()):
            if ts - track.last_ts > self._t.lost_tolerance_seconds:
                if track.episode_id is not None:
                    self._end_episode_locked(track.episode_id, "episodes_lost")
                del self._tracks[track_id]

    # ── control timeline ─────────────────────────────────────────────────────

    def _expire_locked(self, now: float) -> None:
        for episode in list(self._episodes.values()):
            if now - episode.started_control >= self._t.max_episode_seconds:
                self._end_episode_locked(episode.episode_id, "episodes_aged_out")
        while self._alerts and now - self._alerts[0].issued_at >= self._t.alert_ttl_seconds:
            self._alerts.popleft()
            self._counts["alerts_expired"] += 1
        for token, entry in list(self._preparing.items()):
            if now >= entry.deadline:
                self._close_preparing_locked(token, CandidateOutcome.EXPIRED, "candidates_prepare_expired")
        kept = deque()
        for entry in self._candidates:
            if entry.token != self._pending_token and now >= entry.deadline:
                self._counts["candidates_expired"] += 1
                self._remember_outcome_locked(entry.token, CandidateOutcome.EXPIRED)
                self._mark_locked(MarkerKind.CANDIDATE_EXPIRED, entry.episode_id, entry.revision,
                                  entry.crossed_in_at, entry.token)
            else:
                kept.append(entry)
        self._candidates = kept

    # ── candidate preparation (tokens) ───────────────────────────────────────

    def _remember_outcome_locked(self, token: CandidateToken, outcome: CandidateOutcome) -> None:
        self._outcomes[token] = outcome
        self._outcomes.move_to_end(token)
        while len(self._outcomes) > _OUTCOME_FACTOR * self._t.max_candidates:
            self._outcomes.popitem(last=False)
            self._counts["outcomes_evicted"] += 1

    def _close_preparing_locked(self, token: CandidateToken, outcome: CandidateOutcome, counter: str) -> None:
        entry = self._preparing.pop(token)
        self._counts[counter] += 1
        self._remember_outcome_locked(token, outcome)
        self._mark_locked(MarkerKind.CANDIDATE_CLOSED, entry.episode_id, entry.revision,
                          entry.crossed_in_at, token)

    def _token_current_locked(self, token: object) -> bool:
        return (isinstance(token, CandidateToken) and not self._closed and self._session_id is not None
                and token.session_generation == self._session_generation)

    def publish_candidate(self, token: CandidateToken) -> CandidateOutcome:
        """Make a PREPARING candidate claimable after its evidence was sealed.

        The deadline is checked with the injected control clock inside the lock,
        BEFORE any sweep could close the entry; a closed entry answers from its
        tombstone (EXPIRED / ABANDONED / REJECTED_FULL / PUBLISHED) while it is
        kept, UNKNOWN after eviction. An expired candidate is never published.
        """
        with self._lock:
            now = self._clock()
            if not self._token_current_locked(token):
                self._counts["publish_rejected"] += 1
                return CandidateOutcome.STALE
            entry = self._preparing.get(token)
            if entry is None:
                self._counts["publish_rejected"] += 1
                return self._outcomes.get(token, CandidateOutcome.UNKNOWN)
            if now >= entry.deadline:
                self._close_preparing_locked(token, CandidateOutcome.EXPIRED, "candidates_prepare_expired")
                self._counts["publish_rejected"] += 1
                return CandidateOutcome.EXPIRED
            del self._preparing[token]
            self._candidates.append(entry)
            self._remember_outcome_locked(token, CandidateOutcome.PUBLISHED)
            self._counts["candidates_published"] += 1
            self._expire_locked(now)                  # other entries; this one is still valid
            self._condition.notify_all()
            return CandidateOutcome.PUBLISHED

    def abandon_candidate(self, token: CandidateToken) -> CandidateOutcome:
        """Close a PREPARING candidate after an evidence failure (never claimable, no retry).

        Same expiry contract as publish: the deadline is checked with the control
        clock inside the lock, so a candidate past its deadline is recorded EXPIRED
        whether or not a sweep ran first."""
        with self._lock:
            now = self._clock()
            if not self._token_current_locked(token):
                self._counts["abandon_rejected"] += 1
                return CandidateOutcome.STALE
            entry = self._preparing.get(token)
            if entry is None:
                self._counts["abandon_rejected"] += 1
                return self._outcomes.get(token, CandidateOutcome.UNKNOWN)
            if now >= entry.deadline:
                self._close_preparing_locked(token, CandidateOutcome.EXPIRED, "candidates_prepare_expired")
                self._counts["abandon_rejected"] += 1
                return CandidateOutcome.EXPIRED
            self._close_preparing_locked(token, CandidateOutcome.ABANDONED, "candidates_abandoned")
            return CandidateOutcome.ABANDONED

    # ── evidence markers ─────────────────────────────────────────────────────

    def _mark_locked(self, kind: MarkerKind, episode_id: str, revision: int, crossed_in_at: float,
                     token: CandidateToken | None = None, *, keep_samples: bool = False,
                     instance: int | None = None) -> None:
        if instance is None and token is not None:
            instance = token.episode_instance      # candidate markers name their episode instance
        self._marker_seq += 1
        self._counts["markers_emitted"] += 1
        if self._resync_required:                     # the next view covers it
            self._counts["markers_dropped_overflow"] += 1
            return
        if len(self._markers) >= self._t.marker_queue_limit:
            self._counts["marker_overflows"] += 1
            self._counts["markers_dropped_overflow"] += len(self._markers) + 1
            self._markers.clear()
            self._resync_required = True
            return
        self._markers.append(EpisodeMarker(self._marker_seq, kind, episode_id, revision, crossed_in_at,
                                           token, keep_samples, instance))

    def _view_locked(self) -> EvidenceView:
        entered = tuple((e.episode_id, e.instance, e.revision, e.crossed_in_at)
                        for e in self._episodes.values() if e.state is EpisodeState.ENTERED)
        preparing = tuple((entry.episode_id, token) for token, entry in self._preparing.items())
        live = frozenset((*self._preparing, *(entry.token for entry in self._candidates)))
        return EvidenceView(entered, preparing, live)

    def pop_markers(self) -> MarkerBatch:
        """Atomically drain markers; after an overflow, return the authoritative view instead."""
        with self._lock:
            self._expire_locked(self._clock())
            if self._resync_required:
                self._resync_required = False
                self._markers.clear()
                self._counts["resyncs_issued"] += 1
                return MarkerBatch(self._session_id, self._session_generation, self._marker_seq, (),
                                   self._view_locked())
            markers = tuple(self._markers)
            self._markers.clear()
            self._counts["markers_popped"] += len(markers)
            return MarkerBatch(self._session_id, self._session_generation, self._marker_seq, markers)

    def pop_alerts(self) -> tuple[LocalProximityAlert, ...]:
        """Atomically remove and return every queued, unexpired alert (each exactly once)."""
        with self._lock:
            self._expire_locked(self._clock())
            alerts = tuple(self._alerts)
            self._alerts.clear()
            self._counts["alerts_popped"] += len(alerts)
            return alerts

    def wait_for_candidate(self, timeout_seconds: float) -> bool:
        if not _is_number(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            generation = self._wake_generation
            while True:
                if self._closed or self._session_id is None or generation != self._wake_generation:
                    return False
                self._expire_locked(self._clock())
                if self._pending_event_id is None and self._candidates:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)

    def wake_waiters(self) -> None:
        with self._condition:
            self._wake_generation += 1
            self._condition.notify_all()

    def claim_next_candidate(self, now: float) -> PrivateParkingCandidate | None:
        self._require_time(now)
        with self._lock:
            self._expire_locked(self._clock())
            if (self._closed or self._session_id is None or self._pending_event_id is not None
                    or not self._candidates):
                return None
            entry = self._candidates[0]
            self._pending_event_id, self._pending_token = entry.candidate.event_id, entry.token
            self._counts["candidates_claimed"] += 1
            return entry.candidate

    def complete(self, event_id: str, now: float, *, token: CandidateToken | None = None) -> bool:
        return self._finish(event_id, now, token)

    def fail(self, event_id: str, now: float, *, token: CandidateToken | None = None) -> bool:
        return self._finish(event_id, now, token)

    def _finish(self, event_id: str, now: float, token: CandidateToken | None) -> bool:
        """Finish exactly the claimed candidate: both its event id and its token must
        match (a call with the id alone is refused; there is no id-only fallback), so a
        late cleanup can never finish a newer candidate that reuses the same id."""
        self._require_time(now)
        with self._lock:
            if (self._pending_token is None or token != self._pending_token
                    or event_id != self._pending_event_id):
                self._counts["finish_rejected"] += 1
                return False
            for entry in self._candidates:
                if entry.token == token:
                    self._mark_locked(MarkerKind.CANDIDATE_FINISHED, entry.episode_id, entry.revision,
                                      entry.crossed_in_at, entry.token)
            self._candidates = deque(e for e in self._candidates if e.token != token)
            self._pending_event_id, self._pending_token = None, None
            self._counts["candidates_finished"] += 1
            self._condition.notify_all()
            return True

    def get_snapshot(self) -> PrivateParkingDiagnostic:
        with self._lock:
            self._expire_locked(self._clock())
            episodes = tuple(
                EpisodeView(e.episode_id, e.person_track_id, e.state, e.escorted_motor)
                for e in sorted(self._episodes.values(), key=lambda item: item.person_track_id))
            return PrivateParkingDiagnostic(
                self._session_id, self._pending_event_id, episodes, len(self._alerts),
                len(self._candidates), tuple(self._counts.items()), len(self._preparing),
                len(self._markers), self._resync_required, self._pending_token)

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _require_time(now: object) -> None:
        if not _is_number(now):
            raise ValueError("now must be finite")

    def _kind(self, track: TrackedDetection) -> str | None:
        box = track.xyxy
        if (type(track.track_id) is not int or not track.confirmed or not isinstance(track.class_name, str)
                or not _is_number(track.confidence) or len(box) != 4
                or not all(_is_number(value) for value in box) or not (box[2] > box[0] and box[3] > box[1])):
            return None
        label = track.class_name.strip().lower()
        if label == "person" and track.confidence >= self._t.min_person_confidence:
            return "person"
        if label == "motorcycle" and track.confidence >= self._t.min_motor_confidence:
            return "motorcycle"
        return None

    def _parked(self, motor: _Track) -> bool:
        if len(motor.centers) < self._t.parked_min_observations:
            return False
        first = motor.centers[0][1]
        return all(math.dist(first, center) <= self._t.parked_motion for _ts, center in motor.centers)

    @staticmethod
    def _edge_distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
        dx = max(0.0, b[0] - a[2], a[0] - b[2])
        dy = max(0.0, b[1] - a[3], a[1] - b[3])
        return math.hypot(dx, dy)

    @staticmethod
    def _valid_snapshot(snapshot: object) -> bool:
        return (isinstance(snapshot, TrackingSnapshot) and type(snapshot.source_frame_seq) is int
                and _is_number(snapshot.source_timestamp)
                and type(snapshot.frame_width) is int and snapshot.frame_width > 0
                and type(snapshot.frame_height) is int and snapshot.frame_height > 0)

    @staticmethod
    def _domain_is_active(snapshot: object, session_id: str) -> bool:
        return (isinstance(snapshot, DomainSnapshot) and snapshot.source_session_id == session_id
                and snapshot.state is DomainState.LOCKED and snapshot.domain is DomainName.PARKING_MOTORCYCLE)
