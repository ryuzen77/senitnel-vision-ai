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
    worker, trigger, budget, buffer, client, _ = _worker()
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
