"""Synthetic tests for the private-parking trigger core (T1). No video, model or network.

Frame is 1000x1000. Access line y=0.5 from x=0.2 to x=0.8; inside is below (y > 0.5).
Motor zone z1 = [0.55, 0.6, 0.95, 0.95]; parked motorcycle #100 sits in it.
"""

from __future__ import annotations

from collections import deque
import dataclasses
import json
import math
import threading

import pytest

from app.models.domain import DomainName, DomainSnapshot, DomainState
from app.models.private_parking import (
    AuthorizationSignal, EpisodeState, PrivateParkingCandidate, ProximityEvidence, LocalProximityAlert,
    make_thresholds, parse_private_parking_config,
)
from app.models.tracking import TrackedDetection, TrackingSnapshot
from app.services.private_parking_trigger import PrivateParkingTrigger

W = H = 1000
CONFIG = {"line": [0.2, 0.5, 0.8, 0.5], "inside_point": [0.5, 0.8],
          "zones": [{"name": "z1", "rect": [0.55, 0.6, 0.95, 0.95]}]}
ZONE_FOOT = (0.645, 0.88)      # person foot inside z1, next to motor #100


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0       # deliberately unrelated to video timestamps

    def __call__(self) -> float:
        return self.now


class FakeAuthorization:
    def __init__(self) -> None:
        self.queue: deque[AuthorizationSignal] = deque()
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()

    def signals(self, session_id: str):
        pending = tuple(self.queue)
        self.queue.clear()
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("gate never released")
        return pending


def person(track_id: int, fx: float, fy: float, *, w: float = 0.08, h: float = 0.3,
           confirmed: bool = True, confidence: float = 0.9) -> TrackedDetection:
    box = ((fx - w / 2) * W, (fy - h) * H, (fx + w / 2) * W, fy * H)
    return TrackedDetection(track_id, 0, "person", confidence, box,
                            ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "s1", 0, 0.0, confirmed, 5)


def motor(track_id: int, fx: float, fy: float, *, w: float = 0.15, h: float = 0.15) -> TrackedDetection:
    box = ((fx - w / 2) * W, (fy - h) * H, (fx + w / 2) * W, fy * H)
    return TrackedDetection(track_id, 3, "motorcycle", 0.9, box,
                            ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "s1", 0, 0.0, True, 5)


PARKED = motor(100, 0.775, 0.90)


def domain(session: str = "s1", name: DomainName = DomainName.PARKING_MOTORCYCLE) -> DomainSnapshot:
    return DomainSnapshot(session, DomainState.LOCKED, name, 0, 0, 0.0, True, None)


class Rig:
    def __init__(self, *, parked=(PARKED,), thresholds: dict | None = None, authorization=True) -> None:
        raw = dict(CONFIG, thresholds=thresholds or {})
        self.config = parse_private_parking_config(json.dumps(raw))
        assert self.config is not None
        self.clock = FakeClock()
        self.auth = FakeAuthorization() if authorization else None
        self.trigger = PrivateParkingTrigger(self.config, clock=self.clock, authorization=self.auth)
        self.trigger.start_session("s1")
        self.parked = parked
        self.seq = 0
        self.ts = 0.0

    def feed(self, *tracks, dt: float = 0.2, session: str = "s1", domain_snapshot=None) -> None:
        self.seq += 1
        self.ts += dt
        all_tracks = tuple(tracks) + tuple(self.parked)
        self.trigger.observe(domain_snapshot or domain(session),
                             TrackingSnapshot(session, self.seq, self.ts, W, H, all_tracks))

    def walk_in(self, pid: int, *others, x: float = 0.5) -> None:
        for fy in (0.40, 0.45, 0.55):
            self.feed(person(pid, x, fy), *others)

    def approach(self, pid: int, n: int = 3, *others, foot=ZONE_FOOT) -> None:
        for _ in range(n):
            self.feed(person(pid, *foot), *others)

    def count(self, name: str) -> int:
        return self.trigger.get_snapshot().count(name)

    def episodes(self):
        return self.trigger.get_snapshot().episodes


# ── configuration ────────────────────────────────────────────────────────────

