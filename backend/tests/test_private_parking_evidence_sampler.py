"""Synthetic tests for the private-parking evidence sampler (T2). No video, model or network."""

from __future__ import annotations

import dataclasses
import threading

import pytest

from app.models.domain import DomainName
from app.models.event_frame import BufferedFrame, BundleStatus, FrameSelectionPlan
from app.models.private_parking_evidence import (
    EVIDENCE_OFFSET_EPSILON, EvidenceFailure, EvidenceRole, EvidenceSamplerLimits,
)
from app.services.evaluation_worker import EvaluationWorker
from app.services.private_parking_evidence_sampler import EpisodeEvidenceSampler

DT = 0.1                      # 10 fps synthetic stream


class FakeClock:
    def __init__(self) -> None:
        self.now = 500.0      # unrelated to observation timestamps

    def __call__(self) -> float:
        return self.now


def frame(seq: int, ts: float, *, session: str = "s1", size: int = 100) -> BufferedFrame:
    return BufferedFrame(session, seq, ts, 640, 480, b"\xff\xd8" + bytes([seq % 251]) * size + b"\xff\xd9")


class Rig:
    def __init__(self, **limits) -> None:
        self.clock = FakeClock()
        self.sampler = EpisodeEvidenceSampler(EvidenceSamplerLimits(**limits), clock=self.clock)
        self.sampler.start_session("s1")
        self.seq = 0

    def play(self, until: float, *, start: float | None = None, session: str = "s1", size: int = 100) -> None:
        ts = start if start is not None else round(self.seq * DT, 6)
        while ts <= until + 1e-9:
            self.sampler.offer_frame(frame(self.seq, ts, session=session, size=size))
            self.seq += 1
            ts = round(self.seq * DT, 6) if start is None else round(ts + DT, 6)

    def snapshot(self):
        return self.sampler.get_snapshot()


# ── limits ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("overrides", [
    {"journey_capacity": 5}, {"journey_capacity": 2}, {"journey_capacity": True},
    {"marker_tolerance_seconds": float("nan")}, {"marker_tolerance_seconds": 3.0},   # >= pre-roll
    {"max_frame_bytes": 10, "max_total_bytes": 5}, {"max_episodes": 0}, {"max_sealed": 0},
    {"sealed_ttl_seconds": float("inf")}, {"episode_max_age_seconds": 0.5}, {"max_failure_records": 0},
    {"preroll_max_frames": 1}, {"preroll_seconds": "3"},
])
def test_invalid_limits_are_rejected(overrides) -> None:
    with pytest.raises(ValueError):
        EvidenceSamplerLimits(**overrides)


# ── selection ────────────────────────────────────────────────────────────────

def _sealed(rig: Rig, crossed_at=1.03, triggered_at=6.0, event_id="ev1", episode_id="ep1"):
    rig.play(1.5)
    assert rig.sampler.begin_episode("s1", episode_id, crossed_at)
    rig.play(triggered_at)
    return rig.sampler.seal("s1", episode_id, event_id, triggered_at)


def test_five_distinct_ordered_frames_follow_the_episode() -> None:
    rig = Rig()
    result = _sealed(rig)
    assert result.failure is None
    frames = result.evidence.frames
    assert [f.role for f in frames] == [EvidenceRole.CROSSING, *[EvidenceRole.JOURNEY] * 3, EvidenceRole.APPROACH]
    assert len({f.source_frame_seq for f in frames}) == 5
    assert all(a.source_timestamp < b.source_timestamp for a, b in zip(frames, frames[1:]))
    assert frames[0].source_timestamp == 1.0 and frames[-1].source_timestamp == 6.0
    spacing = 0.5                                                  # thinned stride here is <= 4 frames (0.4 s)
    for f, quantile in zip(frames[1:4], (0.25, 0.5, 0.75)):          # near 1/4, 1/2, 3/4 of the walk
        assert abs(f.source_timestamp - (1.0 + quantile * 5.0)) <= spacing
    assert all(f.actual_offset_seconds == f.source_timestamp - 6.0 for f in frames)


def test_crossing_anchor_is_not_locked_too_early() -> None:
    rig = Rig()
    assert rig.sampler.begin_episode("s1", "ep1", 2.0)            # marker before frames arrive
    for seq, ts in ((0, 1.85), (1, 1.95), (2, 2.40)):             # closer frame arrives later
        rig.sampler.offer_frame(frame(seq, ts))
    rig.seq = 3
    rig.play(7.0, start=2.5)
    result = rig.sampler.seal("s1", "ep1", "ev1", 7.0)
    assert result.evidence.frames[0].source_timestamp == 1.95


