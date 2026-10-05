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
from types import SimpleNamespace

import pytest

from app.models.domain import DomainName, DomainSnapshot, DomainState
from app.models.private_parking import (
    AuthorizationSignal, CandidateOutcome, CandidateToken, EpisodeState, LocalProximityAlert, MarkerKind,
    PrivateParkingCandidate, ProximityEvidence, make_thresholds, parse_private_parking_config,
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

    def publish_all(self):
        """Stand-in for the coordinator after a successful seal (T3a1: candidates are
        PREPARING and unclaimable until published with the alert's token)."""
        alerts = self.trigger.pop_alerts()
        for alert in alerts:
            assert self.trigger.publish_candidate(alert.token) is CandidateOutcome.PUBLISHED
        return alerts

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
    rig.publish_all()
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
    assert rig.trigger.claim_next_candidate(0.0) is None          # PREPARING: not claimable yet
    assert rig.trigger.get_snapshot().preparing_candidates == 1
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.PUBLISHED
    candidate = rig.trigger.claim_next_candidate(0.0)
    assert isinstance(alert, LocalProximityAlert) and isinstance(candidate, PrivateParkingCandidate)
    assert alert.event_id == candidate.event_id and alert.motor_track_id == 100 and alert.zone_name == "z1"
    assert alert.token == candidate.token is not None
    assert "token" not in {field.name for field in dataclasses.fields(ProximityEvidence)}   # never in payloads
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
    rig.publish_all()
    candidate = rig.trigger.claim_next_candidate(0.0)
    assert not rig.trigger.fail(candidate.event_id, 0.0)                  # id alone: refused
    assert rig.trigger.fail(candidate.event_id, 0.0, token=candidate.token)
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
    assert rig.trigger.get_snapshot().preparing_candidates == 1
    rig.clock.now += 0.01                      # candidate TTL 2.0 s reached while still PREPARING
    assert not rig.trigger.wait_for_candidate(0) and rig.trigger.claim_next_candidate(0.0) is None
    rig.clock.now += 3.0                       # alert TTL 5.0 s reached
    assert rig.trigger.pop_alerts() == ()
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.count("candidates_prepare_expired") == 1 and snapshot.count("alerts_expired") == 1
    assert snapshot.preparing_candidates == 0


def test_published_candidate_expires_on_the_control_clock_without_observations() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)
    rig.publish_all()
    rig.clock.now += 1.99
    assert rig.trigger.wait_for_candidate(0)
    rig.clock.now += 0.01                      # candidate TTL 2.0 s reached
    assert not rig.trigger.wait_for_candidate(0) and rig.trigger.claim_next_candidate(0.0) is None
    assert rig.count("candidates_expired") == 1


def test_claim_complete_fail_contract() -> None:
    rig = Rig(thresholds={"candidate_ttl_seconds": 30.0})
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    rig.approach(1)
    for _ in range(3):
        rig.feed(person(2, *ZONE_FOOT))
    rig.publish_all()
    first = rig.trigger.claim_next_candidate(0.0)
    assert rig.trigger.get_snapshot().pending_event_id == first.event_id
    assert rig.trigger.claim_next_candidate(0.0) is None        # one pending at a time
    rig.clock.now += 100                                         # pending never expires by TTL
    assert not rig.trigger.complete("other", 0.0, token=first.token)
    assert not rig.trigger.complete(first.event_id, 0.0)                    # no id-only fallback
    assert rig.trigger.complete(first.event_id, 0.0, token=first.token)
    assert not rig.trigger.fail(first.event_id, 0.0, token=first.token)
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
    assert snapshot.queued_alerts == 1 and snapshot.preparing_candidates == 2
    assert snapshot.queued_candidates == 0      # nothing is claimable before publication
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
    assert snapshot.count("candidates_dropped_full") == 1 and snapshot.preparing_candidates == 1
    first, second = rig.trigger.pop_alerts()
    assert rig.trigger.publish_candidate(second.token) is CandidateOutcome.REJECTED_FULL
    assert rig.trigger.publish_candidate(first.token) is CandidateOutcome.PUBLISHED


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
    rig.publish_all()
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
    rig.publish_all()
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
    rig.publish_all()
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


# ── T3a1: candidate tokens, preparation, tombstones and markers ──────────────


