"""Callback contract/API tests; no Langflow, network, video, or runtime worker."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from fastapi.testclient import TestClient

from app.api.evaluation import get_callback_consumer
from app.main import app
from app.models.evaluation import CallbackDisposition, EvaluationCallback, ThreatStatus


TOKEN = "test-callback-token"
HEADERS = {"x-sentinel-callback-token": TOKEN}


class FakeConsumer:
    def __init__(self) -> None:
        self.calls: list[tuple[EvaluationCallback, float]] = []
        self.current_session = "session-1"
        self.pending_event = "event-1"
        self.completed: set[str] = set()
        self.accepted: dict[str, EvaluationCallback] = {}
        self.raise_error = False

    def accept_callback(self, callback: EvaluationCallback, now: float) -> CallbackDisposition:
        self.calls.append((callback, now))
        if self.raise_error:
            raise RuntimeError("sensitive internal exception")
        if callback.source_session_id != self.current_session:
            return CallbackDisposition.REJECTED
        if callback.event_id in self.completed:
            return (CallbackDisposition.DUPLICATE if callback == self.accepted[callback.event_id]
                    else CallbackDisposition.REJECTED)
        if callback.event_id != self.pending_event:
            return CallbackDisposition.REJECTED
        self.completed.add(callback.event_id)
        self.accepted[callback.event_id] = callback
        self.pending_event = None
        return CallbackDisposition.ACCEPTED


def _payload(score: int = 20, status: str = "NORMAL") -> dict:
    return {
        "event_id": "event-1",
        "source_session_id": "session-1",
        "threatScore": score,
        "status": status,
        "reasoning": "Gerakan terlihat; penilaian perlu ditinjau manusia.",
        "suspectProfile": {
            "personDetected": True,
            "clothing": "jaket gelap",
            "observedAction": "berjalan di dekat motor",
            "visibleObjects": ["motor"],
            "distinguishingFeatures": ["helm"],
        },
    }


@pytest.fixture()
def callback_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SENTINEL_CALLBACK_TOKEN", TOKEN)
    fake = FakeConsumer()
    app.dependency_overrides[get_callback_consumer] = lambda: fake
    try:
        yield TestClient(app), fake
    finally:
        app.dependency_overrides.pop(get_callback_consumer, None)


@pytest.mark.parametrize("score,status", [
    (0, "NORMAL"), (34, "NORMAL"), (35, "WARNING"),
    (64, "WARNING"), (65, "CRITICAL"), (100, "CRITICAL"),
])
def test_valid_callback_is_forwarded_as_immutable_assessment(callback_client, score, status) -> None:
    client, fake = callback_client
    response = client.post("/api/evaluation/callback", json=_payload(score, status), headers=HEADERS)
    assert response.status_code == 200 and response.json() == {"status": "accepted"}
    assert len(fake.calls) == 1
    callback, now = fake.calls[0]
    assert callback.event_id == "event-1" and callback.source_session_id == "session-1"
    assert callback.assessment.threat_score == score
    assert callback.assessment.status is ThreatStatus(status)
    assert callback.assessment.subject_profile.person_detected is True
    assert callback.assessment.subject_profile.visible_objects == ("motor",)
    assert isinstance(now, float) and now >= 0
    assert "jpeg" not in repr(callback).lower() and "base64" not in repr(callback).lower()
    with pytest.raises(FrozenInstanceError):
        callback.event_id = "changed"


def test_duplicate_callback_is_idempotent_for_consumer(callback_client) -> None:
    client, fake = callback_client
    first = client.post("/api/evaluation/callback", json=_payload(), headers=HEADERS)
    second = client.post("/api/evaluation/callback", json=_payload(), headers=HEADERS)
    assert first.json() == {"status": "accepted"}
    assert second.status_code == 200 and second.json() == {"status": "duplicate"}
    assert fake.completed == {"event-1"}


def test_conflicting_duplicate_cannot_replace_first_result(callback_client) -> None:
    client, fake = callback_client
    assert client.post("/api/evaluation/callback", json=_payload(), headers=HEADERS).status_code == 200
    conflicting = client.post("/api/evaluation/callback", json=_payload(75, "CRITICAL"), headers=HEADERS)
    assert conflicting.status_code == 409
    assert fake.accepted["event-1"].assessment.threat_score == 20


def test_duplicate_after_session_change_is_stale(callback_client) -> None:
    client, fake = callback_client
    assert client.post("/api/evaluation/callback", json=_payload(), headers=HEADERS).status_code == 200
    fake.current_session = "session-2"
    fake.pending_event = "event-2"
    response = client.post("/api/evaluation/callback", json=_payload(), headers=HEADERS)
    assert response.status_code == 409
    assert fake.completed == {"event-1"}


@pytest.mark.parametrize("change", [
    {"event_id": "other-event"},
    {"source_session_id": "other-session"},
])
def test_wrong_event_or_session_is_not_accepted(callback_client, change) -> None:
    client, fake = callback_client
    body = _payload(); body.update(change)
    response = client.post("/api/evaluation/callback", json=body, headers=HEADERS)
    assert response.status_code == 409
    assert fake.completed == set()


def test_after_stop_or_no_pending_is_rejected(callback_client) -> None:
    client, fake = callback_client
    fake.current_session = None
    fake.pending_event = None
    response = client.post("/api/evaluation/callback", json=_payload(), headers=HEADERS)
    assert response.status_code == 409 and fake.completed == set()


def test_missing_or_bad_token_is_rejected_before_body(callback_client) -> None:
    client, fake = callback_client
    for headers in ({}, {"x-sentinel-callback-token": "wrong"}):
        response = client.post("/api/evaluation/callback", content=b"not json", headers=headers)
        assert response.status_code == 401
    assert fake.calls == []


def test_non_ascii_header_token_returns_401_without_consumer_call(callback_client) -> None:
    client, fake = callback_client
    response = client.post(
        "/api/evaluation/callback", content=b"not json",
        headers={b"x-sentinel-callback-token": b"t\xe9st"},
    )
    assert response.status_code == 401
    assert fake.calls == []


def test_non_ascii_configured_token_does_not_crash(monkeypatch, callback_client) -> None:
    client, fake = callback_client
    monkeypatch.setenv("SENTINEL_CALLBACK_TOKEN", "t\u00e9st")
    response = client.post("/api/evaluation/callback", content=b"not json", headers=HEADERS)
    assert response.status_code == 401
    assert fake.calls == []


def test_unconfigured_token_or_receiver_returns_503(monkeypatch, callback_client) -> None:
    client, fake = callback_client
    monkeypatch.delenv("SENTINEL_CALLBACK_TOKEN")
    assert client.post("/api/evaluation/callback", json=_payload(), headers=HEADERS).status_code == 503
    monkeypatch.setenv("SENTINEL_CALLBACK_TOKEN", TOKEN)
    app.dependency_overrides.pop(get_callback_consumer)
    response = client.post("/api/evaluation/callback", json=_payload(), headers=HEADERS)
    assert response.status_code == 503 and fake.calls == []


@pytest.mark.parametrize("mutation", [
    {"threatScore": -1}, {"threatScore": 101}, {"threatScore": True},
    {"threatScore": 50, "status": "CRITICAL"}, {"status": "UNKNOWN"},
    {"reasoning": "   "}, {"suspectProfile": {}},
    {"event_id": " "}, {"source_session_id": " session-1"},
    {"extra": "not allowed"},
])
def test_invalid_callback_body_never_reaches_consumer(callback_client, mutation) -> None:
    client, fake = callback_client
    body = _payload(); body.update(mutation)
    response = client.post("/api/evaluation/callback", json=body, headers=HEADERS)
    assert response.status_code == 422 and response.json() == {"detail": "Invalid callback payload"}
    assert fake.calls == []


def test_malformed_json_wrong_media_type_and_large_body_are_safe(callback_client) -> None:
    client, fake = callback_client
    malformed = client.post(
        "/api/evaluation/callback", content=b"{secret-not-json}",
        headers={**HEADERS, "content-type": "application/json"},
    )
    assert malformed.status_code == 422 and "secret" not in malformed.text
    unsupported = client.post(
        "/api/evaluation/callback", content=b"{}",
        headers={**HEADERS, "content-type": "text/plain"},
    )
    assert unsupported.status_code == 415
    too_large = client.post(
        "/api/evaluation/callback", content=b"X" * (16 * 1024 + 1),
        headers={**HEADERS, "content-type": "application/json"},
    )
    assert too_large.status_code == 413
    assert fake.calls == []


def test_consumer_exception_does_not_leak_details(callback_client) -> None:
    client, fake = callback_client
    fake.raise_error = True
    response = client.post("/api/evaluation/callback", json=_payload(), headers=HEADERS)
    assert response.status_code == 503
    assert "sensitive internal exception" not in response.text