def test_anchor_tie_keeps_the_earlier_frame() -> None:
    rig = Rig()
    assert rig.sampler.begin_episode("s1", "ep1", 2.0)
    for seq, ts in ((0, 1.9), (1, 2.1), (2, 2.4)):
        rig.sampler.offer_frame(frame(seq, ts))
    rig.seq = 3
    rig.play(7.0, start=2.5)
    assert rig.sampler.seal("s1", "ep1", "ev1", 7.0).evidence.frames[0].source_timestamp == 1.9


def test_long_journey_keeps_the_crossing_and_covers_start_to_latest() -> None:
    rig = Rig(journey_capacity=12)
    rig.play(1.5)
    rig.sampler.begin_episode("s1", "ep1", 1.0)
    rig.play(200.0)                                               # ~2000 frames
    episode = rig.sampler._episodes["ep1"]
    journey = [f.source_timestamp for f in episode.journey]
    assert len(journey) <= 12 and journey[0] <= 1.2              # coverage starts at the walk's start
    assert episode.latest.source_timestamp == 200.0              # ... and reaches the newest frame
    spacing = episode.stride * DT
    result = rig.sampler.seal("s1", "ep1", "ev1", 200.0)
    middle = [f.source_timestamp for f in result.evidence.frames[1:4]]
    for value, quantile in zip(middle, (0.25, 0.5, 0.75)):
        assert abs(value - (1.0 + quantile * 199.0)) <= spacing
    assert result.evidence.frames[0].source_timestamp == 1.0
    assert rig.snapshot().held_bytes <= rig.sampler._limits.max_total_bytes


def test_journey_samples_are_thinned_deterministically() -> None:
    first, second = Rig(journey_capacity=4), Rig(journey_capacity=4)
    for rig in (first, second):
        rig.play(0.5)
        rig.sampler.begin_episode("s1", "ep1", 0.5)
        rig.play(5.0)
    sequences = [[f.source_frame_seq for f in rig.sampler._episodes["ep1"].journey] for rig in (first, second)]
    assert sequences[0] == sequences[1] and len(sequences[0]) <= 4
    assert sequences[0] == sorted(sequences[0])


@pytest.mark.parametrize("crossed_at,triggered_at,failure", [
    (1.0, 1.3, EvidenceFailure.INSUFFICIENT_JOURNEY),          # only two frames in between
    (1.0, 30.0, EvidenceFailure.APPROACH_ANCHOR_MISSING),      # no frame near the trigger
])
def test_short_or_unsupported_evidence_fails_without_duplication(crossed_at, triggered_at, failure) -> None:
    rig = Rig()
    rig.play(1.0)
    rig.sampler.begin_episode("s1", "ep1", crossed_at)
    rig.play(min(triggered_at, 5.0))
    result = rig.sampler.seal("s1", "ep1", "ev1", triggered_at)
    assert result.evidence is None and result.failure is failure
    assert rig.sampler.plan_for("ev1") is None and rig.sampler.failure_for("ev1") is failure


def test_crossing_outside_the_tolerance_is_not_claimed() -> None:
    rig = Rig()
    rig.sampler.offer_frame(frame(0, 0.5))
    rig.sampler.begin_episode("s1", "ep1", 1.0)
    for seq, ts in ((1, 1.5), (2, 1.6)):                      # nearest frame is 0.5 s away
        rig.sampler.offer_frame(frame(seq, ts))
    rig.seq = 3
    rig.play(6.0, start=1.7)
    result = rig.sampler.seal("s1", "ep1", "ev1", 6.0)
    assert result.failure is EvidenceFailure.CROSSING_ANCHOR_MISSING
    assert rig.snapshot().count("crossing_anchor_missing") == 1


def test_duplicate_or_backward_frames_are_rejected() -> None:
    rig = Rig()
    assert rig.sampler.offer_frame(frame(1, 1.0))
    assert not rig.sampler.offer_frame(frame(1, 1.1))           # seq not advancing
    assert not rig.sampler.offer_frame(frame(2, 1.0))           # same timestamp
    assert not rig.sampler.offer_frame(frame(3, 0.9))           # backwards
    assert not rig.sampler.offer_frame(dataclasses.replace(frame(4, 2.0), jpeg=b""))
    assert not rig.sampler.offer_frame(dataclasses.replace(frame(5, 2.1), mime_type="image/png"))
    assert rig.snapshot().count("frames_rejected") == 5