def test_valid_configuration_and_thresholds() -> None:
    config = parse_private_parking_config(json.dumps(dict(CONFIG, thresholds={"approach_observations": 4})))
    assert config is not None and config.zones[0].name == "z1"
    assert config.thresholds.approach_observations == 4 and config.thresholds.line_hysteresis == 0.01
    assert config.line.signed_distance(0.5, 0.8) > 0 > config.line.signed_distance(0.5, 0.2)


def _variant(**changes) -> str:
    document = json.loads(json.dumps(CONFIG))
    document.update(changes)
    return json.dumps(document)


@pytest.mark.parametrize("raw", [
    None, 5, "", "not json", "[]",
    _variant(line=[0.2, 0.5, 0.8]), _variant(line=[0.2, 0.5, 0.8, True]), _variant(line=[0.2, 0.5, 1.2, 0.5]),
    _variant(line=[0.5, 0.5, 0.52, 0.5]),                                  # degenerate
    _variant(inside_point=[0.5, 0.5]), _variant(inside_point=[0.5, 0.505]),   # on / too close to the line
    _variant(zones=[]), _variant(zones=[{"name": "z1", "rect": [0.9, 0.6, 0.5, 0.95]}]),
    _variant(zones=[{"name": "z1", "rect": [0.5, 0.5, 0.52, 0.52]}]),      # too small
    _variant(zones=[{"name": "z1", "rect": [0, 0, .5, .5]}, {"name": "z1", "rect": [.5, .5, 1, 1]}]),
    _variant(zones=[{"name": "Z 1", "rect": [0, 0, .5, .5]}]),
    _variant(zones=[{"name": "z1", "rect": [0, 0, .5, .5], "x": 1}]),
    _variant(extra=1),
    _variant(thresholds={"unknown": 1}), _variant(thresholds={"approach_observations": 0}),
    _variant(thresholds={"approach_observations": 2.0}), _variant(thresholds={"line_hysteresis": True}),
    _variant(thresholds={"max_crossing_gap_seconds": 2.0}),                # above lost tolerance
    _variant(thresholds={"parked_min_observations": 9}),                   # above parked history
    _variant(thresholds={"co_cross_window_seconds": 5.0}),                 # above pairing window
    '{"line": [0.2, 0.5, 0.8, NaN], "inside_point": [0.5, 0.8], "zones": [{"name": "z1", "rect": [0, 0, .5, .5]}]}',
    '{"line": [0.2, 0.5, 0.8, Infinity], "inside_point": [0.5, 0.8], "zones": []}',
    '{"line": [0.2, 0.5, 0.8, 0.5], "line": [0.2, 0.5, 0.8, 0.5], "inside_point": [0.5, 0.8], "zones": []}',
    _variant(pad="x" * 5000),
])
def test_invalid_configuration_disables_the_mode(raw) -> None:
    assert parse_private_parking_config(raw) is None


def test_trigger_requires_a_validated_config() -> None:
    with pytest.raises(ValueError):
        PrivateParkingTrigger(None)
    with pytest.raises(ValueError):
        make_thresholds(max_tracks=0)


# ── crossing and episodes ────────────────────────────────────────────────────

def test_crossing_in_starts_an_episode_and_crossing_out_ends_it() -> None:
    rig = Rig()
    rig.walk_in(1)
    (episode,) = rig.episodes()
    assert episode.person_track_id == 1 and episode.state is EpisodeState.ENTERED
    for fy in (0.45, 0.40):
        rig.feed(person(1, 0.5, fy))
    assert rig.episodes() == () and rig.count("episodes_exited") == 1


def test_jitter_inside_the_band_never_creates_extra_episodes() -> None:
    rig = Rig()
    rig.walk_in(1)
    for fy in (0.505, 0.495, 0.505, 0.495, 0.55, 0.495):
        rig.feed(person(1, 0.5, fy))
    assert len(rig.episodes()) == 1 and rig.count("episodes_started") == 1


def test_crossing_outside_the_segment_is_rejected() -> None:
    rig = Rig()
    rig.walk_in(1, x=0.9)                      # crosses the infinite line beyond P2
    assert rig.episodes() == () and rig.count("crossings_rejected_outside_segment") == 1
    rig.approach(1)
    assert rig.count("events_issued") == 0


