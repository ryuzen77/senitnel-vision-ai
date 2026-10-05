"""T3a1 core contracts across the real private-parking trigger, evidence sampler and
evaluation worker. Synthetic frames and tracks, fake clock and fake client only:
no video, model, network, runtime wiring or speaker.

``prepare`` stands in for the future coordinator (T3b): seal with the alert's
token, then publish; abandon on evidence failure; release on a failed publish.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from app.models.domain import DomainName
from app.models.evaluation import EvaluationFailure, PlanUnavailableReason
from app.models.private_parking import AuthorizationSignal, CandidateOutcome
from app.models.private_parking_evidence import EvidenceFailure, EvidenceSamplerLimits
from app.services.evaluation_budget import EvaluationBudget
from app.services.evaluation_worker import EvaluationWorker
from app.services.private_parking_evidence_sampler import EpisodeEvidenceSampler
from tests.test_evaluation_worker import FakeClient
from tests.test_private_parking_evidence_sampler import frame
from tests.test_private_parking_trigger import ZONE_FOOT, Rig as TriggerRig, person

INSIDE = (0.3, 0.8)          # inside the access line, outside every motor zone


class Rig(TriggerRig):
    """Trigger rig whose every observation also offers the same frame to the sampler.

    As in production (frame timestamps come from time.monotonic), observation
    timestamps and the control clock share one timeline here: the budget compares
    a candidate's triggered_at with the control clock."""

    def __init__(self, *, thresholds: dict | None = None, limits: dict | None = None, sync: bool = True) -> None:
        super().__init__(thresholds=thresholds)
        self.ts = self.clock.now
        self.sampler = EpisodeEvidenceSampler(EvidenceSamplerLimits(**(limits or {})), clock=self.clock)
        self.sampler.start_session("s1")
        self.auto_sync = sync

    def feed(self, *tracks, dt: float = 0.2, session: str = "s1", domain_snapshot=None) -> None:
        self.clock.now += dt
        self.sampler.offer(frame(self.seq + 1, self.ts + dt, session=session))
        super().feed(*tracks, dt=dt, session=session, domain_snapshot=domain_snapshot)
        if self.auto_sync:
            self.sync()

    def sync(self) -> bool:
        return self.sampler.apply_markers(self.trigger.pop_markers())

    def journey(self, pid: int, *others, n: int = 6) -> None:
        for _ in range(n):
            self.feed(person(pid, *INSIDE), *others)

    def issue(self, pid: int = 1, *others, x: float = 0.5, journey: bool = True):
        self.walk_in(pid, *others, x=x)
        if journey:
            self.journey(pid, *others)
        self.approach(pid, 3, *others)
        (alert,) = self.trigger.pop_alerts()
        return alert

    def worker(self, client: FakeClient | None = None, budget: EvaluationBudget | None = None):
        client = client or FakeClient()
        budget = budget or EvaluationBudget()
        worker = EvaluationWorker(self.trigger, budget, self.sampler, client, clock=self.clock,
                                  candidate_strategies={DomainName.PARKING_MOTORCYCLE:
                                                        self.sampler.plan_for_candidate})
        worker.start_session("s1")
        return worker, client, budget


def prepare(rig: Rig, alert, *, between=None):
    result = rig.sampler.seal(alert.source_session_id, alert.episode_id, alert.event_id, alert.triggered_at,
                              token=alert.token)
    if between is not None:
        between()
    if result.evidence is None:
        return result, rig.trigger.abandon_candidate(alert.token)
    outcome = rig.trigger.publish_candidate(alert.token)
    if outcome is not CandidateOutcome.PUBLISHED:
        rig.sampler.release(alert.event_id, token=alert.token)
    return result, outcome


def _thread(target, *args) -> threading.Thread:
    thread = threading.Thread(target=target, args=args)
    thread.start()
    return thread


# ── acceptance 15: nothing claimable before the seal publishes ───────────────

