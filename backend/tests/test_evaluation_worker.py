"""No-transport worker tests with fake trigger, frame buffer, clock, and client."""

from __future__ import annotations

from collections import deque
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
import threading
import time

import pytest

from app.models.domain import DomainName
from app.models.evaluation import (
    CallbackDisposition, EvaluationCallback, EvaluationFailure, EvaluationRequest,
    EvaluationWorkerState, SubjectProfile, ThreatAssessment, ThreatStatus,
)
from app.models.event_frame import (
    BundleDecision, BundleStatus, EventFrameBundle, FrameSelectionPlan, SelectedFrame,
    make_parking_frame_selection_plan,
)
from app.models.parking_candidate import (
    ParkingCandidateEvent, ParkingCandidateEvidence, ParkingCandidateState,
)
from app.services.evaluation_budget import EvaluationBudget
from app.services.evaluation_worker import EvaluationWorker
from app.services.parking_candidate_trigger import ParkingCandidateTrigger
from tests.test_parking_candidate_trigger import _observe_series


class FakeClock:
    def __init__(self, now: float = 0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeTrigger:
    def __init__(self, session: str = "one") -> None:
        self.session = session
        self.pending: str | None = None
        self.events: deque[ParkingCandidateEvent] = deque()
        self.claimed: list[str] = []
        self.completed: list[str] = []
        self.failed: list[str] = []
        self._condition = threading.Condition()
        self._generation = 0

    def queue(self, event: ParkingCandidateEvent) -> None:
        with self._condition:
            self.events.append(event)
            self._condition.notify_all()

    def claim_next_candidate(self, now: float):
        with self._condition:
            if self.pending or not self.events:
                return None
            event = self.events.popleft()
            self.pending = event.event_id
            self.claimed.append(event.event_id)
            return event

    def get_snapshot(self):
        with self._condition:
            return SimpleNamespace(source_session_id=self.session, pending_event_id=self.pending)

    def complete(self, event_id: str, now: float) -> bool:
        with self._condition:
            if self.pending != event_id:
                return False
            self.pending = None
            self.completed.append(event_id)
            return True

    def fail(self, event_id: str, now: float) -> bool:
        with self._condition:
            if self.pending != event_id:
                return False
            self.pending = None
            self.failed.append(event_id)
            return True

    def end_session(self) -> None:
        with self._condition:
            self.session = None
            self.pending = None
            self.events.clear()
            self._generation += 1
            self._condition.notify_all()

    def wait_for_candidate(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            generation = self._generation
            while not self.events:
                if generation != self._generation:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def wake_waiters(self) -> None:
        with self._condition:
            self._generation += 1
            self._condition.notify_all()


class FakeBuffer:
    def __init__(self, session: str = "one", status: BundleStatus = BundleStatus.READY) -> None:
        self.session = session
        self.status = status
        self.calls: list[FrameSelectionPlan] = []
        self.timeout_values: list[float] = []
        self.bundle_override: EventFrameBundle | None = None

    def get_snapshot(self):
        return SimpleNamespace(source_session_id=self.session)

    def wait_for_bundle(self, plan: FrameSelectionPlan, timeout_seconds: float) -> BundleDecision:
        self.calls.append(plan)
        self.timeout_values.append(timeout_seconds)
        if self.status is not BundleStatus.READY:
            return BundleDecision(self.status)
        if self.bundle_override is not None:
            return BundleDecision(BundleStatus.READY, self.bundle_override)
        frames = tuple(
            SelectedFrame(index + 1, plan.triggered_at + offset, offset, offset, 0.0,
                          640, 480, "image/jpeg", b"\xff\xd8jpeg\xff\xd9")
            for index, offset in enumerate(plan.target_offsets_seconds)
        )
        return BundleDecision(BundleStatus.READY, EventFrameBundle(
            plan.event_id, plan.source_session_id, plan.domain, plan.triggered_at,
            frames, sum(len(frame.jpeg) for frame in frames),
        ))


class FakeClient:
    name = "fake-langflow"

    def __init__(self, exception: Exception | None = None) -> None:
        self.exception = exception
        self.requests: list[EvaluationRequest] = []
        self.trigger: FakeTrigger | None = None
        self.budget: EvaluationBudget | None = None

    def dispatch(self, request: EvaluationRequest) -> None:
        self.requests.append(request)
        if self.trigger is not None:
            assert self.trigger._condition.acquire(blocking=False)
            self.trigger._condition.release()
        if self.budget is not None:
            assert self.budget._lock.acquire(blocking=False)
            self.budget._lock.release()
        if self.exception is not None:
            raise self.exception


def _accept(worker: EvaluationWorker, assessment: ThreatAssessment | None = None,
            event_id: str = "e1", session: str = "one", now: float = 11) -> CallbackDisposition:
    return worker.accept_callback(
        EvaluationCallback(event_id, session, assessment or _assessment()), now,
    )


def _assessment(score: int = 10, status: ThreatStatus = ThreatStatus.NORMAL) -> ThreatAssessment:
    return ThreatAssessment(score, status, "Observasi terbatas dari frame.",
                            SubjectProfile(True, "jaket", "berjalan", ("motor",), ("helm",)))


def _event(event_id: str = "e1", at: float = 10.0, *, domain: DomainName = DomainName.PARKING_MOTORCYCLE,
           session: str = "one") -> ParkingCandidateEvent:
    evidence = ParkingCandidateEvidence(4, 2.0, .01, .02, .04, .0, .03)
    return ParkingCandidateEvent(
        event_id, session, domain, 1, 2, at - 2, at, 10, evidence,
        (1, 2, 3, 4, 5), ParkingCandidateState.CANDIDATE,
    )


def _worker(*, client=None, buffer=None, trigger=None, budget=None, clock=None, strategies=None):
    trigger = trigger or FakeTrigger()
    budget = budget or EvaluationBudget()
    buffer = buffer or FakeBuffer()
    client = client or FakeClient()
    clock = clock or FakeClock(10)
    worker = EvaluationWorker(trigger, budget, buffer, client, clock=clock, strategies=strategies)
    return worker, trigger, budget, buffer, client, clock


def test_current_view_idle_disabled_and_active_session() -> None:
    worker, trigger, budget, buffer, client, clock = _worker()
    idle = worker.get_current_view()
    assert (idle.enabled, idle.source_session_id, idle.process_state,
            idle.active_event_id, idle.latest_result) == (True, None, EvaluationWorkerState.IDLE, None, None)
    worker.start_session("one")
    assert worker.get_current_view().source_session_id == "one"
    assert worker.get_current_view().process_state is EvaluationWorkerState.IDLE
    disabled = EvaluationWorker(trigger, budget, buffer, client, clock=clock, enabled=False)
    disabled.start_session("one")
    assert not disabled.get_current_view().enabled
    assert disabled.get_current_view().source_session_id == "one"
    assert disabled.get_current_view().latest_result is None


@pytest.mark.parametrize("score,status", [
    (10, ThreatStatus.NORMAL), (50, ThreatStatus.WARNING), (90, ThreatStatus.CRITICAL),
])
def test_current_view_waiting_completed_and_next_event_identity(score, status) -> None:
    worker, trigger, budget, buffer, client, clock = _worker()
    worker.start_session("one")
    trigger.queue(_event())
    assert worker.process_one(10)
    waiting = worker.get_current_view()
    assert waiting.process_state is EvaluationWorkerState.WAITING_CALLBACK
    assert waiting.active_event_id == "e1" and waiting.latest_result is None
    assert _accept(worker, _assessment(score, status)) is CallbackDisposition.ACCEPTED
    completed = worker.get_current_view()
    assert completed.process_state is EvaluationWorkerState.COMPLETED
    assert completed.active_event_id is None
    assert completed.latest_result is not None
    assert completed.latest_result.event_id == "e1"
    assert completed.latest_result.assessment.status is status
    assert completed.latest_result.assessment is not worker._latest.assessment
    assert completed.latest_result.assessment.subject_profile is not worker._latest.assessment.subject_profile
    trigger.queue(_event("e2", at=22))
    clock.now = 22
    assert worker.process_one(22)
    next_view = worker.get_current_view()
    assert next_view.active_event_id == "e2"
    assert next_view.latest_result is not None and next_view.latest_result.event_id == "e1"
    assert next_view.latest_result.source_session_id == next_view.source_session_id == "one"


def test_current_view_timeout_failure_and_lifecycle_reset() -> None:
    worker, trigger, budget, buffer, client, clock = _worker()
    worker.start_session("one")
    trigger.queue(_event())
    assert worker.process_one(10)
    assert worker.sweep(56)
    timed_out = worker.get_current_view()
    assert timed_out.process_state is EvaluationWorkerState.TIMED_OUT
    assert timed_out.active_event_id is None and timed_out.latest_result is None
    worker.end_session("one")
    stopped = worker.get_current_view()
    assert stopped.source_session_id is None and stopped.latest_result is None
    assert stopped.process_state is EvaluationWorkerState.IDLE
    assert worker.get_snapshot().state is EvaluationWorkerState.CANCELLED
    worker.start_session("two")
    restarted = worker.get_current_view()
    assert restarted.source_session_id == "two" and restarted.process_state is EvaluationWorkerState.IDLE
    assert restarted.active_event_id is None and restarted.latest_result is None
    trigger.session = "two"; buffer.session = "two"
    client.exception = RuntimeError("private dispatch failure")
    trigger.queue(_event("e2", at=70, session="two"))
    clock.now = 70
    assert worker.process_one(70)
    # Generic dispatch errors are uncertain and wait for a callback, not FAILED.
    assert worker.get_current_view().process_state is EvaluationWorkerState.WAITING_CALLBACK


@pytest.mark.parametrize("phase", ["without_evaluation", "pending", "completed"])
def test_current_view_after_stop_is_idle_without_changing_internal_state(phase) -> None:
    worker, trigger, budget, buffer, client, clock = _worker()
    worker.start_session("one")
    if phase != "without_evaluation":
        trigger.queue(_event())
        assert worker.process_one(10)
    if phase == "completed":
        assert _accept(worker) is CallbackDisposition.ACCEPTED
        assert worker.get_current_view().latest_result is not None
    worker.end_session("one")
    assert worker.get_current_view().source_session_id is None
    assert worker.get_current_view().process_state is EvaluationWorkerState.IDLE
    assert worker.get_current_view().active_event_id is None
    assert worker.get_current_view().latest_result is None
    assert worker.get_snapshot().state is EvaluationWorkerState.CANCELLED
    assert _accept(worker) is CallbackDisposition.REJECTED
    assert worker.get_current_view().latest_result is None
    if phase == "pending":
        assert trigger.failed == ["e1"]
        assert budget.get_snapshot(11).pending_event_id is None


def test_current_view_atomic_across_callback_and_session_change() -> None:
    worker, trigger, budget, buffer, client, clock = _worker()
    worker.start_session("one")
    trigger.queue(_event())
    assert worker.process_one(10)
    seen = []
    entered = threading.Event()
    with worker._state_lock:
        thread = threading.Thread(target=lambda: (entered.set(), seen.append(worker.get_current_view())))
        thread.start()
        assert entered.wait(1)
        assert _accept(worker) is CallbackDisposition.ACCEPTED
        worker.end_session("one")
        worker.start_session("two")
    thread.join(1)
    assert not thread.is_alive()
    assert len(seen) == 1
    assert seen[0].source_session_id == "two"
    assert seen[0].active_event_id is None and seen[0].latest_result is None
    assert seen[0].process_state is EvaluationWorkerState.IDLE


@pytest.mark.parametrize("score,status", [
    (0, ThreatStatus.NORMAL), (34, ThreatStatus.NORMAL),
    (35, ThreatStatus.WARNING), (64, ThreatStatus.WARNING),
    (65, ThreatStatus.CRITICAL), (100, ThreatStatus.CRITICAL),
])
def test_success_claim_budget_bundle_client_complete_and_publish(score, status) -> None:
    worker, trigger, budget, buffer, client, clock = _worker()
    client.trigger = trigger; client.budget = budget
    trigger.queue(_event())
    assert worker.process_one(10)
    assert len(client.requests) == len(buffer.calls) == 1
    request = client.requests[0]
    assert request.domain is DomainName.PARKING_MOTORCYCLE
    assert len(request.frame_bundle.frames) == 5
    assert len(set(frame.source_frame_seq for frame in request.frame_bundle.frames)) == 5
    assert "jpeg" not in repr(request).lower()
    assert worker.get_latest_result() is None
    assert worker.get_snapshot().state is EvaluationWorkerState.WAITING_CALLBACK
    assert trigger.pending == "e1" and budget.get_snapshot(10).pending_event_id == "e1"
    assert not hasattr(worker._pending, "frame_bundle")
    assert _accept(worker, _assessment(score, status)) is CallbackDisposition.ACCEPTED
    assert trigger.completed == ["e1"] and trigger.failed == []
    assert budget.get_snapshot(11).total_completed == 1
    assert budget.get_snapshot(11).pending_event_id is None
    record = worker.get_latest_result()
    assert record is not None and record.evaluation_seq == 1
    assert record.assessment.threat_score == score and record.client_name == "fake-langflow"
    assert record.frame_source_sequences == (1, 2, 3, 4, 5)
    with pytest.raises(FrozenInstanceError):
        record.evaluation_seq = 2


@pytest.mark.parametrize("domain", [DomainName.UNKNOWN, DomainName.RETAIL_STORE])
def test_unknown_and_unregistered_retail_never_use_parking_or_budget(domain) -> None:
    # Retail is registered by default now; an explicit parking-only map keeps it unregistered.
    worker, trigger, budget, buffer, client, _ = _worker(
        strategies={DomainName.PARKING_MOTORCYCLE: make_parking_frame_selection_plan})
    trigger.queue(_event(domain=domain))
    assert worker.process_one(10)
    assert trigger.failed == ["e1"] and trigger.pending is None
    assert budget.get_snapshot(10).total_granted == 0
    assert buffer.calls == [] and client.requests == []
    assert worker.get_snapshot().last_failure is EvaluationFailure.NO_STRATEGY


def test_explicit_retail_strategy_is_used_without_parking_fallback() -> None:
    def retail_factory(event_id, session, triggered_at):
        return FrameSelectionPlan(event_id, session, DomainName.RETAIL_STORE, triggered_at,
                                  (-2, -1, -.4, 0, .4), .3, 5)
    worker, trigger, budget, buffer, client, _ = _worker(strategies={DomainName.RETAIL_STORE: retail_factory})
    trigger.queue(_event(domain=DomainName.RETAIL_STORE))
    assert worker.process_one(10)
    assert buffer.calls[0].domain is DomainName.RETAIL_STORE
    assert _accept(worker) is CallbackDisposition.ACCEPTED
    assert worker.get_latest_result().domain is DomainName.RETAIL_STORE


def test_budget_denial_suppresses_pending_without_buffer_or_client() -> None:
    budget = EvaluationBudget()
    first = budget.try_reserve("other", "one", DomainName.PARKING_MOTORCYCLE, 10, 10)
    worker, trigger, _, buffer, client, _ = _worker(budget=budget)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert worker.get_snapshot().last_failure is EvaluationFailure.BUDGET_DENIED
    assert worker.get_snapshot().last_budget_denial_reason == "REQUEST_PENDING"
    assert trigger.pending is None and trigger.failed == ["e1"]
    assert buffer.calls == client.requests == []
    assert budget.complete(first.reservation_id, 10)


def test_stale_candidate_is_denied_and_never_replayed_late() -> None:
    worker, trigger, budget, buffer, client, clock = _worker(clock=FakeClock(20))
    trigger.queue(_event(at=10))
    assert worker.process_one(20)
    assert worker.get_snapshot().last_budget_denial_reason == "STALE_CANDIDATE"
    assert trigger.failed == ["e1"] and not client.requests and not buffer.calls
    assert budget.get_snapshot(20).total_granted == 0


@pytest.mark.parametrize("status,expected", [
    (BundleStatus.WAITING_FOR_FUTURE_FRAME, EvaluationFailure.BUNDLE_TIMEOUT),
    (BundleStatus.INSUFFICIENT_HISTORY, EvaluationFailure.BUNDLE_INVALID),
    (BundleStatus.ENDED, EvaluationFailure.BUNDLE_INVALID),
])
def test_bundle_failure_fails_both_reservations(status, expected) -> None:
    worker, trigger, budget, buffer, client, _ = _worker(buffer=FakeBuffer(status=status))
    trigger.queue(_event())
    assert worker.process_one(10)
    assert buffer.timeout_values == [2.0]
    assert client.requests == [] and trigger.failed == ["e1"]
    assert budget.get_snapshot(10).total_failed == 1 and budget.get_snapshot(10).pending_event_id is None
    assert worker.get_snapshot().last_failure is expected


def test_incomplete_or_duplicate_frame_bundle_rejected() -> None:
    buffer = FakeBuffer()
    plan = make_parking_frame_selection_plan("e1", "one", 10)
    good = buffer.wait_for_bundle(plan, 2).bundle
    buffer.calls.clear(); buffer.timeout_values.clear()
    buffer.bundle_override = replace(good, frames=good.frames[:4])
    worker, trigger, budget, _, client, _ = _worker(buffer=buffer)
    trigger.queue(_event())
    assert worker.process_one(10)
    assert worker.get_snapshot().last_failure is EvaluationFailure.BUNDLE_INVALID
    assert not client.requests and not budget.get_snapshot(10).pending_event_id


@pytest.mark.parametrize("bad_frame", [
    {"deviation_seconds": -0.1}, {"mime_type": "image/png"},
])
def test_malformed_selected_frame_is_rejected_before_client(bad_frame) -> None:
    buffer = FakeBuffer()
    good = buffer.wait_for_bundle(make_parking_frame_selection_plan("e1", "one", 10), 2).bundle
    frames = list(good.frames)
    frames[0] = replace(frames[0], **bad_frame)
    buffer.bundle_override = replace(good, frames=tuple(frames))
    worker, trigger, budget, _, client, _ = _worker(buffer=buffer)
    trigger.queue(_event())
    assert worker.process_one(10)
    assert worker.get_snapshot().last_failure is EvaluationFailure.BUNDLE_INVALID
    assert not client.requests and budget.get_snapshot(10).total_failed == 1


@pytest.mark.parametrize("exception", [RuntimeError("fake failure"), TimeoutError("fake timeout")])
def test_client_exception_and_timeout_do_not_publish(exception) -> None:
    worker, trigger, budget, buffer, client, _ = _worker(client=FakeClient(exception=exception))
    trigger.queue(_event())
    assert worker.process_one(10)
    assert worker.get_latest_result() is None
    assert worker.get_snapshot().last_failure is EvaluationFailure.DISPATCH_UNCERTAIN
    assert worker.get_snapshot().state is EvaluationWorkerState.WAITING_CALLBACK
    assert trigger.pending == "e1" and budget.get_snapshot(10).pending_event_id == "e1"
    assert worker.sweep(55)
    assert trigger.failed == ["e1"] and budget.get_snapshot(55).total_failed == 1


@pytest.mark.parametrize("invalid", [
    _assessment(-1), _assessment(101), _assessment(50, ThreatStatus.CRITICAL),
    replace(_assessment(), reasoning="  "),
    replace(_assessment(), subject_profile=SubjectProfile("yes", "x", "y", (), ())),
    replace(_assessment(), subject_profile=SubjectProfile(True, "x", "y", ["bad"], ())),
    {"threat_score": 10},
])
def test_invalid_callback_is_rejected_without_publishing(invalid) -> None:
    worker, trigger, budget, buffer, client, _ = _worker()
    trigger.queue(_event())
    assert worker.process_one(10)
    assert _accept(worker, invalid) is CallbackDisposition.REJECTED
    assert worker.get_latest_result() is None
    assert trigger.pending == "e1" and budget.get_snapshot(10).pending_event_id == "e1"


def test_session_replacement_during_client_call_discards_stale_result() -> None:
    entered = threading.Event(); release = threading.Event()
    class BlockingClient(FakeClient):
        def dispatch(self, request):
            entered.set()
            assert release.wait(1)
            return super().dispatch(request)
    worker, trigger, budget, buffer, client, _ = _worker(client=BlockingClient())
    trigger.queue(_event())
    worker.start(); worker.start()
    try:
        assert entered.wait(1)
        worker.end_session("one")
        trigger.end_session(); trigger.session = "new"; buffer.session = "new"
        worker.start_session("new")
        release.set()
        deadline = time.monotonic() + 1
        while worker.get_snapshot().active_event_id is not None and time.monotonic() < deadline:
            release.wait(.001)
        assert worker.get_latest_result() is None
        assert _accept(worker) is CallbackDisposition.REJECTED
        assert budget.get_snapshot(10).pending_event_id is None
    finally:
        release.set(); worker.close()


def test_two_candidates_are_never_evaluated_in_parallel() -> None:
    entered = threading.Event(); release = threading.Event()
    class BlockingClient(FakeClient):
        def dispatch(self, request):
            entered.set()
            assert release.wait(1)
            return super().dispatch(request)
    worker, trigger, budget, buffer, client, _ = _worker(client=BlockingClient())
    trigger.queue(_event("a")); trigger.queue(_event("b", 20))
    worker.start()
    try:
        assert entered.wait(1)
        assert not worker.process_one(20)
        assert len(client.requests) == 0  # blocked before FakeClient records it
        release.set()
        deadline = time.monotonic() + 1
        while not client.requests and time.monotonic() < deadline:
            time.sleep(.001)
        assert len(client.requests) == 1
        assert not worker.process_one(20)
    finally:
        release.set(); worker.close()


def test_close_is_idempotent_wakes_idle_thread_and_rejects_new_work() -> None:
    worker, trigger, budget, buffer, client, _ = _worker()
    worker.start(); first_thread = worker._thread
    worker.start()
    assert worker._thread is first_thread and first_thread.daemon
    worker.close(); worker.close()
    assert not first_thread.is_alive()
    trigger.queue(_event())
    assert not worker.process_one(10)
    assert worker.get_snapshot().closed and not client.requests


@pytest.mark.parametrize("timeline,expected_calls", [
    ((5.594, 17.828), 2),
    ((27.844, 42.219, 50.640, 57.219), 2),
])
def test_policy_timelines_limit_fake_calls_and_do_not_queue_denials(timeline, expected_calls) -> None:
    clock = FakeClock()
    worker, trigger, budget, buffer, client, _ = _worker(clock=clock)
    for index, timestamp in enumerate(timeline):
        clock.now = timestamp
        trigger.queue(_event(f"event-{index}", timestamp))
        assert worker.process_one(timestamp)
        if index < expected_calls:
            assert _accept(worker, event_id=f"event-{index}", now=timestamp + 1) is CallbackDisposition.ACCEPTED
        assert trigger.pending is None
    assert len(client.requests) == expected_calls
    assert budget.get_snapshot(timeline[-1] + 1).total_granted == expected_calls
    assert len(set(request.event_id for request in client.requests)) == expected_calls


def test_same_event_id_cannot_call_client_twice() -> None:
    clock = FakeClock()
    worker, trigger, budget, buffer, client, _ = _worker(clock=clock)
    trigger.queue(_event("same", 10)); clock.now = 10
    assert worker.process_one(10)
    assert _accept(worker, event_id="same", now=11) is CallbackDisposition.ACCEPTED
    trigger.queue(_event("same", 20)); clock.now = 20
    assert worker.process_one(20)
    assert len(client.requests) == 1
    assert trigger.failed == ["same"]
    assert worker.get_snapshot().last_budget_denial_reason == "DUPLICATE_EVENT"


def test_duplicate_conflict_and_mismatch_never_complete_twice() -> None:
    worker, trigger, budget, _, client, _ = _worker()
    trigger.queue(_event())
    assert worker.process_one(10)
    assert _accept(worker, event_id="other") is CallbackDisposition.REJECTED
    assert _accept(worker, session="other") is CallbackDisposition.REJECTED
    assert trigger.pending == "e1" and not trigger.completed
    callback = EvaluationCallback("e1", "one", _assessment())
    assert worker.accept_callback(callback, 11) is CallbackDisposition.ACCEPTED
    assert worker.accept_callback(callback, 12) is CallbackDisposition.DUPLICATE
    assert _accept(worker, _assessment(75, ThreatStatus.CRITICAL), now=12) is CallbackDisposition.REJECTED
    assert trigger.completed == ["e1"]
    assert budget.get_snapshot(12).total_completed == 1
    assert worker.get_latest_result().assessment.threat_score == 10


def test_callback_without_pending_and_after_session_end_is_rejected() -> None:
    worker, trigger, budget, _, _, _ = _worker()
    assert _accept(worker) is CallbackDisposition.REJECTED
    trigger.queue(_event())
    assert worker.process_one(10)
    worker.end_session("one")
    trigger.end_session()
    assert _accept(worker) is CallbackDisposition.REJECTED
    assert budget.get_snapshot(11).total_failed == 1
    assert trigger.failed == ["e1"] and worker.get_latest_result() is None
    assert worker.get_snapshot().state is EvaluationWorkerState.CANCELLED


def test_callback_deadline_is_exact_and_late_callback_cleans_pending() -> None:
    worker, trigger, budget, _, _, _ = _worker()
    trigger.queue(_event())
    assert worker.process_one(10)
    assert worker.get_snapshot().callback_deadline == 55
    assert _accept(worker, now=55) is CallbackDisposition.REJECTED
    assert worker.get_snapshot().state is EvaluationWorkerState.TIMED_OUT
    assert worker.get_snapshot().last_failure is EvaluationFailure.CALLBACK_TIMEOUT
    assert trigger.failed == ["e1"] and budget.get_snapshot(55).total_failed == 1
    assert worker.get_latest_result() is None


def test_sweep_times_out_without_callback_and_does_not_retry() -> None:
    worker, trigger, budget, _, client, _ = _worker()
    trigger.queue(_event())
    assert worker.process_one(10)
    assert not worker.sweep(54.999)
    assert worker.sweep(55)
    assert not worker.sweep(56)
    assert len(client.requests) == 1 and trigger.failed == ["e1"]
    assert budget.get_snapshot(56).total_failed == 1


def test_uncertain_dispatch_can_still_complete_from_callback() -> None:
    worker, trigger, budget, _, client, _ = _worker(client=FakeClient(exception=TimeoutError()))
    trigger.queue(_event())
    assert worker.process_one(10)
    assert worker.get_snapshot().state is EvaluationWorkerState.WAITING_CALLBACK
    assert _accept(worker) is CallbackDisposition.ACCEPTED
    assert trigger.completed == ["e1"] and trigger.failed == []
    assert budget.get_snapshot(11).total_completed == 1
    assert len(client.requests) == 1


def test_callback_racing_dispatch_completes_once() -> None:
    entered = threading.Event(); release = threading.Event()
    class BlockingDispatcher(FakeClient):
        def dispatch(self, request):
            entered.set()
            assert release.wait(1)
            return super().dispatch(request)
    worker, trigger, budget, _, client, _ = _worker(client=BlockingDispatcher())
    trigger.queue(_event())
    thread = threading.Thread(target=lambda: worker.process_one(10))
    thread.start()
    try:
        assert entered.wait(1)
        assert worker.get_snapshot().state is EvaluationWorkerState.DISPATCHING
        assert _accept(worker) is CallbackDisposition.ACCEPTED
        release.set(); thread.join(1)
        assert not thread.is_alive()
        assert worker.get_snapshot().state is EvaluationWorkerState.COMPLETED
        assert trigger.completed == ["e1"]
        assert budget.get_snapshot(11).total_completed == 1
    finally:
        release.set(); thread.join(1)


def test_session_replacement_cancels_pending_and_rejects_old_callback() -> None:
    worker, trigger, budget, buffer, _, _ = _worker()
    trigger.queue(_event())
    assert worker.process_one(10)
    worker.start_session("two")
    trigger.end_session(); trigger.session = "two"; buffer.session = "two"
    assert _accept(worker) is CallbackDisposition.REJECTED
    assert budget.get_snapshot(10).total_failed == 1
    assert trigger.failed == ["e1"] and worker.get_latest_result() is None


def test_ended_session_cannot_dispatch_again_before_trigger_cleanup() -> None:
    worker, trigger, budget, _, client, _ = _worker()
    worker.start_session("one")
    worker.end_session("one")
    trigger.queue(_event())
    assert worker.process_one(10)
    assert client.requests == [] and budget.get_snapshot(10).total_granted == 0


@pytest.mark.parametrize("timeout", [0, 60.01, float("nan")])
def test_callback_timeout_configuration_is_bounded(timeout) -> None:
    with pytest.raises(ValueError):
        EvaluationWorker(FakeTrigger(), EvaluationBudget(), FakeBuffer(), FakeClient(),
                         callback_timeout_seconds=timeout)


def test_real_trigger_wait_notifies_on_candidate_without_claiming() -> None:
    trigger = ParkingCandidateTrigger(); trigger.start_session("one")
    results = []
    waiter = threading.Thread(target=lambda: results.append(trigger.wait_for_candidate(1)))
    waiter.start()
    _observe_series(trigger)
    waiter.join(timeout=1)
    assert not waiter.is_alive() and results == [True]
    snapshot = trigger.get_snapshot()
    assert snapshot.pending_event_id is None
    assert any(state is ParkingCandidateState.CANDIDATE for *_, state in snapshot.episode_states)
    trigger.end_session("one")


def test_real_trigger_end_and_close_wake_waiters() -> None:
    for action in ("end", "close"):
        trigger = ParkingCandidateTrigger(); trigger.start_session("one")
        results = []
        waiter = threading.Thread(target=lambda: results.append(trigger.wait_for_candidate(1)))
        waiter.start()
        if action == "end":
            trigger.end_session("one")
        else:
            trigger.close()
        waiter.join(timeout=1)
        assert not waiter.is_alive() and results == [False]


def test_worker_waits_for_future_bundle_without_client_overlap() -> None:
    entered = threading.Event(); release = threading.Event()
    class FutureBuffer(FakeBuffer):
        def wait_for_bundle(self, plan, timeout_seconds):
            entered.set()
            assert release.wait(1)
            return super().wait_for_bundle(plan, timeout_seconds)
    worker, trigger, budget, buffer, client, _ = _worker(buffer=FutureBuffer())
    trigger.queue(_event())
    worker.start()
    try:
        assert entered.wait(1)
        assert client.requests == []
        release.set()
        deadline = time.monotonic() + 1
        while worker.get_snapshot().state is not EvaluationWorkerState.WAITING_CALLBACK and time.monotonic() < deadline:
            time.sleep(.001)
        assert worker.get_latest_result() is None
        assert len(client.requests) == 1
    finally:
        release.set(); worker.close()


def test_close_during_client_call_discards_result_and_clears_budget() -> None:
    entered = threading.Event(); release = threading.Event()
    class BlockingClient(FakeClient):
        def dispatch(self, request):
            entered.set()
            assert release.wait(1)
            return super().dispatch(request)
    worker, trigger, budget, buffer, client, _ = _worker(client=BlockingClient())
    trigger.queue(_event())
    worker.start()
    assert entered.wait(1)
    worker.close(timeout_seconds=.01)
    release.set()
    worker._thread.join(timeout=1)
    assert not worker._thread.is_alive()
    assert worker.get_latest_result() is None
    assert budget.get_snapshot(10).pending_event_id is None
    assert trigger.pending is None


# ── Process-lifetime dispatch quota (SENTINEL_EVALUATION_MAX_DISPATCHES) ──────

from app.services.langflow_evaluation_client import LangflowDispatchRejected, LangflowErrorKind  # noqa: E402


class StopAfterWaits:
    """Deterministic stand-in for the worker stop event: ends _run after N waits."""

    def __init__(self, waits: int) -> None:
        self.remaining = waits
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self.remaining <= 0

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        self.remaining -= 1
        return self.remaining <= 0

    def set(self) -> None:
        self.remaining = 0


def _capped(max_dispatches: int | None = 1, *, client=None, buffer=None):
    trigger = FakeTrigger()
    budget = EvaluationBudget()
    buffer = buffer or FakeBuffer()
    client = client or FakeClient()
    clock = FakeClock(10)
    worker = EvaluationWorker(trigger, budget, buffer, client, clock=clock, max_dispatches=max_dispatches)
    worker.start_session("one")
    return worker, trigger, budget, buffer, client, clock


@pytest.mark.parametrize("value", [0, -1, 1001, True, False, 1.0, "1"])
def test_max_dispatches_constructor_rejects_invalid_values(value) -> None:
    with pytest.raises(ValueError):
        EvaluationWorker(FakeTrigger(), EvaluationBudget(), FakeBuffer(), FakeClient(), max_dispatches=value)


@pytest.mark.parametrize("value", [None, 1, 1000])
def test_max_dispatches_constructor_accepts_none_and_range(value) -> None:
    worker = EvaluationWorker(FakeTrigger(), EvaluationBudget(), FakeBuffer(), FakeClient(), max_dispatches=value)
    snapshot = worker.get_snapshot()
    assert (snapshot.dispatch_attempts, snapshot.max_dispatches) == (0, value)


def test_default_without_limit_keeps_dispatching() -> None:
    worker, trigger, budget, buffer, client, clock = _capped(None)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert _accept(worker, event_id="e1", now=11) is CallbackDisposition.ACCEPTED
    clock.now = 22
    trigger.queue(_event("e2", 22))
    assert worker.process_one(22)
    assert [request.event_id for request in client.requests] == ["e1", "e2"]
    assert worker.get_snapshot().dispatch_attempts == 2
    assert worker.get_snapshot().max_dispatches is None


def test_cap_one_still_accepts_first_callback_then_never_claims_again() -> None:
    worker, trigger, budget, buffer, client, clock = _capped(1)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    trigger.queue(_event("e2", 12))
    # The quota is exhausted while the only attempt is pending; its callback still wins.
    assert _accept(worker, event_id="e1", now=11) is CallbackDisposition.ACCEPTED
    assert worker.get_latest_result().event_id == "e1"
    clock.now = 30
    assert not worker.process_one(30)
    assert len(client.requests) == 1
    assert trigger.claimed == ["e1"]
    assert budget.get_snapshot(30).total_granted == 1
    snapshot = worker.get_snapshot()
    assert (snapshot.dispatch_attempts, snapshot.max_dispatches) == (1, 1)


@pytest.mark.parametrize("outcome", ["rejected-4xx", "uncertain-then-timeout", "timeout", "new-session"])
def test_cap_one_is_never_returned_by_failure_timeout_or_session_change(outcome) -> None:
    client = FakeClient()
    if outcome == "rejected-4xx":
        client.exception = LangflowDispatchRejected(LangflowErrorKind.HTTP_STATUS, status_code=400)
    elif outcome == "uncertain-then-timeout":
        client.exception = RuntimeError("synthetic uncertain ACK")
    worker, trigger, budget, buffer, client, clock = _capped(1, client=client)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    if outcome in ("uncertain-then-timeout", "timeout"):
        assert worker.sweep(55)
    session = "one"
    if outcome == "new-session":
        worker.end_session("one")
        trigger.end_session()
        session = "two"
        trigger.session = buffer.session = session
        worker.start_session(session)
    client.exception = None
    clock.now = 70
    trigger.queue(_event("e2", 70, session=session))
    assert not worker.process_one(70)
    assert len(client.requests) == 1
    assert trigger.claimed == ["e1"]
    assert budget.get_snapshot(70).total_granted == 1
    assert worker.get_snapshot().dispatch_attempts == 1


@pytest.mark.parametrize("status", [BundleStatus.WAITING_FOR_FUTURE_FRAME, BundleStatus.INSUFFICIENT_HISTORY])
def test_failure_before_handoff_does_not_spend_the_quota(status) -> None:
    worker, trigger, budget, buffer, client, clock = _capped(1, buffer=FakeBuffer(status=status))
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert client.requests == [] and worker.get_snapshot().dispatch_attempts == 0
    assert trigger.failed == ["e1"] and budget.get_snapshot(10).pending_event_id is None
    buffer.status = BundleStatus.READY
    clock.now = 22
    trigger.queue(_event("e2", 22))
    assert worker.process_one(22)
    assert [request.event_id for request in client.requests] == ["e2"]
    assert worker.get_snapshot().dispatch_attempts == 1


def test_handoff_check_blocks_dispatch_and_releases_claim_and_grant() -> None:
    # Defense in depth: the early check passed, but the quota is exhausted by the
    # time of handoff (simulated during bundle preparation).
    class ExhaustingBuffer(FakeBuffer):
        worker: EvaluationWorker | None = None

        def wait_for_bundle(self, plan, timeout_seconds):
            assert self.worker is not None
            with self.worker._state_lock:
                self.worker._dispatch_attempts = 1
            return super().wait_for_bundle(plan, timeout_seconds)

    buffer = ExhaustingBuffer()
    worker, trigger, budget, buffer, client, clock = _capped(1, buffer=buffer)
    buffer.worker = worker
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert client.requests == []
    assert worker.get_snapshot().dispatch_attempts == 1
    assert worker.get_snapshot().active_event_id is None
    assert trigger.failed == ["e1"] and trigger.pending is None
    assert budget.get_snapshot(10).pending_event_id is None
    assert budget.get_snapshot(10).total_failed == 1


def test_dispatcher_error_before_http_still_spends_the_attempt() -> None:
    client = FakeClient(LangflowDispatchRejected(LangflowErrorKind.INVALID_REQUEST))
    worker, trigger, budget, buffer, client, clock = _capped(1, client=client)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert worker.get_snapshot().dispatch_attempts == 1
    assert worker.get_snapshot().state is EvaluationWorkerState.FAILED
    assert trigger.failed == ["e1"] and budget.get_snapshot(10).pending_event_id is None
    client.exception = None
    clock.now = 22
    trigger.queue(_event("e2", 22))
    assert not worker.process_one(22)
    assert len(client.requests) == 1 and trigger.claimed == ["e1"]


def test_concurrent_process_one_dispatches_at_most_once() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingClient(FakeClient):
        def dispatch(self, request):
            super().dispatch(request)
            entered.set()
            assert release.wait(2)

    worker, trigger, budget, buffer, client, clock = _capped(1, client=BlockingClient())
    for index in range(20):
        trigger.queue(_event(f"e{index}", 10))

    def hammer(now: float) -> None:
        start.wait(2)
        for _ in range(50):
            worker.process_one(now)

    # Phase 1: one thread is inside dispatch() while seven others race process_one.
    start = threading.Barrier(8)
    threads = [threading.Thread(target=hammer, args=(10,)) for _ in range(8)]
    for thread in threads:
        thread.start()
    assert entered.wait(2)
    release.set()
    for thread in threads:
        thread.join(2)
    assert all(not thread.is_alive() for thread in threads)
    assert len(client.requests) == 1
    # Phase 2: the attempt completes; concurrent calls still never dispatch again.
    assert _accept(worker, event_id=client.requests[0].event_id, now=11) is CallbackDisposition.ACCEPTED
    start = threading.Barrier(8)
    threads = [threading.Thread(target=hammer, args=(30,)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert all(not thread.is_alive() for thread in threads)
    assert len(client.requests) == 1
    assert worker.get_snapshot().dispatch_attempts == 1
    assert len(trigger.claimed) == 1
    assert budget.get_snapshot(30).total_granted == 1


def test_new_worker_starts_with_a_fresh_counter() -> None:
    first, trigger, budget, buffer, client, clock = _capped(1)
    trigger.queue(_event("e1", 10))
    assert first.process_one(10)
    assert first.get_snapshot().dispatch_attempts == 1
    second, trigger2, budget2, buffer2, client2, clock2 = _capped(1)
    assert second.get_snapshot().dispatch_attempts == 0
    trigger2.queue(_event("e9", 10))
    assert second.process_one(10)
    assert second.get_snapshot().dispatch_attempts == 1


def test_run_sweeps_last_attempt_then_idles_without_claiming() -> None:
    worker, trigger, budget, buffer, client, clock = _capped(1)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    trigger.queue(_event("e2", 12))
    candidate_waits: list[float] = []
    trigger.wait_for_candidate = lambda timeout: candidate_waits.append(timeout) or True
    stop = StopAfterWaits(3)
    worker._stop = stop
    states: list[EvaluationWorkerState] = []

    def wait_then_pass_deadline(timeout: float) -> bool:
        states.append(worker.get_snapshot().state)
        clock.now = 56      # the next iteration's sweep expires the only attempt
        return StopAfterWaits.wait(stop, timeout)

    stop.wait = wait_then_pass_deadline
    clock.now = 30          # pending and before the deadline: wait, no claim
    worker._run()
    assert states[0] is EvaluationWorkerState.WAITING_CALLBACK
    assert stop.waits[0] == pytest.approx(0.25)
    assert stop.waits[1:] == [0.5, 0.5]     # existing idle cadence, no busy loop
    assert worker.get_snapshot().state is EvaluationWorkerState.TIMED_OUT
    assert trigger.failed == ["e1"]
    assert candidate_waits == [] and trigger.claimed == ["e1"]
    assert len(client.requests) == 1


# ── Audio gate hook (control plane only) ──────────────────────────────────────

class RecordingGate:
    """Records calls and whether the worker lock was held (worker -> gate order)."""

    def __init__(self, error: Exception | None = None) -> None:
        self.worker: EvaluationWorker | None = None
        self.calls: list[tuple] = []
        self.lock_held: list[bool] = []
        self.error = error

    def _record(self, call: tuple) -> None:
        lock, free = self.worker._state_lock, []

        def probe() -> None:              # from another thread: the lock may be reentrant
            acquired = lock.acquire(blocking=False)
            if acquired:
                lock.release()
            free.append(acquired)

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(1)
        self.lock_held.append(free == [False])
        self.calls.append(call)
        if self.error is not None:
            raise self.error

    def apply_lifecycle(self, epoch, session_id) -> None:
        self._record(("lifecycle", epoch, session_id))

    def offer(self, epoch, event_id, session_id, status) -> None:
        self._record(("offer", epoch, event_id, session_id, status))

    def fail_closed(self) -> None:
        self._record(("fail_closed",))


def _gated_worker(gate):
    trigger, budget, buffer, client = FakeTrigger(), EvaluationBudget(), FakeBuffer(), FakeClient()
    worker = EvaluationWorker(trigger, budget, buffer, client, clock=FakeClock(10), audio_gate=gate)
    if isinstance(gate, RecordingGate):
        gate.worker = worker
    return worker, trigger, buffer


def test_worker_drives_gate_epochs_and_offers_accepted_once_under_its_lock() -> None:
    gate = RecordingGate()
    worker, trigger, _ = _gated_worker(gate)
    worker.start_session("one")
    worker.start_session("one")                                # unchanged: no new epoch
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), now=11) is CallbackDisposition.ACCEPTED
    assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), now=12) is CallbackDisposition.DUPLICATE
    worker.get_current_view()                                  # polling never offers
    worker.end_session("ghost")                                # not the current session: ignored
    worker.end_session("one")
    worker.start_session("two")
    assert gate.calls == [
        ("lifecycle", 1, "one"),
        ("offer", 1, "e1", "one", ThreatStatus.CRITICAL),
        ("lifecycle", 2, None),
        ("lifecycle", 3, "two"),
    ]
    assert all(gate.lock_held)