def test_long_observation_gap_is_not_a_crossing() -> None:
    rig = Rig()
    rig.feed(person(1, 0.5, 0.40))
    rig.feed(person(1, 0.5, 0.55), dt=1.0)    # gap 1.0 s > crossing gap 0.5 s
    assert rig.episodes() == () and rig.count("crossings_rejected_gap") == 1
    rig = Rig()
    rig.feed(person(1, 0.5, 0.40))
    rig.feed(dt=2.0)                           # track lost past tolerance
    rig.feed(person(1, 0.5, 0.55))
    assert rig.episodes() == () and rig.count("unknown_inside_tracks") == 1


def test_first_seen_inside_is_unknown_origin_without_event() -> None:
    rig = Rig()
    rig.feed(person(1, 0.5, 0.7))
    rig.approach(1, 5)
    assert rig.episodes() == () and rig.count("unknown_inside_tracks") == 1
    assert rig.count("events_issued") == 0     # coverage limitation, not "safe"
    for fy in (0.45, 0.40, 0.45, 0.55):        # leaves, then re-enters through the segment
        rig.feed(person(1, 0.5, fy))
    assert len(rig.episodes()) == 1


# ── gesture authorization ────────────────────────────────────────────────────

def test_valid_gesture_authorizes_only_that_episode() -> None:
    rig = Rig()
    rig.walk_in(1)
    (episode,) = rig.episodes()
    rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, episode.episode_id))
    rig.feed(person(1, 0.5, 0.6))
    assert rig.episodes()[0].state is EpisodeState.AUTHORIZED
    rig.approach(1, 5)
    assert rig.count("events_issued") == 0 and rig.count("authorizations_applied") == 1


@pytest.mark.parametrize("make_signal", [
    lambda rig, ep: AuthorizationSignal("s1", 7, rig.ts, None),                  # track without an episode
    lambda rig, ep: AuthorizationSignal("old", 1, rig.ts, None),                 # old session
    lambda rig, ep: AuthorizationSignal("s1", 1, rig.ts, "another-episode"),     # other episode
    lambda rig, ep: AuthorizationSignal("s1", 1, rig.ts - 2.5, None),            # expired / before crossing
    lambda rig, ep: AuthorizationSignal("s1", 1, rig.ts + 5.0, None),            # from the future
    lambda rig, ep: AuthorizationSignal("s1", 1, math.nan, None),
    lambda rig, ep: AuthorizationSignal("s1", True, rig.ts, None),
    lambda rig, ep: "not a signal",
])
def test_invalid_gestures_are_rejected(make_signal) -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    (ep1, _ep2) = rig.episodes()
    rig.auth.queue.append(make_signal(rig, ep1))
    rig.feed(person(1, 0.5, 0.6), person(2, 0.3, 0.6))
    assert {view.person_track_id: view.state for view in rig.episodes()}[1] is EpisodeState.ENTERED
    assert rig.count("authorizations_rejected") == 1


def test_signal_for_an_ended_episode_is_rejected() -> None:
    rig = Rig()
    rig.walk_in(1)
    signal_time = rig.ts
    for fy in (0.45, 0.40):
        rig.feed(person(1, 0.5, fy))           # exits: episode ended
    rig.walk_in(1)                             # new episode for the same track ID
    rig.auth.queue.append(AuthorizationSignal("s1", 1, signal_time, None))
    rig.feed(person(1, 0.5, 0.6))
    assert rig.episodes()[0].state is EpisodeState.ENTERED and rig.count("authorizations_rejected") == 1


def test_track_id_change_never_inherits_authorization() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, None))
    rig.feed(person(1, 0.5, 0.6))
    assert rig.episodes()[0].state is EpisodeState.AUTHORIZED
    rig.feed(person(9, 0.5, 0.62))             # same place, new ID (tracker switch)
    rig.approach(9, 5)
    assert rig.count("events_issued") == 0     # unknown origin: no event, but also not authorized
    assert all(view.person_track_id != 9 for view in rig.episodes())


def test_authorization_after_the_event_does_not_reopen_the_episode() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)
    assert rig.count("events_issued") == 1
    rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, None))
    rig.approach(1, 5)
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.episodes[0].state is EpisodeState.TRIGGERED
    assert snapshot.count("authorizations_after_trigger") == 1 and snapshot.count("events_issued") == 1


# ── entering together with a motorcycle ──────────────────────────────────────

