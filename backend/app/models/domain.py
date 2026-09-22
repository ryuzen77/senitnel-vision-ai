"""In-memory domain classification contracts.

These types deliberately describe only what the lightweight classifier knows;
they are not HTTP response models yet.
"""

from __future__ import annotations

import dataclasses
import enum


class DomainName(str, enum.Enum):
    UNKNOWN = "UNKNOWN"
    PARKING_MOTORCYCLE = "PARKING_MOTORCYCLE"


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
