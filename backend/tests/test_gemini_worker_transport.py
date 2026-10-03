"""Worker with a fake direct (Gemini-style) transport: no network, no speaker."""

from __future__ import annotations

import enum
import logging
import threading

import pytest

from app.models.domain import DomainName
from app.models.evaluation import (
    CallbackDisposition, EvaluationCallback, EvaluationFailure, EvaluationWorkerState, ThreatStatus,
)
from app.services.audio_alert_service import AudioAlertGate
from app.services.evaluation_budget import EvaluationBudget
from app.services.evaluation_worker import EvaluationWorker
from app.services.gemini_evaluation_client import GeminiErrorKind, GeminiEvaluationError
from tests.test_evaluation_worker import (
    FakeBuffer, FakeClock, FakeTrigger, RecordingGate, _GatedBuffer, _accept, _assessment, _event,
    _join_all, _retail_event, _run_in_thread,
)


class FakeDirectClient:
    """Returns a backend-correlated callback like GeminiEvaluationClient (no HTTP)."""

    name = "fake-gemini"

    def __init__(self, score: int = 90, status: ThreatStatus = ThreatStatus.CRITICAL, *,
                 error: Exception | None = None, gated: bool = False, mismatch: bool = False) -> None:
        self.score, self.status, self.error, self.mismatch = score, status, error, mismatch
        self.requests: list = []
        self.entered, self.release = threading.Event(), threading.Event()
        if not gated:
            self.release.set()

    def dispatch(self, request) -> EvaluationCallback:
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test gate was never released")
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        event_id = "someone-else" if self.mismatch else request.event_id
        return EvaluationCallback(event_id, request.source_session_id, _assessment(self.score, self.status))


def _worker(client, *, gate=None, buffer=None, **kwargs):
    trigger, budget = FakeTrigger(), EvaluationBudget()
    worker = EvaluationWorker(trigger, budget, buffer or FakeBuffer(), client, clock=FakeClock(10),
                              audio_gate=gate, **kwargs)
    if isinstance(gate, RecordingGate):
        gate.worker = worker
    worker.start_session("one")
    return worker, trigger, budget


def test_valid_direct_result_completes_through_the_callback_path() -> None:
    gate = RecordingGate()
    client = FakeDirectClient()
    worker, trigger, budget = _worker(client, gate=gate)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    snapshot = worker.get_snapshot()
    assert snapshot.state is EvaluationWorkerState.COMPLETED and snapshot.active_event_id is None
    assert snapshot.dispatch_attempts == 1 and snapshot.published_count == 1
    latest = worker.get_latest_result()
    assert (latest.event_id, latest.source_session_id, latest.client_name) == ("e1", "one", "fake-gemini")
    assert trigger.completed == ["e1"] and budget.get_snapshot(11).total_completed == 1
    assert gate.calls[-1] == ("offer", 1, "e1", "one", ThreatStatus.CRITICAL)
    # Replaying the same result through the HTTP callback path is a duplicate, never a second publish.
    assert _accept(worker, _assessment(90, ThreatStatus.CRITICAL), now=12) is CallbackDisposition.DUPLICATE
    assert worker.get_snapshot().published_count == 1


class _Device:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def start(self, path: str) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")


@pytest.mark.parametrize("score,status,domain,plays", [
    (90, ThreatStatus.CRITICAL, DomainName.PARKING_MOTORCYCLE, True),
    (50, ThreatStatus.WARNING, DomainName.PARKING_MOTORCYCLE, False),
    (10, ThreatStatus.NORMAL, DomainName.PARKING_MOTORCYCLE, False),
    (90, ThreatStatus.CRITICAL, DomainName.RETAIL_STORE, False),
])
def test_only_parking_critical_reaches_the_audio_device(score, status, domain, plays) -> None:
    device = _Device()
    gate = AudioAlertGate(device, "C:/outside/alert.wav", 0.01, join_timeout_seconds=0.5)
    worker, trigger, _ = _worker(FakeDirectClient(score, status), gate=gate)
    trigger.queue(_event("e1", 10) if domain is DomainName.PARKING_MOTORCYCLE else _retail_event("e1", 10))
    assert worker.process_one(10)
    assert worker.get_snapshot().state is EvaluationWorkerState.COMPLETED
    gate.run_once(0)
    assert device.calls == (["start", "stop"] if plays else [])