def _issue(rig: Rig, pid: int = 1, x: float = 0.5):
    rig.walk_in(pid, x=x)
    rig.approach(pid)
    (alert,) = rig.trigger.pop_alerts()
    return alert


def test_preparing_candidate_is_published_only_with_its_token_and_once() -> None:
    rig = Rig()
    alert = _issue(rig)
    assert isinstance(alert.token, CandidateToken)
    assert rig.trigger.claim_next_candidate(0.0) is None and not rig.trigger.wait_for_candidate(0)
    other = CandidateToken(alert.token.session_generation, alert.token.serial + 1000)
    assert rig.trigger.publish_candidate(other) is CandidateOutcome.UNKNOWN
    assert rig.trigger.abandon_candidate(other) is CandidateOutcome.UNKNOWN
    assert rig.trigger.publish_candidate("not-a-token") is CandidateOutcome.STALE
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.PUBLISHED
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.PUBLISHED   # tombstone, no re-queue
    assert rig.trigger.abandon_candidate(alert.token) is CandidateOutcome.PUBLISHED  # cannot abandon it now
    assert rig.trigger.get_snapshot().queued_candidates == 1
    assert rig.trigger.claim_next_candidate(0.0).token == alert.token


def test_publish_wakes_a_waiting_worker() -> None:
    rig = Rig()
    alert = _issue(rig)
    started, result = threading.Event(), []

    def wait():
        started.set()
        result.append(rig.trigger.wait_for_candidate(5))
    waiter = threading.Thread(target=wait)
    waiter.start()
    assert started.wait(2)
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.PUBLISHED
    waiter.join(2)
    assert not waiter.is_alive() and result == [True]


def test_publish_at_or_after_the_deadline_is_expired_and_never_claimable() -> None:
    rig = Rig()
    alert = _issue(rig)
    rig.clock.now += 2.0                             # exactly the TTL: the control clock decides
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.EXPIRED
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.EXPIRED  # tombstone kept
    assert rig.trigger.claim_next_candidate(0.0) is None
    assert rig.count("candidates_prepare_expired") == 1 and rig.count("candidates_published") == 0


def test_expiry_closed_by_another_call_first_still_reports_expired() -> None:
    rig = Rig()
    alert = _issue(rig)
    rig.clock.now += 5.0
    rig.trigger.pop_alerts()                         # a sweep closes the candidate before publish
    assert rig.trigger.get_snapshot().preparing_candidates == 0
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.EXPIRED
    assert rig.trigger.claim_next_candidate(0.0) is None


def test_unknown_after_the_tombstone_is_evicted_but_never_published() -> None:
    rig = Rig(thresholds={"max_candidates": 1, "max_episodes": 16})
    first = _issue(rig)
    rig.clock.now += 3.0
    rig.trigger.pop_alerts()                         # first expires -> tombstone
    tokens = []
    for pid in range(2, 8):                          # 6 more closed candidates evict the first tombstone
        alert = _issue(rig, pid, x=0.25 + 0.05 * pid)
        tokens.append(alert.token)
        assert rig.trigger.abandon_candidate(alert.token) is CandidateOutcome.ABANDONED
    assert rig.count("outcomes_evicted") >= 1
    assert rig.trigger.publish_candidate(first.token) is CandidateOutcome.UNKNOWN
    assert rig.trigger.publish_candidate(tokens[-1]) is CandidateOutcome.ABANDONED
    assert rig.trigger.get_snapshot().queued_candidates == 0


def test_abandoned_candidate_is_closed_without_retry() -> None:
    rig = Rig()
    alert = _issue(rig)
    assert rig.trigger.abandon_candidate(alert.token) is CandidateOutcome.ABANDONED
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.ABANDONED
    rig.approach(1, 10)
    assert rig.count("events_issued") == 1 and rig.count("candidates_abandoned") == 1
    assert rig.trigger.claim_next_candidate(0.0) is None


def test_old_token_never_affects_a_restarted_session_with_the_same_id() -> None:
    rig = Rig()
    old = _issue(rig)
    rig.trigger.end_session("s1")
    rig.trigger.start_session("s1")                  # same session id, new generation
    rig.seq, rig.ts = 0, 0.0
    new = _issue(rig)
    assert new.token.session_generation > old.token.session_generation and new.token.serial > old.token.serial
    assert rig.trigger.publish_candidate(old.token) is CandidateOutcome.STALE
    assert rig.trigger.abandon_candidate(old.token) is CandidateOutcome.STALE
    assert rig.trigger.get_snapshot().preparing_candidates == 1           # the new one is untouched
    assert rig.trigger.publish_candidate(new.token) is CandidateOutcome.PUBLISHED