def _enter_with_motor(rig: Rig, motor_ids=(50,)) -> None:
    for fy in (0.40, 0.45, 0.55, 0.60, 0.65):
        rig.feed(person(1, 0.45, fy), *(motor(m, 0.50 + 0.02 * i, fy + 0.02) for i, m in enumerate(motor_ids)))


def test_motor_entering_with_the_person_is_exempt_but_others_are_not() -> None:
    rig = Rig()
    _enter_with_motor(rig)
    assert rig.episodes()[0].entered_with_motor_track_id == 50 and rig.count("escort_paired") == 1
    escorted_parked = motor(50, 0.775, 0.90)
    rig.parked = (escorted_parked,)            # the escorted motor now parks in the zone; #100 is gone
    for _ in range(10):                        # long enough for its history to read as parked
        rig.feed(person(1, 0.3, 0.8))
    rig.approach(1, 6)
    assert rig.count("events_issued") == 0
    rig.parked = (escorted_parked, motor(101, 0.62, 0.95, w=0.08))
    rig.approach(1, 6, foot=(0.57, 0.95))     # now next to another parked motorcycle
    candidate = rig.trigger.claim_next_candidate(0.0)
    assert candidate is not None and candidate.object_track_id == 101
    assert candidate.evidence.entered_with_motor_track_id == 50


def test_ambiguous_escort_gives_no_exemption() -> None:
    rig = Rig()
    _enter_with_motor(rig, motor_ids=(50, 51))
    assert rig.episodes()[0].entered_with_motor_track_id is None and rig.count("escort_ambiguous") == 1


def test_single_frame_proximity_is_not_an_escort() -> None:
    rig = Rig()
    rig.walk_in(1)
    for fy in (0.40, 0.45, 0.55):              # motor crosses 2 s later (outside the window)
        rig.feed(person(1, 0.5, 0.6), motor(50, 0.52, fy), dt=0.7)
    assert rig.episodes()[0].entered_with_motor_track_id is None and rig.count("escort_paired") == 0


# ── approach and events ──────────────────────────────────────────────────────

def test_passing_by_without_approaching_never_triggers() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1, 6, foot=(0.3, 0.8))       # inside the area, outside every zone
    rig.approach(1, 6, foot=(0.58, 0.7))      # in the zone, far from the motorcycle
    rig.approach(1, 2)                         # close only twice, then leaves
    rig.feed(person(1, 0.3, 0.8))
    rig.approach(1, 2)
    assert rig.count("events_issued") == 0


def test_unauthorized_approach_issues_one_alert_and_one_candidate_with_one_id() -> None:
    rig = Rig()
    rig.walk_in(1)
    crossed_at = rig.ts
    rig.approach(1)
    (alert,) = rig.trigger.pop_alerts()
    assert rig.trigger.pop_alerts() == ()      # popped exactly once
    candidate = rig.trigger.claim_next_candidate(0.0)
    assert isinstance(alert, LocalProximityAlert) and isinstance(candidate, PrivateParkingCandidate)
    assert alert.event_id == candidate.event_id and alert.motor_track_id == 100 and alert.zone_name == "z1"
    assert candidate.domain is DomainName.PARKING_MOTORCYCLE and candidate.started_at == crossed_at
    assert candidate.triggered_at == rig.ts and alert.issued_at == rig.clock.now
    assert isinstance(candidate.evidence, ProximityEvidence)
    for model in (LocalProximityAlert, PrivateParkingCandidate, ProximityEvidence):
        names = {field.name for field in dataclasses.fields(model)}
        assert not names & {"assessment", "status", "threat_score", "jpeg", "frames"}


def test_one_event_per_episode_even_after_claim_failure() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1, 10)
    candidate = rig.trigger.claim_next_candidate(0.0)
    assert rig.trigger.fail(candidate.event_id, 0.0)
    rig.approach(1, 10)
    assert rig.count("events_issued") == 1 and rig.trigger.claim_next_candidate(0.0) is None


