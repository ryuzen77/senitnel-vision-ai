"""Unit tests for the lightweight, session-scoped domain classifier."""

from __future__ import annotations

from app.models.detection import Detection, DetectionResult
from app.models.domain import DomainName, DomainState
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


def test_idle_before_a_session() -> None:
    snapshot = DomainService().get_snapshot()
    assert snapshot.state is DomainState.IDLE
    assert snapshot.domain is DomainName.UNKNOWN
    assert snapshot.confidence == 0.0
    assert snapshot.observation_count == 0


def test_start_session_probes_unknown_domain() -> None:
    service = DomainService()
    service.start_session("session-a")
    snapshot = service.get_snapshot()
    assert snapshot.source_session_id == "session-a"
    assert snapshot.state is DomainState.PROBING
    assert snapshot.domain is DomainName.UNKNOWN
    assert snapshot.locked is False


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
