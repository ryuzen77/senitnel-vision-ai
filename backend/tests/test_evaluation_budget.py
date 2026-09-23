"""Fake-monotonic-time tests for metadata-only global admission control."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
import threading

import pytest

from app.models.domain import DomainName
from app.models.evaluation_budget import BudgetReason
from app.services.evaluation_budget import EvaluationBudget


def _reserve(budget: EvaluationBudget, event: str, now: float, *, created: float | None = None,
             session: str = "one", domain: DomainName = DomainName.PARKING_MOTORCYCLE):
    return budget.try_reserve(event, session, domain, now if created is None else created, now)


def test_first_request_and_pending_slot_and_complete() -> None:
    budget = EvaluationBudget()
    first = _reserve(budget, "a", 0)
    assert first.granted and first.reason is BudgetReason.GRANTED and first.reservation_id
    blocked = _reserve(budget, "b", 1)
    assert blocked.reason is BudgetReason.REQUEST_PENDING and blocked.retry_after_seconds == 29
    assert budget.complete(first.reservation_id, 2)
    assert not budget.complete(first.reservation_id, 2)
    assert _reserve(budget, "b", 9).reason is BudgetReason.MINIMUM_INTERVAL
    second = _reserve(budget, "b", 10)
    assert second.granted and second.reservation_id != first.reservation_id


def test_two_grants_window_limit_and_rolling_expiry() -> None:
    budget = EvaluationBudget()
    a = _reserve(budget, "a", 0); assert budget.complete(a.reservation_id, 0)
    b = _reserve(budget, "b", 10); assert budget.complete(b.reservation_id, 10)
    denied = _reserve(budget, "c", 20)
    assert denied.reason is BudgetReason.WINDOW_LIMIT and denied.retry_after_seconds == 40
    assert _reserve(budget, "c", 60).granted  # exact left-open boundary
    snap = budget.get_snapshot(60)
    assert snap.granted_count_in_window == 2 and snap.oldest_window_expiry == 70


def test_stale_duplicate_and_denials_never_spend_budget() -> None:
    budget = EvaluationBudget()
    assert _reserve(budget, "old", 9, created=0).reason is BudgetReason.STALE_CANDIDATE
    first = _reserve(budget, "a", 10)
    assert budget.complete(first.reservation_id, 10)
    assert _reserve(budget, "a", 11).reason is BudgetReason.DUPLICATE_EVENT
    snap = budget.get_snapshot(11)
    assert snap.total_granted == 1 and snap.granted_count_in_window == 1
    assert dict(snap.total_denied_by_reason)[BudgetReason.STALE_CANDIDATE] == 1
    assert dict(snap.total_denied_by_reason)[BudgetReason.DUPLICATE_EVENT] == 1


def test_failure_spends_budget_and_does_not_retry_storm() -> None:
    budget = EvaluationBudget()
    first = _reserve(budget, "a", 0)
    assert budget.fail(first.reservation_id, 1)
    assert not budget.fail(first.reservation_id, 1)
    assert _reserve(budget, "b", 2).reason is BudgetReason.MINIMUM_INTERVAL
    assert _reserve(budget, "a", 10).reason is BudgetReason.DUPLICATE_EVENT
    assert budget.get_snapshot(10).total_failed == 1


def test_pending_timeout_opens_slot_but_late_completion_is_rejected() -> None:
    budget = EvaluationBudget()
    first = _reserve(budget, "a", 0)
    snap = budget.get_snapshot(30)
    assert snap.pending_reservation_id is None and snap.total_expired == 1
    assert not budget.complete(first.reservation_id, 30)
    assert not budget.fail(first.reservation_id, 30)
    second = _reserve(budget, "b", 30)
    assert second.granted
    assert budget.get_snapshot(30).granted_count_in_window == 2


@pytest.mark.parametrize("bad", [-1, math.nan, math.inf, -math.inf, True])
def test_invalid_timestamps_rejected(bad: float) -> None:
    budget = EvaluationBudget()
    assert budget.try_reserve("a", "one", DomainName.PARKING_MOTORCYCLE, bad, 1).reason is BudgetReason.INVALID_TIME
    assert budget.try_reserve("a", "one", DomainName.PARKING_MOTORCYCLE, 0, bad).reason is BudgetReason.INVALID_TIME
    with pytest.raises(ValueError):
        budget.get_snapshot(bad)


def test_backward_and_future_candidate_time_rejected() -> None:
    budget = EvaluationBudget()
    budget.get_snapshot(10)
    assert _reserve(budget, "a", 9).reason is BudgetReason.INVALID_TIME
    assert budget.try_reserve("a", "one", DomainName.PARKING_MOTORCYCLE, 12, 11).reason is BudgetReason.INVALID_TIME
    assert not budget.complete("missing", 8)
    assert _reserve(budget, "a", 11).granted


def test_new_session_does_not_reset_history() -> None:
    budget = EvaluationBudget()
    first = _reserve(budget, "a", 0, session="old")
    assert budget.complete(first.reservation_id, 1)
    assert _reserve(budget, "b", 2, session="new").reason is BudgetReason.MINIMUM_INTERVAL
    second = _reserve(budget, "b", 10, session="new")
    assert second.granted
    assert budget.get_snapshot(10).pending_session_id == "new"


def test_processed_cache_is_bounded_and_old_event_cannot_be_replayed() -> None:
    budget = EvaluationBudget(processed_event_cache_size=2, minimum_request_interval_seconds=10,
                              max_requests_per_window=10, rolling_window_seconds=10,
                              candidate_max_age_seconds=8)
    for event, now in (("a", 0), ("b", 10), ("c", 20)):
        grant = _reserve(budget, event, now)
        assert grant.granted and budget.complete(grant.reservation_id, now)
    assert len(budget._processed_ids) == len(budget._processed_set) == 2
    assert _reserve(budget, "a", 30, created=0).reason is BudgetReason.STALE_CANDIDATE


def test_concurrent_reserve_only_one_winner() -> None:
    budget = EvaluationBudget()
    barrier = threading.Barrier(8)
    decisions = []
    def attempt(index: int) -> None:
        barrier.wait(timeout=2)
        decisions.append(_reserve(budget, f"event-{index}", 0))
    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=2)
    assert all(not thread.is_alive() for thread in threads)
    assert sum(decision.granted for decision in decisions) == 1
    assert budget.get_snapshot(0).total_granted == 1


def test_close_idempotent_and_snapshot_immutable() -> None:
    budget = EvaluationBudget()
    snap = budget.get_snapshot(0)
    with pytest.raises(FrozenInstanceError):
        snap.total_granted = 9
    budget.close(); budget.close()
    assert _reserve(budget, "a", 0).reason is BudgetReason.CLOSED
    assert budget.get_snapshot(0).closed


def test_constructor_rejects_unsafe_cache_or_policy() -> None:
    with pytest.raises(ValueError):
        EvaluationBudget(max_pending_requests=2)
    with pytest.raises(ValueError):
        EvaluationBudget(processed_event_cache_size=1, minimum_request_interval_seconds=1,
                         candidate_max_age_seconds=8)
    with pytest.raises(ValueError):
        EvaluationBudget(rolling_window_seconds=math.inf)


@pytest.mark.parametrize("timeline,expected", [
    ((5.594, 17.828), (BudgetReason.GRANTED, BudgetReason.GRANTED)),
    ((27.844, 42.219, 50.640, 57.219),
     (BudgetReason.GRANTED, BudgetReason.GRANTED, BudgetReason.MINIMUM_INTERVAL, BudgetReason.WINDOW_LIMIT)),
])
def test_observed_video_timelines_with_immediate_completion(timeline, expected) -> None:
    budget = EvaluationBudget()
    reasons = []
    for index, timestamp in enumerate(timeline):
        decision = _reserve(budget, f"candidate-{index}", timestamp)
        reasons.append(decision.reason)
        if decision.granted:
            assert budget.complete(decision.reservation_id, timestamp)
    assert tuple(reasons) == expected
    assert budget.get_snapshot(timeline[-1]).total_granted == 2


def test_rejected_candidate_is_not_queued_for_later_dispatch() -> None:
    budget = EvaluationBudget()
    first = _reserve(budget, "a", 0); budget.complete(first.reservation_id, 0)
    denied = _reserve(budget, "b", 1)
    assert denied.reason is BudgetReason.MINIMUM_INTERVAL and denied.reservation_id is None
    assert budget.try_reserve("b", "one", DomainName.PARKING_MOTORCYCLE, 1, 10).reason is BudgetReason.STALE_CANDIDATE
    assert budget.get_snapshot(10).total_granted == 1


def test_unknown_never_reserves_and_domain_counts_are_immutable() -> None:
    budget = EvaluationBudget()
    unknown = _reserve(budget, "unknown", 0, domain=DomainName.UNKNOWN)
    assert not unknown.granted and unknown.reason is BudgetReason.UNKNOWN_DOMAIN
    assert unknown.reservation_id is None and unknown.domain is DomainName.UNKNOWN
    retail = _reserve(budget, "retail", 0, domain=DomainName.RETAIL_STORE)
    assert retail.granted and retail.domain is DomainName.RETAIL_STORE
    snapshot = budget.get_snapshot(0)
    assert snapshot.pending_domain is DomainName.RETAIL_STORE
    assert dict(snapshot.total_granted_by_domain) == {
        DomainName.UNKNOWN: 0, DomainName.PARKING_MOTORCYCLE: 0, DomainName.RETAIL_STORE: 1,
    }
    assert dict(snapshot.total_denied_by_domain)[DomainName.UNKNOWN] == 1
    assert snapshot.total_granted == 1 and snapshot.total_denied_by_reason
    with pytest.raises(FrozenInstanceError):
        snapshot.pending_domain = DomainName.UNKNOWN


def test_budget_is_shared_across_parking_and_retail_without_domain_rules() -> None:
    budget = EvaluationBudget()
    parking = _reserve(budget, "parking", 0)
    assert budget.complete(parking.reservation_id, 0)
    assert _reserve(budget, "retail-early", 5, domain=DomainName.RETAIL_STORE).reason is BudgetReason.MINIMUM_INTERVAL
    retail = _reserve(budget, "retail", 10, domain=DomainName.RETAIL_STORE)
    assert retail.granted and budget.complete(retail.reservation_id, 10)
    assert _reserve(budget, "parking-next", 20).reason is BudgetReason.WINDOW_LIMIT
    snapshot = budget.get_snapshot(20)
    assert dict(snapshot.total_granted_by_domain)[DomainName.PARKING_MOTORCYCLE] == 1
    assert dict(snapshot.total_granted_by_domain)[DomainName.RETAIL_STORE] == 1
    assert dict(snapshot.total_denied_by_domain)[DomainName.RETAIL_STORE] == 1
    assert dict(snapshot.total_denied_by_domain)[DomainName.PARKING_MOTORCYCLE] == 1


def test_invalid_domain_is_rejected_before_state_mutation() -> None:
    budget = EvaluationBudget()
    with pytest.raises(ValueError, match="DomainName"):
        budget.try_reserve("event", "session", "RETAIL_STORE", 0, 0)
    assert budget.get_snapshot(0).total_granted == 0
