"""Synthetic TrackingSnapshot tests for the retail display-zone trigger core.

No video, model, GPU or network: tracks, domain snapshots and the clock are fakes.
Frame is 1000x1000 so pixel values read as thousandths of the frame.
"""

from __future__ import annotations

import dataclasses
import math
import threading

import pytest

from app.models.domain import DomainName, DomainSnapshot, DomainState
from app.models.retail_candidate import (
    RetailCandidateEvent, RetailCandidateEvidence, RetailCandidateState as State, RetailZone,
    parse_retail_zones, validate_retail_zones,
)
from app.models.tracking import TrackedDetection, TrackingSnapshot
from app.services.retail_candidate_trigger import RetailCandidateTrigger

W = H = 1000
# Zone A covers x 0-400, zone B x 600-1000; x 400-600 is outside every zone.
ZONES = (RetailZone("rak_kiri", 0.0, 0.0, 0.4, 1.0), RetailZone("rak_kanan", 0.6, 0.0, 1.0, 1.0))


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def person(track_id: int, x: float, *, y: float = 300, w: float = 100, h: float = 300,
           confidence: float = 0.9, confirmed: bool = True, label: str = "person") -> TrackedDetection:
    box = (x, y, x + w, y + h)
    return TrackedDetection(track_id, 0, label, confidence, box, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
                            "s1", 0, 0.0, confirmed, 5)


def domain(session: str = "s1", name: DomainName = DomainName.RETAIL_STORE,
           state: DomainState = DomainState.LOCKED) -> DomainSnapshot:
    return DomainSnapshot(session, state, name, 0, 0, 0.0, state is DomainState.LOCKED, None)


class Rig:
    def __init__(self, *, clock_offset: float = 0.0, **overrides: object) -> None:
        # control clock = video timestamp + clock_offset while frames arrive
        self.clock = FakeClock()
        self.clock_offset = clock_offset
        self.trigger = RetailCandidateTrigger(ZONES, clock=self.clock, **overrides)
        self.trigger.start_session("s1")
        self.seq = 0

    def feed(self, ts: float, *tracks: TrackedDetection, seq: int | None = None,
             session: str = "s1", domain_snapshot: DomainSnapshot | None = None) -> None:
        self.seq = self.seq + 1 if seq is None else seq
        self.clock.now = ts + self.clock_offset
        stamped = tuple(dataclasses.replace(t, source_session_id=session, source_frame_seq=self.seq,
                                            source_timestamp=ts) for t in tracks)
        self.trigger.observe(domain_snapshot or domain(),
                             TrackingSnapshot(session, self.seq, ts, W, H, stamped))

    def series(self, start: float, end: float, *tracks_at: object, step: float = 0.25) -> None:
        """Feed every step from start to end (inclusive); tracks_at are tracks or callables(ts)."""
        count = round((end - start) / step)
        for index in range(count + 1):
            ts = start + index * step
            self.feed(ts, *(t(ts) if callable(t) else t for t in tracks_at))

    def episodes(self):
        return self.trigger.get_snapshot().episodes

    def episode(self):
        (only,) = self.episodes()
        return only

    def has_candidate(self) -> bool:
        return self.trigger.wait_for_candidate(0)


# ── configuration: ROI is mandatory and fail-closed ──────────────────────────

def test_parse_valid_zones() -> None:
    zones = parse_retail_zones('[{"name": "rak_kiri", "rect": [0, 0, 0.4, 1]},'
                               ' {"name": "rak-2", "rect": [0.6, 0.1, 1.0, 0.9]}]')
    assert zones is not None and [z.name for z in zones] == ["rak_kiri", "rak-2"]
    assert (zones[0].x2, zones[1].y1) == (0.4, 0.1)


def _zone_json(*rects: object, names: tuple[str, ...] | None = None) -> str:
    names = names or tuple(f"z{i}" for i in range(len(rects)))
    import json
    return json.dumps([{"name": n, "rect": r} for n, r in zip(names, rects)])


