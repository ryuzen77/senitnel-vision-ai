"""Synthetic tests for the local, side-effect-free parking candidate filter."""

from __future__ import annotations

import math
import threading

from app.models.domain import DomainName, DomainSnapshot, DomainState
from app.models.parking_candidate import ParkingCandidateState
from app.models.tracking import TrackedDetection, TrackingSnapshot
from app.services.parking_candidate_trigger import ParkingCandidateTrigger


def _domain(session: str = "one", domain: DomainName = DomainName.PARKING_MOTORCYCLE, state: DomainState = DomainState.LOCKED) -> DomainSnapshot:
    return DomainSnapshot(session, state, domain, 0, 0, 0.0, state is DomainState.LOCKED, None)


def _track(track_id: int, label: str, box: tuple[float, float, float, float], *, confirmed: bool = True, confidence: float = 0.9, seq: int = 0, timestamp: float = 0.0) -> TrackedDetection:
    return TrackedDetection(track_id, 0 if label == "person" else 3, label, confidence, box, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "one", seq, timestamp, confirmed, 2)


def _snapshot(seq: int, timestamp: float, *tracks: TrackedDetection, session: str = "one", width: int = 100, height: int = 100) -> TrackingSnapshot:
    adjusted = tuple(__import__("dataclasses").replace(track, source_session_id=session, source_frame_seq=seq, source_timestamp=timestamp) for track in tracks)
    return TrackingSnapshot(session, seq, timestamp, width, height, adjusted)


def _observe_series(trigger: ParkingCandidateTrigger, *, moving: str = "person", times: tuple[float, ...] = (0.0, 0.7, 1.4, 2.1), motorcycle_id: int = 2) -> None:
    for seq, timestamp in enumerate(times, 1):
        person_x = 10 + (seq * 4 if moving == "person" else 0)
        motor_x = 40 + (seq if moving == "motorcycle" else 0)
        trigger.observe(_domain(), _snapshot(seq, timestamp, _track(1, "person", (person_x, 20, person_x + 10, 50)), _track(motorcycle_id, "motorcycle", (motor_x, 25, motor_x + 25, 55))))


def test_non_parking_or_probing_domain_does_not_create_pairs() -> None:
    trigger = ParkingCandidateTrigger(); trigger.start_session("one")
    frame = _snapshot(1, 0.0, _track(1, "person", (10, 10, 20, 30)), _track(2, "motorcycle", (25, 10, 50, 35)))
    trigger.observe(_domain(state=DomainState.PROBING, domain=DomainName.UNKNOWN), frame)
    trigger.observe(_domain(domain=DomainName.RETAIL_STORE), _snapshot(2, 1.0, *frame.tracks))
    assert trigger.get_snapshot().pair_states == ()


def test_wrong_session_tentative_low_confidence_and_far_tracks_are_ignored() -> None:
    trigger = ParkingCandidateTrigger(); trigger.start_session("one")
    trigger.observe(_domain(), _snapshot(1, 0, _track(1, "person", (0, 0, 5, 5), confirmed=False), _track(2, "motorcycle", (10, 0, 20, 10))))
    trigger.observe(_domain(), _snapshot(2, 1, _track(1, "person", (0, 0, 5, 5), confidence=.49), _track(2, "motorcycle", (10, 0, 20, 10))))
    trigger.observe(_domain(), _snapshot(3, 2, _track(1, "person", (0, 0, 5, 5)), _track(2, "motorcycle", (80, 80, 90, 90))))
    trigger.observe(_domain(), _snapshot(4, 3, _track(1, "person", (0, 0, 5, 5)), _track(2, "motorcycle", (10, 0, 20, 10)), session="other"))
    assert trigger.get_snapshot().pair_states == ()