def test_worker_claim_while_the_seal_is_held_finds_nothing_until_publication() -> None:
    rig = Rig()
    alert = rig.issue()
    worker, client, budget = rig.worker()
    in_seal, release = threading.Event(), threading.Event()
    original = rig.sampler.seal

    def held_seal(*args, **kwargs):
        in_seal.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    rig.sampler.seal = held_seal
    outcome: list = []
    coordinator = _thread(lambda: outcome.append(prepare(rig, alert)))
    woke: list = []
    waiter = None
    try:
        assert in_seal.wait(2)
        assert not worker.process_one()                                  # PREPARING: nothing to claim
        assert budget.get_snapshot(rig.clock()).total_granted == 0 and client.requests == []
        waiter = _thread(lambda: woke.append(rig.trigger.wait_for_candidate(5)))
    finally:
        release.set()
        coordinator.join(2)
    waiter.join(2)
    assert not coordinator.is_alive() and outcome[0][1] is CandidateOutcome.PUBLISHED and woke == [True]
    assert worker.process_one()
    (request,) = client.requests
    assert request.event_id == alert.event_id and len(request.frame_bundle.frames) == 5
    assert worker.get_snapshot().dispatch_attempts == 1


# ── acceptance 16, 23, 24: stale or late preparation never publishes ─────────

def test_stop_while_the_seal_is_held_leaves_nothing_claimable_and_no_bytes() -> None:
    rig = Rig()
    alert = rig.issue()
    in_seal, release = threading.Event(), threading.Event()
    original = rig.sampler.seal

    def held_seal(*args, **kwargs):
        in_seal.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    rig.sampler.seal = held_seal
    outcome: list = []
    coordinator = _thread(lambda: outcome.append(prepare(rig, alert)))
    try:
        assert in_seal.wait(2)
        rig.trigger.end_session("s1")                                    # Stop
        rig.sampler.end_session("s1")
    finally:
        release.set()
        coordinator.join(2)
    result, published = outcome[0]
    assert result.evidence is None and published is CandidateOutcome.STALE
    assert rig.trigger.claim_next_candidate(0.0) is None and rig.sampler.get_snapshot().held_bytes == 0


def test_restart_with_the_same_session_id_between_seal_and_publish_releases_the_evidence() -> None:
    rig = Rig()
    alert = rig.issue()

    def restart():
        assert rig.sampler.get_snapshot().sealed == 1
        rig.trigger.end_session("s1")
        rig.trigger.start_session("s1")                                  # same id, new generation

    result, published = prepare(rig, alert, between=restart)
    assert result.evidence is not None and published is CandidateOutcome.STALE
    snapshot = rig.sampler.get_snapshot()
    assert snapshot.sealed == 0 and snapshot.count("sealed_released") == 1
    assert rig.trigger.claim_next_candidate(0.0) is None


def test_old_tokens_never_touch_a_new_candidate_after_a_restart_with_the_same_ids() -> None:
    rig = Rig()
    old = rig.issue()
    for service in (rig.trigger, rig.sampler):
        service.end_session("s1")
        service.start_session("s1")
    new = rig.issue(2, x=0.4)
    assert prepare(rig, new)[1] is CandidateOutcome.PUBLISHED
    held = rig.sampler.get_snapshot().held_bytes
    result, outcome = prepare(rig, old)                                  # a late coordinator call
    assert result.failure is EvidenceFailure.STALE_TOKEN and outcome is CandidateOutcome.STALE
    assert rig.trigger.publish_candidate(old.token) is CandidateOutcome.STALE
    assert not rig.sampler.release(new.event_id, token=old.token)
    assert rig.sampler.get_snapshot().held_bytes == held and rig.sampler.get_snapshot().sealed == 1
    assert rig.trigger.claim_next_candidate(0.0).event_id == new.event_id


def test_seal_finishing_after_the_deadline_is_expired_and_released() -> None:
    for sweep_first in (False, True):
        rig = Rig()
        alert = rig.issue()

        def late():
            rig.clock.now += 2.0                                         # deadline passes during the seal
            if sweep_first:
                rig.trigger.pop_alerts()                                 # another call closes it first

        result, outcome = prepare(rig, alert, between=late)
        assert result.evidence is not None and outcome is CandidateOutcome.EXPIRED
        assert rig.sampler.get_snapshot().sealed == 0
        assert rig.trigger.claim_next_candidate(0.0) is None


# ── acceptance 19: expiry while another evaluation is in flight ──────────────