def test_gate_error_never_changes_the_callback_result() -> None:
    worker, trigger, _ = _gated_worker(RecordingGate(error=RuntimeError("audio exploded")))
    worker.start_session("one")
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), now=11) is CallbackDisposition.ACCEPTED
    assert worker.get_latest_result().event_id == "e1"
    assert worker.get_snapshot().state is EvaluationWorkerState.COMPLETED


class _GateDevice:
    def __init__(self, start_gate: threading.Event | None = None) -> None:
        self.calls: list[str] = []
        self.in_start = threading.Event()
        self.start_gate = start_gate

    def start(self, path) -> None:
        self.calls.append("start")
        self.in_start.set()
        if self.start_gate is not None:
            assert self.start_gate.wait(5)

    def stop(self) -> None:
        self.calls.append("stop")


def _real_gate(device, duration: float = 0.01):
    from app.services.audio_alert_service import AudioAlertGate

    return AudioAlertGate(device, "C:/outside/alert.wav", duration, join_timeout_seconds=0.5)


def _accepted_critical(worker, trigger, buffer, session: str, event_id: str, now: float) -> None:
    trigger.session = buffer.session = session
    trigger.queue(_event(event_id, now, session=session))
    assert worker.process_one(now)
    assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), event_id=event_id,
                   session=session, now=now + 1) is CallbackDisposition.ACCEPTED


