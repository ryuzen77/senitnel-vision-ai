"""Single-thread orchestration for fake/local evaluation clients; no transport."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
import math
import threading
import time
from typing import Protocol

from app.models.domain import DomainName
from app.models.evaluation import (
    EvaluationFailure, EvaluationRecord, EvaluationRequest, ThreatAssessment,
    WorkerSnapshot,
)
from app.models.event_frame import (
    BundleStatus, EventFrameBundle, FrameSelectionPlan, make_parking_frame_selection_plan,
)
from app.models.parking_candidate import ParkingCandidateEvent
from app.services.evaluation_budget import EvaluationBudget
from app.services.event_frame_buffer import EventFrameBuffer
from app.services.parking_candidate_trigger import ParkingCandidateTrigger


class LangflowClient(Protocol):
    """Synchronous transport boundary; this module provides no implementation."""

    def evaluate(self, request: EvaluationRequest) -> ThreatAssessment: ...


FrameSelectionPlanFactory = Callable[[str, str, float], FrameSelectionPlan]


class EvaluationWorker:
    """One process-level daemon; synchronous process_one supports fake-clock tests."""

    def __init__(
        self, trigger: ParkingCandidateTrigger, budget: EvaluationBudget,
        frame_buffer: EventFrameBuffer, client: LangflowClient,
        *, clock: Callable[[], float] = time.monotonic,
        strategies: Mapping[DomainName, FrameSelectionPlanFactory] | None = None,
        bundle_timeout_seconds: float = 2.0,
    ) -> None:
        if not math.isfinite(bundle_timeout_seconds) or bundle_timeout_seconds < 0:
            raise ValueError("bundle_timeout_seconds must be finite and non-negative")
        self._trigger = trigger
        self._budget = budget
        self._buffer = frame_buffer
        self._client = client
        self._clock = clock
        self._strategies = dict(strategies) if strategies is not None else {
            DomainName.PARKING_MOTORCYCLE: make_parking_frame_selection_plan,
        }
        self._bundle_timeout = bundle_timeout_seconds
        self._state_lock = threading.Lock()
        self._processing_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._active_event_id: str | None = None
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
                ready = self._trigger.wait_for_candidate(30.0)
                if self._stop.is_set():
                    break
                if ready and not self.process_one(self._clock()):
                    # A candidate may be visible while trigger's own global
                    # claim interval is active. This is a bounded wait, not a spin.
                    self._stop.wait(0.5)
            except Exception:
                self._record_failure(EvaluationFailure.CLIENT_ERROR)
                self._stop.wait(0.5)

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
            if self._closed:
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

    def process_one(self, now: float | None = None) -> bool:
        """Try one candidate. Never retries or retains a frame bundle afterward."""
        if not self._processing_lock.acquire(blocking=False):
            return False
        event: ParkingCandidateEvent | None = None
        reservation_id: str | None = None
        budget_done = trigger_done = False
        try:
            with self._state_lock:
                if self._closed:
                    self._failures[EvaluationFailure.CLOSED] += 1
                    self._last_failure = EvaluationFailure.CLOSED
                    return False
            claim_at = self._clock() if now is None else now
            event = self._trigger.claim_next_candidate(claim_at)
            if event is None:
                return False
            with self._state_lock:
                self._active_event_id = event.event_id
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
            try:
                assessment = self._client.evaluate(request)
            except Exception:
                self._record_failure(EvaluationFailure.CLIENT_ERROR)
                return True
            try:
                if not isinstance(assessment, ThreatAssessment):
                    raise ValueError("client must return ThreatAssessment")
                assessment.validate()
            except (ValueError, AttributeError, TypeError):
                self._record_failure(EvaluationFailure.INVALID_RESPONSE)
                return True
            if not self._current(event):
                self._record_failure(EvaluationFailure.STALE_SESSION)
                return True
            finished_at = self._clock()
            if not self._budget.complete(reservation_id, finished_at):
                self._record_failure(EvaluationFailure.STALE_SESSION)
                return True
            budget_done = True
            if not self._trigger.complete(event.event_id, finished_at):
                self._record_failure(EvaluationFailure.STALE_SESSION)
                return True
            trigger_done = True
            if self._trigger.get_snapshot().source_session_id != event.source_session_id:
                self._record_failure(EvaluationFailure.STALE_SESSION)
                return True
            record = EvaluationRecord(
                0, event.event_id, event.source_session_id, event.domain,
                requested_at, finished_at, assessment,
                tuple(frame.source_frame_seq for frame in request.frame_bundle.frames),
                str(getattr(self._client, "name", type(self._client).__name__)),
            )
            with self._state_lock:
                if self._closed:
                    self._failures[EvaluationFailure.CLOSED] += 1
                    self._last_failure = EvaluationFailure.CLOSED
                    return True
                self._evaluation_seq += 1
                self._latest = EvaluationRecord(
                    self._evaluation_seq, record.event_id, record.source_session_id,
                    record.domain, record.requested_at, record.completed_at,
                    record.assessment, record.frame_source_sequences, record.client_name,
                )
            return True
        except Exception:
            self._record_failure(EvaluationFailure.CLIENT_ERROR)
            return event is not None
        finally:
            # All external methods run outside worker locks. A stopped session
            # can reject these best-effort completions without retaining state.
            if reservation_id is not None and not budget_done:
                try:
                    self._budget.fail(reservation_id, self._clock())
                except Exception:
                    self._record_failure(EvaluationFailure.CLIENT_ERROR)
            if event is not None and not trigger_done:
                try:
                    self._trigger.fail(event.event_id, self._clock())
                except Exception:
                    self._record_failure(EvaluationFailure.CLIENT_ERROR)
            with self._state_lock:
                self._active_event_id = None
            self._processing_lock.release()

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
                self._active_event_id, latest_id, self._evaluation_seq,
                self._discarded_stale,
                tuple((kind, self._failures[kind]) for kind in EvaluationFailure),
                self._last_failure, self._last_budget_denial_reason,
            )

    def close(self, timeout_seconds: float = 2.0) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            self._stop.set()
        self._trigger.wake_waiters()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_seconds)
