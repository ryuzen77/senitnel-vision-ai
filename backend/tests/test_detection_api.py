"""Tests for the read-only latest-detection endpoint."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.detection import Detection, DetectionResult


@dataclass
class FakeDetector:
    result: DetectionResult | None = None
    get_calls: int = 0
    inference_calls: int = 0

    def get_latest_result(self) -> DetectionResult | None:
        self.get_calls += 1
        return self.result


@pytest.fixture()
def fake_detector(monkeypatch: pytest.MonkeyPatch) -> FakeDetector:
    detector = FakeDetector()
    monkeypatch.setattr("app.api.detections.detector_service", detector)
    return detector


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _result(detections: list[Detection] | None = None) -> DetectionResult:
    return DetectionResult(
        detection_seq=1,
        source_session_id="session-abc",
        source_frame_seq=10,
        source_timestamp=12345.67,
        detections=detections if detections is not None else [
            Detection(0, "person", 0.91, (10.0, 20.0, 100.0, 200.0))
        ],
    )


def test_latest_returns_204_with_empty_body_before_result(client: TestClient, fake_detector: FakeDetector) -> None:
    response = client.get("/api/detections/latest")
    assert response.status_code == 204
    assert response.content == b""


def test_latest_returns_complete_result_schema(client: TestClient, fake_detector: FakeDetector) -> None:
    fake_detector.result = _result()
    response = client.get("/api/detections/latest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detection_seq": 1,
        "source_session_id": "session-abc",
        "source_frame_seq": 10,
        "source_timestamp": 12345.67,
        "detections": [
            {
                "class_id": 0,
                "class_name": "person",
                "confidence": 0.91,
                "xyxy": [10.0, 20.0, 100.0, 200.0],
            }
        ],
    }


def test_latest_returns_200_for_completed_empty_detection_result(
    client: TestClient, fake_detector: FakeDetector
) -> None:
    fake_detector.result = _result(detections=[])
    response = client.get("/api/detections/latest")
    assert response.status_code == 200
    assert response.json()["detections"] == []


def test_multiple_gets_are_read_only(client: TestClient, fake_detector: FakeDetector) -> None:
    fake_detector.result = _result()
    first = client.get("/api/detections/latest")
    second = client.get("/api/detections/latest")
    assert first.json() == second.json()
    assert first.json()["detection_seq"] == 1
    assert fake_detector.get_calls == 2
    assert fake_detector.inference_calls == 0


def test_stale_or_inactive_result_is_not_returned(client: TestClient, fake_detector: FakeDetector) -> None:
    # DetectorService clears its latest result when end_session() invalidates it.
    fake_detector.result = None
    response = client.get("/api/detections/latest")
    assert response.status_code == 204
    assert response.content == b""