_EXPECTED_CATEGORY = {
    GeminiErrorKind.INVALID_CONFIG: EvaluationFailure.CLIENT_ERROR,
    GeminiErrorKind.INVALID_REQUEST: EvaluationFailure.CLIENT_ERROR,
    GeminiErrorKind.HTTP_REJECTED: EvaluationFailure.CLIENT_ERROR,
    GeminiErrorKind.TIMEOUT: EvaluationFailure.DISPATCH_UNCERTAIN,
    GeminiErrorKind.CONNECTION_ERROR: EvaluationFailure.DISPATCH_UNCERTAIN,
    GeminiErrorKind.HTTP_SERVER_ERROR: EvaluationFailure.DISPATCH_UNCERTAIN,
    **{kind: EvaluationFailure.INVALID_RESPONSE for kind in (
        GeminiErrorKind.RESPONSE_TOO_LARGE, GeminiErrorKind.INVALID_RESPONSE, GeminiErrorKind.PROMPT_BLOCKED,
        GeminiErrorKind.SAFETY_STOP, GeminiErrorKind.TRUNCATED, GeminiErrorKind.OTHER_STOP,
        GeminiErrorKind.UNKNOWN_STOP)},
}


def test_every_error_kind_has_one_expected_category() -> None:
    assert set(_EXPECTED_CATEGORY) == set(GeminiErrorKind)


@pytest.mark.parametrize("kind", list(GeminiErrorKind))
def test_provider_failures_fail_immediately_without_waiting_for_a_callback(kind) -> None:
    gate = RecordingGate()
    client = FakeDirectClient(error=GeminiEvaluationError(kind))
    worker, trigger, budget = _worker(client, gate=gate)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    snapshot = worker.get_snapshot()
    assert snapshot.state is EvaluationWorkerState.FAILED and snapshot.active_event_id is None
    assert snapshot.last_failure is _EXPECTED_CATEGORY[kind] and snapshot.dispatch_attempts == 1
    assert trigger.failed == ["e1"] and budget.get_snapshot(10).pending_event_id is None
    assert worker.get_latest_result() is None
    assert [call[0] for call in gate.calls] == ["lifecycle"]


def test_result_for_another_event_is_never_published() -> None:
    worker, trigger, budget = _worker(FakeDirectClient(mismatch=True))
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert worker.get_snapshot().state is EvaluationWorkerState.FAILED
    assert worker.get_latest_result() is None and trigger.failed == ["e1"]


def test_result_after_the_deadline_is_rejected_and_fails_the_event() -> None:
    class SlowClient(FakeDirectClient):
        def dispatch(self, request):
            clock.now = 10 + 46                                 # beyond the 45 s result deadline
            return super().dispatch(request)

    clock = FakeClock(10)
    trigger, budget = FakeTrigger(), EvaluationBudget()
    worker = EvaluationWorker(trigger, budget, FakeBuffer(), SlowClient(), clock=clock)
    worker.start_session("one")
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert worker.get_snapshot().state is EvaluationWorkerState.TIMED_OUT
    assert worker.get_latest_result() is None and trigger.failed == ["e1"]


def test_stop_before_handoff_never_calls_the_provider() -> None:
    buffer, client = _GatedBuffer(), FakeDirectClient()
    worker, trigger, budget = _worker(client, buffer=buffer)
    trigger.queue(_event("e1", 10))
    processing, results = _run_in_thread(lambda: worker.process_one(10))
    try:
        assert buffer.entered.wait(2)
        worker.end_session("one")
        worker.start_session("two")
        buffer.release.set()
        _join_all(processing)
    finally:
        buffer.release.set()
        processing.join(2)
    assert results == [True] and client.requests == [] and not client.entered.is_set()
    assert worker.get_snapshot().dispatch_attempts == 0
    assert worker.get_current_view().process_state is EvaluationWorkerState.IDLE


@pytest.mark.parametrize("late", ["result", "error"])
def test_stop_after_handoff_late_outcome_never_touches_the_new_session(late) -> None:
    gate = RecordingGate()
    error = GeminiEvaluationError(GeminiErrorKind.TIMEOUT) if late == "error" else None
    client = FakeDirectClient(gated=True, error=error)
    worker, trigger, budget = _worker(client, gate=gate, max_dispatches=1)
    trigger.queue(_event("e1", 10))
    processing, results = _run_in_thread(lambda: worker.process_one(10))
    lifecycle = threading.Thread(target=lambda: (worker.end_session("one"), worker.start_session("two")))
    try:
        assert client.entered.wait(2)                       # handed off, provider call in flight
        lifecycle.start()
        _join_all(lifecycle)                                # Stop does not wait for HTTP
        client.release.set()
        _join_all(processing)
    finally:
        client.release.set()
        processing.join(2)
        if lifecycle.ident is not None:
            lifecycle.join(2)
    assert results == [True] and len(client.requests) == 1
    view = worker.get_current_view()
    assert (view.source_session_id, view.process_state, view.active_event_id, view.latest_result) == (
        "two", EvaluationWorkerState.IDLE, None, None)
    assert [call[0] for call in gate.calls] == ["lifecycle", "lifecycle", "lifecycle"]
    # Quota of one stays spent after the cancelled attempt; the new session cannot dispatch.
    trigger.session = "two"
    trigger.queue(_event("e2", 30, session="two"))
    assert worker.process_one(30) is False and worker.get_snapshot().dispatch_attempts == 1