def test_candidate_requires_both_four_observations_and_two_seconds_and_motion() -> None:
    trigger = ParkingCandidateTrigger(); trigger.start_session("one")
    _observe_series(trigger, times=(0, .3, .6, .9))
    assert trigger.claim_next_candidate(1.0) is None
    trigger = ParkingCandidateTrigger(); trigger.start_session("one")
    _observe_series(trigger, times=(0, 1.0, 2.1))
    assert trigger.claim_next_candidate(2.2) is None
    trigger = ParkingCandidateTrigger(); trigger.start_session("one")
    _observe_series(trigger, moving="none")
    assert trigger.claim_next_candidate(2.2) is None


def test_person_motorcycle_or_distance_change_creates_candidate() -> None:
    for movement in ("person", "motorcycle"):
        trigger = ParkingCandidateTrigger(); trigger.start_session("one")
        _observe_series(trigger, moving=movement)
        event = trigger.claim_next_candidate(2.2)
        assert event is not None
        assert event.domain is DomainName.PARKING_MOTORCYCLE
        assert event.evidence.proximity_observations == 4
        assert event.state is ParkingCandidateState.API_PENDING
    trigger = ParkingCandidateTrigger(); trigger.start_session("one")
    for seq, x in enumerate((10, 13, 16, 19), 1):
        trigger.observe(_domain(), _snapshot(seq, (seq - 1) * .7, _track(1, "person", (10, 20, 20, 50)), _track(2, "motorcycle", (x + 20, 25, x + 45, 55))))
    assert trigger.claim_next_candidate(2.2) is not None


def test_brief_gap_preserves_evidence_but_stale_pair_is_released() -> None:
    trigger = ParkingCandidateTrigger(); trigger.start_session("one")
    _observe_series(trigger, times=(0, .7))
    trigger.observe(_domain(), _snapshot(3, 1.0))
    for seq, timestamp in ((4, 1.4), (5, 2.1)):
        x = 10 + seq * 4
        trigger.observe(_domain(), _snapshot(seq, timestamp, _track(1, "person", (x, 20, x + 10, 50)), _track(2, "motorcycle", (40, 25, 65, 55))))
    assert trigger.claim_next_candidate(2.2) is not None
    trigger = ParkingCandidateTrigger(pair_stale_seconds=1.0); trigger.start_session("one")
    _observe_series(trigger, times=(0, .7))
    trigger.observe(_domain(), _snapshot(3, 2.0))
    assert trigger.get_snapshot().pair_states == ()


def test_nearest_motorcycle_only_is_reinforced_and_priority_is_deterministic() -> None:
    trigger = ParkingCandidateTrigger(); trigger.start_session("one")
    for seq, timestamp in enumerate((0, .7, 1.4, 2.1), 1):
        person = _track(1, "person", (10 + seq * 4, 20, 20 + seq * 4, 50))
        near = _track(3, "motorcycle", (35, 25, 60, 55))
        far = _track(2, "motorcycle", (65, 25, 90, 55))
        trigger.observe(_domain(), _snapshot(seq, timestamp, person, far, near))
    event = trigger.claim_next_candidate(2.2)
    assert event is not None and event.object_track_id == 3


def test_claim_is_atomic_and_complete_fail_cooldown_and_global_interval_apply() -> None:
    trigger = ParkingCandidateTrigger(global_request_interval_seconds=10, pair_cooldown_seconds=45); trigger.start_session("one")
    _observe_series(trigger)
    candidates: list[object] = []
    workers = [threading.Thread(target=lambda: candidates.append(trigger.claim_next_candidate(2.2))) for _ in range(4)]
    for worker in workers: worker.start()
    for worker in workers: worker.join()
    events = [item for item in candidates if item is not None]
    assert len(events) == 1
    event = events[0]
    assert trigger.complete(event.event_id, 2.3)
    assert not trigger.complete(event.event_id, 2.4)
    assert trigger.get_snapshot().pending_event_id is None
    # A new ready pair is globally rate-limited after a claim.
    _observe_series(trigger, motorcycle_id=4)
    assert trigger.claim_next_candidate(3.0) is None