@pytest.mark.parametrize("raw", [
    None, 123, b"[]", "", "not json", "{}", "[]", "[1]",
    _zone_json(*([[0, 0, 0.5, 0.5]] * 9)),                       # more than 8 zones
    _zone_json([0, 0, 0.5, 0.5], [0.5, 0.5, 1, 1], names=("a", "a")),
    _zone_json([0, 0, 0.5, 0.5], names=("Rak",)),
    _zone_json([0, 0, 0.5, 0.5], names=("a" * 33,)),
    _zone_json([0, 0, 0.5, 0.5], names=("rak kiri",)),
    _zone_json([0, 0, 0.5]),
    _zone_json([0, 0, True, 1]),
    _zone_json(["0", 0, 1, 1]),
    '[{"name": "a", "rect": [NaN, 0, 1, 1]}]',
    '[{"name": "a", "rect": [0, 0, Infinity, 1]}]',
    '[{"name": "a", "rect": [0, 0, 1e999, 1]}]',
    _zone_json([0.5, 0, 0.4, 1]),                                # inverted
    _zone_json([0, 0, 1.1, 1]),                                  # outside the frame
    _zone_json([-0.1, 0, 0.5, 1]),
    _zone_json([0, 0, 0.05, 0.1]),                               # area below 0.01
    '[{"name": "a", "rect": [0, 0, 1, 1], "extra": 1}]',
    '[{"name": "a"}]',
    '[{"name": "a", "rect": [0, 0, 1, 1]}]' + " " * 4096,        # oversized config
])
def test_parse_rejects_every_invalid_config(raw) -> None:
    assert parse_retail_zones(raw) is None


@pytest.mark.parametrize("zones", [
    (), [], None, "rak", ({"name": "a", "rect": [0, 0, 1, 1]},),
    tuple(RetailZone(f"z{i}", 0, 0, 0.5, 0.5) for i in range(9)),
    (RetailZone("a", 0, 0, 0.5, 0.5), RetailZone("a", 0.5, 0.5, 1, 1)),
])
def test_trigger_requires_valid_zones(zones) -> None:
    with pytest.raises(ValueError):
        RetailCandidateTrigger(zones)


def test_zone_rejects_bool_and_non_finite_coordinates() -> None:
    for coordinates in ((0, 0, True, 1), (0, 0, math.nan, 1), (0, 0, math.inf, 1)):
        with pytest.raises(ValueError):
            RetailZone("a", *coordinates)
    assert validate_retail_zones([ZONES[0]]) == (ZONES[0],)


@pytest.mark.parametrize("overrides", [
    {"enter_overlap": True}, {"enter_overlap": math.nan}, {"enter_overlap": math.inf},
    {"enter_overlap": 0.0}, {"enter_overlap": 1.5},
    {"exit_overlap": 0.0}, {"exit_overlap": 0.2}, {"exit_overlap": 0.3},
    {"engage_observations": 0}, {"engage_observations": True}, {"engage_observations": 2.0},
    {"engage_seconds": -1}, {"engage_seconds": 5.0},              # above trigger_dwell_seconds
    {"trigger_dwell_seconds": 0}, {"trigger_dwell_seconds": math.inf},
    {"max_sample_gap_seconds": 0}, {"max_sample_gap_seconds": 2.0},   # above the tolerance
    {"lost_tolerance_seconds": math.inf}, {"lost_tolerance_seconds": False},
    {"relink_distance": 0}, {"relink_area_ratio": 0.5}, {"max_relinks": -1},
    {"candidate_ttl_seconds": 0}, {"zone_cooldown_seconds": -1},
    {"global_request_interval_seconds": math.nan}, {"max_episodes": 0}, {"max_episodes": 257},
    {"sequence_limit": 0}, {"min_person_confidence": 1.5}, {"min_person_confidence": "0.5"},
    {"max_known_tracks": 0}, {"max_known_tracks": True}, {"max_known_tracks": 1025},
    {"clock": "monotonic"},
])
def test_every_parameter_is_validated(overrides) -> None:
    with pytest.raises(ValueError):
        RetailCandidateTrigger(ZONES, **overrides)


def test_parameter_relations_accept_their_boundaries() -> None:
    RetailCandidateTrigger(ZONES, enter_overlap=0.2, exit_overlap=0.19,
                           max_sample_gap_seconds=1.5, lost_tolerance_seconds=1.5,
                           engage_seconds=3.0, trigger_dwell_seconds=3.0)