def test_episodes_do_not_mix() -> None:
    rig = Rig()
    rig.play(1.5)
    rig.sampler.begin_episode("s1", "a", 1.0)
    rig.play(3.5)
    rig.sampler.begin_episode("s1", "b", 3.0)
    rig.play(8.0)
    first = rig.sampler.seal("s1", "a", "ev-a", 6.0).evidence
    second = rig.sampler.seal("s1", "b", "ev-b", 8.0).evidence
    assert first.episode_id == "a" and second.episode_id == "b"
    assert first.frames[0].source_timestamp == 1.0 and second.frames[0].source_timestamp == 3.0
    assert all(1.0 <= f.source_timestamp <= 6.0 for f in first.frames)
    assert all(3.0 <= f.source_timestamp <= 8.0 for f in second.frames)


# ── capacity ─────────────────────────────────────────────────────────────────

def assert_ownership(sampler: EpisodeEvidenceSampler) -> None:
    """Every held reference is accounted for by exactly one owner slot; no leaks."""
    expected: dict[int, int] = {}

    def own(seq: int) -> None:
        expected[seq] = expected.get(seq, 0) + 1
    for frame_ in sampler._preroll:
        own(frame_.source_frame_seq)
    for episode in sampler._episodes.values():
        for frame_ in (episode.anchor, episode.latest, *episode.journey):
            if frame_ is not None:
                own(frame_.source_frame_seq)
    for sealed in sampler._sealed.values():
        for evidence_frame in sealed.evidence.frames:
            own(evidence_frame.source_frame_seq)
    assert {seq: entry[1] for seq, entry in sampler._held.items()} == expected
    assert sampler._held_bytes == sum(len(entry[0].jpeg) for entry in sampler._held.values())
    assert sampler._held_bytes <= sampler._limits.max_total_bytes


BUDGET = {"max_total_bytes": 520, "max_frame_bytes": 104, "preroll_max_frames": 2, "journey_capacity": 4}


def _fill_budget(rig: Rig) -> None:
    """Anchor 0, journey [1, 3, 5], latest stuck at 4 (still in the pre-roll): 0,1,3,4,5 x 104 B = 520 B."""
    assert rig.sampler.begin_episode("s1", "ep1", 0.0)
    for seq in range(7):
        assert rig.sampler.offer_frame(frame(seq, seq * 0.1))
    episode = rig.sampler._episodes["ep1"]
    assert [f.source_frame_seq for f in episode.journey] == [1, 3, 5] and episode.latest.source_frame_seq == 4
    assert sorted(rig.sampler._held) == [0, 1, 3, 4, 5]
    assert rig.snapshot().held_bytes == 520
    assert_ownership(rig.sampler)


def test_latest_slot_is_replaced_when_the_old_frame_can_be_released() -> None:
    rig = Rig(preroll_seconds=0.5, **BUDGET)      # frame 34 pushes frame 4 out of the pre-roll
    _fill_budget(rig)
    assert rig.sampler.offer_frame(frame(34, 3.4))
    episode = rig.sampler._episodes["ep1"]
    assert episode.latest.source_frame_seq == 34   # frame 4 was then only owned by the latest slot
    assert 4 not in rig.sampler._held and rig.snapshot().held_bytes == 520
    assert_ownership(rig.sampler)
    evidence = rig.sampler.seal("s1", "ep1", "ev1", 3.4).evidence
    assert [f.source_frame_seq for f in evidence.frames] == [0, 1, 3, 5, 34]
    assert evidence.frames[-1].role is EvidenceRole.APPROACH
    assert_ownership(rig.sampler)


def test_latest_slot_keeps_its_frame_when_the_old_frame_is_still_owned_elsewhere() -> None:
    rig = Rig(preroll_seconds=3.0, **BUDGET)      # pre-roll keeps frame 4: its bytes are not free
    _fill_budget(rig)
    dropped = rig.snapshot().count("frames_dropped_budget")
    assert rig.sampler.offer_frame(frame(34, 3.4))
    episode = rig.sampler._episodes["ep1"]
    assert episode.latest.source_frame_seq == 4 and 34 not in rig.sampler._held
    assert rig.sampler._held[4][1] == 2 and rig.snapshot().held_bytes == 520     # nothing wrongly freed
    assert rig.snapshot().count("frames_dropped_budget") > dropped
    assert_ownership(rig.sampler)
    result = rig.sampler.seal("s1", "ep1", "ev1", 3.4)
    assert result.evidence is None and result.failure is EvidenceFailure.APPROACH_ANCHOR_MISSING
    assert_ownership(rig.sampler)


