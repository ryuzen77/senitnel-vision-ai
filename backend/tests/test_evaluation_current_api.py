"""Read-only current evaluation view; no callback token or external transport."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.evaluation import get_current_evaluation_reader
from app.main import app
from app.models.evaluation import (
    CallbackDisposition, EvaluationCurrentView, EvaluationLatestView, EvaluationWorkerState,
    SubjectProfile, ThreatAssessment, ThreatStatus,
)
from tests.test_evaluation_worker import _accept, _event, _worker


class FakeReader:
    def __init__(self, view: EvaluationCurrentView) -> None:
        self.view = view
        self.calls = 0

    def get_current_view(self) -> EvaluationCurrentView:
        self.calls += 1
        return self.view


@pytest.fixture()
def current_client():
    initial = EvaluationCurrentView(True, None, EvaluationWorkerState.IDLE, None, None)
    reader = FakeReader(initial)
    app.dependency_overrides[get_current_evaluation_reader] = lambda: reader
    try:
        yield TestClient(app), reader
    finally:
        app.dependency_overrides.pop(get_current_evaluation_reader, None)


def _latest(score: int, status: ThreatStatus, event_id: str = "event-old") -> EvaluationLatestView:
    assessment = ThreatAssessment(
        score, status, "Observasi memerlukan tinjauan manusia.",
        SubjectProfile(True, "jaket", "berjalan", ("motor",), ("helm",)),
    )
    assessment.validate()
    return EvaluationLatestView(event_id, "session-1", assessment)


def test_idle_disabled_and_active_without_result(current_client) -> None:
    client, reader = current_client
    idle = client.get("/api/evaluation/current")
    assert idle.status_code == 200
    assert idle.json() == {
        "enabled": True, "source_session_id": None, "process_state": "IDLE",
        "active_event_id": None, "latest_result": None,
    }
    reader.view = EvaluationCurrentView(False, "session-1", EvaluationWorkerState.IDLE, None, None)
    assert client.get("/api/evaluation/current").json() == {
        "enabled": False, "source_session_id": "session-1", "process_state": "IDLE",
        "active_event_id": None, "latest_result": None,
    }
    reader.view = EvaluationCurrentView(True, "session-1", EvaluationWorkerState.IDLE, None, None)
    assert client.get("/api/evaluation/current").json()["latest_result"] is None


def test_waiting_and_previous_result_have_distinct_ids(current_client) -> None:
    client, reader = current_client
    reader.view = EvaluationCurrentView(
        True, "session-1", EvaluationWorkerState.WAITING_CALLBACK, "event-new",
        _latest(12, ThreatStatus.NORMAL),
    )
    response = client.get("/api/evaluation/current")
    assert response.status_code == 200
    data = response.json()
    assert data["process_state"] == "WAITING_CALLBACK"
    assert data["active_event_id"] == "event-new"
    assert data["latest_result"]["event_id"] == "event-old"
    assert data["latest_result"]["source_session_id"] == "session-1"
    assert set(data) == {"enabled", "source_session_id", "process_state", "active_event_id", "latest_result"}
    assert set(data["latest_result"]) == {"event_id", "source_session_id", "assessment"}
    assert set(data["latest_result"]["assessment"]) == {
        "threatScore", "status", "reasoning", "suspectProfile",
    }
    assert "callback_deadline" not in response.text
    assert "frame_source_sequences" not in response.text
    assert "client_name" not in response.text


@pytest.mark.parametrize("score,status", [
    (12, ThreatStatus.NORMAL), (45, ThreatStatus.WARNING), (78, ThreatStatus.CRITICAL),
])
def test_completed_keeps_process_state_separate_from_assessment(current_client, score, status) -> None:
    client, reader = current_client
    reader.view = EvaluationCurrentView(
        True, "session-1", EvaluationWorkerState.COMPLETED, None, _latest(score, status),
    )
    data = client.get("/api/evaluation/current").json()
    assert data["process_state"] == "COMPLETED"
    assert data["latest_result"]["assessment"]["threatScore"] == score
    assert data["latest_result"]["assessment"]["status"] == status.value
    assert data["latest_result"]["assessment"]["suspectProfile"]["personDetected"] is True


@pytest.mark.parametrize("state", [EvaluationWorkerState.TIMED_OUT, EvaluationWorkerState.FAILED])
def test_failure_states_are_normal_read_responses(current_client, state) -> None:
    client, reader = current_client
    reader.view = EvaluationCurrentView(True, "session-1", state, None, None)
    first = client.get("/api/evaluation/current")
    second = client.get("/api/evaluation/current")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["process_state"] == state.value
    assert "exception" not in first.text and "deadline" not in first.text
    assert reader.calls == 2


def test_unavailable_reader_returns_sanitized_503(current_client) -> None:
    client, reader = current_client
    app.dependency_overrides[get_current_evaluation_reader] = lambda: None
    response = client.get("/api/evaluation/current")
    assert response.status_code == 503
    assert response.json() == {"detail": "Evaluation status unavailable"}

    class BrokenReader:
        def get_current_view(self):
            raise RuntimeError("secret internal diagnostic")

    app.dependency_overrides[get_current_evaluation_reader] = BrokenReader
    response = client.get("/api/evaluation/current")
    assert response.status_code == 503
    assert "secret internal diagnostic" not in response.text


def test_production_dependency_uses_callback_worker_singleton() -> None:
    from app.services.runtime import evaluation_worker

    assert get_current_evaluation_reader() is evaluation_worker


def test_route_reads_real_worker_without_changing_pending_budget_or_result() -> None:
    worker, trigger, budget, buffer, client, clock = _worker()
    worker.start_session("one")
    trigger.queue(_event())
    assert worker.process_one(10)
    pending = worker._pending
    assert pending is not None
    deadline = pending.deadline
    budget_before = budget.get_snapshot(10)
    app.dependency_overrides[get_current_evaluation_reader] = lambda: worker
    try:
        http = TestClient(app)
        waiting = http.get("/api/evaluation/current")
        assert waiting.status_code == 200
        for _ in range(3):
            assert http.get("/api/evaluation/current").json() == waiting.json()
        assert waiting.json()["active_event_id"] == "e1"
        assert worker._pending is pending and worker._pending.deadline == deadline
        assert trigger.pending == "e1" and budget.get_snapshot(10) == budget_before
        assert client.requests and len(client.requests) == 1

        assert _accept(worker) is CallbackDisposition.ACCEPTED
        record = worker.get_latest_result()
        assert record is not None
        completed = http.get("/api/evaluation/current")
        assert completed.status_code == 200
        assert completed.json()["latest_result"]["event_id"] == "e1"
        assert completed.json()["process_state"] == "COMPLETED"
        for _ in range(3):
            assert http.get("/api/evaluation/current").json() == completed.json()
        assert worker.get_latest_result() is record
        assert trigger.completed == ["e1"] and budget.get_snapshot(11).total_completed == 1

        worker.end_session("one")
        assert http.get("/api/evaluation/current").json() == {
            "enabled": True, "source_session_id": None, "process_state": "IDLE",
            "active_event_id": None, "latest_result": None,
        }
        assert worker.get_snapshot().state is EvaluationWorkerState.CANCELLED
        assert _accept(worker) is CallbackDisposition.REJECTED
        worker.start_session("two")
        assert http.get("/api/evaluation/current").json()["latest_result"] is None
    finally:
        app.dependency_overrides.pop(get_current_evaluation_reader, None)