# ── gating and eligibility ───────────────────────────────────────────────────

@pytest.mark.parametrize("snapshot", [
    domain(name=DomainName.PARKING_MOTORCYCLE), domain(name=DomainName.UNKNOWN),
    domain(state=DomainState.PROBING), domain(session="other"),
])
def test_only_a_locked_retail_domain_of_this_session_is_observed(snapshot) -> None:
    rig = Rig()
    for index in range(17):
        rig.feed(index * 0.25, person(1, 100), domain_snapshot=snapshot)
    assert rig.episodes() == () and not rig.has_candidate()


def test_foreign_or_missing_session_is_ignored() -> None:
    rig = Rig()
    rig.series(0.0, 4.0, lambda ts: person(1, 100), step=0.25)  # warm-up of the default session
    rig.trigger.end_session("s1")
    rig.feed(5.0, person(1, 100))
    assert rig.trigger.get_snapshot().source_session_id is None and rig.episodes() == ()
    rig.trigger.start_session("s2")
    rig.feed(6.0, person(1, 100), session="s1")               # old session snapshot
    assert rig.episodes() == ()


@pytest.mark.parametrize("track", [
    person(1, 100, confirmed=False), person(1, 100, confidence=0.4), person(1, 100, label="handbag"),
    person(1, 100, w=-10), person(1, math.nan),
])
def test_ineligible_tracks_never_start_episodes(track) -> None:
    rig = Rig()
    rig.series(0.0, 4.0, track)
    assert rig.episodes() == () and not rig.has_candidate()


def test_duplicate_track_ids_in_one_snapshot_keep_one_episode() -> None:
    rig = Rig()
    rig.feed(0.0, person(1, 100), person(1, 120))
    assert len(rig.episodes()) == 1


# ── trigger while the interaction is still going on ─────────────────────────

def test_passerby_shorter_than_dwell_never_triggers() -> None:
    rig = Rig()
    rig.series(0.0, 1.0, person(1, 100))
    rig.feed(1.25, person(1, 450))                             # walked out of every zone
    assert rig.episodes() == ()
    rig.series(1.5, 6.0, person(1, 450))
    assert not rig.has_candidate() and rig.trigger.get_snapshot().candidates_created == 0


def test_dwell_reaching_the_threshold_triggers_while_still_inside() -> None:
    rig = Rig()
    rig.series(0.0, 2.75, person(1, 100))
    assert not rig.has_candidate() and rig.episode().state is State.ENGAGED
    rig.feed(3.0, person(1, 100))                              # still inside: no exit needed
    assert rig.has_candidate()
    event = rig.trigger.claim_next_candidate(3.0)
    assert event is not None and event.state is State.API_PENDING
    assert (event.domain, event.object_track_id, event.person_track_id) == (DomainName.RETAIL_STORE, None, 1)
    assert (event.started_at, event.triggered_at, event.trigger_source_frame_seq) == (0.0, 3.0, 13)
    evidence = event.evidence
    assert (evidence.zone_name, evidence.zone_index, evidence.dwell_in_zone_seconds) == ("rak_kiri", 0, 3.0)
    assert (evidence.observations_in_zone, evidence.max_observation_gap_seconds) == (13, 0.25)
    assert (evidence.mean_overlap, evidence.max_overlap, evidence.concurrent_episodes_in_zone) == (1.0, 1.0, 1)
    assert event.candidate_frame_sequences == tuple(range(1, 14))


def test_engagement_needs_observations_and_seconds() -> None:
    rig = Rig(engage_observations=3, engage_seconds=1.0)
    rig.series(0.0, 0.75, person(1, 100))
    assert rig.episode().state is State.ENGAGING
    rig.feed(1.0, person(1, 100))
    assert rig.episode().state is State.ENGAGED


# ── zone selection and hysteresis ────────────────────────────────────────────

def test_zone_choice_is_deterministic() -> None:
    rig = Rig()
    rig.feed(0.0, person(1, 300, w=400), person(2, 320, w=400, y=0))
    zones = {view.active_track_id: view.zone_name for view in rig.episodes()}
    assert zones == {1: "rak_kiri", 2: "rak_kanan"}           # tie -> config order; else max overlap