def test_two_people_have_separate_episodes_and_authorization() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, None))
    rig.feed(person(1, 0.5, 0.6), person(2, 0.3, 0.6))
    for _ in range(4):
        rig.feed(person(1, 0.5, 0.6), person(2, *ZONE_FOOT))
    for _ in range(4):
        rig.feed(person(2, 0.3, 0.6), person(1, *ZONE_FOOT))
    (alert,) = rig.trigger.pop_alerts()
    assert alert.person_track_id == 2 and rig.count("events_issued") == 1


# ── candidates, alerts, timeouts and capacity ────────────────────────────────

def test_candidate_and_alert_expire_on_the_control_clock_without_observations() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)
    rig.clock.now += 1.99
    assert rig.trigger.wait_for_candidate(0)
    rig.clock.now += 0.01                      # candidate TTL 2.0 s reached
    assert not rig.trigger.wait_for_candidate(0) and rig.trigger.claim_next_candidate(0.0) is None
    rig.clock.now += 3.0                       # alert TTL 5.0 s reached
    assert rig.trigger.pop_alerts() == ()
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.count("candidates_expired") == 1 and snapshot.count("alerts_expired") == 1


def test_claim_complete_fail_contract() -> None:
    rig = Rig(thresholds={"candidate_ttl_seconds": 30.0})
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    rig.approach(1)
    for _ in range(3):
        rig.feed(person(2, *ZONE_FOOT))
    first = rig.trigger.claim_next_candidate(0.0)
    assert rig.trigger.get_snapshot().pending_event_id == first.event_id
    assert rig.trigger.claim_next_candidate(0.0) is None        # one pending at a time
    rig.clock.now += 100                                         # pending never expires by TTL
    assert not rig.trigger.complete("other", 0.0)
    assert rig.trigger.complete(first.event_id, 0.0) and not rig.trigger.fail(first.event_id, 0.0)
    for bad in (math.nan, math.inf, True, "1"):
        with pytest.raises(ValueError):
            rig.trigger.claim_next_candidate(bad)


def test_episode_times_out_on_the_control_clock() -> None:
    rig = Rig(thresholds={"max_episode_seconds": 10.0})
    rig.walk_in(1)
    rig.clock.now += 10.0                      # no observation needed
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.episodes == () and snapshot.count("episodes_aged_out") == 1
    rig.approach(1, 5)                         # still inside: origin now unknown, no event
    assert rig.count("events_issued") == 0


def test_episode_capacity_is_bounded_and_counted() -> None:
    rig = Rig(thresholds={"max_episodes": 1})
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    assert len(rig.episodes()) == 1 and rig.count("episodes_capacity_dropped") == 1


def test_full_alert_queue_drops_the_new_alert_but_keeps_one_candidate() -> None:
    rig = Rig(thresholds={"alert_queue_limit": 1})
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    rig.approach(1)
    for _ in range(3):
        rig.feed(person(2, *ZONE_FOOT))
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.count("events_issued") == 2 and snapshot.count("alerts_dropped_queue_full") == 1
    assert snapshot.queued_alerts == 1 and snapshot.queued_candidates == 2
    rig.approach(2, 5)
    assert rig.count("events_issued") == 2     # never retried


def test_full_candidate_store_drops_the_new_candidate_but_keeps_the_alert() -> None:
    rig = Rig(thresholds={"max_candidates": 1})
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    rig.approach(1)
    for _ in range(3):
        rig.feed(person(2, *ZONE_FOOT))
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.count("candidates_dropped_full") == 1 and snapshot.queued_candidates == 1
    assert len(rig.trigger.pop_alerts()) == 2


def test_track_capacity_is_bounded() -> None:
    rig = Rig(thresholds={"max_tracks": 2}, parked=())
    rig.feed(*(person(i, 0.1 + 0.1 * i, 0.3) for i in range(5)))
    assert rig.count("tracks_capacity_dropped") == 3


# ── lifecycle and input validation ───────────────────────────────────────────

def test_stop_and_new_session_clear_state_and_reject_old_input() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)
    rig.trigger.end_session("other")
    assert rig.trigger.get_snapshot().queued_alerts == 1
    rig.trigger.end_session("s1")
    snapshot = rig.trigger.get_snapshot()
    assert (snapshot.source_session_id, snapshot.episodes, snapshot.queued_alerts,
            snapshot.queued_candidates) == (None, (), 0, 0)
    rig.trigger.start_session("s2")
    rig.feed(person(1, 0.5, 0.6), session="s1")
    rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, None))
    rig.feed(person(1, 0.5, 0.6), session="s2", domain_snapshot=domain("s2"))
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.count("snapshots_ignored") == 1 and snapshot.count("authorizations_rejected") == 1
    rig.trigger.close()
    rig.trigger.close()
    assert rig.trigger.claim_next_candidate(0.0) is None and not rig.trigger.wait_for_candidate(0)