def test_duplicate_out_of_order_invalid_input_and_lifecycle_are_safe_and_bounded() -> None:
    trigger = ParkingCandidateTrigger(recent_sequence_limit=2); trigger.start_session("one")
    _observe_series(trigger, times=(0, .7, 1.4, 2.1))
    event = trigger.claim_next_candidate(2.2)
    assert event is not None and len(event.candidate_frame_sequences) <= 2
    before = trigger.get_snapshot()
    trigger.observe(_domain(), _snapshot(4, 2.2, _track(1, "person", (0, 0, 1, 1)), _track(2, "motorcycle", (2, 0, 3, 1))))
    trigger.observe(_domain(), _snapshot(5, math.nan))
    assert trigger.get_snapshot() == before
    trigger.end_session("other"); assert trigger.get_snapshot() == before
    trigger.end_session("one"); trigger.end_session("one"); trigger.close(); trigger.close()
    assert trigger.get_snapshot().source_session_id is None


def test_multiple_candidates_have_deterministic_priority_and_invalid_pair_does_not_break_valid_pair() -> None:
    trigger = ParkingCandidateTrigger(); trigger.start_session("one")
    for seq, timestamp in enumerate((0, .7, 1.4, 2.1), 1):
        # Person 9 overlaps the motorcycle and therefore has lower mean distance.
        first = _track(1, "person", (10 + seq * 4, 20, 20 + seq * 4, 50))
        second = _track(9, "person", (50 + seq * 4, 20, 60 + seq * 4, 50))
        motorcycle = _track(2, "motorcycle", (40, 25, 65, 55))
        malformed = _track(8, "person", (0, 0, 1, 1), confidence=math.nan)
        trigger.observe(_domain(), _snapshot(seq, timestamp, first, second, motorcycle, malformed))
    event = trigger.claim_next_candidate(2.2)
    assert event is not None and event.person_track_id == 9


def _episode_frame(trigger: ParkingCandidateTrigger, seq: int, timestamp: float,
                   person_x: float = 10, motor_x: float = 40,
                   motor_id: int = 2, extra_motor: tuple[int, float] | None = None) -> None:
    tracks = [_track(1, "person", (person_x, 20, person_x + 10, 50)),
              _track(motor_id, "motorcycle", (motor_x, 25, motor_x + 25, 55))]
    if extra_motor:
        tracks.append(_track(extra_motor[0], "motorcycle", (extra_motor[1], 25, extra_motor[1] + 25, 55)))
    trigger.observe(_domain(), _snapshot(seq, timestamp, *tracks))


def test_episode_approach_brief_close_low_ratio_and_static_do_not_trigger() -> None:
    for kind in ("approach", "brief", "low_ratio", "static"):
        trigger = ParkingCandidateTrigger()
        trigger.start_session("one")
        if kind == "approach":
            for seq in range(1, 5):
                _episode_frame(trigger, seq, seq * .7, person_x=0, motor_x=40)
            assert trigger.get_snapshot().pair_states[0][2] is ParkingCandidateState.APPROACHING
        elif kind == "brief":
            for seq in range(1, 5):
                _episode_frame(trigger, seq, seq * .2, person_x=10 + seq * 4)
        elif kind == "low_ratio":
            for seq in range(1, 9):
                _episode_frame(trigger, seq, seq * .4, person_x=10 + (seq % 2),
                               motor_x=40 if seq in (1, 2, 5, 6) else 75)
        else:
            for seq in range(1, 5):
                _episode_frame(trigger, seq, (seq - 1) * .7)
        assert trigger.claim_next_candidate(4.0) is None, kind