def test_concurrent_publishes_of_one_token_queue_it_once() -> None:
    rig = Rig()
    alert = _issue(rig)
    results = _run(lambda: rig.trigger.publish_candidate(alert.token), 8)
    assert results.count(CandidateOutcome.PUBLISHED) == 8                # idempotent answer...
    assert rig.trigger.get_snapshot().queued_candidates == 1             # ...one queued candidate
    assert rig.count("candidates_published") == 1


def _kinds(batch):
    return [marker.kind for marker in batch.markers]


def test_markers_follow_every_transition_in_order() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    rig.auth.queue.append(AuthorizationSignal("s1", 2, rig.ts, None))
    rig.feed(person(1, 0.5, 0.6), person(2, 0.3, 0.6))
    batch = rig.trigger.pop_markers()
    assert batch.session_id == "s1" and batch.view is None
    assert _kinds(batch) == [MarkerKind.EPISODE_ENTERED, MarkerKind.EPISODE_ENTERED, MarkerKind.EPISODE_AUTHORIZED]
    assert [m.seq for m in batch.markers] == [1, 2, 3] and batch.upto_seq == 3
    rig.approach(1, 3, person(2, 0.3, 0.6))
    (alert,) = rig.trigger.pop_alerts()
    triggered = rig.trigger.pop_markers()
    assert _kinds(triggered) == [MarkerKind.EPISODE_TRIGGERED]
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.PUBLISHED
    candidate = rig.trigger.claim_next_candidate(0.0)
    assert rig.trigger.complete(candidate.event_id, 0.0, token=candidate.token)
    finished = rig.trigger.pop_markers()
    assert _kinds(finished) == [MarkerKind.CANDIDATE_FINISHED] and finished.markers[0].token == alert.token
    assert rig.trigger.pop_markers().markers == ()


def test_ended_episode_keeps_samples_while_its_candidate_waits_for_the_seal() -> None:
    rig = Rig()
    alert = _issue(rig)
    rig.trigger.pop_markers()
    rig.feed(person(1, 0.5, 0.30))                   # walks out right after the trigger
    (ended,) = rig.trigger.pop_markers().markers
    assert ended.kind is MarkerKind.EPISODE_ENDED and ended.keep_samples
    rig.clock.now += 3.0
    (closed,) = rig.trigger.pop_markers().markers   # preparing expired without a seal
    assert closed.kind is MarkerKind.CANDIDATE_CLOSED and closed.token == alert.token
    assert closed.episode_id == alert.episode_id


def test_published_candidate_expiry_and_full_store_emit_closing_markers() -> None:
    rig = Rig(thresholds={"max_candidates": 1})
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    rig.approach(1)
    for _ in range(3):
        rig.feed(person(2, *ZONE_FOOT))
    first, second = rig.trigger.pop_alerts()
    closed = [m for m in rig.trigger.pop_markers().markers if m.kind is MarkerKind.CANDIDATE_CLOSED]
    assert [m.token for m in closed] == [second.token]                  # rejected at issue (store full)
    assert rig.trigger.publish_candidate(first.token) is CandidateOutcome.PUBLISHED
    rig.clock.now += 2.0
    (expired,) = rig.trigger.pop_markers().markers
    assert expired.kind is MarkerKind.CANDIDATE_EXPIRED and expired.token == first.token


def test_marker_overflow_returns_an_authoritative_view_once() -> None:
    rig = Rig(thresholds={"marker_queue_limit": 4})
    rig.walk_in(1)
    rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, None))
    rig.feed(person(1, 0.5, 0.6))                                       # AUTHORIZED
    inside = [person(1, 0.5, 0.6)]
    for pid, x in ((2, 0.30), (3, 0.36), (4, 0.42), (5, 0.62)):
        rig.walk_in(pid, *inside, x=x)
        inside.append(person(pid, x, 0.6))
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.resync_required and snapshot.count("marker_overflows") == 1
    batch = rig.trigger.pop_markers()
    assert batch.markers == () and batch.view is not None and batch.upto_seq == 6
    entered = {episode_id for episode_id, _instance, _revision, _crossed in batch.view.entered}
    assert len(entered) == 4                                            # the authorized one is not listed
    authorized = [e.episode_id for e in snapshot.episodes if e.state is EpisodeState.AUTHORIZED]
    assert authorized and authorized[0] not in entered
    assert rig.trigger.pop_markers().view is None and not rig.trigger.get_snapshot().resync_required


