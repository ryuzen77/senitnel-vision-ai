"""Unit tests for the lightweight, session-scoped domain classifier."""

from __future__ import annotations

from app.models.detection import Detection, DetectionResult
import math

import pytest

from app.models.domain import DecisionSource, DomainName, DomainState
from app.services.domain_service import DomainService


def _result(
    sequence: int,
    session_id: str = "session-a",
    detections: list[Detection] | None = None,
) -> DetectionResult:
    return DetectionResult(
        detection_seq=sequence,
        source_session_id=session_id,
        source_frame_seq=sequence,
        source_timestamp=float(sequence),
        detections=detections or [],
    )


def _motorcycle(confidence: float = 0.9) -> Detection:
    return Detection(3, "Motorcycle", confidence, (1.0, 2.0, 3.0, 4.0))


def _scene_decision(service: DomainService, domain: DomainName, session_id: str = "session-a") -> bool:
    return service.apply_scene_decision(
        session_id=session_id,
        domain=domain,
        scene_model="openai/clip-vit-base-patch32",
        mean_top_score=0.31,
        consistency=0.8,
        mean_margin=0.04,
        sample_count=5,
    )


def test_idle_before_a_session() -> None:
    snapshot = DomainService().get_snapshot()
    assert snapshot.state is DomainState.IDLE
    assert snapshot.domain is DomainName.UNKNOWN
    assert snapshot.confidence == 0.0
    assert snapshot.observation_count == 0
    assert snapshot.decision_source is None
    assert snapshot.scene_model is None
    assert snapshot.scene_sample_count is None
    assert snapshot.scene_top_score is None
    assert snapshot.scene_mean_margin is None
    assert snapshot.scene_consistency is None


def test_start_session_probes_unknown_domain() -> None:
    service = DomainService()
    service.start_session("session-a")
    snapshot = service.get_snapshot()
    assert snapshot.source_session_id == "session-a"
    assert snapshot.state is DomainState.PROBING
    assert snapshot.domain is DomainName.UNKNOWN
    assert snapshot.locked is False
    assert snapshot.decision_source is None


def test_three_motorcycle_hits_lock_parking_motorcycle() -> None:
    service = DomainService()
    service.start_session("session-a")
    for sequence in range(3):
        service.observe(_result(sequence, detections=[_motorcycle()]))
    snapshot = service.get_snapshot()
    assert snapshot.state is DomainState.LOCKED
    assert snapshot.domain is DomainName.PARKING_MOTORCYCLE
    assert snapshot.observation_count == 3
    assert snapshot.evidence_hits == 3
    assert snapshot.confidence == 1.0
    assert snapshot.decision_source is DecisionSource.YOLO
    assert snapshot.scene_model is None
    assert snapshot.scene_sample_count is None


@pytest.mark.parametrize("domain", [DomainName.PARKING_MOTORCYCLE, DomainName.RETAIL_STORE, DomainName.UNKNOWN])
def test_scene_can_atomically_lock_each_allowed_domain(domain: DomainName) -> None:
    service = DomainService()
    service.start_session("session-a")
    assert _scene_decision(service, domain) is True
    snapshot = service.get_snapshot()
    assert snapshot.state is DomainState.LOCKED
    assert snapshot.domain is domain
    assert snapshot.locked is True
    assert snapshot.decision_source is DecisionSource.CLIP
    assert snapshot.scene_model == "openai/clip-vit-base-patch32"
    assert snapshot.scene_sample_count == 5
    assert snapshot.scene_top_score == 0.31
    assert snapshot.scene_mean_margin == 0.04
    assert snapshot.scene_consistency == 0.8


def test_scene_wrong_session_idle_and_second_lock_do_not_mutate() -> None:
    service = DomainService()
    assert _scene_decision(service, DomainName.RETAIL_STORE) is False
    service.start_session("session-a")
    before = service.get_snapshot()
    assert _scene_decision(service, DomainName.RETAIL_STORE, "other") is False
    assert service.get_snapshot() == before
    assert _scene_decision(service, DomainName.RETAIL_STORE) is True
    locked = service.get_snapshot()
    assert _scene_decision(service, DomainName.PARKING_MOTORCYCLE) is False
    assert service.get_snapshot() == locked


