"""Fake-only production wiring and session lifecycle tests; no HTTP dispatch."""

from __future__ import annotations

import logging
import os
import time

from fastapi.testclient import TestClient

from app.api.evaluation import get_callback_consumer
from app.main import app
from app.models.evaluation import CallbackDisposition, EvaluationCallback
from app.models.video import VideoState
from app.services import runtime
from app.services.evaluation_budget import EvaluationBudget
from app.services.evaluation_worker import EvaluationWorker
from app.services.video_service import VideoService
from tests.conftest import FakeCapture
from tests.test_evaluation_worker import (
    FakeBuffer, FakeClient, FakeClock, FakeTrigger, _assessment, _event,
)


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.005)
    return predicate()


class RecordingWorker:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.ended: list[str] = []

    def start_session(self, session_id: str) -> None:
        self.started.append(session_id)

    def end_session(self, session_id: str) -> None:
        self.ended.append(session_id)


def test_runtime_owns_one_budget_worker_and_optional_client(monkeypatch) -> None:
    assert not os.getenv("SENTINEL_LANGFLOW_URL")
    assert not os.getenv("SENTINEL_CALLBACK_TOKEN")
    assert runtime.langflow_client is None
    assert not runtime.evaluation_enabled
    assert isinstance(runtime.evaluation_budget, EvaluationBudget)
    assert isinstance(runtime.evaluation_worker, EvaluationWorker)
    assert runtime.video_service._evaluation_worker is runtime.evaluation_worker
    assert runtime.evaluation_worker._budget is runtime.evaluation_budget
    assert runtime.evaluation_worker._trigger is runtime.parking_candidate_trigger
    assert runtime.evaluation_worker._buffer is runtime.event_frame_buffer
    if runtime.langflow_client is not None:
        assert runtime.evaluation_worker._client is runtime.langflow_client
    monkeypatch.setenv("SENTINEL_LANGFLOW_URL", "https://example.invalid/api/v1/webhook/flow")
    constructed = []
    monkeypatch.setattr(runtime, "LangflowEvaluationClient", lambda: constructed.append(object()) or constructed[-1])
    assert runtime._create_langflow_client() is constructed[0]
    assert len(constructed) == 1


def test_partial_external_configuration_warns_without_values(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=runtime.__name__):
        monkeypatch.setenv("SENTINEL_LANGFLOW_URL", "https://secret.invalid/api/v1/webhook/flow")
        monkeypatch.delenv("SENTINEL_CALLBACK_TOKEN", raising=False)
        assert not runtime._external_evaluation_enabled(object())
        monkeypatch.delenv("SENTINEL_LANGFLOW_URL")
        monkeypatch.setenv("SENTINEL_CALLBACK_TOKEN", "private-token")
        assert not runtime._external_evaluation_enabled(None)
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "External evaluation disabled: callback token is missing",
        "External evaluation disabled: Langflow URL is missing",
    ]
    assert "secret.invalid" not in str(messages)
    assert "private-token" not in str(messages)


def test_callback_dependency_uses_production_worker_identity(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "evaluation_enabled", True)
    assert get_callback_consumer() is runtime.evaluation_worker
    monkeypatch.setattr(runtime, "evaluation_enabled", False)
    assert get_callback_consumer() is None


def test_callback_route_reaches_same_injected_runtime_worker(monkeypatch) -> None:
    class FakeCallbackWorker:
        def __init__(self) -> None:
            self.calls = []
        def accept_callback(self, callback, now):
            self.calls.append((callback, now))
            return CallbackDisposition.ACCEPTED
    fake = FakeCallbackWorker()
    monkeypatch.setattr(runtime, "evaluation_worker", fake)
    monkeypatch.setattr(runtime, "evaluation_enabled", True)
    monkeypatch.setenv("SENTINEL_CALLBACK_TOKEN", "synthetic-token")
    payload = {
        "event_id": "e1", "source_session_id": "s1", "threatScore": 20,
        "status": "NORMAL", "reasoning": "Observasi terbatas.",
        "suspectProfile": {
            "personDetected": True, "clothing": "jaket", "observedAction": "berjalan",
            "visibleObjects": ["motor"], "distinguishingFeatures": ["helm"],
        },
    }
    response = TestClient(app).post(
        "/api/evaluation/callback", json=payload,
        headers={"x-sentinel-callback-token": "synthetic-token"},
    )
    assert response.status_code == 200 and response.json() == {"status": "accepted"}
    assert len(fake.calls) == 1 and fake.calls[0][0].event_id == "e1"


def test_two_video_sessions_both_dispatch_and_accept_callback() -> None:
    trigger = FakeTrigger()
    buffer = FakeBuffer()
    budget = EvaluationBudget()
    clock = FakeClock(10)
    dispatcher = FakeClient()
    worker = EvaluationWorker(trigger, budget, buffer, dispatcher, clock=clock)
    video = VideoService(evaluation_worker=worker)
    try:
        for index, at in enumerate((10.0, 22.0), start=1):
            clock.now = at
            assert video.claim_start() is not None
            session = video._session_id
            assert worker._session_id == session
            trigger.session = buffer.session = session
            event_id = f"event-{index}"
            trigger.queue(_event(event_id, at, session=session))
            assert worker.process_one(at)
            assert worker.get_snapshot().active_event_id == event_id
            assert worker.accept_callback(
                EvaluationCallback(event_id, session, _assessment()), at + 1,
            ) is CallbackDisposition.ACCEPTED
            assert worker.get_latest_result().event_id == event_id
            video.stop()
            assert worker._session_id is None and worker.get_latest_result() is None
            trigger.end_session()
        assert len(dispatcher.requests) == 2
        assert budget.get_snapshot(23).total_completed == 2
        assert worker.get_snapshot().discarded_stale_count == 0
    finally:
        video.stop(); worker.close()