def test_episode_keeps_its_zone_while_engagement_is_valid() -> None:
    rig = Rig()
    rig.series(0.0, 1.0, person(1, 100))
    first = rig.episode().episode_id
    rig.feed(1.25, person(1, 385))                             # 0.15 in A: hysteresis band
    rig.feed(1.5, person(1, 360, w=300))                       # A 0.13 >= exit, B 0.20 larger
    view = rig.episode()
    assert (view.episode_id, view.zone_name, view.dwell_seconds) == (first, "rak_kiri", 1.5)
    rig.feed(1.75, person(1, 380, w=300))                      # A 0.07 < exit: leaves A, enters B
    view = rig.episode()
    assert view.zone_name == "rak_kanan" and view.episode_id != first and view.dwell_seconds == 0.0


def test_hysteresis_band_does_not_engage_a_new_episode() -> None:
    rig = Rig()
    rig.series(0.0, 4.0, person(1, 385))                       # only 0.15 inside from the start
    assert rig.episodes() == ()                                # below enter: never starts


# ── gaps, tolerance and returning IDs ────────────────────────────────────────

@pytest.mark.parametrize("gap,empty_frames,same_episode,dwell", [
    (0.5, False, True, 1.5),      # == max_sample_gap: counted as presence
    (0.75, False, True, 1.0),     # above it: continues, adds nothing (not even 0.5)
    (0.75, True, True, 1.0),      # same, with frames in between that miss the track
    (1.5, True, True, 1.0),       # == lost tolerance: still the same episode
    (1.75, False, False, 0.0),    # above tolerance: same ID returns as a new episode
    (1.75, True, False, 0.0),
])
def test_observation_gap_rules(gap, empty_frames, same_episode, dwell) -> None:
    rig = Rig()
    rig.series(0.0, 1.0, person(1, 100))
    first = rig.episode().episode_id
    if empty_frames:
        ts = 1.25
        while ts < 1.0 + gap:
            rig.feed(ts)
            ts += 0.25
    rig.feed(1.0 + gap, person(1, 100))
    view = rig.episode()
    assert (view.episode_id == first) is same_episode
    assert view.dwell_seconds == dwell
    assert rig.trigger.get_snapshot().gap_resets == (0 if same_episode else 1)
    if not same_episode:
        assert view.state is State.ENGAGING


def test_gap_above_sample_tolerance_restarts_engagement_counting() -> None:
    rig = Rig(engage_observations=3, engage_seconds=0.5)
    rig.feed(0.0, person(1, 100))
    rig.feed(0.25, person(1, 100))
    rig.feed(1.0, person(1, 100))                              # gap 0.75: engagement restarts
    rig.feed(1.25, person(1, 100))
    assert rig.episode().state is State.ENGAGING
    rig.series(1.5, 1.5, person(1, 100))
    assert rig.episode().state is State.ENGAGED


def test_skipped_sequences_use_timestamps_and_disorder_is_ignored() -> None:
    rig = Rig()
    for index in range(5):
        rig.feed(index * 0.25, person(1, 100), seq=1 + index * 50)
    assert rig.episode().dwell_seconds == 1.0
    rig.feed(1.25, person(1, 100), seq=201)                    # sequence did not advance
    rig.feed(0.5, person(1, 100), seq=500)                     # timestamp went back
    assert rig.episode().dwell_seconds == 1.0


# ── relinking: spatial and temporal continuity, never zone alone ─────────────

def test_unambiguous_new_track_continues_the_episode() -> None:
    rig = Rig()
    rig.series(0.0, 1.0, person(1, 100))
    first = rig.episode().episode_id
    rig.feed(1.25)                                             # occluded
    rig.feed(1.75, person(5, 110))                             # new ID nearby, gap 0.75
    view = rig.episode()
    assert (view.episode_id, view.active_track_id, view.relink_count, view.dwell_seconds) == (first, 5, 1, 1.0)
    rig.series(2.0, 3.75, person(5, 110))
    event = rig.trigger.claim_next_candidate(3.75)
    assert event is not None and event.person_track_id == 5
    assert (event.evidence.initial_person_track_id, event.evidence.active_person_track_id,
            event.evidence.relink_count) == (1, 5, 1)