@pytest.mark.parametrize("snapshot_domain", [domain(name=DomainName.RETAIL_STORE), domain(session="x"),
                                             DomainSnapshot("s1", DomainState.PROBING,
                                                            DomainName.PARKING_MOTORCYCLE, 0, 0, 0.0, False, None)])
def test_only_locked_parking_domain_of_this_session_is_observed(snapshot_domain) -> None:
    rig = Rig()
    for fy in (0.40, 0.45, 0.55):
        rig.feed(person(1, 0.5, fy), domain_snapshot=snapshot_domain)
    assert rig.episodes() == () and rig.count("snapshots_ignored") == 3


def test_disordered_snapshots_and_ineligible_tracks_are_ignored() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.trigger.observe(domain(), TrackingSnapshot("s1", rig.seq, rig.ts + 1, W, H, ()))   # seq not advancing
    rig.trigger.observe(domain(), TrackingSnapshot("s1", rig.seq + 1, rig.ts - 1, W, H, ()))  # ts backwards
    assert rig.count("snapshots_ignored") == 2
    rig2 = Rig()
    for fy in (0.40, 0.45, 0.55):
        rig2.feed(person(1, 0.5, fy, confirmed=False), person(2, 0.3, fy, confidence=0.3))
    assert rig2.episodes() == ()


def test_authorization_source_errors_are_counted_and_never_break_observation() -> None:
    rig = Rig()

    def broken(session_id):
        raise RuntimeError("detector unavailable")
    rig.auth.signals = broken
    rig.walk_in(1)
    assert len(rig.episodes()) == 1 and rig.count("authorization_source_errors") == 3


# ── concurrency ──────────────────────────────────────────────────────────────

def _run(target, n: int):
    barrier, results, errors = threading.Barrier(n, timeout=5), [], []

    def run():
        try:
            barrier.wait()
            results.append(target())
        except Exception as error:                       # pragma: no cover - reported below
            errors.append(error)
    threads = [threading.Thread(target=run) for _ in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert errors == []
    return results


def test_concurrent_claims_return_exactly_one_candidate() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)
    results = _run(lambda: rig.trigger.claim_next_candidate(0.0), 8)
    assert sum(result is not None for result in results) == 1


def test_concurrent_pops_deliver_each_alert_once() -> None:
    rig = Rig(thresholds={"alert_queue_limit": 8})
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    rig.approach(1)
    for _ in range(3):
        rig.feed(person(2, *ZONE_FOOT))
    results = _run(rig.trigger.pop_alerts, 6)
    delivered = [alert.event_id for batch in results for alert in batch]
    assert len(delivered) == 2 and len(set(delivered)) == 2


def test_concurrent_identical_observations_issue_one_event() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1, 2)
    snapshot = TrackingSnapshot("s1", rig.seq + 1, rig.ts + 0.2, W, H, (person(1, *ZONE_FOOT), PARKED))
    _run(lambda: rig.trigger.observe(domain(), snapshot), 6)
    assert rig.count("events_issued") == 1 and rig.count("snapshots_ignored") == 5


def test_signal_fetched_outside_the_lock_is_revalidated_after_a_session_change() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, None))
    rig.auth.entered.clear()
    rig.auth.release.clear()
    observing = threading.Thread(target=rig.feed, args=(person(1, 0.5, 0.6),))
    observing.start()
    try:
        assert rig.auth.entered.wait(2)                  # adapter call in progress
        reader = threading.Thread(target=rig.trigger.get_snapshot)
        reader.start()
        reader.join(2)
        assert not reader.is_alive()                     # the lock is not held during the call
        rig.trigger.end_session("s1")
        rig.trigger.start_session("s2")
    finally:
        rig.auth.release.set()
        observing.join(2)
    assert not observing.is_alive()
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.source_session_id == "s2" and snapshot.episodes == ()
    assert all(value == 0 for _name, value in snapshot.counts)          # new session diagnostics untouched