def test_accepted_critical_is_granted_once_by_the_real_gate() -> None:
    device = _GateDevice()
    gate = _real_gate(device)
    worker, trigger, buffer = _gated_worker(gate)
    worker.start_session("A")
    _accepted_critical(worker, trigger, buffer, "A", "e1", 10)
    assert gate.run_once(0) and not gate.run_once(0)
    assert device.calls == ["start", "stop"]


def test_old_session_callback_after_new_session_is_never_offered() -> None:
    device = _GateDevice()
    gate = _real_gate(device)
    worker, trigger, buffer = _gated_worker(gate)
    worker.start_session("A")
    trigger.session = buffer.session = "A"
    trigger.queue(_event("e1", 10, session="A"))
    assert worker.process_one(10)
    worker.end_session("A")
    worker.start_session("B")
    assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), session="A",
                   now=11) is not CallbackDisposition.ACCEPTED
    gate.offer(1, "e1", "A", ThreatStatus.CRITICAL)             # a late offer with the old epoch
    assert not gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert device.calls == [] and snapshot.granted == 0
    assert (snapshot.epoch, snapshot.session_short_id) == (3, "B")


def test_offer_queued_then_session_end_never_reaches_the_device() -> None:
    device = _GateDevice()
    gate = _real_gate(device)
    worker, trigger, buffer = _gated_worker(gate)
    worker.start_session("A")
    _accepted_critical(worker, trigger, buffer, "A", "e1", 10)
    assert gate.get_snapshot().queued_event_short_id is not None
    worker.end_session("A")                                    # invalidation before grant
    assert not gate.run_once(0)
    assert device.calls == [] and gate.get_snapshot().dropped_stale == 1