def test_quota_one_is_not_returned_after_a_provider_failure() -> None:
    client = FakeDirectClient(error=GeminiEvaluationError(GeminiErrorKind.HTTP_REJECTED, status_code=429))
    worker, trigger, _ = _worker(client, max_dispatches=1)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    worker.end_session("one")
    worker.start_session("two")
    trigger.session = "two"
    trigger.queue(_event("e2", 30, session="two"))
    assert worker.process_one(30) is False
    assert len(client.requests) == 1 and worker.get_snapshot().dispatch_attempts == 1


def test_worker_logs_durations_without_assessment_content(caplog) -> None:
    worker, trigger, _ = _worker(FakeDirectClient())
    trigger.queue(_event("e1", 10))
    with caplog.at_level(logging.INFO, logger="app.services.evaluation_worker"):
        assert worker.process_one(10)
    text = " ".join(caplog.messages)
    assert "bundle_ms=" in text and "publish_ms=" in text and "disposition=ACCEPTED" in text
    assert "Observasi" not in text and "jaket" not in text



@pytest.mark.parametrize("transition", ["stop", "switch"])
def test_late_generic_transport_exception_after_stop_leaves_the_new_session_alone(transition) -> None:
    gate = RecordingGate()
    client = FakeDirectClient(gated=True, error=RuntimeError("unexpected transport bug"))
    worker, trigger, budget = _worker(client, gate=gate)
    trigger.queue(_event("e1", 10))
    processing, results = _run_in_thread(lambda: worker.process_one(10))

    def change() -> None:
        worker.end_session("one")
        if transition == "switch":
            worker.start_session("two")

    lifecycle = threading.Thread(target=change)
    try:
        assert client.entered.wait(2)                       # handed off; provider call in flight
        lifecycle.start()
        _join_all(lifecycle)
        before = worker.get_current_view()
        client.release.set()                                # the late exception now surfaces
        _join_all(processing)
    finally:
        client.release.set()
        processing.join(2)
        if lifecycle.ident is not None:
            lifecycle.join(2)
    assert results == [True] and len(client.requests) == 1
    after = worker.get_current_view()
    assert after == before                                  # session, state, pending and result unchanged
    assert after.active_event_id is None and after.latest_result is None
    assert after.source_session_id == ("two" if transition == "switch" else None)
    snapshot = worker.get_snapshot()
    assert snapshot.dispatch_attempts == 1 and snapshot.active_event_id is None
    assert snapshot.last_failure is EvaluationFailure.DISPATCH_UNCERTAIN    # diagnostic only
    assert budget.get_snapshot(10).pending_event_id is None and trigger.failed == ["e1"]
    assert "offer" not in [call[0] for call in gate.calls]



# ── Metadata logging (safe fields only) ──────────────────────────────────────

SECRET_TEXT = "sk-live-SECRET-123 https://private.invalid/v1beta/models/x:generateContent"


def _worker_log_records(caplog):
    return [r for r in caplog.records if r.name == "app.services.evaluation_worker"]


def test_success_logs_dispatch_and_accepted_result_once_at_info(caplog) -> None:
    worker, trigger, _ = _worker(FakeDirectClient())
    trigger.queue(_event("e1", 10))
    with caplog.at_level(logging.INFO, logger="app.services.evaluation_worker"):
        assert worker.process_one(10)
    records = _worker_log_records(caplog)
    messages = [r.getMessage() for r in records]
    assert [r.levelno for r in records] == [logging.INFO, logging.INFO]
    assert messages[0].startswith("evaluation dispatch attempt 1/unlimited event=e1 session=one "
                                  "domain=PARKING_MOTORCYCLE bundle_ms=")
    assert messages[1].startswith("evaluation result event=e1 domain=PARKING_MOTORCYCLE "
                                  "disposition=ACCEPTED publish_ms=")
    assert "dispatch failed" not in caplog.text


