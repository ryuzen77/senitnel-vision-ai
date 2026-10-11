"""Private-parking contracts across the real trigger, evidence sampler, coordinator and
evaluation worker (T3a1 contracts, driven by the T3b coordinator). Synthetic frames and
tracks, fake clock, fake client and a recording audio gate only: no video, model,
network, runtime wiring or speaker.

The coordinator handles every alert inside ``observe`` (audio first, then seal and
publish / abandon / release with the alert's own identity). Tests that need to act
"during" a seal wrap ``sampler.seal``; tests that need a late or stale call use the
coordinator's own ``_prepare`` with a recorded alert.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from app.models.domain import DomainName
from app.models.evaluation import EvaluationFailure, PlanUnavailableReason
from app.models.private_parking import AuthorizationSignal, CandidateOutcome
from app.models.private_parking_evidence import EvidenceFailure, EvidenceSamplerLimits
from app.models.tracking import TrackingSnapshot
from app.services.evaluation_budget import EvaluationBudget
from app.services.evaluation_worker import EvaluationWorker
from app.services.private_parking_coordinator import PrivateParkingCoordinator
from app.services.private_parking_evidence_sampler import EpisodeEvidenceSampler
from tests.test_evaluation_worker import FakeClient
from tests.test_private_parking_evidence_sampler import frame
from tests.test_private_parking_trigger import H, W, ZONE_FOOT, Rig as TriggerRig, domain, person

INSIDE = (0.3, 0.8)          # inside the access line, outside every motor zone
WAIT = 5.0


class RecordingGate:
    def __init__(self, log: list) -> None:
        self.log = log

    def offer_local(self, event_id: str, session_id: str) -> None:
        self.log.append(("audio", event_id))


class Rig(TriggerRig):
    """Every observation goes through the real coordinator; every frame goes to the
    sampler (in production through the tee). Observation timestamps and the control
    clock share one timeline, as in production (frames use time.monotonic)."""

    def __init__(self, *, thresholds: dict | None = None, limits: dict | None = None,
                 coordinate: bool = True) -> None:
        super().__init__(thresholds=thresholds)
        self.ts = self.clock.now
        self.sampler = EpisodeEvidenceSampler(EvidenceSamplerLimits(**(limits or {})), clock=self.clock)
        self.sampler.start_session("s1")
        self.log: list = []
        self.coordinator = PrivateParkingCoordinator(self.trigger, self.sampler, audio_gate=RecordingGate(self.log))
        self.coordinate = coordinate
        self.alerts: list = []
        self.outcomes: dict = {}
        self.seal_results: dict = {}
        pop, publish, abandon, seal = (self.trigger.pop_alerts, self.trigger.publish_candidate,
                                       self.trigger.abandon_candidate, self.sampler.seal)

        def recorded_pop():
            alerts = pop()
            self.alerts.extend(alerts)
            return alerts

        def recorded_publish(token):
            self.outcomes[token.event_id] = outcome = publish(token)
            return outcome

        def recorded_abandon(token):
            self.outcomes[token.event_id] = outcome = abandon(token)
            return outcome

        def recorded_seal(*args, **kwargs):
            self.log.append(("seal", args[2]))
            self.seal_results[args[2]] = result = self.seal_impl(seal, *args, **kwargs)
            return result

        self.seal_impl = lambda real, *args, **kwargs: real(*args, **kwargs)   # tests may wrap it
        self.trigger.pop_alerts = recorded_pop
        self.trigger.publish_candidate = recorded_publish
        self.trigger.abandon_candidate = recorded_abandon
        self.sampler.seal = recorded_seal

    def feed(self, *tracks, dt: float = 0.2, session: str = "s1", domain_snapshot=None) -> None:
        self.clock.now += dt
        self.seq += 1
        self.ts += dt
        self.sampler.offer(frame(self.seq, self.ts, session=session))
        snapshot = TrackingSnapshot(session, self.seq, self.ts, W, H, tuple(tracks) + tuple(self.parked))
        if self.coordinate:
            self.coordinator.observe(domain_snapshot or domain(session), snapshot)
        else:
            self.trigger.observe(domain_snapshot or domain(session), snapshot)

    def journey(self, pid: int, *others, n: int = 6) -> None:
        for _ in range(n):
            self.feed(person(pid, *INSIDE), *others)

    def lead_up(self, pid: int = 1, *others, x: float = 0.5, journey: bool = True) -> None:
        self.walk_in(pid, *others, x=x)
        if journey:
            self.journey(pid, *others)
        self.approach(pid, 2, *others)                 # one observation short of the trigger

    def final(self, pid: int = 1, *others) -> None:
        self.feed(person(pid, *ZONE_FOOT), *others)

    def issue(self, pid: int = 1, *others, x: float = 0.5, journey: bool = True):
        self.lead_up(pid, *others, x=x, journey=journey)
        self.final(pid, *others)
        return self.alerts[-1]

    def worker(self, client: FakeClient | None = None, budget: EvaluationBudget | None = None):
        client = client or FakeClient()
        budget = budget or EvaluationBudget()
        worker = EvaluationWorker(self.trigger, budget, self.sampler, client, clock=self.clock,
                                  candidate_strategies={DomainName.PARKING_MOTORCYCLE:
                                                        self.sampler.plan_for_candidate})
        worker.start_session("s1")
        return worker, client, budget

    def hold_seals(self):
        """Make the next seal wait on a gate; returns (entered, release)."""
        entered, release = threading.Event(), threading.Event()

        def held(real, *args, **kwargs):
            entered.set()
            assert release.wait(WAIT)
            return real(*args, **kwargs)

        self.seal_impl = held
        return entered, release


def _thread(target, *args) -> threading.Thread:
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


# ── audio first; acceptance 10 and 15 ────────────────────────────────────────

def test_audio_runs_before_the_seal_in_the_same_observation() -> None:
    rig = Rig()
    alert = rig.issue()
    audio, seal = rig.log.index(("audio", alert.event_id)), rig.log.index(("seal", alert.event_id))
    assert audio < seal and rig.outcomes[alert.event_id] is CandidateOutcome.PUBLISHED


def test_worker_claim_while_the_seal_is_held_finds_nothing_until_publication() -> None:
    rig = Rig()
    worker, client, budget = rig.worker()
    rig.lead_up()
    entered, release = rig.hold_seals()
    observer = _thread(rig.final)
    woke: list = []
    waiter = None
    try:
        assert entered.wait(WAIT)
        alert = rig.alerts[-1]
        assert ("audio", alert.event_id) in rig.log                        # local warning already offered
        assert not worker.process_one()                                    # PREPARING: nothing to claim
        assert budget.get_snapshot(rig.clock()).total_granted == 0 and client.requests == []
        waiter = _thread(lambda: woke.append(rig.trigger.wait_for_candidate(5)))
    finally:
        release.set()
        observer.join(WAIT)
    waiter.join(WAIT)
    assert not observer.is_alive() and rig.outcomes[alert.event_id] is CandidateOutcome.PUBLISHED
    assert woke == [True]
    assert worker.process_one()
    (request,) = client.requests
    assert request.event_id == alert.event_id and len(request.frame_bundle.frames) == 5
    assert worker.get_snapshot().dispatch_attempts == 1


# ── acceptance 16, 23, 24: stale or late preparation never publishes (K5) ────

def test_stop_while_the_seal_is_held_leaves_nothing_claimable_and_no_bytes() -> None:
    rig = Rig()
    rig.lead_up()
    entered, release = rig.hold_seals()
    observer = _thread(rig.final)
    try:
        assert entered.wait(WAIT)
        rig.coordinator.end_session("s1")                                  # detector slot: Stop
        rig.sampler.end_session("s1")                                      # tee slot: Stop
    finally:
        release.set()
        observer.join(WAIT)
    alert = rig.alerts[-1]
    assert rig.seal_results[alert.event_id].evidence is None
    assert rig.outcomes[alert.event_id] is CandidateOutcome.STALE
    assert rig.trigger.claim_next_candidate(0.0) is None and rig.sampler.get_snapshot().held_bytes == 0


def test_restart_with_the_same_session_id_between_seal_and_publish_releases_the_evidence() -> None:
    rig = Rig()
    rig.lead_up()

    def seal_then_restart(real, *args, **kwargs):
        result = real(*args, **kwargs)
        assert rig.sampler.get_snapshot().sealed == 1
        rig.coordinator.end_session("s1")
        rig.coordinator.start_session("s1")                                # same id, new generation
        return result

    rig.seal_impl = seal_then_restart
    rig.final()
    alert = rig.alerts[-1]
    assert rig.seal_results[alert.event_id].evidence is not None
    assert rig.outcomes[alert.event_id] is CandidateOutcome.STALE
    snapshot = rig.sampler.get_snapshot()
    assert snapshot.sealed == 0 and snapshot.count("sealed_released") == 1
    assert rig.trigger.claim_next_candidate(0.0) is None


def test_old_tokens_never_touch_a_new_candidate_after_a_restart_with_the_same_ids() -> None:
    rig = Rig()
    old = rig.issue()
    rig.coordinator.end_session("s1")
    rig.sampler.end_session("s1")
    rig.sampler.start_session("s1")
    rig.coordinator.start_session("s1")
    new = rig.issue(2, x=0.4)
    assert rig.outcomes[new.event_id] is CandidateOutcome.PUBLISHED
    held = rig.sampler.get_snapshot().held_bytes
    rig.coordinator._prepare(old)                                          # a late call  # noqa: SLF001
    assert rig.seal_results[old.event_id].failure is EvidenceFailure.STALE_TOKEN
    assert rig.outcomes[old.event_id] is CandidateOutcome.STALE
    assert not rig.sampler.release(new.event_id, token=old.token)
    assert rig.sampler.get_snapshot().held_bytes == held and rig.sampler.get_snapshot().sealed == 1
    assert rig.trigger.claim_next_candidate(0.0).event_id == new.event_id


def test_seal_finishing_after_the_deadline_is_expired_and_released() -> None:
    for sweep_first in (False, True):
        rig = Rig()
        rig.lead_up()

        def late(real, *args, **kwargs):
            result = real(*args, **kwargs)
            rig.clock.now += 2.0                                           # deadline passes during the seal
            if sweep_first:
                rig.trigger.get_snapshot()                                 # another call closes it first
            return result

        rig.seal_impl = late
        rig.final()
        alert = rig.alerts[-1]
        assert rig.seal_results[alert.event_id].evidence is not None
        assert rig.outcomes[alert.event_id] is CandidateOutcome.EXPIRED
        assert rig.sampler.get_snapshot().sealed == 0
        assert rig.trigger.claim_next_candidate(0.0) is None


# ── acceptance 3 and 19; K2 ──────────────────────────────────────────────────

def test_a_blocked_dispatch_never_delays_the_local_warning_or_the_evidence() -> None:
    rig = Rig()
    in_dispatch, release_dispatch = threading.Event(), threading.Event()

    class BlockingClient(FakeClient):
        def dispatch(self, request):
            in_dispatch.set()
            assert release_dispatch.wait(WAIT)
            return super().dispatch(request)

    worker, client, _ = rig.worker(BlockingClient())
    first = rig.issue()
    dispatcher = _thread(worker.process_one)
    try:
        assert in_dispatch.wait(WAIT)                                      # HTTP "in flight"
        second = rig.issue(2, person(1, *INSIDE), x=0.4)
        assert ("audio", second.event_id) in rig.log
        assert rig.outcomes[second.event_id] is CandidateOutcome.PUBLISHED
    finally:
        release_dispatch.set()
        dispatcher.join(WAIT)
    assert [request.event_id for request in client.requests] == [first.event_id]


def test_published_candidate_expires_unclaimed_while_another_evaluation_is_pending() -> None:
    rig = Rig()
    worker, client, _ = rig.worker()
    first = rig.issue()
    assert worker.process_one()                                            # in flight (waiting callback)
    second = rig.issue(2, person(1, *INSIDE), x=0.4)
    assert rig.outcomes[second.event_id] is CandidateOutcome.PUBLISHED
    assert not worker.process_one()                                        # one evaluation at a time
    rig.clock.now += 2.0
    rig.feed(person(2, *INSIDE))                                           # next observation drains CANDIDATE_EXPIRED
    assert rig.trigger.get_snapshot().count("candidates_expired") == 1
    assert rig.sampler.failure_for(second.event_id, token=second.token) is EvidenceFailure.RELEASED
    assert [request.event_id for request in client.requests] == [first.event_id]


def test_without_any_worker_candidates_are_published_and_their_evidence_expires() -> None:
    rig = Rig()
    alert = rig.issue()
    assert rig.outcomes[alert.event_id] is CandidateOutcome.PUBLISHED
    rig.clock.now += 2.0
    rig.feed(person(1, *INSIDE))
    assert rig.trigger.get_snapshot().count("candidates_expired") == 1
    assert rig.sampler.get_snapshot().sealed == 0


# ── acceptance 22 and 27 ─────────────────────────────────────────────────────

def test_seal_failure_abandons_without_budget_or_dispatch() -> None:
    rig = Rig()
    worker, client, budget = rig.worker()
    alert = rig.issue(journey=False)                                       # too few journey frames
    assert rig.seal_results[alert.event_id].failure is EvidenceFailure.INSUFFICIENT_JOURNEY
    assert rig.outcomes[alert.event_id] is CandidateOutcome.ABANDONED
    assert rig.trigger.publish_candidate(alert.token) is CandidateOutcome.ABANDONED
    assert not worker.process_one()
    assert budget.get_snapshot(rig.clock()).total_granted == 0 and client.requests == []
    assert rig.trigger.get_snapshot().count("candidates_abandoned") == 1


def test_released_or_expired_evidence_ends_the_claim_without_budget_or_dispatch() -> None:
    for reason in (PlanUnavailableReason.RELEASED, PlanUnavailableReason.EXPIRED):
        rig = Rig(thresholds={"candidate_ttl_seconds": 5.0}, limits={"sealed_ttl_seconds": 1.0})
        worker, client, budget = rig.worker()
        alert = rig.issue()
        assert rig.outcomes[alert.event_id] is CandidateOutcome.PUBLISHED
        if reason is PlanUnavailableReason.RELEASED:
            assert rig.sampler.release(alert.event_id, token=alert.token)
        else:
            rig.clock.now += 1.5                                           # sealed TTL < candidate TTL
        assert worker.process_one()
        snapshot = worker.get_snapshot()
        assert snapshot.last_failure is EvaluationFailure.EVIDENCE_UNAVAILABLE
        assert dict(snapshot.plan_unavailable_counts)[reason] == 1
        assert rig.trigger.get_snapshot().count("candidates_finished") == 1           # trigger.fail
        assert budget.get_snapshot(rig.clock()).total_granted == 0 and client.requests == []


# ── acceptance 25, 26, 30: overflow resync through the coordinator ───────────

GROUP = ((12, 0.30), (13, 0.36), (14, 0.42), (15, 0.62), (16, 0.68))


def _flood(rig: Rig, *keep, before_crossing=None) -> None:
    """Five people cross in together on the third feed: five ENTERED markers overflow a
    queue limit of 4 inside ONE observation."""
    for index, fy in enumerate((0.40, 0.45, 0.55)):
        if index == 2 and before_crossing is not None:
            before_crossing()
        rig.feed(*keep, *(person(pid, x, fy) for pid, x in GROUP))


def test_overflow_that_loses_the_authorized_marker_still_cancels_its_samples() -> None:
    rig = Rig(thresholds={"marker_queue_limit": 4})
    rig.walk_in(1)
    rig.journey(1, n=2)
    assert rig.sampler.get_snapshot().active_episodes == 1
    _flood(rig, person(1, *INSIDE),
           before_crossing=lambda: rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, None)))
    snapshot = rig.sampler.get_snapshot()
    assert snapshot.count("views_applied") == 1 and snapshot.count("episodes_cancelled_resync") == 1
    assert snapshot.active_episodes == 5
    authorized = [e.episode_id for e in rig.trigger.get_snapshot().episodes if e.person_track_id == 1]
    assert rig.sampler.seal("s1", authorized[0], "evx", rig.ts).failure is EvidenceFailure.UNKNOWN_EPISODE


def test_an_older_view_never_overwrites_a_newer_authorization() -> None:
    rig = Rig(thresholds={"marker_queue_limit": 4}, coordinate=False)     # sampler ordering only
    rig.walk_in(1)
    assert rig.sampler.apply_markers(rig.trigger.pop_markers())
    _flood(rig, person(1, *INSIDE))
    old_view = rig.trigger.pop_markers()                                   # lists person 1 as ENTERED
    rig.auth.queue.append(AuthorizationSignal("s1", 1, rig.ts, None))
    rig.feed(person(1, *INSIDE))
    newer = rig.trigger.pop_markers()                                      # AUTHORIZED, seq > view
    assert newer.markers[0].seq > old_view.upto_seq
    assert rig.sampler.apply_markers(newer)
    assert not rig.sampler.apply_markers(old_view)                         # late view: ignored
    snapshot = rig.sampler.get_snapshot()
    assert snapshot.count("views_stale") == 1 and snapshot.active_episodes == 0
    rig.trigger.end_session("s1")
    rig.trigger.start_session("s1")
    rig.sampler.start_session("s1")
    assert rig.sampler.apply_markers(rig.trigger.pop_markers())            # new generation adopted
    assert not rig.sampler.apply_markers(newer)                            # old generation: ignored


def test_overflow_while_preparing_keeps_the_samples_needed_by_the_seal() -> None:
    rig = Rig(thresholds={"marker_queue_limit": 4})
    rig.walk_in(1)
    rig.journey(1)
    _flood(rig, person(1, *ZONE_FOOT))             # third close observation = the crossing feed
    (alert,) = rig.alerts
    assert rig.sampler.get_snapshot().count("views_applied") == 1
    assert rig.sampler.get_snapshot().count("episodes_cancelled_resync") == 0
    assert rig.seal_results[alert.event_id].failure is None
    assert rig.outcomes[alert.event_id] is CandidateOutcome.PUBLISHED


# ── marker sync failure holds publication (real cores) ───────────────────────

def _fail_next_apply(rig: Rig) -> None:
    real = rig.sampler.apply_markers
    state = {"failed": False}

    def apply(batch):
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("apply failed")
        return real(batch)

    rig.sampler.apply_markers = apply


def test_a_failed_marker_apply_holds_publication_until_the_resync_view() -> None:
    rig = Rig()
    rig.lead_up()
    _fail_next_apply(rig)
    rig.final()
    alert = rig.alerts[-1]
    assert ("audio", alert.event_id) in rig.log                            # audio still ran
    assert alert.event_id not in rig.seal_results and alert.event_id not in rig.outcomes
    assert rig.trigger.get_snapshot().preparing_candidates == 1
    rig.feed(person(1, *ZONE_FOOT))                                        # the view is applied first
    assert rig.sampler.get_snapshot().count("views_applied") == 1
    assert rig.outcomes[alert.event_id] is CandidateOutcome.PUBLISHED


def test_a_held_candidate_that_expires_is_never_published_and_its_samples_go() -> None:
    rig = Rig()
    rig.lead_up()
    _fail_next_apply(rig)
    rig.final()
    alert = rig.alerts[-1]
    rig.clock.now += 2.0                                                   # expires while held
    rig.feed(person(1, *ZONE_FOOT))
    assert rig.seal_results[alert.event_id].failure is EvidenceFailure.UNKNOWN_EPISODE
    assert rig.outcomes[alert.event_id] is CandidateOutcome.EXPIRED
    assert rig.trigger.claim_next_candidate(0.0) is None
    assert rig.sampler.get_snapshot().active_episodes == 0 and rig.sampler.get_snapshot().sealed == 0


# ── delta 2: episode instances with a reused episode_id in one generation ────

def test_a_late_seal_with_the_old_token_cannot_take_a_new_episode_with_the_same_id(monkeypatch) -> None:
    from app.services import private_parking_trigger as module
    minted = iter(["same-ep", "ev-a", "same-ep", "ev-b"])                 # crossing A, issue A, crossing B, issue B
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex=next(minted)))
    rig = Rig()
    rig.lead_up()

    def refuse_first(real, *args, **kwargs):                               # A's own seal never happens
        raise RuntimeError("seal unavailable")

    rig.seal_impl = refuse_first
    rig.final()
    alert_a = rig.alerts[-1]
    assert rig.outcomes[alert_a.event_id] is CandidateOutcome.ABANDONED
    rig.seal_impl = lambda real, *args, **kwargs: real(*args, **kwargs)
    rig.feed(person(1, 0.5, 0.30))                                         # A walks out
    rig.lead_up(2, x=0.4)                                                  # B: same episode_id, same generation
    before = rig.sampler.get_snapshot()
    rig.coordinator._prepare(alert_a)                                      # a late call  # noqa: SLF001
    assert rig.seal_results[alert_a.event_id].failure is EvidenceFailure.STALE_TOKEN
    after = rig.sampler.get_snapshot()
    assert (after.active_episodes, after.held_frames, after.held_bytes, after.sealed) == \
        (before.active_episodes, before.held_frames, before.held_bytes, before.sealed)
    rig.final(2)
    alert_b = rig.alerts[-1]
    assert alert_b.episode_id == alert_a.episode_id
    assert alert_b.token.episode_instance != alert_a.token.episode_instance
    assert rig.outcomes[alert_b.event_id] is CandidateOutcome.PUBLISHED


def test_an_exception_right_after_an_applied_batch_or_view_still_resyncs_and_publishes() -> None:
    rig = Rig()
    rig.lead_up()
    real = rig.sampler.apply_markers
    applied: list = []
    raise_after = {"count": 2}          # a marker batch, then the resync view that follows it

    def apply_then_raise(batch):
        result = real(batch)            # the sampler has applied it ...
        applied.append(batch)
        if raise_after["count"]:
            raise_after["count"] -= 1
            raise RuntimeError("after apply")   # ... but the coordinator sees a failure
        return result

    rig.sampler.apply_markers = apply_then_raise
    emitted = rig.trigger.get_snapshot().count("markers_emitted")
    rig.final()                                                            # batch, then view: both raise
    alert = rig.alerts[-1]
    assert alert.event_id not in rig.outcomes                              # held: sync not proven
    views = [batch for batch in applied if batch.view is not None]
    assert len(views) == 1 and views[0].upto_seq > applied[0].upto_seq     # the view took a newer seq
    rig.feed(person(1, *ZONE_FOOT))                                        # the next view proves sync
    assert rig.trigger.get_snapshot().count("markers_emitted") == emitted + 1   # only the TRIGGERED marker
    views = [batch for batch in applied if batch.view is not None]
    assert len(views) == 2 and views[1].upto_seq > views[0].upto_seq
    assert rig.outcomes[alert.event_id] is CandidateOutcome.PUBLISHED
    snapshot = rig.sampler.get_snapshot()
    assert snapshot.count("views_applied") == 2 and snapshot.sealed == 1
    state = (snapshot.active_episodes, snapshot.held_frames, snapshot.held_bytes, snapshot.sealed)
    stale_markers = snapshot.count("markers_stale")
    assert not real(views[0])                                              # an old view is refused
    real(applied[0])                                                       # old markers are skipped
    after = rig.sampler.get_snapshot()
    assert (after.active_episodes, after.held_frames, after.held_bytes, after.sealed) == state
    assert after.count("views_stale") == 1
    assert after.count("markers_stale") == stale_markers + len(applied[0].markers) > stale_markers
    assert rig.trigger.claim_next_candidate(0.0).event_id == alert.event_id
