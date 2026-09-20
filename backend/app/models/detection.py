"""Internal data contracts for asynchronous object detection."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel


@dataclasses.dataclass(frozen=True)
class Detection:
    """One object found in a source frame."""

    class_id: int
    class_name: str
    confidence: float
    xyxy: tuple[float, float, float, float]


@dataclasses.dataclass(frozen=True)
class DetectionResult:
    """Latest complete detector output for one source frame."""

    detection_seq: int
    source_session_id: str
    source_frame_seq: int
    source_timestamp: float
    detections: list[Detection]


class DetectionResponse(BaseModel):
    """JSON representation of one detection."""

    class_id: int
    class_name: str
    confidence: float
    xyxy: tuple[float, float, float, float]


class DetectionResultResponse(BaseModel):
    """JSON representation of the latest completed detector result."""

    detection_seq: int
    source_session_id: str
    source_frame_seq: int
    source_timestamp: float
    detections: list[DetectionResponse]