def test_stalled_device_start_keeps_callback_and_lifecycle_responsive() -> None:
    release = threading.Event()
    device = _GateDevice(start_gate=release)
    gate = _real_gate(device, duration=4.0)
    gate.start()
    worker, trigger, buffer = _gated_worker(gate)
    try:
        worker.start_session("A")
        _accepted_critical(worker, trigger, buffer, "A", "e1", 10)
        assert device.in_start.wait(2)                         # device thread is stuck in start
        trigger.pending = None
        worker._budget = EvaluationBudget()
        trigger.queue(_event("e2", 30, session="A"))
        assert worker.process_one(30)
        dispositions: list[CallbackDisposition] = []
        callback = threading.Thread(target=lambda: dispositions.append(_accept(
            worker, _assessment(90, ThreatStatus.CRITICAL), event_id="e2", session="A", now=31)))
        callback.start()
        callback.join(1)
        assert not callback.is_alive() and dispositions == [CallbackDisposition.ACCEPTED]
        assert gate.get_snapshot().dropped_cooldown == 1        # offered, refused without the device
        lifecycle = threading.Thread(target=lambda: (worker.end_session("A"), worker.start_session("B")))
        reader = threading.Thread(target=lambda: (worker.get_current_view(), gate.get_snapshot()))
        for thread in (lifecycle, reader):
            thread.start()
        for thread in (lifecycle, reader):
            thread.join(1)
            assert not thread.is_alive()
        assert worker.get_current_view().source_session_id == "B"
        assert gate.get_snapshot().attempt_state == "STARTING"
    finally:
        release.set()
        gate.close()
    assert device.calls == ["start", "stop"]                   # in-flight attempt stopped, no second grant


