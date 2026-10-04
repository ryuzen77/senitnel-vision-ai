"""Contracts for the private-parking access mode (within PARKING_MOTORCYCLE).

Metadata only: no images, no identities. An episode is an anonymous track that
was observed crossing an owner-configured access line from outside to inside.
A gesture signal only authorizes that episode; it is not proof of identity or
ownership. An event means "an episode without authorization stayed close to a
parked motorcycle"; it is never an assessment and never a verdict.

All thresholds are provisional and uncalibrated.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import math
import re

from app.models.domain import DomainName

MAX_ZONES = 8
MAX_CONFIG_BYTES = 4096
MIN_LINE_LENGTH = 0.05
MIN_ZONE_AREA = 0.005
_ZONE_NAME = re.compile(r"[a-z0-9_-]{1,32}")


def _is_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _unit(value: object, name: str) -> float:
    if not _is_number(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return float(value)


@dataclasses.dataclass(frozen=True)
class AccessLine:
    """Segment P1-P2 in normalized frame coordinates; the inside side is the side
    that contains ``inside_point`` (explicit, never inferred)."""

    x1: float
    y1: float
    x2: float
    y2: float
    inside_x: float
    inside_y: float

    def __post_init__(self) -> None:
        for name in ("x1", "y1", "x2", "y2", "inside_x", "inside_y"):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        if self.length < MIN_LINE_LENGTH:
            raise ValueError("access line is degenerate")

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    def raw_side(self, x: float, y: float) -> float:
        return ((self.x2 - self.x1) * (y - self.y1) - (self.y2 - self.y1) * (x - self.x1)) / self.length

    def signed_distance(self, x: float, y: float) -> float:
        """Distance to the infinite line; positive on the inside."""
        sign = 1.0 if self.raw_side(self.inside_x, self.inside_y) > 0 else -1.0
        return sign * self.raw_side(x, y)

    def segment_parameter(self, x: float, y: float) -> float:
        """Projection of a point on the segment: 0 at P1, 1 at P2."""
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        return ((x - self.x1) * dx + (y - self.y1) * dy) / (dx * dx + dy * dy)


@dataclasses.dataclass(frozen=True)
class MotorZone:
    name: str
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _ZONE_NAME.fullmatch(self.name) is None:
            raise ValueError("zone name must match [a-z0-9_-]{1,32}")
        for name in ("x1", "y1", "x2", "y2"):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        if not (self.x1 < self.x2 and self.y1 < self.y2):
            raise ValueError("zone must satisfy x1 < x2 and y1 < y2")
        if (self.x2 - self.x1) * (self.y2 - self.y1) < MIN_ZONE_AREA:
            raise ValueError("zone is too small")

    def contains(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


# name -> (default, low, high, kind); kind "float" or "int". Provisional, uncalibrated.
THRESHOLD_SPECS: dict[str, tuple[float, float, float, str]] = {
    "line_hysteresis": (0.01, 0.0, 0.1, "float"),           # normalized distance band around the line
    "segment_margin": (0.0, 0.0, 0.1, "float"),             # allowed overshoot beyond P1/P2 (segment fraction)
    "max_crossing_gap_seconds": (0.5, 0.05, 5.0, "float"),  # last-outside -> first-inside observation gap
    "lost_tolerance_seconds": (1.5, 0.1, 10.0, "float"),    # observation gap that ends a track/episode
    "max_episode_seconds": (300.0, 5.0, 3600.0, "float"),   # control clock
    "signal_max_age_seconds": (2.0, 0.1, 30.0, "float"),    # gesture signal age vs latest observation
    "min_person_confidence": (0.5, 0.0, 1.0, "float"),
    "min_motor_confidence": (0.5, 0.0, 1.0, "float"),
    "approach_distance": (0.05, 0.0, 0.5, "float"),         # person-motor box edge distance
    "approach_observations": (3, 1, 50, "int"),
    "parked_motion": (0.02, 0.0, 0.5, "float"),             # max motor centre drift over history
    "parked_min_observations": (3, 1, 50, "int"),
    "parked_history": (8, 1, 100, "int"),
    "co_cross_window_seconds": (1.5, 0.0, 10.0, "float"),   # person vs motor crossing time difference
    "co_move_distance": (0.15, 0.0, 1.0, "float"),          # person-motor centre distance while entering
    "co_move_observations": (3, 1, 50, "int"),
    "pairing_window_seconds": (3.0, 0.1, 30.0, "float"),
    "candidate_ttl_seconds": (2.0, 0.1, 30.0, "float"),     # control clock
    "alert_ttl_seconds": (5.0, 0.1, 60.0, "float"),         # control clock
    "max_tracks": (64, 1, 512, "int"),
    "max_episodes": (16, 1, 256, "int"),
    "alert_queue_limit": (8, 1, 256, "int"),
    "max_candidates": (4, 1, 64, "int"),
}


@dataclasses.dataclass(frozen=True)
class PrivateParkingThresholds:
    values: tuple[tuple[str, float], ...]

    def __getattr__(self, name: str) -> float:
        for key, value in object.__getattribute__(self, "values"):
            if key == name:
                return value
        raise AttributeError(name)


def make_thresholds(**overrides: object) -> PrivateParkingThresholds:
    unknown = set(overrides) - set(THRESHOLD_SPECS)
    if unknown:
        raise ValueError("unknown threshold")
    values: dict[str, float] = {}
    for name, (default, low, high, kind) in THRESHOLD_SPECS.items():
        value = overrides.get(name, default)
        if kind == "int":
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        elif not _is_number(value) or not low <= value <= high:
            raise ValueError(f"{name} must be a finite number in [{low}, {high}]")
        values[name] = value if kind == "int" else float(value)
    if values["max_crossing_gap_seconds"] > values["lost_tolerance_seconds"]:
        raise ValueError("max_crossing_gap_seconds must not exceed lost_tolerance_seconds")
    if values["co_cross_window_seconds"] > values["pairing_window_seconds"]:
        raise ValueError("co_cross_window_seconds must not exceed pairing_window_seconds")
    if values["parked_min_observations"] > values["parked_history"]:
        raise ValueError("parked_min_observations must not exceed parked_history")
    return PrivateParkingThresholds(tuple(values.items()))


@dataclasses.dataclass(frozen=True)
class PrivateParkingConfig:
    line: AccessLine
    zones: tuple[MotorZone, ...]
    thresholds: PrivateParkingThresholds

    def __post_init__(self) -> None:
        if not isinstance(self.line, AccessLine) or not isinstance(self.thresholds, PrivateParkingThresholds):
            raise ValueError("invalid config parts")
        zones = tuple(self.zones) if isinstance(self.zones, (tuple, list)) else None
        if (zones is None or not 1 <= len(zones) <= MAX_ZONES
                or not all(isinstance(zone, MotorZone) for zone in zones)
                or len({zone.name for zone in zones}) != len(zones)):
            raise ValueError("between 1 and 8 uniquely named zones are required")
        object.__setattr__(self, "zones", zones)
        inside = abs(self.line.signed_distance(self.line.inside_x, self.line.inside_y))
        if inside <= 2 * self.thresholds.line_hysteresis or inside < 0.02:
            raise ValueError("inside_point is too close to the access line")


def parse_private_parking_config(raw: object) -> PrivateParkingConfig | None:
    """Parse ``{"line": [x1,y1,x2,y2], "inside_point": [x,y], "zones": [{"name", "rect"}],
    "thresholds": {...}}``. Fail-closed: any violation returns None (no guessing)."""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CONFIG_BYTES:
        return None

    def reject_constant(_constant: str) -> object:
        raise ValueError("non-finite constant")

    def unique_object(pairs: list) -> dict:
        document: dict = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("duplicate key")
            document[key] = value
        return document

    try:
        document = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object)
        if not isinstance(document, dict) or not {"line", "inside_point", "zones"} <= set(document) \
                or not set(document) <= {"line", "inside_point", "zones", "thresholds"}:
            return None
        line, inside, zones = document["line"], document["inside_point"], document["zones"]
        if (not isinstance(line, list) or len(line) != 4 or not isinstance(inside, list) or len(inside) != 2
                or not isinstance(zones, list)):
            return None
        parsed_zones = []
        for zone in zones:
            if not isinstance(zone, dict) or set(zone) != {"name", "rect"}:
                return None
            rect = zone["rect"]
            if not isinstance(rect, list) or len(rect) != 4:
                return None
            parsed_zones.append(MotorZone(zone["name"], *rect))
        thresholds = document.get("thresholds", {})
        if not isinstance(thresholds, dict):
            return None
        return PrivateParkingConfig(AccessLine(*line, *inside), tuple(parsed_zones), make_thresholds(**thresholds))
    except (ValueError, TypeError, RecursionError):
        return None


class EpisodeOrigin(str, enum.Enum):
    CROSSED_IN = "CROSSED_IN"          # observed outside -> inside through the segment
    UNKNOWN_INSIDE = "UNKNOWN_INSIDE"  # first seen inside: origin unknown, NOT considered authorized


class EpisodeState(str, enum.Enum):
    ENTERED = "ENTERED"          # crossed in, no authorization
    AUTHORIZED = "AUTHORIZED"    # valid gesture signal for this episode only
    TRIGGERED = "TRIGGERED"      # event issued; never re-triggers


@dataclasses.dataclass(frozen=True)
class AuthorizationSignal:
    """A gesture detector observation. Not identity, not ownership."""

    session_id: str
    track_id: int
    observed_at: float           # observation timeline
    episode_id: str | None = None


@dataclasses.dataclass(frozen=True)
class ProximityEvidence:
    episode_id: str
    zone_name: str
    crossed_in_at: float
    approach_observations: int
    min_edge_distance: float
    entered_with_motor_track_id: int | None


@dataclasses.dataclass(frozen=True)
class LocalProximityAlert:
    """Local warning request; shares event_id with the evaluation candidate."""

    event_id: str
    source_session_id: str
    episode_id: str
    person_track_id: int
    motor_track_id: int
    zone_name: str
    triggered_at: float          # observation timeline
    issued_at: float             # control clock


@dataclasses.dataclass(frozen=True)
class PrivateParkingCandidate:
    """Evaluation candidate in the PARKING_MOTORCYCLE domain (never an assessment)."""

    event_id: str
    source_session_id: str
    domain: DomainName
    person_track_id: int
    object_track_id: int | None
    started_at: float            # crossing time (observation timeline)
    triggered_at: float          # approach confirmation (observation timeline)
    evidence: ProximityEvidence


@dataclasses.dataclass(frozen=True)
class EpisodeView:
    episode_id: str
    person_track_id: int
    state: EpisodeState
    entered_with_motor_track_id: int | None


@dataclasses.dataclass(frozen=True)
class PrivateParkingDiagnostic:
    source_session_id: str | None
    pending_event_id: str | None
    episodes: tuple[EpisodeView, ...]
    queued_alerts: int
    queued_candidates: int
    counts: tuple[tuple[str, int], ...]

    def count(self, name: str) -> int:
        return dict(self.counts)[name]