def test_cumulative_back_and_forth_creates_engaged_candidate_before_departure() -> None:
    trigger = ParkingCandidateTrigger(separation_threshold=.2)
    trigger.start_session("one")
    for seq, x in enumerate((10, 17, 10, 17, 10), 1):
        _episode_frame(trigger, seq, (seq - 1) * .55, person_x=x)
    event = trigger.claim_next_candidate(2.3)
    assert event is not None
    assert event.episode_state_at_trigger is ParkingCandidateState.ENGAGED
    assert event.evidence.person_motion_normalized < .01
    assert event.cumulative_person_path_normalized >= .03
    assert event.close_observation_ratio == 1.0
    assert event.continuous_close_duration >= 2
    assert event.episode_id == event.evidence.episode_id
    assert event.initial_object_track_id == event.active_object_track_id == 2


def test_transition_candidate_on_separation_after_valid_engagement() -> None:
    trigger = ParkingCandidateTrigger(minimum_person_motion=1, minimum_motorcycle_motion=1,
                                      minimum_distance_range=1, separation_threshold=.02)
    trigger.start_session("one")
    for seq, timestamp in enumerate((0, .7, 1.4, 2.1), 1):
        _episode_frame(trigger, seq, timestamp)
    assert trigger.claim_next_candidate(2.2) is None
    _episode_frame(trigger, 5, 2.3, motor_x=44)
    event = trigger.claim_next_candidate(2.4)
    assert event is not None
    assert event.episode_state_at_trigger is ParkingCandidateState.TRANSITION
    assert event.separation_after_engagement >= .02


def test_motor_lock_does_not_switch_to_slightly_nearer_neighbor() -> None:
    trigger = ParkingCandidateTrigger()
    trigger.start_session("one")
    _episode_frame(trigger, 1, 0, motor_x=40, extra_motor=(3, 42))
    for seq in range(2, 5):
        _episode_frame(trigger, seq, (seq - 1) * .7, person_x=10 + seq * 4,
                       motor_x=42, extra_motor=(3, 39))
    event = trigger.claim_next_candidate(2.2)
    assert event is not None and event.object_track_id == 2
    assert trigger.get_snapshot().episode_states[0][3] == 2


def test_unambiguous_reassociation_stays_in_one_episode_and_ambiguous_is_rejected() -> None:
    trigger = ParkingCandidateTrigger()
    trigger.start_session("one")
    _episode_frame(trigger, 1, 0)
    trigger.observe(_domain(), _snapshot(2, .4, _track(1, "person", (14, 20, 24, 50))))
    _episode_frame(trigger, 3, .7, person_x=18, motor_x=41, motor_id=8)
    assert trigger.get_snapshot().episode_states[0][2:5] == (2, 8, 1)
    for seq, timestamp in ((4, 1.4), (5, 2.1)):
        _episode_frame(trigger, seq, timestamp, person_x=10 + seq * 4, motor_x=41, motor_id=8)
    event = trigger.claim_next_candidate(2.2)
    assert event is not None
    assert (event.initial_object_track_id, event.active_object_track_id, event.reassociation_count) == (2, 8, 1)
    for seq in range(6, 12):
        _episode_frame(trigger, seq, 2.1 + seq * .2, person_x=10 + seq, motor_x=41, motor_id=8)
    assert trigger.get_snapshot().pending_event_id == event.event_id
    assert trigger.complete(event.event_id, 4.5)
    assert trigger.claim_next_candidate(5.0) is None

    ambiguous = ParkingCandidateTrigger(); ambiguous.start_session("one")
    _episode_frame(ambiguous, 1, 0)
    _episode_frame(ambiguous, 2, .4, motor_x=40, extra_motor=(9, 41))
    ambiguous.observe(_domain(), _snapshot(3, .7,
        _track(1, "person", (18, 20, 28, 50)),
        _track(8, "motorcycle", (40, 25, 65, 55)),
        _track(9, "motorcycle", (41, 25, 66, 55))))
    assert ambiguous.get_snapshot().episode_states[0][3:5] == (2, 0)