@pytest.mark.parametrize("case", ["far", "late", "size", "known-id"])
def test_relink_requires_every_continuity_condition(case) -> None:
    rig = Rig()
    outsider = person(0, 450)
    rig.series(0.0, 1.0, person(1, 100), outsider)
    first = rig.episode().episode_id
    returning = {
        "far": (1.5, person(5, 300)),                          # 0.14 of the diagonal away
        "late": (2.75, person(5, 110)),                        # gap 1.75 > tolerance
        "size": (1.5, person(5, 100, y=100, h=700)),           # same center, area x2.3
        "known-id": (1.5, person(0, 110)),                     # an existing track walks in
    }[case]
    rig.feed(*returning)
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.relinks == 0
    in_zone = [view for view in snapshot.episodes if view.zone_name == "rak_kiri"]
    assert all(view.episode_id != first or view.active_track_id == 1 for view in in_zone)
    assert any(view.active_track_id == returning[1].track_id and view.episode_id != first for view in in_zone)


def test_edge_truncated_boxes_skip_only_the_size_check() -> None:
    rig = Rig()
    rig.series(0.0, 1.0, person(1, 0))                         # touches the left frame edge
    rig.feed(1.5, person(5, 0, w=30))                          # area ratio 3.3 but truncated
    view = rig.episode()
    assert (view.active_track_id, view.relink_count) == (5, 1)
    rig.series(1.75, 3.5, person(5, 0, w=30))
    event = rig.trigger.claim_next_candidate(3.5)
    # The 0.5 s relink gap counts as presence, so 3.0 s is reached at t=3.0 (12 observations).
    assert event is not None and event.triggered_at == 3.0
    assert event.evidence.edge_truncated_observations == 12


def test_ambiguous_relinks_never_merge_people() -> None:
    rig = Rig()
    rig.series(0.0, 1.0, person(1, 100), person(2, 150))
    rig.feed(1.5, person(7, 125))                              # near both lost episodes
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.relinks == 0 and snapshot.ambiguous_relinks == 1
    assert {view.active_track_id for view in snapshot.episodes} == {1, 2, 7}

    rig = Rig()
    rig.series(0.0, 1.0, person(1, 100))
    rig.feed(1.5, person(7, 105), person(8, 95))               # two new tracks near one episode
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.relinks == 0 and snapshot.ambiguous_relinks == 2
    assert {view.active_track_id for view in snapshot.episodes} == {1, 7, 8}


# ── atomic zone reservation, cooldown and TTL ────────────────────────────────

def test_two_subjects_crossing_together_reserve_the_zone_once() -> None:
    rig = Rig()
    rig.series(0.0, 3.0, person(1, 50), person(2, 200))
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.candidates_created == 1 and snapshot.zone_reservation_conflicts == 1
    states = {view.active_track_id: view.state for view in snapshot.episodes}
    assert states == {1: State.CANDIDATE, 2: State.ENGAGED}
    event = rig.trigger.claim_next_candidate(3.0)
    assert event.person_track_id == 1 and event.evidence.concurrent_episodes_in_zone == 2
    assert rig.trigger.complete(event.event_id, 3.0)
    assert rig.trigger.get_snapshot().zones[0].cooldown_until == 63.0
    first_retrigger = None
    ts = 3.25
    while ts <= 64.0 and first_retrigger is None:
        rig.feed(ts, person(1, 50), person(2, 200))
        if rig.has_candidate():
            first_retrigger = ts
        ts += 0.25
    assert first_retrigger == 63.0                             # other subject, after zone cooldown
    assert rig.trigger.get_snapshot().zone_reservation_conflicts == 1