def test_long_stream_under_a_small_budget_never_exceeds_it_and_leaks_nothing() -> None:
    rig = Rig(max_total_bytes=3000, max_frame_bytes=200)
    rig.play(1.5)
    rig.sampler.begin_episode("s1", "ep1", 1.0)
    for _ in range(80):
        rig.play(round(rig.seq * DT, 6))
        assert rig.snapshot().held_bytes <= 3000
        assert_ownership(rig.sampler)
    assert rig.snapshot().count("frames_dropped_budget") > 0
    rig.sampler.end_session("s1")
    assert rig.snapshot().held_bytes == 0 and rig.sampler._held == {}


def test_oversized_frame_is_rejected() -> None:
    rig = Rig(max_frame_bytes=50)
    assert not rig.sampler.offer_frame(frame(0, 0.0, size=100))


def test_episode_sealed_and_failure_capacities_are_bounded() -> None:
    rig = Rig(max_episodes=1, max_sealed=1, max_failure_records=2)
    rig.play(1.5)
    assert rig.sampler.begin_episode("s1", "a", 1.0)
    assert not rig.sampler.begin_episode("s1", "b", 1.2)
    assert not rig.sampler.begin_episode("s1", "a", 1.0)
    assert rig.snapshot().count("episodes_rejected_capacity") == 1
    rig.play(6.0)
    assert rig.sampler.seal("s1", "a", "ev-a", 6.0).failure is None
    rig.sampler.begin_episode("s1", "c", 6.0)
    rig.play(12.0)
    assert rig.sampler.seal("s1", "c", "ev-c", 12.0).failure is EvidenceFailure.CAPACITY
    for index in range(3):
        rig.sampler.seal("s1", f"missing-{index}", f"ev-x{index}", 12.0)
    snapshot = rig.snapshot()
    assert snapshot.failure_records == 2 and snapshot.count("failure_records_evicted") >= 2


# ── immutability, plans and the production validator ────────────────────────

def test_sealed_evidence_is_immutable_and_unaffected_by_later_frames() -> None:
    rig = Rig()
    evidence = _sealed(rig).evidence
    plan = rig.sampler.plan_for("ev1")
    before = rig.sampler.wait_for_bundle(plan).bundle
    rig.play(20.0)
    after = rig.sampler.wait_for_bundle(plan).bundle
    assert before == after and after.frames[-1].source_timestamp == 6.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        evidence.frames[0].source_timestamp = 0.0      # type: ignore[misc]


def test_evidence_bundle_passes_the_production_worker_validator_with_fractional_timestamps() -> None:
    rig = Rig()
    for seq in range(5):
        rig.sampler.offer_frame(frame(seq, 0.1 + seq * 0.1))      # 0.30000000000000004 style values
    rig.sampler.begin_episode("s1", "ep1", 0.3)
    for seq in range(5, 120):
        rig.sampler.offer_frame(frame(seq, 0.1 + seq * 0.1))
    triggered = 0.1 + 110 * 0.1
    assert rig.sampler.seal("s1", "ep1", "ev1", triggered).failure is None
    plan = rig.sampler.plan_for("ev1")
    assert plan.domain is DomainName.PARKING_MOTORCYCLE and plan.required_frame_count == 5
    assert plan.maximum_target_deviation_seconds == EVIDENCE_OFFSET_EPSILON
    decision = rig.sampler.wait_for_bundle(plan)
    assert decision.status is BundleStatus.READY
    assert EvaluationWorker._valid_bundle(decision.bundle, plan)
    assert all(f.source_timestamp == e.source_timestamp
               for f, e in zip(decision.bundle.frames, rig.sampler._sealed["ev1"].evidence.frames))