# ── Lifecycle update failure: audio fails closed, evaluation continues ────────

def _break_lifecycle(gate, *, partial: bool) -> None:
    """partial=False fails before any change; partial=True after the epoch moved."""
    if partial:
        def broken_invalidate() -> None:
            raise RuntimeError("invalidate exploded")
        gate._invalidate_locked = broken_invalidate
    else:
        def broken_apply(epoch, session_id) -> None:
            raise RuntimeError("lifecycle exploded")
        gate.apply_lifecycle = broken_apply


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("transition", ["end", "switch"])
def test_failed_lifecycle_update_fails_closed_and_never_grants(partial, transition, caplog) -> None:
    device = _GateDevice()
    gate = _real_gate(device)
    worker, trigger, buffer = _gated_worker(gate)
    worker.start_session("A")
    _accepted_critical(worker, trigger, buffer, "A", "e1", 10)
    assert gate.get_snapshot().queued_event_short_id is not None     # old-session slot queued
    _break_lifecycle(gate, partial=partial)
    with caplog.at_level("WARNING"):
        if transition == "end":
            worker.end_session("A")
        else:
            worker.start_session("B")
    assert "Audio alert disabled: lifecycle update failed" in caplog.text
    assert "exploded" not in caplog.text
    assert worker._audio_gate is None
    assert worker.get_current_view().source_session_id == (None if transition == "end" else "B")
    assert gate.get_snapshot().epoch == (2 if partial else 1)       # partial update really happened
    assert not gate.run_once(0)                                      # no old grant
    # Evaluation keeps working for the next session; audio stays closed.
    if transition == "end":
        worker.start_session("B")
    trigger.pending = None
    worker._budget = EvaluationBudget()
    _accepted_critical(worker, trigger, buffer, "B", "e2", 40)
    assert worker.get_latest_result().event_id == "e2"
    gate.offer(99, "e3", "B", ThreatStatus.CRITICAL)                 # nothing can reopen it
    assert not gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert device.calls == [] and snapshot.granted == 0
    assert snapshot.closed and snapshot.lifecycle_fault


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()