def test_scene_first_prevents_yolo_and_yolo_first_prevents_scene() -> None:
    scene_first = DomainService()
    scene_first.start_session("session-a")
    assert _scene_decision(scene_first, DomainName.RETAIL_STORE) is True
    for sequence in range(3):
        scene_first.observe(_result(sequence, detections=[_motorcycle()]))
    assert scene_first.get_snapshot().domain is DomainName.RETAIL_STORE
    assert scene_first.get_snapshot().decision_source is DecisionSource.CLIP

    yolo_first = DomainService()
    yolo_first.start_session("session-a")
    for sequence in range(3):
        yolo_first.observe(_result(sequence, detections=[_motorcycle()]))
    before = yolo_first.get_snapshot()
    assert _scene_decision(yolo_first, DomainName.RETAIL_STORE) is False
    assert yolo_first.get_snapshot() == before


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sample_count": 0}, "positive integer"),
        ({"mean_top_score": math.inf}, "finite"),
        ({"mean_margin": math.nan}, "finite"),
        ({"consistency": 1.1}, "between"),
    ],
)
def test_invalid_scene_input_fails_without_partial_mutation(kwargs: dict[str, object], message: str) -> None:
    service = DomainService()
    service.start_session("session-a")
    before = service.get_snapshot()
    values: dict[str, object] = {
        "session_id": "session-a",
        "domain": DomainName.RETAIL_STORE,
        "scene_model": "openai/clip-vit-base-patch32",
        "mean_top_score": 0.31,
        "consistency": 0.8,
        "mean_margin": 0.04,
        "sample_count": 5,
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        service.apply_scene_decision(**values)  # type: ignore[arg-type]
    assert service.get_snapshot() == before


def test_two_hits_in_five_observations_does_not_lock() -> None:
    service = DomainService()
    service.start_session("session-a")
    for sequence, hit in enumerate([True, False, True, False, False]):
        service.observe(_result(sequence, detections=[_motorcycle()] if hit else []))
    snapshot = service.get_snapshot()
    assert snapshot.state is DomainState.PROBING
    assert snapshot.observation_count == 5
    assert snapshot.evidence_hits == 2
    assert snapshot.confidence == 0.4


def test_many_motorcycles_in_one_result_count_as_one_hit() -> None:
    service = DomainService()
    service.start_session("session-a")
    service.observe(_result(0, detections=[_motorcycle(), _motorcycle(), _motorcycle()]))
    snapshot = service.get_snapshot()
    assert snapshot.observation_count == 1
    assert snapshot.evidence_hits == 1


def test_low_confidence_and_empty_results_are_non_hits() -> None:
    service = DomainService()
    service.start_session("session-a")
    service.observe(_result(0, detections=[_motorcycle(0.49)]))
    service.observe(_result(1))
    snapshot = service.get_snapshot()
    assert snapshot.observation_count == 2
    assert snapshot.evidence_hits == 0
    assert snapshot.confidence == 0.0


def test_duplicate_out_of_order_and_foreign_results_are_ignored() -> None:
    service = DomainService()
    service.start_session("session-a")
    service.observe(_result(2, detections=[_motorcycle()]))
    service.observe(_result(2, detections=[_motorcycle()]))
    service.observe(_result(1, detections=[_motorcycle()]))
    service.observe(_result(3, session_id="session-b", detections=[_motorcycle()]))
    snapshot = service.get_snapshot()
    assert snapshot.observation_count == 1
    assert snapshot.evidence_hits == 1
    assert snapshot.last_detection_seq == 2


def test_locked_domain_does_not_change_after_later_observations() -> None:
    service = DomainService()
    service.start_session("session-a")
    for sequence in range(3):
        service.observe(_result(sequence, detections=[_motorcycle()]))
    service.observe(_result(3))
    snapshot = service.get_snapshot()
    assert snapshot.state is DomainState.LOCKED
    assert snapshot.domain is DomainName.PARKING_MOTORCYCLE
    assert snapshot.observation_count == 3
    assert snapshot.last_detection_seq == 2


def test_end_session_and_new_session_clear_all_evidence() -> None:
    service = DomainService()
    service.start_session("session-a")
    service.observe(_result(0, detections=[_motorcycle()]))
    service.end_session("other")
    assert service.get_snapshot().observation_count == 1
    service.end_session("session-a")
    snapshot = service.get_snapshot()
    assert snapshot.state is DomainState.IDLE
    assert snapshot.domain is DomainName.UNKNOWN
    assert snapshot.observation_count == 0
    service.start_session("session-b")
    assert service.get_snapshot().last_detection_seq is None
    assert service.get_snapshot().decision_source is None


def test_starting_a_new_session_replaces_the_previous_window() -> None:
    service = DomainService()
    service.start_session("session-a")
    service.observe(_result(0, detections=[_motorcycle()]))
    service.start_session("session-b")
    for sequence in range(6):
        service.observe(_result(sequence, session_id="session-b"))
    snapshot = service.get_snapshot()
    assert snapshot.source_session_id == "session-b"
    assert snapshot.state is DomainState.PROBING
    assert snapshot.observation_count == 5
    assert snapshot.evidence_hits == 0


def test_start_and_end_session_clear_scene_metadata() -> None:
    service = DomainService()
    service.start_session("session-a")
    assert _scene_decision(service, DomainName.RETAIL_STORE) is True
    service.end_session("session-a")
    idle = service.get_snapshot()
    assert idle.decision_source is None
    assert idle.scene_model is None
    service.start_session("session-b")
    assert service.get_snapshot().scene_sample_count is None