def test_interface_matches_the_evaluation_worker_protocol() -> None:
    rig = Rig()
    for name in ("observe", "start_session", "end_session", "close", "wait_for_candidate",
                 "wake_waiters", "claim_next_candidate", "complete", "fail", "get_snapshot", "pop_alerts"):
        assert callable(getattr(rig.trigger, name))
    rig.walk_in(1)
    rig.approach(1)
    candidate = rig.trigger.claim_next_candidate(0.0)
    for field in ("event_id", "source_session_id", "domain", "started_at", "triggered_at",
                  "person_track_id", "object_track_id", "evidence"):
        assert hasattr(candidate, field)
    snapshot = rig.trigger.get_snapshot()
    assert (snapshot.source_session_id, snapshot.pending_event_id) == ("s1", candidate.event_id)


def test_signal_older_than_its_maximum_age_is_rejected_even_after_the_crossing() -> None:
    rig = Rig()
    rig.walk_in(1)
    crossed_at = rig.ts
    for _ in range(15):                        # 3 s later, still inside
        rig.feed(person(1, 0.3, 0.8))
    rig.auth.queue.append(AuthorizationSignal("s1", 1, crossed_at + 0.1, None))   # after crossing, 2.9 s old
    rig.feed(person(1, 0.3, 0.8))
    assert rig.episodes()[0].state is EpisodeState.ENTERED and rig.count("authorizations_rejected") == 1


def test_a_moving_motorcycle_in_the_zone_is_not_a_parked_target() -> None:
    rig = Rig(parked=())
    rig.walk_in(1)
    for step in range(8):                      # motorcycle drifting through the zone, next to the person
        rig.feed(person(1, *ZONE_FOOT), motor(102, 0.70 + 0.03 * step, 0.92))
    assert rig.count("events_issued") == 0


def test_approach_confirmation_is_bound_to_one_motorcycle() -> None:
    motor_a = motor(100, 0.775, 0.90)                 # right of the person
    motor_b = motor(101, 0.90, 0.70, w=0.08, h=0.10)  # upper-right corner of the zone
    rig = Rig(parked=(motor_a, motor_b))
    for _ in range(4):                                # let both read as parked
        rig.feed(person(1, 0.5, 0.40))
    rig.walk_in(1)
    for _ in range(2):
        rig.feed(person(1, *ZONE_FOOT))               # 2 x close to A only
    for _ in range(2):
        rig.feed(person(1, 0.90, 0.85, w=0.04))       # now nearest to B
    assert rig.count("events_issued") == 0            # A's run did not carry over to B
    rig.feed(person(1, 0.90, 0.85, w=0.04))           # third consecutive observation for B
    candidate = rig.trigger.claim_next_candidate(0.0)
    assert rig.count("events_issued") == 1 and candidate.object_track_id == 101


def test_lost_association_resets_the_confirmation_run() -> None:
    rig = Rig()
    rig.walk_in(1)
    for _ in range(2):
        rig.feed(person(1, *ZONE_FOOT))
    rig.feed(person(1, 0.3, 0.8))                     # association lost
    for _ in range(2):
        rig.feed(person(1, *ZONE_FOOT))
    assert rig.count("events_issued") == 0
    rig.feed(person(1, *ZONE_FOOT))
    assert rig.count("events_issued") == 1


def test_adapter_failure_after_a_session_change_leaves_the_new_session_clean() -> None:
    rig = Rig()
    rig.walk_in(1)
    gate_entered, gate_release = threading.Event(), threading.Event()

    def failing(session_id):
        gate_entered.set()
        if not gate_release.wait(5):
            raise RuntimeError("gate never released")
        raise RuntimeError("detector failed late")
    rig.auth.signals = failing
    observing = threading.Thread(target=rig.feed, args=(person(1, 0.5, 0.6),))
    observing.start()
    try:
        assert gate_entered.wait(2)                   # adapter call held outside the lock
        rig.trigger.end_session("s1")
        rig.trigger.start_session("s2")
    finally:
        gate_release.set()
        observing.join(2)
    assert not observing.is_alive()
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.source_session_id == "s2" and snapshot.episodes == ()
    assert all(value == 0 for _name, value in snapshot.counts)