def test_exact_zero_deviation_plan_is_documented_as_float_fragile() -> None:
    """A strict 0.0 plan fails as soon as triggered_at + offset != timestamp in floating point."""
    assert 0.7 + (0.1 - 0.7) != 0.1                       # the arithmetic behind the epsilon
    rig = Rig()
    for seq in range(5):
        rig.sampler.offer_frame(frame(seq, 0.1 + seq * 0.1))
    rig.sampler.begin_episode("s1", "ep1", 0.3)
    for seq in range(5, 120):
        rig.sampler.offer_frame(frame(seq, 0.1 + seq * 0.1))
    rig.sampler.seal("s1", "ep1", "ev1", 0.1 + 110 * 0.1)
    plan = rig.sampler.plan_for("ev1")
    strict = dataclasses.replace(plan, maximum_target_deviation_seconds=0.0)
    deviations = [abs(e.source_timestamp - (plan.triggered_at + e.actual_offset_seconds))
                  for e in rig.sampler._sealed["ev1"].evidence.frames]
    assert any(deviations)                                # this stream really hits float rounding
    assert rig.sampler.wait_for_bundle(strict).status is BundleStatus.INVALID_PLAN
    epsilon = rig.sampler.wait_for_bundle(plan)
    assert not EvaluationWorker._valid_bundle(epsilon.bundle, strict)   # worker would reject 0.0 as well
    assert EvaluationWorker._valid_bundle(epsilon.bundle, plan)


@pytest.mark.parametrize("change", [
    {"target_offsets_seconds": (-5.0, -3.0, -2.0, -1.0, 0.0)}, {"domain": DomainName.RETAIL_STORE},
    {"triggered_at": 6.1}, {"maximum_target_deviation_seconds": float("nan")},
])
def test_mismatched_plans_are_refused(change) -> None:
    rig = Rig()
    _sealed(rig)
    decision = rig.sampler.wait_for_bundle(dataclasses.replace(rig.sampler.plan_for("ev1"), **change))
    assert decision.status is BundleStatus.INVALID_PLAN and decision.bundle is None


def test_unknown_event_or_session_never_waits_and_never_falls_back() -> None:
    rig = Rig()
    _sealed(rig)
    plan = rig.sampler.plan_for("ev1")
    assert rig.sampler.wait_for_bundle(dataclasses.replace(plan, event_id="other")).status \
        is BundleStatus.INSUFFICIENT_HISTORY
    assert rig.sampler.wait_for_bundle(dataclasses.replace(plan, source_session_id="s0")).status \
        is BundleStatus.SESSION_MISMATCH


# ── lifecycle and expiry ─────────────────────────────────────────────────────

def test_sealed_evidence_expires_on_the_control_clock_without_observations() -> None:
    rig = Rig(sealed_ttl_seconds=30.0)
    _sealed(rig)
    plan = rig.sampler.plan_for("ev1")
    rig.clock.now += 29.99
    assert rig.sampler.wait_for_bundle(plan).status is BundleStatus.READY
    rig.clock.now += 0.01
    assert rig.sampler.wait_for_bundle(plan).status is BundleStatus.INSUFFICIENT_HISTORY
    assert rig.sampler.plan_for("ev1") is None and rig.sampler.failure_for("ev1") is EvidenceFailure.EXPIRED
    assert rig.snapshot().count("sealed_expired") == 1


def test_episode_ages_out_on_the_control_clock() -> None:
    rig = Rig(episode_max_age_seconds=10.0)
    rig.play(1.5)
    rig.sampler.begin_episode("s1", "ep1", 1.0)
    rig.clock.now += 10.0
    snapshot = rig.snapshot()
    assert snapshot.active_episodes == 0 and snapshot.count("episodes_aged_out") == 1
    assert rig.sampler.seal("s1", "ep1", "ev1", 1.4).failure is EvidenceFailure.UNKNOWN_EPISODE


def test_evidence_survives_the_episode_but_not_stop() -> None:
    rig = Rig()
    _sealed(rig)
    assert not rig.sampler.end_episode("s1", "ep1")              # already sealed: nothing to end
    assert rig.sampler.plan_for("ev1") is not None
    rig.sampler.end_session("s1")
    snapshot = rig.snapshot()
    assert (snapshot.sealed, snapshot.held_frames, snapshot.held_bytes) == (0, 0, 0)