def test_expired_candidate_releases_its_zone_into_cooldown_without_retry() -> None:
    rig = Rig()
    rig.series(0.0, 3.0, person(1, 100), person(2, 700))
    assert rig.trigger.get_snapshot().candidates_created == 2  # one per zone
    first = rig.trigger.claim_next_candidate(3.0)
    assert first.evidence.zone_name == "rak_kiri"
    assert rig.trigger.claim_next_candidate(3.0) is None       # one pending at a time
    assert rig.trigger.complete(first.event_id, 3.0)
    rig.clock.now = 3.999999                                   # no observation: just before the deadline
    assert rig.has_candidate() and rig.trigger.claim_next_candidate(4.0) is None   # global interval
    rig.clock.now = 4.0                                        # expires exactly at the deadline
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.candidates_expired == 1 and not rig.has_candidate()
    assert snapshot.zones[1] == dataclasses.replace(snapshot.zones[1], reserved_episode_id=None,
                                                    cooldown_until=64.0)
    expired = next(view for view in snapshot.episodes if view.zone_name == "rak_kanan")
    assert expired.state is State.DONE
    rig.series(4.25, 70.0, person(2, 700))                     # stays: tombstone, no retry
    assert rig.trigger.get_snapshot().candidates_created == 2
    rig.feed(70.25, person(2, 450))                            # leaves, then re-enters later
    rig.series(70.5, 73.5, person(2, 700))
    assert rig.trigger.get_snapshot().candidates_created == 3 and rig.has_candidate()


def test_ttl_and_cooldown_progress_without_new_observations() -> None:
    rig = Rig(candidate_ttl_seconds=1.0)
    rig.series(0.0, 3.0, person(1, 100))
    rig.clock.now = 3.5
    event = rig.trigger.claim_next_candidate(0.0)              # 'now' is not used for decisions
    assert event is not None
    assert rig.trigger.fail(event.event_id, 0.0)
    assert rig.trigger.get_snapshot().zones[0].cooldown_until == 63.5   # from the injected clock

    rig = Rig(candidate_ttl_seconds=1.0)
    rig.series(0.0, 3.0, person(1, 100))
    rig.clock.now = 4.5
    assert not rig.has_candidate()
    assert rig.trigger.claim_next_candidate(4.5) is None
    assert rig.trigger.get_snapshot().candidates_expired == 1


def test_claim_complete_fail_contract() -> None:
    rig = Rig(candidate_ttl_seconds=10.0, global_request_interval_seconds=5.0)  # deadline 13.0
    rig.series(0.0, 3.0, person(1, 100), person(2, 700))
    first = rig.trigger.claim_next_candidate(3.0)
    assert not rig.trigger.complete("other", 3.0) and not rig.trigger.fail("other", 3.0)
    rig.clock.now = 5.0
    assert rig.trigger.fail(first.event_id, 5.0)
    assert not rig.trigger.complete(first.event_id, 5.0)       # already finished
    assert rig.trigger.get_snapshot().zones[0].cooldown_until == 65.0
    rig.clock.now = 7.99
    assert rig.trigger.claim_next_candidate(7.99) is None     # global interval from the claim
    rig.clock.now = 8.0
    second = rig.trigger.claim_next_candidate(8.0)
    assert second is not None and second.evidence.zone_name == "rak_kanan"
    for bad in (math.nan, math.inf, True, "1"):
        with pytest.raises(ValueError):
            rig.trigger.claim_next_candidate(bad)
        with pytest.raises(ValueError):
            rig.trigger.complete(second.event_id, bad)


def test_one_candidate_per_episode_even_after_the_zone_cooldown() -> None:
    rig = Rig(zone_cooldown_seconds=10.0)
    rig.series(0.0, 3.0, person(1, 100))
    event = rig.trigger.claim_next_candidate(3.0)
    assert rig.trigger.complete(event.event_id, 3.0)
    rig.series(3.25, 30.0, person(1, 100))
    assert rig.trigger.get_snapshot().candidates_created == 1
    assert rig.episode().state is State.DONE
    rig.feed(30.25, person(1, 450))
    rig.series(30.5, 33.5, person(1, 100))
    assert rig.trigger.get_snapshot().candidates_created == 2


def test_reserved_and_claimed_episodes_survive_loss_until_resolved() -> None:
    rig = Rig(candidate_ttl_seconds=5.0)
    rig.series(0.0, 3.0, person(1, 100))
    rig.series(3.25, 5.0)                                      # subject disappears for 2 s
    assert rig.episode().state is State.CANDIDATE and rig.episode().lost
    event = rig.trigger.claim_next_candidate(5.25)
    assert event is not None
    rig.series(5.25, 9.0)
    assert rig.episode().state is State.API_PENDING
    assert rig.trigger.complete(event.event_id, 9.0)
    rig.feed(9.25)
    assert rig.episodes() == ()                                # DONE and long gone: discarded