def test_failed_lifecycle_update_cancels_the_active_attempt() -> None:
    device = _GateDevice()
    gate = _real_gate(device, duration=5.0)
    gate.start()
    worker, trigger, buffer = _gated_worker(gate)
    try:
        worker.start_session("A")
        _accepted_critical(worker, trigger, buffer, "A", "e1", 10)
        assert _wait_until(lambda: gate.get_snapshot().attempt_state == "IN_FLIGHT")
        _break_lifecycle(gate, partial=True)
        worker.start_session("B")
        assert _wait_until(lambda: gate.get_snapshot().attempt_state is None)   # well before 5 s
        snapshot = gate.get_snapshot()
        assert snapshot.stopped == 1 and snapshot.lifecycle_fault
        assert device.calls == ["start", "stop"]
    finally:
        assert gate.close() is True


def test_fail_closed_error_still_detaches_the_gate_and_keeps_callbacks_working(caplog) -> None:
    class BrokenGate(RecordingGate):
        def apply_lifecycle(self, epoch, session_id) -> None:
            if epoch > 1:
                raise RuntimeError("lifecycle exploded")
            super().apply_lifecycle(epoch, session_id)

        def fail_closed(self) -> None:
            super().fail_closed()
            raise RuntimeError("fail closed exploded")

    gate = BrokenGate()
    worker, trigger, buffer = _gated_worker(gate)
    worker.start_session("A")
    with caplog.at_level("WARNING"):
        worker.start_session("B")
    assert "fail-closed did not complete" in caplog.text and "exploded" not in caplog.text
    _accepted_critical(worker, trigger, buffer, "B", "e1", 10)
    assert worker.get_latest_result().event_id == "e1"
    assert gate.calls == [("lifecycle", 1, "A"), ("fail_closed",)]  # no later offer reaches it
    assert all(gate.lock_held)


