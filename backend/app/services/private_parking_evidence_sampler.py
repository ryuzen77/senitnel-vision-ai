"""Per-episode evidence sampler for the private-parking mode (T2: isolated, not wired).

Ownership and memory
--------------------
Frames are production ``BufferedFrame`` objects (already JPEG-encoded by
VideoService; the sampler never encodes, waits or calls out under its lock).
Every frame the sampler references (pre-roll, crossing anchor candidate,
journey samples, "latest" slot, sealed evidence) is counted ONCE by
``source_frame_seq`` in a reference-counted table; ``held_bytes`` is the sum of
those unique JPEGs and never exceeds ``max_total_bytes``. When a new frame does
not fit, it is not admitted (``frames_dropped_budget``); anchors and sealed
evidence are never evicted to make room. This bounds the application image
buffer only, not process memory (Python objects, encoder buffers, base64 later).

Selection (deterministic, by timestamps and explicit markers only)
------------------------------------------------------------------
* Crossing anchor: the frame nearest ``crossed_at`` within the marker
  tolerance, searched in the pre-roll and in frames that arrive later; it is
  only final once a frame newer than ``crossed_at + tolerance`` was seen, so a
  closer later frame can still replace an earlier candidate (tie: earlier).
* Journey: frames after ``crossed_at`` are admitted with binary thinning
  (capacity K even; when full keep indices 0, 2, 4, ... and double the stride),
  plus one "latest" slot that always holds the newest journey frame, so
  coverage spans from the start of the walk to the newest frame.
* Approach anchor (at ``seal``): nearest frame to ``triggered_at`` within the
  tolerance among pre-roll, latest slot and journey samples.
* Three middle frames: candidates strictly between the two anchors; greedily
  nearest to the 1/4, 1/2, 3/4 points, in order, never reusing a frame and
  always leaving enough later candidates.
Fewer than five distinct frames is an explicit failure: no duplication, no
frames from another episode or session, no waiting for future frames.

Lifecycle
---------
Observation timestamps order frames and relate them to markers. The injected
control clock drives episode max age and sealed-evidence TTL, so both expire
without new observations. Sealed evidence is immutable and kept until
``release(event_id)`` or its TTL; it survives the end of its episode but not
Stop, a new session or ``close``. Every call is checked against the current
session, so late input from an old session never recreates state.

Plans and bundles (for T3)
--------------------------
``plan_for(event_id)`` returns a plan whose offsets ARE the sealed frames'
actual offsets (deviation tolerance ``EVIDENCE_OFFSET_EPSILON`` for float
rounding). ``wait_for_bundle`` returns READY only for a plan that matches the
sealed evidence exactly; it never waits. Missing/expired private evidence must
not fall back to the legacy parking buffer on T3.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable
import dataclasses
import math
import threading
import time

from app.models.domain import DomainName
from app.models.event_frame import (
    BufferedFrame, BundleDecision, BundleStatus, EventFrameBundle, FrameSelectionPlan, SelectedFrame,
)
from app.models.private_parking_evidence import (
    EVIDENCE_FRAME_COUNT, EVIDENCE_OFFSET_EPSILON, EvidenceFailure, EvidenceFrame, EvidenceRole,
    EvidenceSamplerLimits, SamplerDiagnostic, SealedEvidence, SealResult,
)

_COUNTERS = (
    "frames_offered", "frames_rejected", "frames_dropped_budget", "episodes_started",
    "episodes_rejected_capacity", "episodes_rejected_duplicate", "episodes_cancelled",
    "episodes_ended", "episodes_aged_out", "crossing_anchor_missing", "seals_succeeded",
    "seals_failed", "sealed_rejected_capacity", "sealed_expired", "sealed_released",
    "failure_records_evicted", "bundles_served", "bundles_refused", "stale_calls_ignored",
)


def _finite(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


@dataclasses.dataclass
class _Episode:
    episode_id: str
    crossed_at: float
    started_control: float
    anchor: BufferedFrame | None = None
    anchor_final: bool = False
    failed: EvidenceFailure | None = None
    journey: list = dataclasses.field(default_factory=list)
    stride: int = 1
    seen: int = 0
    latest: BufferedFrame | None = None


@dataclasses.dataclass
class _Sealed:
    evidence: SealedEvidence
    deadline: float


class EpisodeEvidenceSampler:
    def __init__(self, limits: EvidenceSamplerLimits | None = None, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        limits = limits if limits is not None else EvidenceSamplerLimits()
        if not isinstance(limits, EvidenceSamplerLimits):
            raise ValueError("limits must be EvidenceSamplerLimits")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._limits = limits
        self._clock = clock
        self._lock = threading.Lock()
        self._closed = False
        self._generation = 0
        with self._lock:
            self._reset_locked(None)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _reset_locked(self, session_id: str | None) -> None:
        self._generation += 1
        self._session_id = session_id
        self._held: dict[int, list] = {}          # seq -> [frame, refcount]
        self._held_bytes = 0
        self._preroll: deque[BufferedFrame] = deque()
        self._episodes: dict[str, _Episode] = {}
        self._sealed: OrderedDict[str, _Sealed] = OrderedDict()
        self._failures: OrderedDict[str, EvidenceFailure] = OrderedDict()
        self._last_seq: int | None = None
        self._last_ts: float | None = None
        self._counts = dict.fromkeys(_COUNTERS, 0)

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

    def _active(self, session_id: object) -> bool:
        if self._closed or self._session_id is None or session_id != self._session_id:
            self._counts["stale_calls_ignored"] += 1
            return False
        return True

    # ── reference-counted ownership ──────────────────────────────────────────

    def _hold(self, frame: BufferedFrame) -> bool:
        entry = self._held.get(frame.source_frame_seq)
        if entry is not None:
            entry[1] += 1
            return True
        if self._held_bytes + len(frame.jpeg) > self._limits.max_total_bytes:
            self._counts["frames_dropped_budget"] += 1
            return False
        self._held[frame.source_frame_seq] = [frame, 1]
        self._held_bytes += len(frame.jpeg)
        return True

    def _drop(self, frame: BufferedFrame | None) -> None:
        if frame is None:
            return
        entry = self._held.get(frame.source_frame_seq)
        if entry is None:
            return
        entry[1] -= 1
        if entry[1] <= 0:
            del self._held[frame.source_frame_seq]
            self._held_bytes -= len(frame.jpeg)

    def _replace_locked(self, old: BufferedFrame | None, new: BufferedFrame) -> bool:
        """Atomically move one reference from ``old`` to ``new``.

        Capacity is computed on ownership AFTER the replacement: the old frame's
        bytes only count as freed when this slot is its last owner (pre-roll,
        anchors, journey, other episodes and sealed evidence all hold their own
        references). If the new frame does not fit, nothing changes.
        """
        if old is not None and old.source_frame_seq == new.source_frame_seq:
            return True
        entry = self._held.get(new.source_frame_seq)
        if entry is not None:                     # already owned elsewhere: no new bytes
            entry[1] += 1
            self._drop(old)
            return True
        freed = 0
        if old is not None:
            old_entry = self._held.get(old.source_frame_seq)
            if old_entry is not None and old_entry[1] == 1:
                freed = len(old.jpeg)
        if self._held_bytes - freed + len(new.jpeg) > self._limits.max_total_bytes:
            self._counts["frames_dropped_budget"] += 1
            return False
        self._drop(old)                           # free first, so the limit holds throughout
        self._held[new.source_frame_seq] = [new, 1]
        self._held_bytes += len(new.jpeg)
        return True

    def _release_episode(self, episode: _Episode) -> None:
        self._drop(episode.anchor)
        self._drop(episode.latest)
        for frame in episode.journey:
            self._drop(frame)
        episode.anchor, episode.latest, episode.journey = None, None, []

    # ── frames ───────────────────────────────────────────────────────────────

    def offer_frame(self, frame: BufferedFrame) -> bool:
        with self._lock:
            self._expire_locked(self._clock())
            if not isinstance(frame, BufferedFrame) or not self._active(frame.source_session_id):
                return False
            self._counts["frames_offered"] += 1
            limits = self._limits
            if (not isinstance(frame.jpeg, bytes) or not frame.jpeg or len(frame.jpeg) > limits.max_frame_bytes
                    or frame.mime_type != "image/jpeg" or type(frame.source_frame_seq) is not int
                    or not _finite(frame.source_timestamp)
                    or type(frame.frame_width) is not int or frame.frame_width <= 0
                    or type(frame.frame_height) is not int or frame.frame_height <= 0
                    or (self._last_seq is not None and frame.source_frame_seq <= self._last_seq)
                    or (self._last_ts is not None and frame.source_timestamp <= self._last_ts)):
                self._counts["frames_rejected"] += 1
                return False
            self._last_seq, self._last_ts = frame.source_frame_seq, frame.source_timestamp
            self._add_preroll_locked(frame)
            for episode in self._episodes.values():
                self._observe_episode_locked(episode, frame)
            return True

    def _add_preroll_locked(self, frame: BufferedFrame) -> None:
        limits = self._limits
        while self._preroll and (len(self._preroll) >= limits.preroll_max_frames
                                 or frame.source_timestamp - self._preroll[0].source_timestamp
                                 > limits.preroll_seconds):
            self._drop(self._preroll.popleft())
        if self._hold(frame):
            self._preroll.append(frame)

    def _observe_episode_locked(self, episode: _Episode, frame: BufferedFrame) -> None:
        if episode.failed is not None:
            return
        tolerance = self._limits.marker_tolerance_seconds
        ts = frame.source_timestamp
        if not episode.anchor_final:
            if abs(ts - episode.crossed_at) <= tolerance:
                self._offer_anchor_locked(episode, frame)
            elif ts > episode.crossed_at + tolerance:
                episode.anchor_final = True
                if episode.anchor is None:
                    self._fail_working_locked(episode, EvidenceFailure.CROSSING_ANCHOR_MISSING)
                    return
        if ts > episode.crossed_at:
            self._admit_journey_locked(episode, frame)

    def _offer_anchor_locked(self, episode: _Episode, frame: BufferedFrame) -> None:
        current = episode.anchor
        if current is not None and (abs(frame.source_timestamp - episode.crossed_at)
                                    >= abs(current.source_timestamp - episode.crossed_at)):
            return                                       # tie keeps the earlier frame
        if self._replace_locked(current, frame):
            episode.anchor = frame

    def _admit_journey_locked(self, episode: _Episode, frame: BufferedFrame) -> None:
        if self._replace_locked(episode.latest, frame):  # newest-frame slot: always the latest
            episode.latest = frame
        index = episode.seen
        episode.seen += 1
        if index % episode.stride:
            return
        if len(episode.journey) >= self._limits.journey_capacity:
            kept = episode.journey[::2]
            for dropped in episode.journey[1::2]:
                self._drop(dropped)
            episode.journey = kept
            episode.stride *= 2
            if index % episode.stride:
                return
        if self._hold(frame):
            episode.journey.append(frame)

    def _fail_working_locked(self, episode: _Episode, failure: EvidenceFailure) -> None:
        episode.failed = failure
        if failure is EvidenceFailure.CROSSING_ANCHOR_MISSING:
            self._counts["crossing_anchor_missing"] += 1
        self._release_episode(episode)

    # ── episode markers (decisions come from the trigger) ────────────────────

    def begin_episode(self, session_id: str, episode_id: str, crossed_at: float) -> bool:
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            if not self._active(session_id):
                return False
            if not isinstance(episode_id, str) or not episode_id or not _finite(crossed_at):
                self._counts["stale_calls_ignored"] += 1
                return False
            if episode_id in self._episodes:
                self._counts["episodes_rejected_duplicate"] += 1
                return False
            if len(self._episodes) >= self._limits.max_episodes:
                self._counts["episodes_rejected_capacity"] += 1
                return False
            episode = _Episode(episode_id, float(crossed_at), now)
            self._episodes[episode_id] = episode
            self._counts["episodes_started"] += 1
            for frame in tuple(self._preroll):           # back-fill from the pre-roll, in order
                self._observe_episode_locked(episode, frame)
            return True

    def cancel_episode(self, session_id: str, episode_id: str) -> bool:
        """E.g. authorized before any trigger: evidence is discarded."""
        return self._remove_episode(session_id, episode_id, "episodes_cancelled")

    def end_episode(self, session_id: str, episode_id: str) -> bool:
        return self._remove_episode(session_id, episode_id, "episodes_ended")

    def _remove_episode(self, session_id: str, episode_id: str, counter: str) -> bool:
        with self._lock:
            self._expire_locked(self._clock())
            if not self._active(session_id):
                return False
            episode = self._episodes.pop(episode_id, None)
            if episode is None:
                return False
            self._release_episode(episode)
            self._counts[counter] += 1
            return True

    def seal(self, session_id: str, episode_id: str, event_id: str, triggered_at: float) -> SealResult:
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            if not self._active(session_id) or not isinstance(event_id, str) or not event_id \
                    or not _finite(triggered_at) or event_id in self._sealed or event_id in self._failures:
                return SealResult(None, EvidenceFailure.UNKNOWN_EPISODE)
            episode = self._episodes.pop(episode_id, None)
            if episode is None:
                return self._record_failure_locked(event_id, EvidenceFailure.UNKNOWN_EPISODE)
            try:
                failure, frames = self._select_locked(episode, float(triggered_at))
                if failure is None and len(self._sealed) >= self._limits.max_sealed:
                    failure = EvidenceFailure.CAPACITY
                    self._counts["sealed_rejected_capacity"] += 1
                if failure is not None:
                    return self._record_failure_locked(event_id, failure)
                evidence = SealedEvidence(event_id, session_id, episode_id, float(triggered_at), tuple(
                    EvidenceFrame(role, frame.source_frame_seq, frame.source_timestamp,
                                  frame.source_timestamp - float(triggered_at), frame.frame_width,
                                  frame.frame_height, frame.jpeg)
                    for role, frame in frames))
                for _role, frame in frames:
                    self._held[frame.source_frame_seq][1] += 1     # sealed ownership (already held)
                self._sealed[event_id] = _Sealed(evidence, now + self._limits.sealed_ttl_seconds)
                self._counts["seals_succeeded"] += 1
                return SealResult(evidence, None)
            finally:
                self._release_episode(episode)

    def _select_locked(self, episode: _Episode, triggered_at: float):
        if episode.failed is not None:
            return episode.failed, ()
        crossing = episode.anchor
        if crossing is None:
            self._counts["crossing_anchor_missing"] += 1
            return EvidenceFailure.CROSSING_ANCHOR_MISSING, ()
        tolerance = self._limits.marker_tolerance_seconds
        pool = {frame.source_frame_seq: frame for frame in (*self._preroll, *episode.journey, episode.latest)
                if frame is not None and frame.source_frame_seq in self._held}
        approach = None
        for frame in sorted(pool.values(), key=lambda f: f.source_frame_seq):
            gap = abs(frame.source_timestamp - triggered_at)
            if (gap <= tolerance and frame.source_timestamp > crossing.source_timestamp
                    and (approach is None or gap < abs(approach.source_timestamp - triggered_at))):
                approach = frame
        if approach is None:
            return EvidenceFailure.APPROACH_ANCHOR_MISSING, ()
        candidates = sorted((frame for frame in pool.values()
                             if crossing.source_timestamp < frame.source_timestamp < approach.source_timestamp),
                            key=lambda f: f.source_frame_seq)
        if len(candidates) < 3:
            return EvidenceFailure.INSUFFICIENT_JOURNEY, ()
        start, end = crossing.source_timestamp, approach.source_timestamp
        chosen, low = [], 0
        for pick, quantile in enumerate((0.25, 0.5, 0.75)):
            target = start + quantile * (end - start)
            window = range(low, len(candidates) - (2 - pick))
            index = min(window, key=lambda i: (abs(candidates[i].source_timestamp - target), i))
            chosen.append(candidates[index])
            low = index + 1
        return None, ((EvidenceRole.CROSSING, crossing), *((EvidenceRole.JOURNEY, f) for f in chosen),
                      (EvidenceRole.APPROACH, approach))

    def _record_failure_locked(self, event_id: str, failure: EvidenceFailure) -> SealResult:
        self._counts["seals_failed"] += 1
        self._failures[event_id] = failure
        while len(self._failures) > self._limits.max_failure_records:
            self._failures.popitem(last=False)
            self._counts["failure_records_evicted"] += 1
        return SealResult(None, failure)

    # ── control timeline ─────────────────────────────────────────────────────

    def _expire_locked(self, now: float) -> None:
        for episode_id, episode in list(self._episodes.items()):
            if now - episode.started_control >= self._limits.episode_max_age_seconds:
                self._release_episode(episode)
                del self._episodes[episode_id]
                self._counts["episodes_aged_out"] += 1
        for event_id, sealed in list(self._sealed.items()):
            if now >= sealed.deadline:
                self._release_sealed_locked(event_id)
                self._counts["sealed_expired"] += 1
                self._failures[event_id] = EvidenceFailure.EXPIRED
                while len(self._failures) > self._limits.max_failure_records:
                    self._failures.popitem(last=False)
                    self._counts["failure_records_evicted"] += 1

    def _release_sealed_locked(self, event_id: str) -> None:
        sealed = self._sealed.pop(event_id, None)
        if sealed is None:
            return
        for evidence_frame in sealed.evidence.frames:
            entry = self._held.get(evidence_frame.source_frame_seq)
            if entry is not None:
                self._drop(entry[0])

    def release(self, event_id: str) -> bool:
        with self._lock:
            if event_id not in self._sealed:
                return False
            self._release_sealed_locked(event_id)
            self._counts["sealed_released"] += 1
            return True

    # ── plans and bundles ────────────────────────────────────────────────────

    def plan_for(self, event_id: str) -> FrameSelectionPlan | None:
        """Evidence-derived plan: offsets are the sealed frames' actual offsets."""
        with self._lock:
            self._expire_locked(self._clock())
            sealed = self._sealed.get(event_id)
            if sealed is None:
                return None
            evidence = sealed.evidence
            return FrameSelectionPlan(
                evidence.event_id, evidence.source_session_id, DomainName.PARKING_MOTORCYCLE,
                evidence.triggered_at, tuple(frame.actual_offset_seconds for frame in evidence.frames),
                EVIDENCE_OFFSET_EPSILON, EVIDENCE_FRAME_COUNT)

    def wait_for_bundle(self, plan: FrameSelectionPlan, timeout_seconds: float = 0.0) -> BundleDecision:
        """Never waits: sealed evidence is complete or the event has no bundle."""
        with self._lock:
            self._expire_locked(self._clock())
            if self._closed:
                return BundleDecision(BundleStatus.CLOSED)
            if not isinstance(plan, FrameSelectionPlan):
                self._counts["bundles_refused"] += 1
                return BundleDecision(BundleStatus.INVALID_PLAN)
            if plan.source_session_id != self._session_id:
                self._counts["bundles_refused"] += 1
                return BundleDecision(BundleStatus.SESSION_MISMATCH)
            sealed = self._sealed.get(plan.event_id)
            if sealed is None:
                self._counts["bundles_refused"] += 1
                return BundleDecision(BundleStatus.INSUFFICIENT_HISTORY)
            evidence = sealed.evidence
            frames = []
            for frame in evidence.frames:
                deviation = abs(frame.source_timestamp - (plan.triggered_at + frame.actual_offset_seconds))
                frames.append(SelectedFrame(frame.source_frame_seq, frame.source_timestamp,
                                            frame.actual_offset_seconds, frame.actual_offset_seconds,
                                            deviation, frame.frame_width, frame.frame_height,
                                            "image/jpeg", frame.jpeg))
            if (plan.domain is not DomainName.PARKING_MOTORCYCLE
                    or plan.triggered_at != evidence.triggered_at
                    or plan.required_frame_count != EVIDENCE_FRAME_COUNT
                    or plan.target_offsets_seconds != tuple(f.actual_offset_seconds for f in evidence.frames)
                    or not _finite(plan.maximum_target_deviation_seconds)
                    or any(f.deviation_seconds > plan.maximum_target_deviation_seconds for f in frames)):
                self._counts["bundles_refused"] += 1
                return BundleDecision(BundleStatus.INVALID_PLAN)
            self._counts["bundles_served"] += 1
            return BundleDecision(BundleStatus.READY, EventFrameBundle(
                evidence.event_id, evidence.source_session_id, DomainName.PARKING_MOTORCYCLE,
                evidence.triggered_at, tuple(frames), evidence.total_jpeg_bytes))

    def failure_for(self, event_id: str) -> EvidenceFailure | None:
        with self._lock:
            self._expire_locked(self._clock())
            return self._failures.get(event_id)

    def get_snapshot(self) -> SamplerDiagnostic:
        with self._lock:
            self._expire_locked(self._clock())
            return SamplerDiagnostic(self._session_id, len(self._episodes), len(self._sealed),
                                     len(self._failures), len(self._held), self._held_bytes,
                                     tuple(self._counts.items()))