def test_candidate_leaving_its_zone_keeps_the_reservation_until_ttl() -> None:
    rig = Rig()
    rig.series(0.0, 3.0, person(1, 100))
    rig.feed(3.25, person(1, 450))
    assert rig.episode().state is State.CANDIDATE
    assert rig.trigger.get_snapshot().zones[0].reserved_episode_id == rig.episode().episode_id


# ── lifecycle, bounds and contracts ──────────────────────────────────────────

def test_session_lifecycle_and_close() -> None:
    rig = Rig()
    rig.series(0.0, 3.0, person(1, 100))
    event = rig.trigger.claim_next_candidate(3.0)
    rig.trigger.end_session("other")                           # not the active session
    assert rig.trigger.get_snapshot().pending_event_id == event.event_id
    rig.trigger.end_session("s1")
    snapshot = rig.trigger.get_snapshot()
    assert (snapshot.source_session_id, snapshot.pending_event_id, snapshot.episodes) == (None, None, ())
    assert not rig.trigger.complete(event.event_id, 3.0)
    rig.trigger.start_session("s2")
    rig.feed(4.0, person(1, 100), session="s1")
    assert rig.episodes() == ()
    rig.trigger.close()
    rig.trigger.close()
    rig.trigger.start_session("s3")
    assert rig.trigger.get_snapshot().source_session_id is None
    assert rig.trigger.claim_next_candidate(5.0) is None and not rig.has_candidate()


class _ParkedSignalCondition(threading.Condition):
    """Test instrumentation: signals when a waiter is about to park."""

    def __init__(self, lock) -> None:
        super().__init__(lock)
        self.parked = threading.Event()

    def wait(self, timeout=None):
        self.parked.set()              # still holding the lock; wait() releases it
        return super().wait(timeout)


@pytest.mark.parametrize("interrupt", ["start", "close", "wake"])
def test_wait_for_candidate_is_interrupted(interrupt) -> None:
    rig = Rig()
    condition = _ParkedSignalCondition(rig.trigger._lock)
    rig.trigger._condition = condition
    results: list[bool] = []
    waiter = threading.Thread(target=lambda: results.append(rig.trigger.wait_for_candidate(5.0)))
    waiter.start()
    try:
        assert condition.parked.wait(2)
        {"start": lambda: rig.trigger.start_session("s2"), "close": rig.trigger.close,
         "wake": rig.trigger.wake_waiters}[interrupt]()
        waiter.join(2)
        assert not waiter.is_alive() and results == [False]
    finally:
        rig.trigger.close()            # releases a waiter left behind by a failed assertion
        waiter.join(2)


def test_memory_stays_bounded() -> None:
    rig = Rig()
    rig.feed(0.0, *(person(index, index * 9, w=8) for index in range(40)))
    snapshot = rig.trigger.get_snapshot()
    assert len(snapshot.episodes) == 32 and snapshot.dropped_capacity == 8

    rig = Rig(trigger_dwell_seconds=10.0)
    rig.series(0.0, 10.0, person(1, 100))
    event = rig.trigger.claim_next_candidate(10.0)
    assert len(event.candidate_frame_sequences) == 32 and event.candidate_frame_sequences[-1] == 41
    rig.trigger.complete(event.event_id, 10.0)
    for index in range(10_000):
        rig.feed(10.25 + index * 0.25, person(1, 100))
    assert len(rig.episodes()) == 1
    assert len(rig.trigger._episodes[rig.episode().episode_id].sequences) == 32


def test_contracts_carry_no_image_data_and_no_object_track() -> None:
    for model in (RetailCandidateEvent, RetailCandidateEvidence):
        assert all("bytes" not in str(field.type) for field in dataclasses.fields(model))
    rig = Rig()
    rig.series(0.0, 3.0, person(1, 100))
    event = rig.trigger.claim_next_candidate(3.0)
    assert "jpeg" not in repr(event).lower()
    with pytest.raises(ValueError):
        dataclasses.replace(event, object_track_id=3)
    with pytest.raises(ValueError):
        dataclasses.replace(event, domain=DomainName.PARKING_MOTORCYCLE)