# ── Retail wiring W1: strategy, request contract, failed bundles, audio ──────

def _retail_event(event_id: str = "r1", at: float = 10.0, *, session: str = "one"):
    from app.models.retail_candidate import (
        RetailCandidateEvent, RetailCandidateEvidence, RetailCandidateState,
    )
    evidence = RetailCandidateEvidence(
        "episode-1", "rak_kiri", 0, 4, 4, 0, 3.0, 13, 0.25, 1.0, 1.0, 0.0, 0.0, 0.01, 0, 1)
    return RetailCandidateEvent(event_id, session, DomainName.RETAIL_STORE, 4, at - 3.0, at, 99,
                                evidence, tuple(range(1, 14)), RetailCandidateState.CANDIDATE)


def _real_buffer(*timestamps: float, session: str = "one"):
    from app.models.event_frame import BufferedFrame
    from app.services.event_frame_buffer import EventFrameBuffer

    buffer = EventFrameBuffer()
    buffer.start_session(session)
    for seq, ts in enumerate(timestamps):
        assert buffer.offer(BufferedFrame(session, seq, ts, 640, 480, b"\xff\xd8frame\xff\xd9"))
    return buffer


def _steps(start: float, end: float, step: float = 0.1) -> tuple[float, ...]:
    return tuple(round(start + index * step, 3) for index in range(round((end - start) / step) + 1))


def _retail_worker(buffer, **kwargs):
    trigger, budget, client = FakeTrigger(), EvaluationBudget(), FakeClient()
    worker = EvaluationWorker(trigger, budget, buffer, client, clock=FakeClock(10.5),
                              bundle_timeout_seconds=0.05, **kwargs)
    worker.start_session("one")
    trigger.queue(_retail_event())
    return worker, trigger, budget, client


def test_retail_candidate_reaches_the_request_with_its_own_plan_and_no_object_track() -> None:
    worker, trigger, budget, client = _retail_worker(_real_buffer(*_steps(7.0, 10.5)))
    assert worker.process_one(10.5)
    (request,) = client.requests
    assert request.domain is DomainName.RETAIL_STORE and request.object_track_id is None
    assert request.person_track_id == 4 and request.evidence.zone_name == "rak_kiri"
    frames = request.frame_bundle.frames
    assert len(frames) == 5 and request.frame_bundle.domain is DomainName.RETAIL_STORE
    assert tuple(frame.target_offset_seconds for frame in frames) == (-2.0, -1.0, -.4, 0.0, .4)
    assert worker.get_snapshot().state is EvaluationWorkerState.WAITING_CALLBACK
    assert budget.get_snapshot(10.5).pending_event_id == "r1" and trigger.pending == "r1"


@pytest.mark.parametrize("case,failure", [
    ("short-history", EvaluationFailure.BUNDLE_INVALID),        # oldest frame after T-2
    ("deviation", EvaluationFailure.BUNDLE_INVALID),            # no frame within 0.3 s of T-1
    ("future-timeout", EvaluationFailure.BUNDLE_TIMEOUT),       # T+0.4 never arrives
])
def test_incomplete_retail_bundle_never_dispatches_and_releases_reservations(case, failure) -> None:
    timestamps = {
        "short-history": _steps(9.0, 10.5),
        "deviation": tuple(ts for ts in _steps(7.0, 10.5) if not 8.6 <= ts <= 9.4),
        "future-timeout": _steps(7.0, 10.2),
    }[case]
    worker, trigger, budget, client = _retail_worker(_real_buffer(*timestamps))
    assert worker.process_one(10.5)
    assert client.requests == []
    assert trigger.failed == ["r1"] and trigger.pending is None
    snapshot = budget.get_snapshot(10.5)
    assert snapshot.pending_event_id is None and snapshot.total_failed == 1
    assert worker.get_snapshot().last_failure is failure
    assert worker.get_snapshot().active_event_id is None


@pytest.mark.parametrize("switch_point", ["during-bundle", "after-bundle"])
def test_session_change_around_the_retail_bundle_never_dispatches(switch_point) -> None:
    real = _real_buffer(*_steps(7.0, 10.5))

    class SwitchingBuffer:
        def __init__(self) -> None:
            self.trigger = None

        def get_snapshot(self):
            return real.get_snapshot()

        def wait_for_bundle(self, plan, timeout_seconds):
            if switch_point == "during-bundle":
                real.end_session("one")                       # Stop/EOF while waiting
                return real.wait_for_bundle(plan, timeout_seconds)
            decision = real.wait_for_bundle(plan, timeout_seconds)
            self.trigger.session = "two"                      # new session after READY
            return decision

    buffer = SwitchingBuffer()
    worker, trigger, budget, client = _retail_worker(buffer)
    buffer.trigger = trigger
    assert worker.process_one(10.5)
    assert client.requests == []
    snapshot = budget.get_snapshot(10.5)
    assert snapshot.pending_event_id is None and snapshot.total_failed == 1
    assert trigger.failed == ["r1"] and trigger.pending is None
    expected = (EvaluationFailure.BUNDLE_INVALID if switch_point == "during-bundle"
                else EvaluationFailure.STALE_SESSION)
    assert worker.get_snapshot().last_failure is expected