@pytest.mark.parametrize("error,category,kind,http", [
    (GeminiEvaluationError(GeminiErrorKind.HTTP_REJECTED, status_code=429), "CLIENT_ERROR", "HTTP_REJECTED", "429"),
    (GeminiEvaluationError(GeminiErrorKind.HTTP_SERVER_ERROR, status_code=503), "DISPATCH_UNCERTAIN",
     "HTTP_SERVER_ERROR", "503"),
    (GeminiEvaluationError(GeminiErrorKind.TIMEOUT), "DISPATCH_UNCERTAIN", "TIMEOUT", "-"),
    (GeminiEvaluationError(GeminiErrorKind.TRUNCATED), "INVALID_RESPONSE", "TRUNCATED", "-"),
    (RuntimeError(SECRET_TEXT), "DISPATCH_UNCERTAIN", "UNEXPECTED", "-"),
])
def test_each_failed_dispatch_logs_one_safe_warning(caplog, error, category, kind, http) -> None:
    worker, trigger, _ = _worker(FakeDirectClient(error=error))
    trigger.queue(_event("e1", 10))
    with caplog.at_level(logging.INFO, logger="app.services.evaluation_worker"):
        assert worker.process_one(10)
    failures = [r for r in _worker_log_records(caplog) if "dispatch failed" in r.getMessage()]
    assert len(failures) == 1 and failures[0].levelno == logging.WARNING
    assert failures[0].getMessage() == (
        f"evaluation dispatch failed event=e1 domain=PARKING_MOTORCYCLE category={category} "
        f"kind={kind} http={http}")
    assert failures[0].exc_info is None                     # no raw exception or traceback
    for leaked in ("sk-live-SECRET", "private.invalid", "RuntimeError", "generateContent"):
        assert leaked not in caplog.text


def test_unexpected_processing_error_logs_a_generic_category(caplog) -> None:
    class ExplodingBuffer(FakeBuffer):
        def wait_for_bundle(self, plan, timeout_seconds):
            raise RuntimeError(SECRET_TEXT)

    worker, trigger, _ = _worker(FakeDirectClient(), buffer=ExplodingBuffer())
    trigger.queue(_event("e1", 10))
    with caplog.at_level(logging.INFO, logger="app.services.evaluation_worker"):
        assert worker.process_one(10)
    (record,) = _worker_log_records(caplog)
    assert record.levelno == logging.WARNING and record.exc_info is None
    assert record.getMessage() == ("evaluation dispatch failed event=e1 domain=PARKING_MOTORCYCLE "
                                   "category=CLIENT_ERROR kind=UNEXPECTED http=-")
    assert "sk-live-SECRET" not in caplog.text and "private.invalid" not in caplog.text


def test_rejected_direct_result_is_logged_at_warning_without_content(caplog) -> None:
    worker, trigger, _ = _worker(FakeDirectClient(mismatch=True))
    trigger.queue(_event("e1", 10))
    with caplog.at_level(logging.INFO, logger="app.services.evaluation_worker"):
        assert worker.process_one(10)
    (result,) = [r for r in _worker_log_records(caplog) if r.getMessage().startswith("evaluation result")]
    assert result.levelno == logging.WARNING and "disposition=REJECTED" in result.getMessage()
    assert "Observasi" not in caplog.text and "jaket" not in caplog.text and "someone-else" not in caplog.text



class _LeakyKind:
    """Look-alike of an error kind whose value carries sensitive text."""

    value = SECRET_TEXT


@pytest.mark.parametrize("kind,status", [
    (_LeakyKind(), SECRET_TEXT),                         # not an allowlisted enum member
    (SECRET_TEXT, 99999),                                # raw string kind, out-of-range status
    (enum.Enum("FakeKind", {"LEAK": SECRET_TEXT}).LEAK, True),   # enum, but not a transport enum
])
def test_unknown_kind_or_status_is_logged_as_generic(caplog, kind, status) -> None:
    error = GeminiEvaluationError(GeminiErrorKind.TIMEOUT)
    error.kind = kind                                     # tampered after construction
    error.status_code = status
    worker, trigger, _ = _worker(FakeDirectClient(error=error))
    trigger.queue(_event("e1", 10))
    with caplog.at_level(logging.INFO, logger="app.services.evaluation_worker"):
        assert worker.process_one(10)
    (failure,) = [r for r in _worker_log_records(caplog) if "dispatch failed" in r.getMessage()]
    assert failure.getMessage() == ("evaluation dispatch failed event=e1 domain=PARKING_MOTORCYCLE "
                                    "category=DISPATCH_UNCERTAIN kind=UNEXPECTED http=-")
    for leaked in ("sk-live-SECRET", "private.invalid", "_LeakyKind", "FakeKind", "99999"):
        assert leaked not in caplog.text


def test_known_langflow_kind_is_allowlisted(caplog) -> None:
    from app.services.langflow_evaluation_client import LangflowDispatchRejected, LangflowErrorKind

    error = LangflowDispatchRejected(LangflowErrorKind.HTTP_STATUS, status_code=403)
    worker, trigger, _ = _worker(FakeDirectClient(error=error))
    trigger.queue(_event("e1", 10))
    with caplog.at_level(logging.INFO, logger="app.services.evaluation_worker"):
        assert worker.process_one(10)
    (failure,) = [r for r in _worker_log_records(caplog) if "dispatch failed" in r.getMessage()]
    assert failure.getMessage().endswith("category=CLIENT_ERROR kind=HTTP_STATUS http=403")