def test_view_lists_a_triggered_episode_whose_candidate_still_waits_for_the_seal() -> None:
    rig = Rig(thresholds={"marker_queue_limit": 4})
    alert = _issue(rig)
    inside = [person(1, 0.3, 0.8)]
    for pid, x in ((2, 0.30), (3, 0.36), (4, 0.42), (5, 0.62)):
        rig.walk_in(pid, *inside, x=x)
        inside.append(person(pid, x, 0.6))
    view = rig.trigger.pop_markers().view
    assert view is not None
    assert (alert.episode_id, alert.token) in view.preparing and alert.token in view.live_tokens
    assert alert.episode_id not in {episode_id for episode_id, _i, _r, _c in view.entered}


# ── T3a1 delta 1: exact candidate identity and consistent expiry ─────────────

def _fixed_ids(monkeypatch, value: str = "f" * 32) -> None:
    """Force every uuid the trigger mints (episode and event ids) to repeat."""
    from app.services import private_parking_trigger as module
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex=value))


def _claimed(rig: Rig):
    rig.publish_all()
    return rig.trigger.claim_next_candidate(0.0)


def test_old_cleanup_never_finishes_a_new_candidate_with_the_same_event_id(monkeypatch) -> None:
    _fixed_ids(monkeypatch)
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)
    old = _claimed(rig)
    rig.trigger.end_session("s1")
    rig.trigger.start_session("s1")                   # same session id, new generation
    rig.seq, rig.ts = 0, 0.0
    rig.walk_in(1)
    rig.approach(1)
    new = _claimed(rig)
    assert old.event_id == new.event_id and old.token != new.token
    for finish in (rig.trigger.complete, rig.trigger.fail):
        assert not finish(old.event_id, 0.0, token=old.token)      # late cleanup of the old claim
        assert not finish(new.event_id, 0.0)                       # id alone: no fallback
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.pending_token == new.token and snapshot.queued_candidates == 1
    assert snapshot.count("finish_rejected") == 4 and snapshot.count("candidates_finished") == 0
    assert rig.trigger.fail(new.event_id, 0.0, token=new.token)
    assert rig.trigger.get_snapshot().queued_candidates == 0


def test_published_expiry_spares_only_the_claimed_token() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)
    claimed = _claimed(rig)
    rig.clock.now += 100.0                             # far past every deadline
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.pending_token == claimed.token and snapshot.queued_candidates == 1
    assert rig.trigger.complete(claimed.event_id, 0.0, token=claimed.token)


@pytest.mark.parametrize("sweep_first", [False, True])
def test_abandon_after_the_deadline_is_recorded_as_expired(sweep_first) -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)
    (alert,) = rig.trigger.pop_alerts()
    rig.clock.now += 2.0                               # deadline reached (control clock)
    if sweep_first:
        rig.trigger.get_snapshot()                     # a sweep closes it first
    assert rig.trigger.abandon_candidate(alert.token) is CandidateOutcome.EXPIRED
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.EXPIRED
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.count("candidates_prepare_expired") == 1 and snapshot.count("candidates_abandoned") == 0
    assert rig.trigger.claim_next_candidate(0.0) is None


def test_abandon_just_before_the_deadline_is_abandoned() -> None:
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)
    (alert,) = rig.trigger.pop_alerts()
    rig.clock.now += 1.99
    assert rig.trigger.abandon_candidate(alert.token) is CandidateOutcome.ABANDONED
    assert rig.count("candidates_abandoned") == 1


