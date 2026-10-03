"""Immutable, JPEG-only contracts for temporal event frame selection."""

from __future__ import annotations

import dataclasses
import enum

from app.models.domain import DomainName


@dataclasses.dataclass(frozen=True)
class BufferedFrame:
    source_session_id: str
    source_frame_seq: int
    source_timestamp: float
    frame_width: int
    frame_height: int
    jpeg: bytes = dataclasses.field(repr=False)
    mime_type: str = "image/jpeg"


@dataclasses.dataclass(frozen=True)
class FrameSelectionPlan:
    event_id: str
    source_session_id: str
    domain: DomainName
    triggered_at: float
    target_offsets_seconds: tuple[float, ...]
    maximum_target_deviation_seconds: float
    required_frame_count: int


class BundleStatus(str, enum.Enum):
    READY = "READY"
    WAITING_FOR_FUTURE_FRAME = "WAITING_FOR_FUTURE_FRAME"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    TARGET_DEVIATION_EXCEEDED = "TARGET_DEVIATION_EXCEEDED"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    ENDED = "ENDED"
    CLOSED = "CLOSED"
    INVALID_PLAN = "INVALID_PLAN"


@dataclasses.dataclass(frozen=True)
class SelectedFrame:
    source_frame_seq: int
    source_timestamp: float
    target_offset_seconds: float
    actual_offset_seconds: float
    deviation_seconds: float
    frame_width: int
    frame_height: int
    mime_type: str
    jpeg: bytes = dataclasses.field(repr=False)


@dataclasses.dataclass(frozen=True)
class EventFrameBundle:
    event_id: str
    source_session_id: str
    domain: DomainName
    triggered_at: float
    frames: tuple[SelectedFrame, ...]
    total_jpeg_bytes: int


@dataclasses.dataclass(frozen=True)
class BundleDecision:
    status: BundleStatus
    bundle: EventFrameBundle | None = None


@dataclasses.dataclass(frozen=True)
class EventFrameBufferSnapshot:
    source_session_id: str | None
    frame_count: int
    total_jpeg_bytes: int
    oldest_source_frame_seq: int | None
    latest_source_frame_seq: int | None
    oldest_source_timestamp: float | None
    latest_source_timestamp: float | None
    closed: bool


def make_parking_frame_selection_plan(
    event_id: str, source_session_id: str, triggered_at: float,
    *, maximum_target_deviation_seconds: float = 0.30,
) -> FrameSelectionPlan:
    """Parking-specific time targets."""
    return FrameSelectionPlan(
        event_id, source_session_id, DomainName.PARKING_MOTORCYCLE, triggered_at,
        (-2.0, -1.0, -0.4, 0.0, 0.4), maximum_target_deviation_seconds, 5,
    )


def make_retail_frame_selection_plan(
    event_id: str, source_session_id: str, triggered_at: float,
    *, maximum_target_deviation_seconds: float = 0.30,
) -> FrameSelectionPlan:
    """Retail display-zone targets, separate from parking.

    Provisional and uncalibrated: the offsets currently equal parking's. Buffer
    retention is not guaranteed (byte and frame caps can shorten it), so a
    plan that cannot be filled fails as a whole; it is never sent partially.
    """
    return FrameSelectionPlan(
        event_id, source_session_id, DomainName.RETAIL_STORE, triggered_at,
        (-2.0, -1.0, -0.4, 0.0, 0.4), maximum_target_deviation_seconds, 5,
    )
