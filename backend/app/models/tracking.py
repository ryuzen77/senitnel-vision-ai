"""Internal, session-scoped anonymous tracking contracts.

Track IDs are transient correlation labels only.  They are not identities,
are never persisted across sessions, and must not be used to infer ownership
or intent.
"""

from __future__ import annotations

import dataclasses

@dataclasses.dataclass(frozen=True)
class TrackingDetectionInput:
    """Detector fields needed for CPU association, with no image payload."""

    class_id: int
    class_name: str
    confidence: float
    xyxy: tuple[float, float, float, float]


@dataclasses.dataclass(frozen=True)
class TrackingFrame:
    """Detector observations plus source geometry; never carries BGR/JPEG data.

    The future VideoService adapter must populate width/height from
    ``FramePacket.frame.shape`` rather than infer them from any bounding box.
    """

    source_session_id: str
    source_frame_seq: int
    source_timestamp: float
    frame_width: int
    frame_height: int
    detections: tuple[TrackingDetectionInput, ...]


# Kept as a descriptive alias for callers that use the audit terminology.
TrackingObservation = TrackingFrame


@dataclasses.dataclass(frozen=True)
class TrackedDetection:
    """One detection associated with an anonymous track in the current session."""

    track_id: int
    class_id: int
    class_name: str
    confidence: float
    xyxy: tuple[float, float, float, float]
    center: tuple[float, float]
    source_session_id: str
    source_frame_seq: int
    source_timestamp: float
    confirmed: bool
    hit_count: int


@dataclasses.dataclass(frozen=True)
class TrackingSnapshot:
    """One tracker output snapshot for local downstream consumers.

    This metadata-only contract deliberately does not carry a frame, JPEG, or
    crop.  ``track_id`` values remain anonymous and session-scoped.
    """

    source_session_id: str
    source_frame_seq: int
    source_timestamp: float
    frame_width: int
    frame_height: int
    tracks: tuple[TrackedDetection, ...]
