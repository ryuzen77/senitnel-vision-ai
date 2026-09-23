"""Small, thread-safe, process-wide admission control; never dispatches requests."""

from __future__ import annotations

from collections import Counter, deque
import dataclasses
import math
import threading
import uuid

from app.models.evaluation_budget import BudgetDecision, BudgetReason, BudgetSnapshot
from app.models.domain import DomainName


@dataclasses.dataclass(frozen=True)
class _Pending:
    reservation_id: str
    event_id: str
    session_id: str
    domain: DomainName
    granted_at: float


class EvaluationBudget:
    """Budget survives session changes; caller supplies monotonic timestamps.

    Granted event IDs are retained in a bounded cache. Since grants are spaced
    by the minimum interval, a cached ID cannot be evicted while its candidate
    is still young enough to be admitted again under the default policy.
    """

    def __init__(
        self, *, max_pending_requests: int = 1,
        minimum_request_interval_seconds: float = 10.0,
        max_requests_per_window: int = 2,
        rolling_window_seconds: float = 60.0,
        candidate_max_age_seconds: float = 8.0,
        pending_timeout_seconds: float = 30.0,
        processed_event_cache_size: int = 256,
    ) -> None:
        if max_pending_requests != 1:
            raise ValueError("only one global pending request is supported")
        if min(max_requests_per_window, processed_event_cache_size) < 1:
            raise ValueError("window and cache sizes must be positive")
        values = (minimum_request_interval_seconds, rolling_window_seconds,
                  candidate_max_age_seconds, pending_timeout_seconds)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("budget durations must be finite and positive")
        if processed_event_cache_size * minimum_request_interval_seconds <= candidate_max_age_seconds:
            raise ValueError("event cache must outlive candidate admission age")
        self._interval = minimum_request_interval_seconds
        self._window_limit = max_requests_per_window
        self._window_seconds = rolling_window_seconds
        self._candidate_age = candidate_max_age_seconds
        self._pending_timeout = pending_timeout_seconds
        self._cache_size = processed_event_cache_size
        self._lock = threading.Lock()
        self._grant_times: deque[float] = deque()
        self._processed_ids: deque[str] = deque()
        self._processed_set: set[str] = set()
        self._pending: _Pending | None = None
        self._last_granted_at: float | None = None
        self._last_now: float | None = None
        self._total_granted = self._total_completed = self._total_failed = self._total_expired = 0
        self._denied: Counter[BudgetReason] = Counter()
        self._granted_by_domain: Counter[DomainName] = Counter()
        self._denied_by_domain: Counter[DomainName] = Counter()
        self._closed = False

    @staticmethod
    def _valid_time(value: float) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0

    def _advance(self, now: float) -> bool:
        if not self._valid_time(now) or (self._last_now is not None and now < self._last_now):
            return False
        self._last_now = now
        while self._grant_times and self._grant_times[0] + self._window_seconds <= now:
            self._grant_times.popleft()
        if self._pending is not None and self._pending.granted_at + self._pending_timeout <= now:
            self._pending = None
            self._total_expired += 1
        return True

    def _deny(self, event_id: str, domain: DomainName, reason: BudgetReason, retry: float | None = None) -> BudgetDecision:
        self._denied[reason] += 1
        self._denied_by_domain[domain] += 1
        return BudgetDecision(False, reason, None, event_id, domain, retry)

    def try_reserve(self, event_id: str, source_session_id: str, domain: DomainName,
                    candidate_created_at: float, now: float) -> BudgetDecision:
        with self._lock:
            if not isinstance(domain, DomainName):
                raise ValueError("domain must be a DomainName")
            if self._closed:
                return self._deny(event_id, domain, BudgetReason.CLOSED)
            if (not self._valid_time(candidate_created_at) or not self._valid_time(now)
                    or candidate_created_at > now or not self._advance(now)):
                return self._deny(event_id, domain, BudgetReason.INVALID_TIME)
            if not event_id or not source_session_id:
                raise ValueError("event_id and source_session_id must be nonempty")
            if domain is DomainName.UNKNOWN:
                return self._deny(event_id, domain, BudgetReason.UNKNOWN_DOMAIN)
            if event_id in self._processed_set:
                return self._deny(event_id, domain, BudgetReason.DUPLICATE_EVENT)
            if now - candidate_created_at > self._candidate_age:
                return self._deny(event_id, domain, BudgetReason.STALE_CANDIDATE)
            if self._pending is not None:
                return self._deny(event_id, domain, BudgetReason.REQUEST_PENDING,
                                  max(0.0, self._pending.granted_at + self._pending_timeout - now))
            if self._last_granted_at is not None and now < self._last_granted_at + self._interval:
                return self._deny(event_id, domain, BudgetReason.MINIMUM_INTERVAL,
                                  self._last_granted_at + self._interval - now)
            if len(self._grant_times) >= self._window_limit:
                return self._deny(event_id, domain, BudgetReason.WINDOW_LIMIT,
                                  max(0.0, self._grant_times[0] + self._window_seconds - now))
            reservation_id = str(uuid.uuid4())
            self._pending = _Pending(reservation_id, event_id, source_session_id, domain, now)
            self._grant_times.append(now)
            self._last_granted_at = now
            if len(self._processed_ids) == self._cache_size:
                self._processed_set.remove(self._processed_ids.popleft())
            self._processed_ids.append(event_id)
            self._processed_set.add(event_id)
            self._total_granted += 1
            self._granted_by_domain[domain] += 1
            return BudgetDecision(True, BudgetReason.GRANTED, reservation_id, event_id, domain, None)

    def _finish(self, reservation_id: str, now: float, *, failed: bool) -> bool:
        with self._lock:
            if self._closed or not self._advance(now) or self._pending is None:
                return False
            if self._pending.reservation_id != reservation_id:
                return False
            self._pending = None
            if failed:
                self._total_failed += 1
            else:
                self._total_completed += 1
            return True

    def complete(self, reservation_id: str, now: float) -> bool:
        return self._finish(reservation_id, now, failed=False)

    def fail(self, reservation_id: str, now: float) -> bool:
        return self._finish(reservation_id, now, failed=True)

    def get_snapshot(self, now: float) -> BudgetSnapshot:
        with self._lock:
            if not self._advance(now):
                raise ValueError("now must be finite, non-negative, and monotonic")
            pending = self._pending
            return BudgetSnapshot(
                pending.reservation_id if pending else None,
                pending.event_id if pending else None,
                pending.session_id if pending else None,
                pending.domain if pending else None,
                self._last_granted_at,
                len(self._grant_times),
                max(0, self._window_limit - len(self._grant_times)),
                self._last_granted_at + self._interval if self._last_granted_at is not None else None,
                self._grant_times[0] + self._window_seconds if self._grant_times else None,
                self._total_granted, self._total_completed, self._total_failed, self._total_expired,
                tuple((reason, self._denied[reason]) for reason in BudgetReason if reason is not BudgetReason.GRANTED),
                tuple((domain, self._granted_by_domain[domain]) for domain in DomainName),
                tuple((domain, self._denied_by_domain[domain]) for domain in DomainName),
                self._closed,
            )

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending = None
