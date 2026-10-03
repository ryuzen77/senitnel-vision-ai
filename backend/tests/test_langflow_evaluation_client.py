"""Mock-only tests for the Langflow Webhook ACK transport; no live network."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.models.domain import DomainName
from app.models.evaluation import (
    CallbackDisposition, EvaluationFailure, EvaluationRequest, EvaluationWorkerState,
)
from app.models.event_frame import EventFrameBundle, SelectedFrame
from app.models.parking_candidate import ParkingCandidateEvidence
from app.services.evaluation_budget import EvaluationBudget
from app.services.langflow_evaluation_client import (
    LangflowDispatchRejected, LangflowDispatchUncertain, LangflowErrorKind,
    LangflowEvaluationClient,
)
from tests.test_evaluation_worker import FakeBuffer, FakeClock, FakeTrigger, _accept, _event, _worker


URL = "https://langflow.example/api/v1/webhook/flow-id"
JPEG = b"\xff\xd8synthetic\xff\xd9"


def _request() -> EvaluationRequest:
    offsets = (-2.0, -1.0, -.4, 0.0, .4)
    frames = tuple(
        SelectedFrame(index + 1, 10 + offset, offset, offset, 0.0,
                      640, 480, "image/jpeg", JPEG)
        for index, offset in enumerate(offsets)
    )
    bundle = EventFrameBundle("event-1", "session-1", DomainName.PARKING_MOTORCYCLE,
                              10.0, frames, len(JPEG) * 5)
    evidence = ParkingCandidateEvidence(4, 2.1, .01, .02, .04, .0, .03)
    return EvaluationRequest("event-1", "session-1", DomainName.PARKING_MOTORCYCLE,
                             8.0, 10.0, 7, 5, evidence, bundle)


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_webhook_payload_has_exact_top_level_correlation_and_five_images() -> None:
    captured = []
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202, content=b"")
    client = LangflowEvaluationClient(
        url=URL, api_key="synthetic-key", timeout_seconds=4,
        model="gemini-3-flash-preview", http_client=_mock_client(handler),
    )
    assert client.dispatch(_request()) is None
    assert len(captured) == 1
    sent = captured[0]
    assert sent.method == "POST" and str(sent.url) == URL
    assert sent.headers["x-api-key"] == "synthetic-key"
    assert sent.headers["content-type"] == "application/json"
    body = json.loads(sent.content)
    assert set(body) == {"event_id", "source_session_id", "model", "input"}
    assert (body["event_id"], body["source_session_id"], body["model"]) == (
        "event-1", "session-1", "gemini-3-flash-preview",
    )
    assert [item["type"] for item in body["input"]] == ["text", "image", "image", "image", "image", "image"]
    prompt = body["input"][0]["text"]
    assert "\n" not in prompt and "\r" not in prompt
    metadata = json.loads(prompt.split("Event metadata: ", 1)[1])
    assert metadata["domain"] == "PARKING_MOTORCYCLE"
    assert metadata["trigger"]["person_track_id"] == 7
    assert [item["source_frame_seq"] for item in metadata["frames"]] == [1, 2, 3, 4, 5]
    assert "event_id" not in metadata and "source_session_id" not in metadata
    for item in body["input"][1:]:
        assert set(item) == {"type", "data", "mime_type"}
        assert item["type"] == "image" and item["mime_type"] == "image/jpeg"
        assert not item["data"].startswith("data:")
        assert base64.b64decode(item["data"], validate=True) == JPEG
    assert sent.content == json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert "synthetic-key" not in repr(client) and "synthetic" not in repr(_request())


def _langflow_1_12_2_webhook_output(raw_body: bytes) -> dict:
    """Offline copy of Langflow Desktop 1.12.2's Webhook path; no Langflow import.

    langflow/api/v1/endpoints.py webhook_run_flow passes the whole raw body as
    tweaks[<Webhook id>] = {"data": data.decode()}; lfx WebhookComponent.build_data
    then applies exactly one json.loads and returns Data(data=body).
    """
    webhook_data = raw_body.decode() if isinstance(raw_body, bytes) else raw_body
    parsed = json.loads(webhook_data.replace('"\n"', '"\\n"') or "{}")
    assert isinstance(parsed, dict)
    return parsed


def test_raw_body_survives_langflow_desktop_webhook_parsing_with_flat_keys() -> None:
    sent = []
    def handler(request):
        sent.append(request)
        return httpx.Response(202, json={"message": "Task started in the background", "status": "in progress"})
    LangflowEvaluationClient(url=URL, http_client=_mock_client(handler)).dispatch(_request())
    output = _langflow_1_12_2_webhook_output(sent[0].content)
    assert set(output) == {"event_id", "source_session_id", "model", "input"}
    assert (output["event_id"], output["source_session_id"]) == ("event-1", "session-1")
    assert isinstance(output["input"], list) and len(output["input"]) == 6
    for item in output["input"][1:]:
        assert set(item) == {"type", "data", "mime_type"}
        assert item["mime_type"] == "image/jpeg"
        assert base64.b64decode(item["data"], validate=True) == JPEG


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b""),
    httpx.Response(202, content=b"not-json"),
    httpx.Response(204),
    httpx.Response(200, json={"threatScore": 100, "status": "CRITICAL"}),
])
def test_any_2xx_is_ack_without_response_parsing(response) -> None:
    calls = []
    def handler(request):
        calls.append(request)
        return response
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(handler))
    assert client.dispatch(_request()) is None
    assert len(calls) == 1


def test_environment_model_and_optional_api_key(monkeypatch) -> None:
    monkeypatch.setenv("SENTINEL_LANGFLOW_URL", URL)
    monkeypatch.setenv("SENTINEL_LANGFLOW_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("SENTINEL_LANGFLOW_MODEL", "model-other")
    monkeypatch.delenv("SENTINEL_LANGFLOW_API_KEY", raising=False)
    monkeypatch.setenv("SENTINEL_LANGFLOW_COMPONENT_ID", "ignored-component")
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200)
    client = LangflowEvaluationClient(http_client=_mock_client(handler))
    client.dispatch(_request())
    assert client._timeout == 7.5
    assert "x-api-key" not in seen[0].headers
    body = json.loads(seen[0].content)
    assert body["model"] == "model-other"
    assert "tweaks" not in body and "ignored-component" not in seen[0].content.decode("utf-8")


def test_unicode_metadata_is_utf8_and_prompt_has_no_newline() -> None:
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200)
    request = _request()
    request = EvaluationRequest(
        request.event_id, request.source_session_id, request.domain, request.started_at,
        request.triggered_at, request.person_track_id, request.object_track_id,
        {"lokasi": "parkiran m\u00e9sjid\nsekitar"}, request.frame_bundle,
    )
    LangflowEvaluationClient(url=URL, http_client=_mock_client(handler)).dispatch(request)
    assert "m\u00e9sjid".encode("utf-8") in seen[0].content
    prompt = json.loads(seen[0].content)["input"][0]["text"]
    assert "\n" not in prompt and "\r" not in prompt


@pytest.mark.parametrize("url,timeout", [
    ("", 2), ("not-a-url", 2), ("ftp://example.com/webhook", 2),
    ("https://user:pass@example.com/api/v1/webhook/flow-id", 2),
    ("https://langflow.example/api/v1/run/flow-id", 2),
    ("https://langflow.example/api/v1/webhook", 2),
    ("https://langflow.example/api/v1/webhook/flow-id?x=1", 2),
    (URL, 0), (URL, float("nan")),
])
def test_invalid_config_is_definitive_before_network(url, timeout) -> None:
    with pytest.raises(LangflowDispatchRejected) as error:
        LangflowEvaluationClient(url=url, timeout_seconds=timeout, http_client=_mock_client(lambda _: None))
    assert error.value.kind is LangflowErrorKind.INVALID_CONFIG


def test_invalid_model_is_rejected() -> None:
    with pytest.raises(LangflowDispatchRejected) as error:
        LangflowEvaluationClient(url=URL, model="", http_client=_mock_client(lambda _: None))
    assert error.value.kind is LangflowErrorKind.INVALID_CONFIG


@pytest.mark.parametrize("configured_timeout", ["0", "-1", "10.01"])
def test_environment_ack_timeout_must_be_positive_and_at_most_ten(
    monkeypatch, configured_timeout,
) -> None:
    monkeypatch.setenv("SENTINEL_LANGFLOW_TIMEOUT_SECONDS", configured_timeout)
    with pytest.raises(LangflowDispatchRejected) as error:
        LangflowEvaluationClient(url=URL, http_client=_mock_client(lambda _: None))
    assert error.value.kind is LangflowErrorKind.INVALID_CONFIG


def test_ten_second_ack_timeout_is_accepted() -> None:
    client = LangflowEvaluationClient(
        url=URL, timeout_seconds=10, http_client=_mock_client(lambda _: httpx.Response(204)),
    )
    assert client._timeout == 10
    assert client.dispatch(_request()) is None


@pytest.mark.parametrize("status", [301, 400, 401, 408, 429, 499])
def test_redirect_and_4xx_are_definitive_without_retry(status) -> None:
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private response body", headers={"location": "https://elsewhere.example"})
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(handler))
    with pytest.raises(LangflowDispatchRejected) as error:
        client.dispatch(_request())
    assert error.value.kind is LangflowErrorKind.HTTP_STATUS
    assert error.value.status_code == status and len(calls) == 1
    assert "private" not in str(error.value) and "elsewhere" not in str(error.value)


@pytest.mark.parametrize("status", [500, 502, 503, 599])
def test_5xx_is_uncertain_without_retry(status) -> None:
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private response body")
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(handler))
    with pytest.raises(LangflowDispatchUncertain) as error:
        client.dispatch(_request())
    assert error.value.kind is LangflowErrorKind.HTTP_STATUS
    assert error.value.status_code == status and len(calls) == 1
    assert "private" not in str(error.value)


@pytest.mark.parametrize("exception,kind", [
    (httpx.ConnectError("private host detail"), LangflowErrorKind.CONNECTION_ERROR),
    (httpx.ReadTimeout("private timeout detail"), LangflowErrorKind.TIMEOUT),
])
def test_transport_errors_are_uncertain_and_sanitized(exception, kind) -> None:
    calls = []
    def handler(request):
        calls.append(request)
        raise exception
    client = LangflowEvaluationClient(url=URL, api_key="private-key", http_client=_mock_client(handler))
    with pytest.raises(LangflowDispatchUncertain) as error:
        client.dispatch(_request())
    assert error.value.kind is kind and len(calls) == 1
    message = str(error.value)
    assert all(secret not in message for secret in (
        "private", URL, "data:image", base64.b64encode(JPEG).decode("ascii"), "synthetic",
    ))


def test_default_five_second_ack_timeout_keeps_forty_five_second_callback_window(monkeypatch) -> None:
    monkeypatch.delenv("SENTINEL_LANGFLOW_TIMEOUT_SECONDS", raising=False)
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private timeout detail")
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(handler))
    assert client._timeout == 5.0
    trigger = FakeTrigger("one")
    budget = EvaluationBudget()
    worker, *_ = _worker(
        client=client, trigger=trigger, buffer=FakeBuffer("one"),
        budget=budget, clock=FakeClock(10),
    )
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10) and len(calls) == 1
    assert worker.get_snapshot().state is EvaluationWorkerState.WAITING_CALLBACK
    assert worker.get_snapshot().callback_deadline == 55.0
    assert trigger.pending == "e1" and budget.get_snapshot(10).pending_event_id == "e1"
    assert _accept(worker, now=11) is CallbackDisposition.ACCEPTED
    assert budget.get_snapshot(11).total_completed == 1


def test_invalid_bundle_never_sends_request_or_leaks_jpeg() -> None:
    calls = []
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(lambda request: calls.append(request)))
    request = _request()
    invalid = EvaluationRequest(
        request.event_id, request.source_session_id, request.domain, request.started_at,
        request.triggered_at, request.person_track_id, request.object_track_id,
        request.evidence,
        EventFrameBundle(request.event_id, request.source_session_id, request.domain,
                         request.triggered_at, request.frame_bundle.frames[:4], len(JPEG) * 4),
    )
    with pytest.raises(LangflowDispatchRejected) as error:
        client.dispatch(invalid)
    assert error.value.kind is LangflowErrorKind.INVALID_REQUEST and not calls
    assert "synthetic" not in str(error.value)


@pytest.mark.parametrize("status,exception,waiting", [
    (400, None, False), (429, None, False),
    (500, None, True),
    (None, httpx.ReadTimeout("private"), True),
    (None, httpx.ConnectError("private"), True),
])
def test_worker_classifies_mock_transport_result(status, exception, waiting) -> None:
    calls = []
    def handler(request):
        calls.append(request)
        if exception is not None:
            raise exception
        return httpx.Response(status, text="private body")
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(handler))
    trigger = FakeTrigger("one")
    buffer = FakeBuffer("one")
    budget = EvaluationBudget()
    clock = FakeClock(10)
    worker, *_ = _worker(client=client, trigger=trigger, buffer=buffer, budget=budget, clock=clock)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10) and len(calls) == 1
    snapshot = budget.get_snapshot(10)
    if waiting:
        assert worker.get_snapshot().state is EvaluationWorkerState.WAITING_CALLBACK
        assert worker.get_snapshot().last_failure is EvaluationFailure.DISPATCH_UNCERTAIN
        assert trigger.pending == "e1" and snapshot.pending_event_id == "e1"
        assert snapshot.total_failed == 0
        assert worker.sweep(55)
        assert budget.get_snapshot(55).total_failed == 1 and trigger.failed == ["e1"]
    else:
        assert worker.get_snapshot().state is EvaluationWorkerState.FAILED
        assert worker.get_snapshot().last_failure is EvaluationFailure.CLIENT_ERROR
        assert trigger.pending is None and trigger.failed == ["e1"]
        assert snapshot.pending_event_id is None and snapshot.total_failed == 1
    assert worker.get_latest_result() is None


def test_retail_request_without_object_track_keeps_the_webhook_schema() -> None:
    from dataclasses import replace

    captured = []
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202)
    parking = _request()
    bundle = replace(parking.frame_bundle, domain=DomainName.RETAIL_STORE)
    retail = replace(parking, domain=DomainName.RETAIL_STORE, object_track_id=None,
                     evidence={"zone_name": "rak_kiri"}, frame_bundle=bundle)
    LangflowEvaluationClient(url=URL, http_client=_mock_client(handler)).dispatch(retail)
    body = json.loads(captured[0].content)
    assert set(body) == {"event_id", "source_session_id", "model", "input"}
    metadata = json.loads(body["input"][0]["text"].split("Event metadata: ", 1)[1])
    assert metadata["domain"] == "RETAIL_STORE"
    assert metadata["trigger"]["object_track_id"] is None
    assert metadata["trigger"]["person_track_id"] == 7
