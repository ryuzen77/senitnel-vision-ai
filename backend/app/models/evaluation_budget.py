"""Metadata-only contracts for the process-wide external evaluation budget."""

from __future__ import annotations

import dataclasses
import enum

from app.models.domain import DomainName


class BudgetReason(str, enum.Enum):
    GRANTED = "GRANTED"
    STALE_CANDIDATE = "STALE_CANDIDATE"
    REQUEST_PENDING = "REQUEST_PENDING"
    MINIMUM_INTERVAL = "MINIMUM_INTERVAL"
    WINDOW_LIMIT = "WINDOW_LIMIT"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    UNKNOWN_DOMAIN = "UNKNOWN_DOMAIN"
    CLOSED = "CLOSED"
    INVALID_TIME = "INVALID_TIME"


@dataclasses.dataclass(frozen=True)
class BudgetDecision:
    granted: bool
    reason: BudgetReason
    reservation_id: str | None
    event_id: str
    domain: DomainName
    retry_after_seconds: float | None


@dataclasses.dataclass(frozen=True)
class BudgetSnapshot:
    pending_reservation_id: str | None
    pending_event_id: str | None
    pending_session_id: str | None
    pending_domain: DomainName | None
    last_granted_at: float | None
    granted_count_in_window: int
    remaining_requests_in_window: int
    next_interval_ready_at: float | None
    oldest_window_expiry: float | None
    total_granted: int
    total_completed: int
    total_failed: int
    total_expired: int
    total_denied_by_reason: tuple[tuple[BudgetReason, int], ...]
    total_granted_by_domain: tuple[tuple[DomainName, int], ...]
    total_denied_by_domain: tuple[tuple[DomainName, int], ...]
    closed: bool