@pytest.mark.parametrize("cleanup", ["cancel", "end", "stop", "new-session", "close"])
def test_cleanup_releases_ownership(cleanup) -> None:
    rig = Rig()
    rig.play(1.5)
    rig.sampler.begin_episode("s1", "ep1", 1.0)
    rig.play(4.0)
    preroll_bytes = sum(len(f.jpeg) for f in rig.sampler._preroll)
    {"cancel": lambda: rig.sampler.cancel_episode("s1", "ep1"),
     "end": lambda: rig.sampler.end_episode("s1", "ep1"),
     "stop": lambda: rig.sampler.end_session("s1"),
     "new-session": lambda: rig.sampler.start_session("s2"),
     "close": rig.sampler.close}[cleanup]()
    snapshot = rig.snapshot()
    assert snapshot.active_episodes == 0
    expected = preroll_bytes if cleanup in ("cancel", "end") else 0
    assert snapshot.held_bytes == expected


def test_release_frees_sealed_ownership_once() -> None:
    rig = Rig()
    _sealed(rig)
    assert rig.sampler.release("ev1") and not rig.sampler.release("ev1")
    assert rig.snapshot().held_bytes == sum(len(f.jpeg) for f in rig.sampler._preroll)


def test_old_session_input_never_recreates_state() -> None:
    rig = Rig()
    rig.play(1.5)
    rig.sampler.begin_episode("s1", "ep1", 1.0)
    rig.sampler.start_session("s2")
    assert not rig.sampler.offer_frame(frame(100, 10.0, session="s1"))   # late frame encoded for s1
    assert not rig.sampler.begin_episode("s1", "ep2", 10.0)
    assert rig.sampler.seal("s1", "ep1", "ev1", 10.0).evidence is None
    assert not rig.sampler.cancel_episode("s1", "ep1")
    snapshot = rig.snapshot()
    assert (snapshot.source_session_id, snapshot.active_episodes, snapshot.held_bytes) == ("s2", 0, 0)
    assert snapshot.count("stale_calls_ignored") == 4


# ── concurrency ──────────────────────────────────────────────────────────────