def _two_published_with_one_event_id(monkeypatch, ttl: float = 30.0):
    """Same generation, two candidates sharing an event id (episode ids stay distinct)."""
    from app.services import private_parking_trigger as module
    minted = iter(["ep-a", "ep-b", "same-event", "same-event"])
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex=next(minted)))
    rig = Rig(thresholds={"candidate_ttl_seconds": ttl})
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    rig.approach(1)
    for _ in range(3):
        rig.feed(person(2, *ZONE_FOOT))
    first, second = rig.publish_all()
    assert first.event_id == second.event_id and first.token != second.token
    return rig, first, second


def test_finishing_one_candidate_never_removes_another_with_the_same_event_id(monkeypatch) -> None:
    rig, first, second = _two_published_with_one_event_id(monkeypatch)
    claimed = rig.trigger.claim_next_candidate(0.0)
    assert claimed.token == first.token
    assert rig.trigger.complete(claimed.event_id, 0.0, token=first.token)
    assert rig.trigger.get_snapshot().queued_candidates == 1                 # the other one survives
    assert rig.trigger.claim_next_candidate(0.0).token == second.token


def test_expiry_spares_only_the_claimed_token_not_its_event_id(monkeypatch) -> None:
    rig, first, second = _two_published_with_one_event_id(monkeypatch, ttl=2.0)
    assert rig.trigger.claim_next_candidate(0.0).token == first.token
    rig.clock.now += 2.0
    snapshot = rig.trigger.get_snapshot()
    assert snapshot.count("candidates_expired") == 1 and snapshot.queued_candidates == 1
    assert snapshot.pending_token == first.token


# ── delta 2: episode instances ───────────────────────────────────────────────

def _minted(monkeypatch, *values) -> None:
    from app.services import private_parking_trigger as module
    sequence = iter(values)
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex=next(sequence)))


def test_a_reused_episode_id_gets_a_new_instance_bound_to_its_markers_and_token(monkeypatch) -> None:
    _minted(monkeypatch, "same-ep", "ev-a", "same-ep", "ev-b")
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)
    (alert_a,) = rig.trigger.pop_alerts()
    rig.feed(person(1, 0.5, 0.30))                                      # A leaves while still preparing
    first = rig.trigger.pop_markers().markers
    rig.walk_in(2, x=0.4)
    rig.approach(2)
    (alert_b,) = rig.trigger.pop_alerts()
    second = rig.trigger.pop_markers().markers
    instance_a = {m.episode_instance for m in first}
    instance_b = {m.episode_instance for m in second if m.kind is not MarkerKind.CANDIDATE_CLOSED}
    assert len(instance_a) == 1 and len(instance_b) == 1 and instance_a != instance_b
    assert alert_a.episode_id == alert_b.episode_id == "same-ep"
    assert alert_a.token.episode_instance in instance_a and alert_b.token.episode_instance in instance_b
    ended = [m for m in first if m.kind is MarkerKind.EPISODE_ENDED]
    assert ended and ended[0].keep_samples                             # kept by instance, not by id


def test_a_minted_id_that_is_still_live_is_replaced_or_the_crossing_is_dropped(monkeypatch) -> None:
    _minted(monkeypatch, "dup", "dup", "fresh")
    rig = Rig()
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    ids = sorted(e.episode_id for e in rig.episodes())
    assert ids == ["dup", "fresh"] and rig.count("episode_id_collisions") == 1

    _minted(monkeypatch, *(["dup"] * 10))
    rig = Rig()
    rig.walk_in(1)
    rig.walk_in(2, person(1, 0.5, 0.6), x=0.3)
    assert [e.episode_id for e in rig.episodes()] == ["dup"]           # never overwritten
    assert rig.count("episode_id_collisions") == 4 and rig.count("episodes_capacity_dropped") == 1


def test_an_episode_ending_keeps_samples_only_for_its_own_preparing_candidate(monkeypatch) -> None:
    _minted(monkeypatch, "same-ep", "ev-a", "same-ep")
    rig = Rig()
    rig.walk_in(1)
    rig.approach(1)                                                     # A preparing (clock not advanced)
    rig.feed(person(1, 0.5, 0.30))                                      # A ends while preparing
    rig.walk_in(2, x=0.4)                                               # B: same id, no candidate
    rig.feed(person(2, 0.4, 0.30))                                      # B ends
    ended = [m for m in rig.trigger.pop_markers().markers if m.kind is MarkerKind.EPISODE_ENDED]
    assert [m.keep_samples for m in ended] == [True, False]
    assert ended[0].episode_instance != ended[1].episode_instance