def test_video_stop_before_callback_releases_budget_and_rejects_late_result() -> None:
    trigger = FakeTrigger()
    buffer = FakeBuffer()
    budget = EvaluationBudget()
    worker = EvaluationWorker(trigger, budget, buffer, FakeClient(), clock=FakeClock(10))
    video = VideoService(evaluation_worker=worker)
    try:
        assert video.claim_start() is not None
        session = video._session_id
        trigger.session = buffer.session = session
        trigger.queue(_event("e1", 10, session=session))
        assert worker.process_one(10)
        assert trigger.pending == "e1" and budget.get_snapshot(10).pending_event_id == "e1"
        video.stop()
        trigger.end_session()
        assert trigger.failed == ["e1"]
        assert budget.get_snapshot(10).pending_event_id is None
        assert budget.get_snapshot(10).total_failed == 1
        assert worker.accept_callback(
            EvaluationCallback("e1", session, _assessment()), 11,
        ) is CallbackDisposition.REJECTED
        assert worker.get_latest_result() is None
    finally:
        video.stop(); worker.close()


def test_video_terminal_paths_end_worker_session(monkeypatch, tmp_path) -> None:
    worker = RecordingWorker()
    video = VideoService(evaluation_worker=worker)
    assert video.claim_start() is not None
    video.stop()
    assert worker.started == worker.ended

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_args: FakeCapture(opened=False))
    assert video.start_webcam()
    assert video.get_status().state is VideoState.ERROR
    assert worker.started == worker.ended
    video.stop()

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_args: FakeCapture(opened=False))
    token = video.claim_start()
    assert token is not None
    video.start_file(tmp_path / "unopenable.mp4", token)
    assert video.get_status().state is VideoState.ERROR
    assert worker.started == worker.ended
    video.stop()

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_args: FakeCapture(opened=True, reads=[(False, None)]))
    token = video.claim_start()
    assert token is not None
    video.start_file(tmp_path / "fake.mp4", token)
    assert _wait_for(lambda: video.get_status().state is VideoState.IDLE)
    assert worker.started == worker.ended

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_args: FakeCapture(opened=True, reads=[(False, None)] * 5))
    assert video.start_webcam()
    assert _wait_for(lambda: video.get_status().state is VideoState.ERROR)
    assert worker.started == worker.ended
    video.stop()

    class ExplodingCapture(FakeCapture):
        def read(self):
            raise RuntimeError("fake capture failure")
    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_args: ExplodingCapture(opened=True))
    assert video.start_webcam()
    assert _wait_for(lambda: video.get_status().state is VideoState.ERROR)
    assert worker.started == worker.ended
    video.stop()
    assert len(worker.started) == 6 and len(worker.ended) == 6


def test_missing_langflow_config_disables_external_evaluation(monkeypatch) -> None:
    monkeypatch.delenv("SENTINEL_LANGFLOW_URL", raising=False)
    assert runtime._create_langflow_client() is None
    monkeypatch.setenv("SENTINEL_LANGFLOW_URL", "not-a-webhook-url")
    assert runtime._create_langflow_client() is None
    trigger = FakeTrigger()
    budget = EvaluationBudget()
    dispatcher = FakeClient()
    worker = EvaluationWorker(trigger, budget, FakeBuffer(), dispatcher,
                              clock=FakeClock(10), enabled=False)
    worker.start(); worker.start()
    try:
        trigger.queue(_event())
        assert not worker.process_one(10)
        assert dispatcher.requests == [] and trigger.pending is None
        assert budget.get_snapshot(10).total_granted == 0
    finally:
        worker.close(); worker.close()
    assert not worker._thread.is_alive()


def test_lifespan_starts_worker_before_serving_and_shutdown_is_ordered(monkeypatch, tmp_path) -> None:
    from app import main
    starts = []
    stops = []
    class StartupWorker:
        def start(self): starts.append("worker.start")
    monkeypatch.setattr(main, "evaluation_worker", StartupWorker())
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(main, "shutdown_services", lambda *_a, **_kw: stops.append("shutdown"))
    with TestClient(main.app) as client:
        assert starts == ["worker.start"]
        assert client.get("/api/health").status_code == 200
    assert stops == ["shutdown"]

    events = []
    class Service:
        def __init__(self, label): self.label = label; self.closed = False
        def stop(self):
            if not self.closed: events.append("video.stop"); self.closed = True
        def close(self):
            if not self.closed: events.append(self.label + ".close"); self.closed = True
    services = [Service(label) for label in (
        "video", "scene", "detector", "trigger", "tracker", "buffer",
        "worker", "client", "budget",
    )]
    args = (*services[:6],)
    kwargs = {
        "evaluation_worker": services[6], "langflow_client": services[7],
        "evaluation_budget": services[8],
    }
    runtime.shutdown_services(*args, **kwargs)
    expected = [
        "video.stop", "worker.close", "client.close", "scene.close", "detector.close",
        "trigger.close", "tracker.close", "buffer.close", "budget.close",
    ]
    assert events == expected
    runtime.shutdown_services(*args, **kwargs)
    assert events == expected
