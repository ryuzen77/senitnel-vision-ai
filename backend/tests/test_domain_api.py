"""Tests for the read-only current-domain API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.detection import Detection, DetectionResult
from app.models.domain import DomainName, DomainState
from app.services.detector_service import detector_service
from app.services.domain_service import DomainService, domain_service


def _result(sequence: int, detections: list[Detection] | None = None) -> DetectionResult:
    return DetectionResult(
        detection_seq=sequence,
        source_session_id="session-abc",
        source_frame_seq=sequence + 10,
        source_timestamp=float(sequence),
        detections=detections or [],
    )


def _motorcycle() -> Detection:
    return Detection(3, "motorcycle", 0.9, (1.0, 2.0, 3.0, 4.0))


@pytest.fixture()
def fake_domain_service(monkeypatch: pytest.MonkeyPatch) -> DomainService:
    service = DomainService()
    monkeypatch.setattr("app.api.domain.domain_service", service)
    return service


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_idle_snapshot_is_complete_and_returns_200(client: TestClient, fake_domain_service: DomainService) -> None:
    response = client.get("/api/domain/current")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "source_session_id": None,
        "state": "IDLE",
        "domain": "UNKNOWN",
        "observation_count": 0,
        "evidence_hits": 0,
        "confidence": 0.0,
        "locked": False,
        "last_detection_seq": None,
    }


def test_probing_unknown_snapshot_serializes(client: TestClient, fake_domain_service: DomainService) -> None:
    fake_domain_service.start_session("session-abc")
    response = client.get("/api/domain/current")
    assert response.status_code == 200
    assert response.json()["state"] == "PROBING"
    assert response.json()["domain"] == "UNKNOWN"
    assert response.json()["source_session_id"] == "session-abc"


def test_locked_parking_motorcycle_snapshot_serializes(client: TestClient, fake_domain_service: DomainService) -> None:
    fake_domain_service.start_session("session-abc")
    for sequence in (6, 7, 8):
        fake_domain_service.observe(_result(sequence, [_motorcycle()]))
    response = client.get("/api/domain/current")
    assert response.status_code == 200
    assert response.json() == {
        "source_session_id": "session-abc",
        "state": "LOCKED",
        "domain": "PARKING_MOTORCYCLE",
        "observation_count": 3,
        "evidence_hits": 3,
        "confidence": 1.0,
        "locked": True,
        "last_detection_seq": 8,
    }


def test_repeated_get_is_read_only_and_confidence_is_bounded(
    client: TestClient, fake_domain_service: DomainService
) -> None:
    fake_domain_service.start_session("session-abc")
    fake_domain_service.observe(_result(1, [_motorcycle()]))
    before = fake_domain_service.get_snapshot()
    first = client.get("/api/domain/current")
    second = client.get("/api/domain/current")
    after = fake_domain_service.get_snapshot()
    assert first.json() == second.json()
    assert after == before
    assert isinstance(first.json()["confidence"], float)
    assert 0.0 <= first.json()["confidence"] <= 1.0


def test_router_and_production_detector_share_the_domain_singleton() -> None:
    assert domain_service is detector_service._result_consumer  # noqa: SLF001
    assert domain_service.get_snapshot().state in DomainState
    assert domain_service.get_snapshot().domain in DomainName