def test_interface_matches_what_the_evaluation_worker_uses() -> None:
    rig = Rig()
    for name in ("start_session", "end_session", "close", "observe", "wait_for_candidate",
                 "wake_waiters", "claim_next_candidate", "complete", "fail", "get_snapshot"):
        assert callable(getattr(rig.trigger, name))
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.source_session_id == "s1" and snapshot.pending_event_id is None
    rig.series(0.0, 3.0, person(1, 100))
    event = rig.trigger.claim_next_candidate(3.0)
    for name in ("event_id", "source_session_id", "domain", "started_at", "triggered_at",
                 "person_track_id", "object_track_id", "evidence"):
        assert hasattr(event, name)
    assert rig.trigger.get_snapshot().pending_event_id == event.event_id


# ── control clock independent of video timestamps ────────────────────────────

@pytest.mark.parametrize("video_start,clock_start", [(0.0, 1000.0), (5000.0, 10.0)])
def test_ttl_uses_the_control_clock_for_any_offset(video_start, clock_start) -> None:
    def candidate_rig() -> Rig:
        rig = Rig(clock_offset=clock_start - video_start)
        rig.series(video_start, video_start + 3.0, person(1, 100))
        return rig

    rig = candidate_rig()
    assert rig.has_candidate()
    rig.clock.now = clock_start + 3.999                        # no new observation
    assert rig.has_candidate()
    event = rig.trigger.claim_next_candidate(rig.clock.now)
    assert event is not None and event.triggered_at == video_start + 3.0   # observation time
    rig.clock.now = clock_start + 3.999 + 1.0
    assert rig.trigger.complete(event.event_id, rig.clock.now)
    assert rig.trigger.get_snapshot().zones[0].cooldown_until == pytest.approx(clock_start + 64.999)

    rig = candidate_rig()
    rig.clock.now = clock_start + 4.0                          # exactly the deadline
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.candidates_expired == 1 and not rig.has_candidate()
    assert rig.trigger.claim_next_candidate(rig.clock.now) is None
    assert snapshot.zones[0].cooldown_until == clock_start + 64.0


# ── relink eligibility from recent identity history, not ID order ────────────

def test_lower_tentative_id_confirmed_after_a_higher_id_can_relink() -> None:
    rig = Rig()
    rig.series(0.0, 1.0, person(1, 100), person(9, 450))       # ID 9 seen first, outside
    first = rig.episode().episode_id
    rig.feed(1.25, person(3, 110, confirmed=False), person(9, 450))   # tentative: not an identity yet
    rig.feed(1.5, person(3, 110), person(9, 450))              # lower ID confirmed now
    view = next(v for v in rig.episodes() if v.zone_name == "rak_kiri")
    assert (view.episode_id, view.active_track_id, view.relink_count) == (first, 3, 1)


def test_identity_ages_out_after_the_tolerance_window() -> None:
    rig = Rig()
    rig.feed(0.0, person(4, 450))                              # seen once, then gone
    rig.series(0.25, 1.0, person(1, 100))
    rig.feed(1.25)
    rig.feed(1.75, person(4, 110))                             # last seen 1.75 s ago: aged out
    view = rig.episode()
    assert (view.active_track_id, view.relink_count) == (4, 1)


def test_identity_eviction_suppresses_relink_until_history_is_complete() -> None:
    rig = Rig(max_known_tracks=2)
    rig.series(0.0, 1.0, person(1, 100), person(5, 420, y=0), person(6, 420, y=600))
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.identity_evictions > 0
    rig.feed(1.25)
    rig.feed(1.5, person(7, 110))                              # evicted IDs may still be alive
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.relinks == 0 and snapshot.relinks_suppressed == 1
    assert {view.active_track_id for view in snapshot.episodes} == {1, 7}
    rig.series(1.75, 3.0, person(7, 110))                      # last eviction at 1.0: window ends 2.5
    rig.feed(3.25)
    rig.feed(3.5, person(8, 115))
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.relinks == 1
    assert any(view.active_track_id == 8 and view.relink_count == 1 for view in snapshot.episodes)


def test_identity_history_stays_bounded() -> None:
    rig = Rig(max_known_tracks=8)
    for index in range(200):
        rig.feed(index * 0.25, person(index, 450))
    assert len(rig.trigger._known_tracks) <= 8
