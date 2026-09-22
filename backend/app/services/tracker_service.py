"""CPU-only, deterministic anonymous tracker for person and motorcycle boxes."""

from __future__ import annotations

import dataclasses
import math
import threading
from collections.abc import Iterable

from app.models.detection import Detection
from app.models.tracking import TrackedDetection, TrackingFrame


@dataclasses.dataclass
class _Track:
    track_id: int
    detection: Detection
    center: tuple[float, float]
    hit_count: int
    last_timestamp: float
    last_frame_seq: int


class TrackerService:
    """Associate relevant detector boxes within one session using IoU and centers.

    IDs are anonymous, process-memory-only labels; they are not people or
    vehicle identities and disappear at session end.
    """

    _SUPPORTED_CLASSES = frozenset({"person", "motorcycle"})

    def __init__(
        self,
        *,
        min_confirmed_hits: int = 2,
        lost_track_grace_seconds: float = 1.5,
        minimum_iou: float = 0.10,
        maximum_normalized_center_displacement: float = 0.12,
        reference_elapsed_seconds: float = 1.0,
    ) -> None:
        if min_confirmed_hits < 1:
            raise ValueError("min_confirmed_hits must be positive")
        for name, value in {
            "lost_track_grace_seconds": lost_track_grace_seconds,
            "minimum_iou": minimum_iou,
            "maximum_normalized_center_displacement": maximum_normalized_center_displacement,
            "reference_elapsed_seconds": reference_elapsed_seconds,
        }.items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if reference_elapsed_seconds == 0:
            raise ValueError("reference_elapsed_seconds must be positive")
        self._min_confirmed_hits = min_confirmed_hits
        self._lost_track_grace_seconds = lost_track_grace_seconds
        self._minimum_iou = minimum_iou
        self._maximum_displacement = maximum_normalized_center_displacement
        self._reference_elapsed_seconds = reference_elapsed_seconds
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._last_frame_seq: int | None = None
        self._next_track_id = 0
        self._tracks: dict[int, _Track] = {}
        self._closed = False

    def start_session(self, session_id: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._session_id = session_id
            self._last_frame_seq = None
            self._next_track_id = 0
            self._tracks.clear()

    def observe(self, frame: TrackingFrame) -> tuple[TrackedDetection, ...]:
        """Associate one valid detector frame without retaining image bytes."""
        with self._lock:
            if (
                self._closed
                or frame.source_session_id != self._session_id
                or (self._last_frame_seq is not None and frame.source_frame_seq <= self._last_frame_seq)
                or not self._valid_frame(frame)
            ):
                return ()
            self._last_frame_seq = frame.source_frame_seq
            self._expire_tracks(frame.source_timestamp)
            candidates = self._relevant_detections(frame.detections)
            matches = self._associate(candidates, frame)
            tracked: list[TrackedDetection] = []
            matched_detection_indexes = set()
            for detection_index, track_id in matches.items():
                detection = candidates[detection_index]
                track = self._tracks[track_id]
                track.detection = detection
                track.center = self._center(detection.xyxy)
                track.hit_count += 1
                track.last_timestamp = frame.source_timestamp
                track.last_frame_seq = frame.source_frame_seq
                matched_detection_indexes.add(detection_index)
                tracked.append(self._as_tracked(track, frame))
            for detection_index, detection in enumerate(candidates):
                if detection_index in matched_detection_indexes:
                    continue
                track = _Track(
                    track_id=self._next_track_id,
                    detection=detection,
                    center=self._center(detection.xyxy),
                    hit_count=1,
                    last_timestamp=frame.source_timestamp,
                    last_frame_seq=frame.source_frame_seq,
                )
                self._tracks[track.track_id] = track
                self._next_track_id += 1
                tracked.append(self._as_tracked(track, frame))
            return tuple(sorted(tracked, key=lambda item: item.track_id))

    def get_latest_tracks(self) -> tuple[TrackedDetection, ...]:
        with self._lock:
            if self._session_id is None:
                return ()
            return tuple(
                self._as_tracked(track, TrackingFrame(
                    source_session_id=self._session_id,
                    source_frame_seq=track.last_frame_seq,
                    source_timestamp=track.last_timestamp,
                    frame_width=1,
                    frame_height=1,
                    detections=(),
                ))
                for track in sorted(self._tracks.values(), key=lambda item: item.track_id)
            )

    def end_session(self, session_id: str) -> None:
        with self._lock:
            if session_id != self._session_id:
                return
            self._session_id = None
            self._last_frame_seq = None
            self._next_track_id = 0
            self._tracks.clear()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._session_id = None
            self._last_frame_seq = None
            self._tracks.clear()

    def _associate(self, detections: list[Detection], frame: TrackingFrame) -> dict[int, int]:
        diagonal = math.hypot(frame.frame_width, frame.frame_height)
        pairs: list[tuple[float, float, int, int]] = []
        for detection_index, detection in enumerate(detections):
            for track in self._tracks.values():
                if detection.class_name.strip().lower() != track.detection.class_name.strip().lower():
                    continue
                iou = self._iou(detection.xyxy, track.detection.xyxy)
                distance = self._center_distance(self._center(detection.xyxy), track.center) / diagonal
                elapsed = max(0.0, frame.source_timestamp - track.last_timestamp)
                allowed_distance = self._maximum_displacement * max(1.0, elapsed / self._reference_elapsed_seconds)
                if iou >= self._minimum_iou or distance <= allowed_distance:
                    # Stable score/order: higher IoU, then lower distance, then IDs.
                    pairs.append((-iou, distance, track.track_id, detection_index))
        matches: dict[int, int] = {}
        used_tracks: set[int] = set()
        for _negative_iou, _distance, track_id, detection_index in sorted(pairs):
            if track_id in used_tracks or detection_index in matches:
                continue
            used_tracks.add(track_id)
            matches[detection_index] = track_id
        return matches

    def _expire_tracks(self, timestamp: float) -> None:
        self._tracks = {
            track_id: track
            for track_id, track in self._tracks.items()
            if timestamp - track.last_timestamp <= self._lost_track_grace_seconds
        }

    def _relevant_detections(self, detections: Iterable[Detection]) -> list[Detection]:
        selected = []
        for detection in detections:
            label = detection.class_name.strip().lower()
            minimum_confidence = 0.50 if label in self._SUPPORTED_CLASSES else None
            if minimum_confidence is None or detection.confidence < minimum_confidence:
                continue
            if self._valid_bbox(detection.xyxy):
                selected.append(detection)
        # Establish a deterministic input order even if detector output order changes.
        return sorted(selected, key=lambda item: (item.class_name.strip().lower(), item.class_id, *item.xyxy, -item.confidence))

    @staticmethod
    def _valid_frame(frame: TrackingFrame) -> bool:
        return (
            frame.frame_width > 0
            and frame.frame_height > 0
            and math.isfinite(frame.source_timestamp)
        )

    @staticmethod
    def _valid_bbox(box: tuple[float, float, float, float]) -> bool:
        if len(box) != 4 or not all(math.isfinite(value) for value in box):
            return False
        return box[2] > box[0] and box[3] > box[1]

    @staticmethod
    def _center(box: tuple[float, float, float, float]) -> tuple[float, float]:
        return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    @staticmethod
    def _center_distance(first: tuple[float, float], second: tuple[float, float]) -> float:
        return math.hypot(first[0] - second[0], first[1] - second[1])

    @staticmethod
    def _iou(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
        left, top = max(first[0], second[0]), max(first[1], second[1])
        right, bottom = min(first[2], second[2]), min(first[3], second[3])
        intersection = max(0.0, right - left) * max(0.0, bottom - top)
        if intersection == 0:
            return 0.0
        first_area = (first[2] - first[0]) * (first[3] - first[1])
        second_area = (second[2] - second[0]) * (second[3] - second[1])
        return intersection / (first_area + second_area - intersection)

    def _as_tracked(self, track: _Track, frame: TrackingFrame) -> TrackedDetection:
        detection = track.detection
        return TrackedDetection(
            track_id=track.track_id,
            class_id=detection.class_id,
            class_name=detection.class_name,
            confidence=detection.confidence,
            xyxy=detection.xyxy,
            center=track.center,
            source_session_id=frame.source_session_id,
            source_frame_seq=frame.source_frame_seq,
            source_timestamp=frame.source_timestamp,
            confirmed=track.hit_count >= self._min_confirmed_hits,
            hit_count=track.hit_count,
        )
