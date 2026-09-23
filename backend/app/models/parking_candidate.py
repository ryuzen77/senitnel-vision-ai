"""Internal contracts for local parking-interaction candidate selection.

Candidates are prompts for later review only.  They do not identify people,
infer ownership, or determine unlawful conduct.
"""

from __future__ import annotations

import dataclasses
import enum

from app.models.domain import DomainName


class ParkingCandidateState(str, enum.Enum):
    OBSERVING = "OBSERVING"
    CANDIDATE = "CANDIDATE"
    API_PENDING = "API_PENDING"
    COOLDOWN = "COOLDOWN"
    RELEASED = "RELEASED"


@dataclasses.dataclass(frozen=True)
class ParkingCandidateEvidence:
    proximity_observations: int
    dwell_seconds: float
    minimum_distance_normalized: float
    mean_distance_normalized: float
    person_motion_normalized: float
    motorcycle_motion_normalized: float
    pair_distance_range_normalized: float


@dataclasses.dataclass(frozen=True)
class ParkingCandidateEvent:
    event_id: str
    source_session_id: str
    domain: DomainName
    person_track_id: int
    object_track_id: int
    started_at: float
    triggered_at: float
    trigger_source_frame_seq: int
    evidence: ParkingCandidateEvidence
    candidate_frame_sequences: tuple[int, ...]
    state: ParkingCandidateState


@dataclasses.dataclass(frozen=True)
class ParkingCandidateDiagnostic:
    """Immutable, metadata-only state useful to future internal consumers."""

    source_session_id: str | None
    pending_event_id: str | None
    pair_states: tuple[tuple[int, int, ParkingCandidateState], ...]
