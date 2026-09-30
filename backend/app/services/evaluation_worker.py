"""Session-safe asynchronous evaluation coordinator; no HTTP implementation here."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
import threading
import time
from typing import Protocol

from app.models.domain import DomainName
from app.models.evaluation import (
    CallbackDisposition, EvaluationCallback, EvaluationFailure, EvaluationRecord,
    EvaluationRequest, EvaluationWorkerState, ThreatAssessment, WorkerSnapshot,
)
from app.models.event_frame import (
    BundleStatus, EventFrameBundle, FrameSelectionPlan, make_parking_frame_selection_plan,
)
from app.models.parking_candidate import ParkingCandidateEvent
from app.services.evaluation_budget import EvaluationBudget
from app.services.event_frame_buffer import EventFrameBuffer
from app.services.parking_candidate_trigger import ParkingCandidateTrigger


class LangflowDispatcher(Protocol):
    """A normal return means Webhook dispatch ACK, never an assessment.

    A raised exception is an uncertain dispatch: the remote flow might still
    call back. Implementations must not automatically retry.
    """

    def dispatch(self, request: EvaluationRequest) -> None: ...


FrameSelectionPlanFactory = Callable[[str, str, float], FrameSelectionPlan]


@dataclass(frozen=True)
class _PendingEvaluation:
    """Only correlation metadata is retained; no bundle, JPEG, or base64."""

    event_id: str
    source_session_id: str
    domain: DomainName
    reservation_id: str
    requested_at: float
    deadline: float
    frame_source_sequences: tuple[int, ...]
    client_name: str


class EvaluationWorker:
    """One process-level worker; dispatch and callback completion are separate."""

    def __init__(
        self, trigger: ParkingCandidateTrigger, budget: EvaluationBudget,
        frame_buffer: EventFrameBuffer, client: LangflowDispatcher,
        *, clock: Callable[[], float] = time.monotonic,
        strategies: Mapping[DomainName, FrameSelectionPlanFactory] | None = None,
        bundle_timeout_seconds: float = 2.0,
        callback_timeout_seconds: float = 20.0,
    ) -> None:
        if not math.isfinite(bundle_timeout_seconds) or bundle_timeout_seconds < 0:
            raise ValueError("bundle_timeout_seconds must be finite and non-negative")
        if not math.isfinite(callback_timeout_seconds) or not 0 < callback_timeout_seconds <= 20:
            raise ValueError("callback_timeout_seconds must be finite and at most 20")
        if not callable(getattr(client, "dispatch", None)):
            raise TypeError("client must implement asynchronous Webhook dispatch")
        self._trigger = trigger
        self._budget = budget
        self._buffer = frame_buffer
        self._client = client
        self._clock = clock
        self._strategies = dict(strategies) if strategies is not None else {
            DomainName.PARKING_MOTORCYCLE: make_parking_frame_selection_plan,
        }
        self._bundle_timeout = bundle_timeout_seconds
        self._callback_timeout = callback_timeout_seconds
        self._state_lock = threading.RLock()
        self._processing_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._state = EvaluationWorkerState.IDLE
        self._session_id: str | None = None
        self._ended_session_id: str | None = None
        self._pending: _PendingEvaluation | None = None
        self._last_accepted: EvaluationCallback | None = None
        self._latest: EvaluationRecord | None = None
        self._evaluation_seq = 0
        self._discarded_stale = 0
        self._failures: Counter[EvaluationFailure] = Counter()
        self._last_failure: EvaluationFailure | None = None
        self._last_budget_denial_reason: str | None = None

    def start(self) -> None:
        with self._state_lock:
            if self._closed or self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="evaluation-worker", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sweep(self._clock())
                with self._state_lock:
                    pending = self._pending
                if pending is not None:
                    self._stop.wait(min(max(pending.deadline - self._clock(), 0.0), 0.25))
                    continue
                ready = self._trigger.wait_for_candidate(0.5)
                if ready and not self._stop.is_set() and not self.process_one(self._clock()):
                    self._stop.wait(0.1)
            except Exception:
                self._record_failure(EvaluationFailure.CLIENT_ERROR)
                self._stop.wait(0.1)

    def _record_failure(self, category: EvaluationFailure, budget_reason: str | None = None) -> None:
        with self._state_lock:
            self._failures[category] += 1
            self._last_failure = category
            if budget_reason is not None:
                self._last_budget_denial_reason = budget_reason
            if category is EvaluationFailure.STALE_SESSION:
                self._discarded_stale += 1

    def _current(self, event: ParkingCandidateEvent) -> bool:
        with self._state_lock:
            if (self._closed or self._ended_session_id == event.source_session_id
                    or (self._session_id is not None and self._session_id != event.source_session_id)):
                return False
        snapshot = self._trigger.get_snapshot()
        return (snapshot.source_session_id == event.source_session_id
                and snapshot.pending_event_id == event.event_id
                and self._buffer.get_snapshot().source_session_id == event.source_session_id)

    @staticmethod
    def _valid_bundle(bundle: EventFrameBundle, plan: FrameSelectionPlan) -> bool:
        frames = bundle.frames
        if (bundle.event_id != plan.event_id or bundle.source_session_id != plan.source_session_id
                or bundle.domain is not plan.domain or bundle.triggered_at != plan.triggered_at
                or len(frames) != 5 or plan.required_frame_count != 5
                or len(plan.target_offsets_seconds) != 5
                or not math.isfinite(plan.maximum_target_deviation_seconds)
                or plan.maximum_target_deviation_seconds < 0
                or len({frame.source_frame_seq for frame in frames}) != 5
                or tuple(frame.target_offset_seconds for frame in frames) != plan.target_offsets_seconds
                or bundle.total_jpeg_bytes != sum(len(frame.jpeg) for frame in frames)
                or bundle.total_jpeg_bytes <= 0):
            return False
        return all(
            isinstance(frame.jpeg, bytes) and bool(frame.jpeg)
            and frame.mime_type == "image/jpeg"
            and frame.frame_width > 0 and frame.frame_height > 0
            and math.isfinite(frame.deviation_seconds)
            and 0 <= frame.deviation_seconds <= plan.maximum_target_deviation_seconds
            and abs(frame.source_timestamp - (plan.triggered_at + frame.target_offset_seconds))
            <= plan.maximum_target_deviation_seconds
            for frame in frames
        ) and all(
            left.source_timestamp < right.source_timestamp
            and left.source_frame_seq < right.source_frame_seq
            for left, right in zip(frames, frames[1:])
        )

    def _fail_pending_locked(self, now: float, state: EvaluationWorkerState,
                             category: EvaluationFailure) -> None:
        pending = self._pending
        if pending is None:
            return
        self._pending = None
        self._state = state
        try:
            try:
                self._budget.fail(pending.reservation_id, now)
            except Exception:
                self._record_failure(EvaluationFailure.CLIENT_ERROR)
        finally:
            try:
                self._trigger.fail(pending.event_id, now)
            except Exception:
                self._record_failure(EvaluationFailure.CLIENT_ERROR)
        self._record_failure(category)

    def sweep(self, now: float | None = None) -> bool:
        """Expire a callback deadline; also called by the daemon with a real clock."""
        checked_at = self._clock() if now is None else now
        if not math.isfinite(checked_at) or checked_at < 0:
            raise ValueError("now must be finite and non-negative")
        with self._state_lock:
            if self._pending is None or checked_at < self._pending.deadline:
                return False
            self._fail_pending_locked(checked_at, EvaluationWorkerState.TIMED_OUT,
                                      EvaluationFailure.CALLBACK_TIMEOUT)
            return True

    def start_session(self, session_id: str) -> None:
        with self._state_lock:
            if self._closed or self._session_id == session_id:
                return
            if self._pending is not None:
                self._fail_pending_locked(self._clock(), EvaluationWorkerState.CANCELLED,
                                          EvaluationFailure.CANCELLED)
            self._session_id = session_id
            self._ended_session_id = None
            self._last_accepted = None
            self._latest = None
            self._state = EvaluationWorkerState.IDLE

    def end_session(self, session_id: str) -> None:
        with self._state_lock:
            if self._session_id != session_id:
                return
            if self._pending is not None:
                self._fail_pending_locked(self._clock(), EvaluationWorkerState.CANCELLED,
                                          EvaluationFailure.CANCELLED)
            self._session_id = None
            self._ended_session_id = session_id
            self._last_accepted = None
            self._latest = None
            self._state = EvaluationWorkerState.CANCELLED

    def process_one(self, now: float | None = None) -> bool:
        """Claim and dispatch one event; a 2xx ACK leaves budget/trigger pending."""
        if not self._processing_lock.acquire(blocking=False):
            return False
        event: ParkingCandidateEvent | None = None
        reservation_id: str | None = None
        handed_off = False
        try:
            with self._state_lock:
                if self._closed:
                    self._record_failure(EvaluationFailure.CLOSED)
                    return False
                if self._pending is not None:
                    return False
            claim_at = self._clock() if now is None else now
            event = self._trigger.claim_next_candidate(claim_at)
            if event is None:
                return False
            with self._state_lock:
                if self._ended_session_id == event.source_session_id:
                    self._record_failure(EvaluationFailure.STALE_SESSION)
                    return True
                if self._session_id is None:
                    self._session_id = event.source_session_id
            strategy = self._strategies.get(event.domain)
            if event.domain is DomainName.UNKNOWN or strategy is None:
                self._record_failure(EvaluationFailure.NO_STRATEGY)
                return True
            if not self._current(event):
                self._record_failure(EvaluationFailure.STALE_SESSION)
                return True
            decision = self._budget.try_reserve(
                event.event_id, event.source_session_id, event.domain, event.triggered_at, claim_at,
            )
            if not decision.granted:
                self._record_failure(EvaluationFailure.BUDGET_DENIED, decision.reason.value)
                return True
            reservation_id = decision.reservation_id
            assert reservation_id is not None
            plan = strategy(event.event_id, event.source_session_id, event.triggered_at)
            if (not isinstance(plan, FrameSelectionPlan) or plan.domain is not event.domain
                    or plan.event_id != event.event_id or plan.source_session_id != event.source_session_id):
                self._record_failure(EvaluationFailure.BUNDLE_INVALID)
                return True
            bundle_decision = self._buffer.wait_for_bundle(plan, self._bundle_timeout)
            if bundle_decision.status is BundleStatus.WAITING_FOR_FUTURE_FRAME:
                self._record_failure(EvaluationFailure.BUNDLE_TIMEOUT)
                return True
            if (bundle_decision.status is not BundleStatus.READY or bundle_decision.bundle is None
                    or not self._valid_bundle(bundle_decision.bundle, plan)):
                self._record_failure(EvaluationFailure.BUNDLE_INVALID)
                return True
            if not self._current(event):
                self._record_failure(EvaluationFailure.STALE_SESSION)
                return True
            request = EvaluationRequest(
                event.event_id, event.source_session_id, event.domain, event.started_at,
                event.triggered_at, event.person_track_id, event.object_track_id,
                event.evidence, bundle_decision.bundle,
            )
            requested_at = self._clock()
            with self._state_lock:
                if not self._current(event):
                    self._record_failure(EvaluationFailure.STALE_SESSION)
                    return True
                self._pending = _PendingEvaluation(
                    event.event_id, event.source_session_id, event.domain,
                    reservation_id, requested_at, requested_at + self._callback_timeout,
                    tuple(frame.source_frame_seq for frame in request.frame_bundle.frames),
                    str(getattr(self._client, "name", type(self._client).__name__)),
                )
                self._state = EvaluationWorkerState.DISPATCHING
                handed_off = True
            try:
                self._client.dispatch(request)
            except Exception:
                # Timeout/connection failure can happen after the Webhook received
                # the body. Never retry or release the reservation prematurely.
                self._record_failure(EvaluationFailure.DISPATCH_UNCERTAIN)
            finally:
                with self._state_lock:
                    if self._pending is not None and self._pending.event_id == event.event_id:
                        self._state = EvaluationWorkerState.WAITING_CALLBACK
            self.sweep(self._clock())
            return True
        except Exception:
            self._record_failure(EvaluationFailure.CLIENT_ERROR)
            with self._state_lock:
                if self._pending is not None and event is not None and self._pending.event_id == event.event_id:
                    self._fail_pending_locked(self._clock(), EvaluationWorkerState.FAILED,
                                              EvaluationFailure.CLIENT_ERROR)
            return event is not None
        finally:
            if not handed_off:
                if reservation_id is not None:
                    try:
                        self._budget.fail(reservation_id, self._clock())
                    except Exception:
                        self._record_failure(EvaluationFailure.CLIENT_ERROR)
                if event is not None:
                    try:
                        self._trigger.fail(event.event_id, self._clock())
                    except Exception:
                        self._record_failure(EvaluationFailure.CLIENT_ERROR)
                with self._state_lock:
                    if event is not None and self._state is EvaluationWorkerState.IDLE:
                        self._state = EvaluationWorkerState.FAILED
            self._processing_lock.release()

    def accept_callback(self, callback: EvaluationCallback, now: float) -> CallbackDisposition:
        """First matching callback wins; a conflicting replay is rejected."""
        with self._state_lock:
            if (not isinstance(callback, EvaluationCallback)
                    or not isinstance(callback.assessment, ThreatAssessment)
                    or self._closed or not math.isfinite(now) or now < 0
                    or callback.source_session_id != self._session_id
                    or self._trigger.get_snapshot().source_session_id != callback.source_session_id):
                return CallbackDisposition.REJECTED
            if self._last_accepted is not None and callback.event_id == self._last_accepted.event_id:
                return (CallbackDisposition.DUPLICATE if callback == self._last_accepted
                        else CallbackDisposition.REJECTED)
            pending = self._pending
            if pending is None or callback.event_id != pending.event_id:
                return CallbackDisposition.REJECTED
            if now >= pending.deadline:
                self._fail_pending_locked(now, EvaluationWorkerState.TIMED_OUT,
                                          EvaluationFailure.CALLBACK_TIMEOUT)
                return CallbackDisposition.REJECTED
            try:
                callback.assessment.validate()
            except (ValueError, TypeError, AttributeError):
                return CallbackDisposition.REJECTED
            if (self._buffer.get_snapshot().source_session_id != pending.source_session_id
                    or self._trigger.get_snapshot().pending_event_id != pending.event_id):
                return CallbackDisposition.REJECTED
            if not self._budget.complete(pending.reservation_id, now):
                self._fail_pending_locked(now, EvaluationWorkerState.FAILED,
                                          EvaluationFailure.STALE_SESSION)
                return CallbackDisposition.REJECTED
            if not self._trigger.complete(pending.event_id, now):
                self._pending = None
                self._state = EvaluationWorkerState.FAILED
                self._record_failure(EvaluationFailure.STALE_SESSION)
                return CallbackDisposition.REJECTED
            self._pending = None
            self._state = EvaluationWorkerState.COMPLETED
            self._evaluation_seq += 1
            self._latest = EvaluationRecord(
                self._evaluation_seq, pending.event_id, pending.source_session_id,
                pending.domain, pending.requested_at, now, callback.assessment,
                pending.frame_source_sequences, pending.client_name,
            )
            self._last_accepted = callback
            return CallbackDisposition.ACCEPTED

    def get_latest_result(self) -> EvaluationRecord | None:
        session = self._trigger.get_snapshot().source_session_id
        with self._state_lock:
            return self._latest if self._latest is not None and self._latest.source_session_id == session else None

    def get_snapshot(self) -> WorkerSnapshot:
        session = self._trigger.get_snapshot().source_session_id
        with self._state_lock:
            latest_id = (self._latest.event_id if self._latest is not None
                         and self._latest.source_session_id == session else None)
            return WorkerSnapshot(
                self._thread is not None and self._thread.is_alive(), self._closed,
                self._pending.event_id if self._pending is not None else None,
                latest_id, self._evaluation_seq, self._discarded_stale,
                tuple((kind, self._failures[kind]) for kind in EvaluationFailure),
                self._last_failure, self._last_budget_denial_reason, self._state,
                self._pending.deadline if self._pending is not None else None,
            )

    def close(self, timeout_seconds: float = 2.0) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            if self._pending is not None:
                self._fail_pending_locked(self._clock(), EvaluationWorkerState.CANCELLED,
                                          EvaluationFailure.CANCELLED)
            thread = self._thread
        self._trigger.wake_waiters()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_seconds)