def test_published_candidate_expires_unclaimed_while_another_evaluation_is_pending() -> None:
    rig = Rig()
    worker, client, _ = rig.worker()
    first = rig.issue()
    assert prepare(rig, first)[1] is CandidateOutcome.PUBLISHED
    assert worker.process_one()                                          # in flight (waiting callback)
    second = rig.issue(2, person(1, *INSIDE), x=0.4)
    assert prepare(rig, second)[1] is CandidateOutcome.PUBLISHED
    assert not worker.process_one()                                      # one evaluation at a time
    rig.clock.now += 2.0
    assert rig.sync()                                                    # CANDIDATE_EXPIRED -> release
    assert rig.trigger.get_snapshot().count("candidates_expired") == 1
    assert rig.sampler.failure_for(second.event_id, token=second.token) is EvidenceFailure.RELEASED
    assert [request.event_id for request in client.requests] == [first.event_id]


# ── acceptance 22: seal failure ──────────────────────────────────────────────

def test_seal_failure_abandons_without_budget_or_dispatch() -> None:
    rig = Rig()
    worker, client, budget = rig.worker()
    alert = rig.issue(journey=False)                                     # too few journey frames
    result, outcome = prepare(rig, alert)
    assert result.failure is EvidenceFailure.INSUFFICIENT_JOURNEY and outcome is CandidateOutcome.ABANDONED
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.ABANDONED
    assert not worker.process_one()
    assert budget.get_snapshot(rig.clock()).total_granted == 0 and client.requests == []
    assert rig.trigger.get_snapshot().count("candidates_abandoned") == 1


# ── acceptance 27: PlanUnavailable from the real store ───────────────────────

def test_released_or_expired_evidence_ends_the_claim_without_budget_or_dispatch() -> None:
    for reason in (PlanUnavailableReason.RELEASED, PlanUnavailableReason.EXPIRED):
        rig = Rig(thresholds={"candidate_ttl_seconds": 5.0}, limits={"sealed_ttl_seconds": 1.0})
        worker, client, budget = rig.worker()
        alert = rig.issue()
        assert prepare(rig, alert)[1] is CandidateOutcome.PUBLISHED
        if reason is PlanUnavailableReason.RELEASED:
            assert rig.sampler.release(alert.event_id, token=alert.token)
        else:
            rig.clock.now += 1.5                                         # sealed TTL < candidate TTL
        assert worker.process_one()
        snapshot = worker.get_snapshot()
        assert snapshot.last_failure is EvaluationFailure.EVIDENCE_UNAVAILABLE
        assert dict(snapshot.plan_unavailable_counts)[reason] == 1
        assert rig.trigger.get_snapshot().count("candidates_finished") == 1          # trigger.fail
        assert budget.get_snapshot(rig.clock()).total_granted == 0 and client.requests == []


# ── acceptance 25, 26, 30: overflow resync ───────────────────────────────────

def _flood(rig: Rig, *keep) -> None:
    """Five people cross in together (0.6 s): five ENTERED markers overflow a limit of 4
    well inside the 2 s candidate TTL."""
    group = ((12, 0.30), (13, 0.36), (14, 0.42), (15, 0.62), (16, 0.68))
    for fy in (0.40, 0.45, 0.55):
        rig.feed(*keep, *(person(pid, x, fy) for pid, x in group))


def test_overflow_that_loses_the_authorized_marker_still_cancels_its_samples() -> None:
    rig = Rig(thresholds={"marker_queue_limit": 4})
    rig.walk_in(1)
    rig.journey(1, n=2)
    assert rig.sampler.get_snapshot().active_episodes == 1
    rig.auto_sync = False
    rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, None))
    rig.feed(person(1, *INSIDE))                                         # AUTHORIZED (queued, not applied)
    _flood(rig, person(1, *INSIDE))                                      # overflow drops the queue
    held_before = rig.sampler.get_snapshot().held_bytes
    assert rig.sync()                                                    # authoritative view
    snapshot = rig.sampler.get_snapshot()
    assert snapshot.count("views_applied") == 1 and snapshot.count("episodes_cancelled_resync") == 1
    assert snapshot.active_episodes == 5 and snapshot.held_bytes <= held_before
    authorized = [e.episode_id for e in rig.trigger.get_snapshot().episodes if e.person_track_id == 1]
    assert rig.sampler.seal("s1", authorized[0], "evx", rig.ts).failure is EvidenceFailure.UNKNOWN_EPISODE


