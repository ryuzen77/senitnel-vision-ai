"""In-memory domain classification contracts.

These types deliberately describe only what the lightweight classifier knows;
they are not HTTP response models yet.
"""

from __future__ import annotations

import dataclasses
import enum

from pydantic import BaseModel


class DomainName(str, enum.Enum):
    UNKNOWN = "UNKNOWN"
    PARKING_MOTORCYCLE = "PARKING_MOTORCYCLE"
    RETAIL_STORE = "RETAIL_STORE"


class DecisionSource(str, enum.Enum):
    YOLO = "YOLO"
    CLIP = "CLIP"


class DomainState(str, enum.Enum):
    IDLE = "IDLE"
    PROBING = "PROBING"
    LOCKED = "LOCKED"


@dataclasses.dataclass(frozen=True)
class DomainSnapshot:
    source_session_id: str | None
    state: DomainState
    domain: DomainName
    observation_count: int
    evidence_hits: int
    confidence: float
    locked: bool
    last_detection_seq: int | None
    decision_source: DecisionSource | None = None
    scene_model: str | None = None
    scene_sample_count: int | None = None
    scene_top_score: float | None = None
    scene_mean_margin: float | None = None
    scene_consistency: float | None = None


class DomainSnapshotResponse(BaseModel):
    """Stable JSON representation of the current domain snapshot."""

    source_session_id: str | None
    state: DomainState
    domain: DomainName
    observation_count: int
    evidence_hits: int
    confidence: float
    locked: bool
    last_detection_seq: int | None
    decision_source: DecisionSource | None = None
    scene_model: str | None = None
    scene_sample_count: int | None = None
    scene_top_score: float | None = None
    scene_mean_margin: float | None = None
    scene_consistency: float | None = None