def test_cooldown_requires_release_and_new_engagement() -> None:
    trigger = ParkingCandidateTrigger(pair_cooldown_seconds=1, global_request_interval_seconds=0)
    trigger.start_session("one")
    _observe_series(trigger)
    first = trigger.claim_next_candidate(2.2)
    assert first is not None and trigger.fail(first.event_id, 2.3)
    for seq in range(5, 10):
        _episode_frame(trigger, seq, 2.3 + seq * .4, person_x=10 + seq * 4)
    assert trigger.claim_next_candidate(6.0) is None
    # First far observation releases the tombstone. A later close starts a new episode.
    trigger.observe(_domain(), _snapshot(10, 6.1, _track(1, "person", (0, 20, 10, 50))))
    assert trigger.get_snapshot().pair_states == ()
    for seq, timestamp in ((11, 6.2), (12, 6.9), (13, 7.6), (14, 8.3)):
        _episode_frame(trigger, seq, timestamp, person_x=10 + (seq - 10) * 4)
    second = trigger.claim_next_candidate(8.4)
    assert second is not None and second.episode_id != first.episode_id


def test_history_and_sequence_buffers_remain_bounded_over_long_episode() -> None:
    trigger = ParkingCandidateTrigger(history_limit=3, recent_sequence_limit=2, history_horizon_seconds=1)
    trigger.start_session("one")
    for seq in range(1, 80):
        _episode_frame(trigger, seq, seq * .1, person_x=10 + seq % 2)
    episode = trigger._episodes[1]
    assert len(episode.history) <= 3
    assert len(episode.sequences) <= 2
    assert all(7.9 - item.timestamp <= 1 for item in episode.history)


def test_locked_motor_replacement_requires_grace_and_unique_position() -> None:
    trigger = ParkingCandidateTrigger()
    trigger.start_session("one")
    _episode_frame(trigger, 1, 0)
    # A different ID cannot replace the locked motor after grace expires.
    trigger.observe(_domain(), _snapshot(2, 1.6, _track(1, "person", (14, 20, 24, 50)),
                                         _track(8, "motorcycle", (40, 25, 65, 55))))
    assert trigger.get_snapshot().episode_states[0][3:5] == (2, 0)

    ambiguous = ParkingCandidateTrigger(); ambiguous.start_session("one")
    _episode_frame(ambiguous, 1, 0)
    ambiguous.observe(_domain(), _snapshot(2, .4, _track(1, "person", (14, 20, 24, 50)),
                                           _track(8, "motorcycle", (40, 25, 65, 55)),
                                           _track(9, "motorcycle", (42, 25, 67, 55))))
    assert ambiguous.get_snapshot().episode_states[0][3:5] == (2, 0)


def test_candidate_releases_after_sustained_separation_and_does_not_reissue() -> None:
    trigger = ParkingCandidateTrigger()
    trigger.start_session("one")
    _observe_series(trigger)
    episode_id = trigger.get_snapshot().episode_states[0][1]
    trigger.observe(_domain(), _snapshot(5, 2.5, _track(1, "person", (95, 20, 100, 50)),
                                         _track(2, "motorcycle", (40, 25, 65, 55))))
    assert trigger.get_snapshot().episode_states[0][1] == episode_id
    trigger.observe(_domain(), _snapshot(6, 4.0, _track(1, "person", (95, 20, 100, 50)),
                                         _track(2, "motorcycle", (40, 25, 65, 55))))
    assert trigger.get_snapshot().pair_states == ()
    assert trigger.claim_next_candidate(4.1) is None


def test_long_approach_does_not_dilute_stable_close_ratio() -> None:
    trigger = ParkingCandidateTrigger()
    trigger.start_session("one")
    for seq in range(1, 21):
        _episode_frame(trigger, seq, seq * .1, person_x=-5, motor_x=40)
    assert trigger.get_snapshot().pair_states[0][2] is ParkingCandidateState.APPROACHING
    for seq, timestamp in enumerate((2.1, 2.8, 3.5, 4.2), 21):
        _episode_frame(trigger, seq, timestamp, person_x=10 + (seq - 20) * 4)
    event = trigger.claim_next_candidate(4.3)
    assert event is not None
    assert event.close_observation_ratio == 1.0
