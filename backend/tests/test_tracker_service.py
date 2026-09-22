"""Synthetic, CPU-only tests for anonymous person/motorcycle tracking."""

from __future__ import annotations

import math
import threading

from app.models.detection import Detection
from app.models.tracking import TrackingFrame
from app.services.tracker_service import TrackerService


def _detection(label: str, box: tuple[float, float, float, float], confidence: float = 0.9) -> Detection:
    return Detection(0 if label == "person" else 3, label, confidence, box)


def _frame(session: str, seq: int, timestamp: float, *detections: Detection, width: int = 100, height: int = 100) -> TrackingFrame:
    return TrackingFrame(session, seq, timestamp, width, height, tuple(detections))


def test_person_moves_slowly_and_motorcycle_static_keep_separate_ids() -> None:
    tracker = TrackerService()
    tracker.start_session("one")
    first = tracker.observe(_frame("one", 1, 0.0, _detection("person", (10, 10, 30, 50)), _detection("motorcycle", (60, 50, 90, 80))))
    second = tracker.observe(_frame("one", 2, 0.5, _detection("person", (13, 10, 33, 50)), _detection("motorcycle", (60, 50, 90, 80))))
    assert {item.class_name: item.track_id for item in first} == {item.class_name: item.track_id for item in second}
    assert all(item.confirmed and item.hit_count == 2 for item in second)


def test_two_people_get_distinct_ids_and_detection_order_does_not_change_matching() -> None:
    tracker = TrackerService()
    tracker.start_session("one")
    left = _detection("person", (5, 5, 20, 50))
    right = _detection("person", (70, 5, 85, 50))
    first = tracker.observe(_frame("one", 1, 0.0, left, right))
    second = tracker.observe(_frame("one", 2, 0.3, right, left))
    assert len({item.track_id for item in first}) == 2
    assert [(item.track_id, item.xyxy) for item in second] == [(item.track_id, item.xyxy) for item in first]


def test_duplicate_out_of_order_wrong_session_and_low_confidence_are_ignored() -> None:
    tracker = TrackerService()
    tracker.start_session("one")
    first = tracker.observe(_frame("one", 2, 1.0, _detection("person", (1, 1, 20, 40))))
    assert tracker.observe(_frame("one", 2, 1.1, _detection("person", (1, 1, 20, 40)))) == ()
    assert tracker.observe(_frame("one", 1, 1.2, _detection("person", (1, 1, 20, 40)))) == ()
    assert tracker.observe(_frame("other", 3, 1.3, _detection("person", (1, 1, 20, 40)))) == ()
    assert tracker.observe(_frame("one", 3, 1.4, _detection("person", (1, 1, 20, 40), 0.49))) == ()
    assert tracker.get_latest_tracks()[0].track_id == first[0].track_id


def test_invalid_boxes_are_ignored_and_frame_dimensions_normalize_distance() -> None:
    tracker = TrackerService(minimum_iou=0.9, maximum_normalized_center_displacement=0.12)
    tracker.start_session("one")
    assert tracker.observe(_frame("one", 1, 0.0, _detection("person", (1, 1, math.nan, 4)))) == ()
    first = tracker.observe(_frame("one", 2, 0.1, _detection("person", (10, 10, 30, 30)), width=200, height=100))
    second = tracker.observe(_frame("one", 3, 0.2, _detection("person", (30, 10, 50, 30)), width=200, height=100))
    assert first[0].track_id == second[0].track_id


def test_short_occlusion_keeps_id_but_expired_track_gets_new_id() -> None:
    tracker = TrackerService(lost_track_grace_seconds=1.5)
    tracker.start_session("one")
    first = tracker.observe(_frame("one", 1, 0.0, _detection("motorcycle", (40, 40, 80, 80))))
    assert tracker.observe(_frame("one", 2, 0.5)) == ()
    recovered = tracker.observe(_frame("one", 3, 1.0, _detection("motorcycle", (40, 40, 80, 80))))
    assert recovered[0].track_id == first[0].track_id
    assert tracker.observe(_frame("one", 4, 3.0)) == ()
    new_track = tracker.observe(_frame("one", 5, 3.1, _detection("motorcycle", (40, 40, 80, 80))))
    assert new_track[0].track_id != first[0].track_id


def test_low_confidence_does_not_extend_track_lifetime_or_store_image_data() -> None:
    tracker = TrackerService(lost_track_grace_seconds=1.5)
    tracker.start_session("one")
    tracker.observe(_frame("one", 1, 0.0, _detection("person", (10, 10, 30, 50))))
    assert tracker.observe(_frame("one", 2, 1.4, _detection("person", (10, 10, 30, 50), 0.49))) == ()
    tracker.observe(_frame("one", 3, 1.6))
    assert tracker.get_latest_tracks() == ()
    assert "_pending_frame" not in vars(tracker)
    assert "_latest_jpeg" not in vars(tracker)


def test_new_session_resets_state_and_end_close_are_idempotent() -> None:
    tracker = TrackerService()
    tracker.start_session("one")
    tracker.observe(_frame("one", 1, 0.0, _detection("person", (1, 1, 10, 20))))
    tracker.start_session("two")
    assert tracker.get_latest_tracks() == ()
    assert tracker.observe(_frame("two", 1, 0.0, _detection("person", (1, 1, 10, 20))))[0].track_id == 0
    tracker.end_session("two")
    tracker.end_session("two")
    tracker.close()
    tracker.close()
    assert tracker.get_latest_tracks() == ()


def test_concurrent_observe_and_read_are_safe() -> None:
    tracker = TrackerService()
    tracker.start_session("one")
    errors: list[Exception] = []

    def write() -> None:
        try:
            for seq in range(1, 30):
                tracker.observe(_frame("one", seq, seq / 10, _detection("person", (10, 10, 30, 50))))
        except Exception as exc:  # pragma: no cover - assertion below validates safety.
            errors.append(exc)

    def read() -> None:
        try:
            for _ in range(30):
                tracker.get_latest_tracks()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    writers = [threading.Thread(target=write), threading.Thread(target=read)]
    for worker in writers:
        worker.start()
    for worker in writers:
        worker.join(timeout=1.0)
    assert all(not worker.is_alive() for worker in writers)
    assert errors == []
