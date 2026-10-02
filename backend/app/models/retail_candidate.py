"""Internal contracts for local retail display-zone candidate selection.

A candidate only says that an anonymous person track stayed in an owner-defined
display zone long enough to be worth an external review. It never identifies a
person, infers ownership, or states that merchandise was taken. The pipeline has
no merchandise, hand, pose, concealment or payment signal, so none is claimed.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import math
import re

from app.models.domain import DomainName

MAX_RETAIL_ZONES = 8
MIN_ZONE_AREA = 0.01
MAX_ZONE_CONFIG_BYTES = 4096
_ZONE_NAME = re.compile(r"[a-z0-9_-]{1,32}")


def _is_number(value: object) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


@dataclasses.dataclass(frozen=True)
class RetailZone:
    """Owner-configured display rectangle in normalized frame coordinates."""

    name: str
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _ZONE_NAME.fullmatch(self.name) is None:
            raise ValueError("zone name must match [a-z0-9_-]{1,32}")
        coordinates = (self.x1, self.y1, self.x2, self.y2)
        if not all(_is_number(value) for value in coordinates):
            raise ValueError("zone coordinates must be finite numbers")
        if not (0.0 <= self.x1 < self.x2 <= 1.0 and 0.0 <= self.y1 < self.y2 <= 1.0):
            raise ValueError("zone coordinates must satisfy 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1")
        if (self.x2 - self.x1) * (self.y2 - self.y1) < MIN_ZONE_AREA:
            raise ValueError(f"zone area must be at least {MIN_ZONE_AREA}")


def validate_retail_zones(zones: object) -> tuple[RetailZone, ...]:
    """Return the zones as a tuple or raise ValueError; never repairs a config."""
    if not isinstance(zones, (tuple, list)):
        raise ValueError("zones must be a sequence of RetailZone")
    zones = tuple(zones)
    if not 1 <= len(zones) <= MAX_RETAIL_ZONES:
        raise ValueError(f"between 1 and {MAX_RETAIL_ZONES} zones are required")
    if not all(isinstance(zone, RetailZone) for zone in zones):
        raise ValueError("zones must be RetailZone instances")
    if len({zone.name for zone in zones}) != len(zones):
        raise ValueError("zone names must be unique")
    return zones


def parse_retail_zones(raw: object) -> tuple[RetailZone, ...] | None:
    """Parse ``[{"name": ..., "rect": [x1, y1, x2, y2]}, ...]``; None when invalid.

    Fail-closed: any violation rejects the whole config. There is no default
    zone and no whole-frame fallback.
    """
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_ZONE_CONFIG_BYTES:
        return None

    def reject_constant(_constant: str) -> object:
        raise ValueError("non-finite constants are not allowed")

    try:
        items = json.loads(raw, parse_constant=reject_constant)
        if not isinstance(items, list):
            return None
        zones = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"name", "rect"}:
                return None
            rect = item["rect"]
            if not isinstance(rect, list) or len(rect) != 4 or not all(_is_number(v) for v in rect):
                return None
            zones.append(RetailZone(item["name"], *(float(v) for v in rect)))
        return validate_retail_zones(zones)
    except (ValueError, TypeError, RecursionError):
        return None


class RetailCandidateState(str, enum.Enum):
    ENGAGING = "ENGAGING"        # inside a zone, presence not yet established
    ENGAGED = "ENGAGED"          # established presence, dwell accumulating
    CANDIDATE = "CANDIDATE"      # zone reserved, waiting to be claimed (TTL)
    API_PENDING = "API_PENDING"  # claimed by the evaluation worker
    DONE = "DONE"                # never triggers again; tombstone while present


@dataclasses.dataclass(frozen=True)
class RetailCandidateEvidence:
    """Metadata-only, neutral measurements of zone presence."""

    episode_id: str
    zone_name: str
    zone_index: int
    initial_person_track_id: int
    active_person_track_id: int
    relink_count: int
    dwell_in_zone_seconds: float
    observations_in_zone: int
    max_observation_gap_seconds: float
    mean_overlap: float
    max_overlap: float
    box_height_variation: float
    box_width_variation: float
    center_path_in_zone_normalized: float
    edge_truncated_observations: int
    concurrent_episodes_in_zone: int


@dataclasses.dataclass(frozen=True)
class RetailCandidateEvent:
    event_id: str
    source_session_id: str
    domain: DomainName
    person_track_id: int
    started_at: float
    triggered_at: float
    trigger_source_frame_seq: int
    evidence: RetailCandidateEvidence
    candidate_frame_sequences: tuple[int, ...]
    state: RetailCandidateState
    # No object signal exists for retail; always None (never a placeholder ID).
    object_track_id: None = None

    def __post_init__(self) -> None:
        if self.object_track_id is not None:
            raise ValueError("retail candidates carry no object track")
        if self.domain is not DomainName.RETAIL_STORE:
            raise ValueError("retail candidates belong to RETAIL_STORE")


@dataclasses.dataclass(frozen=True)
class RetailEpisodeView:
    episode_id: str
    zone_name: str
    active_track_id: int
    state: RetailCandidateState
    dwell_seconds: float
    lost: bool
    relink_count: int


@dataclasses.dataclass(frozen=True)
class RetailZoneView:
    name: str
    reserved_episode_id: str | None
    cooldown_until: float | None


@dataclasses.dataclass(frozen=True)
class RetailCandidateDiagnostic:
    """Immutable, metadata-only view; no frames, crops or identities."""

    source_session_id: str | None
    pending_event_id: str | None
    episodes: tuple[RetailEpisodeView, ...]
    zones: tuple[RetailZoneView, ...]
    candidates_created: int
    candidates_expired: int
    relinks: int
    ambiguous_relinks: int
    gap_resets: int
    dropped_capacity: int
    zone_reservation_conflicts: int
    identity_evictions: int
    relinks_suppressed: int