def test_bundle_retrieval_races_cleanup_without_mixed_bundles() -> None:
    for _round in range(25):
        rig = Rig()
        evidence = _sealed(rig).evidence
        plan = rig.sampler.plan_for("ev1")
        barrier, results = threading.Barrier(4, timeout=5), []

        def fetch() -> None:
            barrier.wait()
            results.append(rig.sampler.wait_for_bundle(plan))

        def cleanup(action) -> None:
            barrier.wait()
            action()
        threads = [threading.Thread(target=fetch), threading.Thread(target=fetch),
                   threading.Thread(target=cleanup, args=(lambda: rig.sampler.release("ev1"),)),
                   threading.Thread(target=cleanup, args=(lambda: rig.sampler.end_session("s1"),))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
        for decision in results:
            if decision.status is BundleStatus.READY:
                assert [f.source_frame_seq for f in decision.bundle.frames] == \
                    [f.source_frame_seq for f in evidence.frames]
            else:
                assert decision.bundle is None
        snapshot = rig.snapshot()
        assert (snapshot.sealed, snapshot.held_bytes) == (0, 0)


def test_frames_racing_a_session_change_do_not_survive_it() -> None:
    rig = Rig()
    rig.play(1.5)
    rig.sampler.begin_episode("s1", "ep1", 1.0)
    barrier = threading.Barrier(2, timeout=5)

    def stream() -> None:
        barrier.wait()
        for seq in range(1000, 1200):
            rig.sampler.offer_frame(frame(seq, 100.0 + seq * 0.01, session="s1"))

    def switch() -> None:
        barrier.wait()
        rig.sampler.start_session("s2")
    threads = [threading.Thread(target=stream), threading.Thread(target=switch)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    snapshot = rig.snapshot()
    assert (snapshot.source_session_id, snapshot.active_episodes, snapshot.held_bytes) == ("s2", 0, 0)


def test_sparse_candidates_are_never_reused_for_two_journey_slots() -> None:
    rig = Rig()
    rig.sampler.begin_episode("s1", "ep1", 1.0)
    for seq, ts in enumerate((1.0, 1.5, 4.0, 9.0, 9.5, 10.0)):   # 4.0 is nearest to two quantiles
        assert rig.sampler.offer_frame(frame(seq, ts))
    evidence = rig.sampler.seal("s1", "ep1", "ev1", 10.0).evidence
    assert [f.source_timestamp for f in evidence.frames] == [1.0, 4.0, 9.0, 9.5, 10.0]


# ── deterministic interleavings ──────────────────────────────────────────────

def _ready_to_seal() -> Rig:
    rig = Rig()
    rig.play(1.5)
    rig.sampler.begin_episode("s1", "ep1", 1.0)
    rig.play(6.0)
    return rig


@pytest.mark.parametrize("cleanup", ["end_session", "cancel", "new_session"])
@pytest.mark.parametrize("order", ["seal_first", "cleanup_first"])
def test_seal_versus_cleanup_in_both_orders(order, cleanup) -> None:
    rig = _ready_to_seal()
    action = {"end_session": lambda: rig.sampler.end_session("s1"),
              "cancel": lambda: rig.sampler.cancel_episode("s1", "ep1"),
              "new_session": lambda: rig.sampler.start_session("s2")}[cleanup]
    if order == "seal_first":
        result = rig.sampler.seal("s1", "ep1", "ev1", 6.0)
        assert result.failure is None and len(result.evidence.frames) == 5
        action()
        survives = cleanup == "cancel"             # sealed evidence outlives its episode, not the session
        assert (rig.sampler.plan_for("ev1") is not None) is survives
    else:
        action()
        result = rig.sampler.seal("s1", "ep1", "ev1", 6.0)
        assert result.evidence is None and result.failure is EvidenceFailure.UNKNOWN_EPISODE
        assert rig.sampler.plan_for("ev1") is None
    assert rig.snapshot().active_episodes == 0
    assert_ownership(rig.sampler)


@pytest.mark.parametrize("order", ["fetch_first", "seal_first"])
def test_seal_versus_bundle_retrieval_in_both_orders(order) -> None:
    rig = _ready_to_seal()
    guessed = FrameSelectionPlan("ev1", "s1", DomainName.PARKING_MOTORCYCLE, 6.0,
                                 (-5.0, -3.0, -2.0, -1.0, 0.0), EVIDENCE_OFFSET_EPSILON, 5)
    if order == "fetch_first":
        assert rig.sampler.plan_for("ev1") is None
        assert rig.sampler.wait_for_bundle(guessed).status is BundleStatus.INSUFFICIENT_HISTORY
    assert rig.sampler.seal("s1", "ep1", "ev1", 6.0).failure is None
    plan = rig.sampler.plan_for("ev1")
    decision = rig.sampler.wait_for_bundle(plan)
    assert decision.status is BundleStatus.READY and EvaluationWorker._valid_bundle(decision.bundle, plan)
    assert rig.sampler.wait_for_bundle(guessed).status is BundleStatus.INVALID_PLAN
    assert_ownership(rig.sampler)


def _race(*actions):
    barrier, results, errors = threading.Barrier(len(actions), timeout=5), {}, []

    def run(name, action):
        try:
            barrier.wait()
            results[name] = action()
        except Exception as error:        # pragma: no cover - reported below
            errors.append(error)
    threads = [threading.Thread(target=run, args=item) for item in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert errors == []
    return results


def test_racing_seal_and_cleanup_yield_only_the_two_valid_outcomes() -> None:
    for _round in range(40):
        rig = _ready_to_seal()
        results = _race(("seal", lambda: rig.sampler.seal("s1", "ep1", "ev1", 6.0)),
                        ("stop", lambda: rig.sampler.end_session("s1")))
        seal = results["seal"]
        assert (seal.evidence is None) != (seal.failure is None)
        if seal.evidence is not None:
            assert len({f.source_frame_seq for f in seal.evidence.frames}) == 5
        else:
            assert seal.failure is EvidenceFailure.UNKNOWN_EPISODE
        snapshot = rig.snapshot()                 # Stop always wins in the end
        assert (snapshot.sealed, snapshot.active_episodes, snapshot.held_bytes) == (0, 0, 0)
        assert_ownership(rig.sampler)


def test_racing_seal_and_bundle_retrieval_never_return_partial_bundles() -> None:
    for _round in range(40):
        rig = _ready_to_seal()

        def fetch():
            plan = rig.sampler.plan_for("ev1")
            return None if plan is None else rig.sampler.wait_for_bundle(plan)
        results = _race(("seal", lambda: rig.sampler.seal("s1", "ep1", "ev1", 6.0)), ("fetch", fetch))
        assert results["seal"].failure is None
        decision = results["fetch"]
        if decision is not None:                  # fetched after the seal: complete and consistent
            assert decision.status is BundleStatus.READY
            assert [f.source_frame_seq for f in decision.bundle.frames] == [
                f.source_frame_seq for f in results["seal"].evidence.frames]
        assert_ownership(rig.sampler)