def test_an_older_view_never_overwrites_a_newer_authorization() -> None:
    rig = Rig(thresholds={"marker_queue_limit": 4})
    rig.walk_in(1)
    rig.auto_sync = False
    _flood(rig, person(1, *INSIDE))
    old_view = rig.trigger.pop_markers()                                 # lists person 1 as ENTERED
    rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, None))
    rig.feed(person(1, *INSIDE))
    newer = rig.trigger.pop_markers()                                    # AUTHORIZED, seq > view
    assert newer.markers[0].seq > old_view.upto_seq
    assert rig.sampler.apply_markers(newer)
    assert not rig.sampler.apply_markers(old_view)                       # late view: ignored
    snapshot = rig.sampler.get_snapshot()
    assert snapshot.count("views_stale") == 1 and snapshot.active_episodes == 0
    rig.trigger.end_session("s1")
    rig.trigger.start_session("s1")
    rig.sampler.start_session("s1")
    assert rig.sampler.apply_markers(rig.trigger.pop_markers())          # new generation adopted
    assert not rig.sampler.apply_markers(newer)                          # old generation: ignored


def test_overflow_while_preparing_keeps_the_samples_needed_by_the_seal() -> None:
    rig = Rig(thresholds={"marker_queue_limit": 4})
    rig.walk_in(1)
    rig.journey(1)
    rig.auto_sync = False
    rig.approach(1)                                                      # TRIGGERED, candidate PREPARING
    (alert,) = rig.trigger.pop_alerts()
    _flood(rig, person(1, *ZONE_FOOT))
    assert rig.trigger.get_snapshot().resync_required
    assert rig.sync()
    assert rig.sampler.get_snapshot().count("episodes_cancelled_resync") == 0
    result, outcome = prepare(rig, alert)
    assert result.failure is None and outcome is CandidateOutcome.PUBLISHED


def test_a_preparing_candidate_that_expires_before_its_seal_releases_its_samples() -> None:
    rig = Rig(thresholds={"marker_queue_limit": 4})
    rig.walk_in(1)
    rig.journey(1)
    rig.auto_sync = False
    rig.approach(1)
    (alert,) = rig.trigger.pop_alerts()
    _flood(rig, person(1, *ZONE_FOOT))
    rig.clock.now += 2.0                                                 # expires before any seal
    assert rig.sync()
    snapshot = rig.sampler.get_snapshot()
    assert snapshot.active_episodes == 5                                 # only the five new ENTERED ones
    result, outcome = prepare(rig, alert)                                # seal may still be attempted
    assert result.failure is EvidenceFailure.UNKNOWN_EPISODE and outcome is CandidateOutcome.EXPIRED
    assert rig.trigger.claim_next_candidate(0.0) is None


# ── delta 2: episode instances with a reused episode_id in one generation ────

def test_a_late_seal_with_the_old_token_cannot_take_a_new_episode_with_the_same_id(monkeypatch) -> None:
    from app.services import private_parking_trigger as module
    minted = iter(["same-ep", "ev-a", "same-ep", "ev-b"])           # crossing A, issue A, crossing B, issue B
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex=next(minted)))
    rig = Rig()
    alert_a = rig.issue()
    entered, release = threading.Event(), threading.Event()
    original = rig.sampler.seal

    def held_seal(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    rig.sampler.seal = held_seal
    late: list = []
    coordinator = _thread(lambda: late.append(prepare(rig, alert_a)))
    try:
        assert entered.wait(2)                                       # the old seal is in flight
        rig.sampler.seal = original
        rig.feed(person(1, 0.5, 0.30))                               # A walks out (samples kept: preparing)
        rig.clock.now += 2.0
        assert rig.sync()                                            # A's candidate closes -> A released
        alert_b = rig.issue(2, x=0.4)                                # B: same episode_id, same generation
        assert alert_b.episode_id == alert_a.episode_id
        assert alert_b.token.episode_instance != alert_a.token.episode_instance
        before = rig.sampler.get_snapshot()
    finally:
        release.set()
        coordinator.join(2)
    result, outcome = late[0]
    assert result.failure is EvidenceFailure.STALE_TOKEN and outcome is CandidateOutcome.EXPIRED
    after = rig.sampler.get_snapshot()
    assert (after.active_episodes, after.held_frames, after.held_bytes, after.sealed) == \
        (before.active_episodes, before.held_frames, before.held_bytes, before.sealed)
    wrong = rig.sampler.seal("s1", alert_b.episode_id, alert_b.event_id, alert_b.triggered_at, token=alert_a.token)
    assert wrong.failure is EvidenceFailure.STALE_TOKEN
    assert prepare(rig, alert_b)[1] is CandidateOutcome.PUBLISHED