def test_retail_critical_completes_without_audio_while_parking_still_offers() -> None:
    gate = RecordingGate()
    trigger, budget, buffer, client = FakeTrigger(), EvaluationBudget(), FakeBuffer(), FakeClient()
    worker = EvaluationWorker(trigger, budget, buffer, client, clock=FakeClock(10), audio_gate=gate)
    gate.worker = worker
    worker.start_session("one")
    trigger.queue(_retail_event("r1", 10.0))
    assert worker.process_one(10.0)
    assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), event_id="r1",
                   now=11) is CallbackDisposition.ACCEPTED
    assert worker.get_snapshot().state is EvaluationWorkerState.COMPLETED
    assert worker.get_latest_result().domain is DomainName.RETAIL_STORE
    assert [call[0] for call in gate.calls] == ["lifecycle"]              # no offer for retail
    trigger.queue(_event("p1", 30.0))
    assert worker.process_one(30.0)
    assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), event_id="p1",
                   now=31) is CallbackDisposition.ACCEPTED
    worker.end_session("one")
    assert gate.calls == [
        ("lifecycle", 1, "one"),
        ("offer", 1, "p1", "one", ThreatStatus.CRITICAL),
        ("lifecycle", 2, None),
    ]
    assert all(gate.lock_held)


def test_worker_no_longer_depends_on_parking_types() -> None:
    import inspect
    from app.services import evaluation_worker

    source = inspect.getsource(evaluation_worker)
    assert "ParkingCandidateTrigger" not in source and "ParkingCandidateEvent" not in source
    assert DomainName.RETAIL_STORE in EvaluationWorker(
        FakeTrigger(), EvaluationBudget(), FakeBuffer(), FakeClient())._strategies


# ── Handoff boundary: Stop before vs after the committed dispatch ────────────

class _GatedBuffer(FakeBuffer):
    """Holds wait_for_bundle until released (the pre-handoff window)."""

    def __init__(self) -> None:
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def wait_for_bundle(self, plan, timeout_seconds):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test gate was never released")
        return super().wait_for_bundle(plan, timeout_seconds)


class _GatedClient(FakeClient):
    """Holds dispatch() before the request reaches the (fake) transport."""

    def __init__(self) -> None:
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def dispatch(self, request) -> None:
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test gate was never released")
        super().dispatch(request)


def _run_in_thread(target) -> tuple[threading.Thread, list]:
    results: list = []
    thread = threading.Thread(target=lambda: results.append(target()))
    thread.start()
    return thread, results


def _join_all(*threads: threading.Thread) -> None:
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()


def _gated_audio_worker(buffer, client, **kwargs):
    trigger, budget, gate = FakeTrigger(), EvaluationBudget(), RecordingGate()
    worker = EvaluationWorker(trigger, budget, buffer, client, clock=FakeClock(10),
                              audio_gate=gate, **kwargs)
    gate.worker = worker
    worker.start_session("one")
    trigger.queue(_event("e1", 10))
    return worker, trigger, budget, gate


@pytest.mark.parametrize("transition", ["stop", "switch"])
def test_stop_or_switch_before_handoff_never_calls_the_dispatcher(transition) -> None:
    buffer, client = _GatedBuffer(), FakeClient()
    worker, trigger, budget, gate = _gated_audio_worker(buffer, client)
    processing, results = _run_in_thread(lambda: worker.process_one(10))
    try:
        assert buffer.entered.wait(2)                       # claimed, budget reserved, no handoff yet
        worker.end_session("one")
        if transition == "switch":
            worker.start_session("two")
        buffer.release.set()
        _join_all(processing)
    finally:
        buffer.release.set()
        processing.join(2)
    assert results == [True] and client.requests == []
    snapshot = worker.get_snapshot()
    assert snapshot.dispatch_attempts == 0 and snapshot.active_event_id is None
    assert snapshot.last_failure is EvaluationFailure.STALE_SESSION
    assert trigger.failed == ["e1"] and trigger.pending is None
    reservation = budget.get_snapshot(10)
    assert reservation.pending_event_id is None and reservation.total_failed == 1
    view = worker.get_current_view()
    assert view.source_session_id == ("two" if transition == "switch" else None)
    assert view.process_state is EvaluationWorkerState.IDLE and view.active_event_id is None
    assert "offer" not in [call[0] for call in gate.calls]


def test_stop_in_the_last_window_before_the_handoff_lock_is_caught() -> None:
    """The in-lock re-check catches a Stop that lands after the unlocked check."""
    class HookClock(FakeClock):
        hook = None

        def __call__(self) -> float:
            hook, self.hook = self.hook, None
            if hook is not None:
                hook()
            return self.now

    clock = HookClock(10)
    trigger, budget, client = FakeTrigger(), EvaluationBudget(), FakeClient()

    def stop_from_another_thread() -> None:
        stopper = threading.Thread(target=worker.end_session, args=("one",))
        stopper.start()
        _join_all(stopper)                                  # no worker lock is held here

    class ArmingBuffer(FakeBuffer):
        def wait_for_bundle(self, plan, timeout_seconds):
            decision = super().wait_for_bundle(plan, timeout_seconds)
            clock.hook = stop_from_another_thread           # next clock(): requested_at
            return decision

    worker = EvaluationWorker(trigger, budget, ArmingBuffer(), client, clock=clock)
    worker.start_session("one")
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert client.requests == [] and worker.get_snapshot().dispatch_attempts == 0
    assert worker.get_snapshot().last_failure is EvaluationFailure.STALE_SESSION
    assert trigger.failed == ["e1"] and budget.get_snapshot(10).pending_event_id is None


@pytest.mark.parametrize("max_dispatches", [None, 1])
def test_stop_after_handoff_never_waits_and_the_old_attempt_stays_isolated(max_dispatches) -> None:
    buffer, client = FakeBuffer(), _GatedClient()
    worker, trigger, budget, gate = _gated_audio_worker(buffer, client, max_dispatches=max_dispatches)
    processing, results = _run_in_thread(lambda: worker.process_one(10))
    lifecycle = threading.Thread(target=lambda: (worker.end_session("one"), worker.start_session("two")))
    try:
        assert client.entered.wait(2)                       # handoff done, request not sent yet
        snapshot = worker.get_snapshot()
        assert snapshot.state is EvaluationWorkerState.DISPATCHING and snapshot.dispatch_attempts == 1
        assert worker.process_one(10) is False              # no second attempt meanwhile
        lifecycle.start()
        _join_all(lifecycle)                                # Stop + new session without the dispatcher
        assert client.requests == []                        # dispatcher still held
        view = worker.get_current_view()
        assert (view.source_session_id, view.process_state, view.active_event_id) == (
            "two", EvaluationWorkerState.IDLE, None)
        assert trigger.failed == ["e1"] and budget.get_snapshot(10).pending_event_id is None
        assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), event_id="e1", session="one",
                       now=11) is CallbackDisposition.REJECTED
        client.release.set()
        _join_all(processing)
    finally:
        client.release.set()
        processing.join(2)
        if lifecycle.is_alive() or lifecycle.ident is not None:
            lifecycle.join(2)
    assert results == [True] and len(client.requests) == 1  # reached transport exactly once
    snapshot = worker.get_snapshot()
    assert snapshot.dispatch_attempts == 1 and snapshot.active_event_id is None
    assert snapshot.state is EvaluationWorkerState.IDLE     # late ACK did not revive WAITING_CALLBACK
    assert worker.get_current_view().latest_result is None
    assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), event_id="e1", session="one",
                   now=12) is CallbackDisposition.REJECTED
    assert [call[0] for call in gate.calls] == ["lifecycle", "lifecycle", "lifecycle"]

    # The new session is isolated from the old attempt; the spent quota stays spent.
    trigger.session = buffer.session = "two"
    trigger.queue(_event("e2", 30, session="two"))
    if max_dispatches == 1:
        assert worker.process_one(30) is False
        assert len(client.requests) == 1 and trigger.claimed == ["e1"]
        assert worker.get_snapshot().dispatch_attempts == 1
        return
    assert worker.process_one(30)
    assert len(client.requests) == 2 and worker.get_snapshot().dispatch_attempts == 2
    assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), event_id="e2", session="two",
                   now=31) is CallbackDisposition.ACCEPTED
    assert worker.get_current_view().latest_result.event_id == "e2"
    assert gate.calls[-1] == ("offer", 3, "e2", "two", ThreatStatus.CRITICAL)
