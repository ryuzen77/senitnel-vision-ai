"""Mock-transport tests for isolated Langflow v1 evaluation; no live network."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.models.domain import DomainName
from app.models.evaluation import EvaluationFailure, EvaluationRequest, ThreatStatus
from app.models.event_frame import EventFrameBundle, SelectedFrame
from app.models.parking_candidate import ParkingCandidateEvidence
from app.services.evaluation_budget import EvaluationBudget
from app.services.langflow_evaluation_client import (
    LangflowErrorKind, LangflowEvaluationClient, LangflowEvaluationError,
)
from tests.test_evaluation_worker import FakeBuffer, FakeClock, FakeTrigger, _event, _worker


URL = "https://langflow.example/api/v1/run/flow-id"


def _request() -> EvaluationRequest:
    jpeg = b"\xff\xd8synthetic\xff\xd9"
    offsets = (-2.0, -1.0, -.4, 0.0, .4)
    frames = tuple(
        SelectedFrame(index + 1, 10 + offset, offset, offset, 0.0,
                      640, 480, "image/jpeg", jpeg)
        for index, offset in enumerate(offsets)
    )
    bundle = EventFrameBundle("event-1", "session-1", DomainName.PARKING_MOTORCYCLE,
                              10.0, frames, len(jpeg) * 5)
    evidence = ParkingCandidateEvidence(4, 2.1, .01, .02, .04, .0, .03)
    return EvaluationRequest("event-1", "session-1", DomainName.PARKING_MOTORCYCLE,
                             8.0, 10.0, 7, 5, evidence, bundle)


def _assessment_data(score=20, status="NORMAL") -> dict:
    return {
        "threat_score": score, "status": status,
        "reasoning": "Gerakan teramati, belum ada kesimpulan lain.",
        "subject_profile": {
            "person_detected": True, "clothing": "jaket",
            "observed_action": "berjalan di dekat motor",
            "visible_objects": ["motor"], "distinguishing_features": ["helm"],
        },
    }


def _langflow_body(text: str) -> dict:
    return {"outputs": [{"outputs": [{"results": {"message": {"text": text}}}]}]}


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_request_contains_five_data_uris_and_metadata_without_extra_encode_or_log() -> None:
    captured = []
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_langflow_body(json.dumps(_assessment_data())))
    transport = _mock_client(handler)
    client = LangflowEvaluationClient(url=URL, api_key="secret", timeout_seconds=4, http_client=transport)
    assessment = client.evaluate(_request())
    assert assessment.status is ThreatStatus.NORMAL and assessment.threat_score == 20
    assert len(captured) == 1
    sent = captured[0]
    assert sent.method == "POST" and str(sent.url) == URL
    assert sent.headers["x-api-key"] == "secret"
    outer = json.loads(sent.content)
    assert outer["input_type"] == outer["output_type"] == "chat"
    assert outer["session_id"] == "session-1"
    payload = json.loads(outer["input_value"])
    assert (payload["event_id"], payload["source_session_id"], payload["domain"]) == (
        "event-1", "session-1", "PARKING_MOTORCYCLE",
    )
    assert payload["trigger"]["person_track_id"] == 7
    assert payload["trigger"]["object_track_id"] == 5
    assert payload["trigger"]["evidence"]["proximity_observations"] == 4
    assert len(payload["frames"]) == 5
    assert [frame["source_frame_seq"] for frame in payload["frames"]] == [1, 2, 3, 4, 5]
    assert [frame["target_offset_seconds"] for frame in payload["frames"]] == [-2, -1, -.4, 0, .4]
    for frame in payload["frames"]:
        assert frame["source_timestamp"] > 0
        assert frame["image"].startswith("data:image/jpeg;base64,")
        assert base64.b64decode(frame["image"].split(",", 1)[1]) == b"\xff\xd8synthetic\xff\xd9"
    assert "synthetic" not in repr(client) and "secret" not in repr(client)
    assert "synthetic" not in repr(_request())


def test_env_config_and_optional_key(monkeypatch) -> None:
    monkeypatch.setenv("SENTINEL_LANGFLOW_URL", URL)
    monkeypatch.delenv("SENTINEL_LANGFLOW_API_KEY", raising=False)
    monkeypatch.setenv("SENTINEL_LANGFLOW_TIMEOUT_SECONDS", "7.5")
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=_langflow_body(json.dumps(_assessment_data())))
    client = LangflowEvaluationClient(http_client=_mock_client(handler))
    client.evaluate(_request())
    assert client._timeout == 7.5
    assert "x-api-key" not in seen[0].headers


@pytest.mark.parametrize("url,timeout", [
    ("", 2), ("not-a-url", 2), ("ftp://example.com/run", 2),
    ("https://user:pass@example.com/run", 2), (URL, 0), (URL, float("nan")),
])
def test_invalid_config_fails_before_network(url, timeout) -> None:
    with pytest.raises(LangflowEvaluationError) as error:
        LangflowEvaluationClient(url=url, timeout_seconds=timeout, http_client=_mock_client(lambda _: None))
    assert error.value.kind is LangflowErrorKind.INVALID_CONFIG


@pytest.mark.parametrize("status", [301, 400, 401, 429, 500])
def test_http_status_is_safe_and_not_retried(status) -> None:
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="SECRET-BODY")
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(handler))
    with pytest.raises(LangflowEvaluationError) as error:
        client.evaluate(_request())
    assert error.value.kind is LangflowErrorKind.HTTP_STATUS
    assert error.value.status_code == status and len(calls) == 1
    assert "SECRET-BODY" not in str(error.value)


@pytest.mark.parametrize("exception,kind", [
    (httpx.ConnectError("secret host detail"), LangflowErrorKind.CONNECTION_ERROR),
    (httpx.ReadTimeout("secret timeout detail"), LangflowErrorKind.TIMEOUT),
])
def test_transport_errors_are_safe_and_not_retried(exception, kind) -> None:
    calls = []
    def handler(request):
        calls.append(request)
        raise exception
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(handler))
    with pytest.raises(LangflowEvaluationError) as error:
        client.evaluate(_request())
    assert error.value.kind is kind and len(calls) == 1
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("response,kind", [
    (httpx.Response(200, content=b""), LangflowErrorKind.EMPTY_RESPONSE),
    (httpx.Response(200, content=b"not-json"), LangflowErrorKind.MALFORMED_JSON),
    (httpx.Response(200, json={"outputs": []}), LangflowErrorKind.EMPTY_RESPONSE),
    (httpx.Response(200, json=_langflow_body(" ")), LangflowErrorKind.EMPTY_RESPONSE),
    (httpx.Response(200, json=_langflow_body("not-json")), LangflowErrorKind.MALFORMED_JSON),
])
def test_empty_or_malformed_langflow_response(response, kind) -> None:
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(lambda _: response))
    with pytest.raises(LangflowEvaluationError) as error:
        client.evaluate(_request())
    assert error.value.kind is kind


@pytest.mark.parametrize("score,status", [(0, "NORMAL"), (34, "NORMAL"),
                                           (35, "WARNING"), (64, "WARNING"),
                                           (65, "CRITICAL"), (100, "CRITICAL")])
def test_valid_score_status_ranges(score, status) -> None:
    text = json.dumps(_assessment_data(score, status))
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(
        lambda _: httpx.Response(200, json=_langflow_body(text))))
    assert client.evaluate(_request()).threat_score == score


@pytest.mark.parametrize("mutation", [
    {"threat_score": -1}, {"threat_score": 101}, {"threat_score": True},
    {"threat_score": 50, "status": "CRITICAL"}, {"reasoning": " "},
    {"subject_profile": {}},
])
def test_invalid_assessment_is_rejected(mutation) -> None:
    body = _assessment_data(); body.update(mutation)
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(
        lambda _: httpx.Response(200, json=_langflow_body(json.dumps(body)))))
    with pytest.raises(LangflowEvaluationError) as error:
        client.evaluate(_request())
    assert error.value.kind is LangflowErrorKind.INVALID_RESPONSE


def test_camel_case_aliases_and_json_fence() -> None:
    body = _assessment_data()
    profile = body.pop("subject_profile")
    body["threatScore"] = body.pop("threat_score")
    body["suspectProfile"] = {
        "personDetected": profile["person_detected"], "clothing": profile["clothing"],
        "observedAction": profile["observed_action"],
        "visibleObjects": profile["visible_objects"],
        "distinguishingFeatures": profile["distinguishing_features"],
    }
    fenced = "```json\n" + json.dumps(body) + "\n```"
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(
        lambda _: httpx.Response(200, json=_langflow_body(fenced))))
    assert client.evaluate(_request()).subject_profile.visible_objects == ("motor",)


def test_invalid_bundle_is_rejected_before_transport() -> None:
    calls = []
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(lambda request: calls.append(request)))
    request = _request()
    invalid = EvaluationRequest(
        request.event_id, request.source_session_id, request.domain, request.started_at,
        request.triggered_at, request.person_track_id, request.object_track_id,
        request.evidence,
        EventFrameBundle(request.event_id, request.source_session_id, request.domain,
                         request.triggered_at, request.frame_bundle.frames[:4], 1),
    )
    with pytest.raises(LangflowEvaluationError) as error:
        client.evaluate(invalid)
    assert error.value.kind is LangflowErrorKind.INVALID_REQUEST and not calls


def test_evaluation_worker_fails_pending_on_http_client_error() -> None:
    client = LangflowEvaluationClient(url=URL, http_client=_mock_client(
        lambda _: httpx.Response(500, text="never log me")))
    trigger = FakeTrigger("one")
    buffer = FakeBuffer("one")
    budget = EvaluationBudget()
    clock = FakeClock(10)
    worker, *_ = _worker(client=client, trigger=trigger, buffer=buffer, budget=budget, clock=clock)
    trigger.queue(_event("e1", 10))
    assert worker.process_one(10)
    assert worker.get_latest_result() is None
    assert worker.get_snapshot().last_failure is EvaluationFailure.CLIENT_ERROR
    assert trigger.pending is None and trigger.failed == ["e1"]
    assert budget.get_snapshot(10).pending_reservation_id is None
    assert budget.get_snapshot(10).total_failed == 1
