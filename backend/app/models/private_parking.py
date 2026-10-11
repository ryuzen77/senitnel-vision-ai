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
    """Finite real number. An int too large for a float (``math.isfinite`` raises
    OverflowError) is not a number here, so validation fails closed instead of raising."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _unit(value: object, name: str) -> float:
    if not _is_number(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return float(value)


class InsideSide(str, enum.Enum):
    """Inside side seen from P1 towards P2 on screen (x right, y down):
    ``left`` <=> ``raw_side < 0``, ``right`` <=> ``raw_side > 0``."""

    LEFT = "left"
    RIGHT = "right"


@dataclasses.dataclass(frozen=True)
class AccessLine:
    """Segment P1-P2 in normalized frame coordinates. The inside side is explicit,
    never inferred: either the side that contains ``inside_point`` (startup file) or
    ``inside`` left/right relative to P1 -> P2 (operator configuration); exactly one."""

    x1: float
    y1: float
    x2: float
    y2: float
    inside_x: float | None = None
    inside_y: float | None = None
    inside: InsideSide | None = None

    def __post_init__(self) -> None:
        for name in ("x1", "y1", "x2", "y2"):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        has_point = self.inside_x is not None or self.inside_y is not None
        if has_point == (self.inside is not None):
            raise ValueError("exactly one of inside_point or inside side is required")
        if has_point:
            for name in ("inside_x", "inside_y"):
                object.__setattr__(self, name, _unit(getattr(self, name), name))
        else:
            if not isinstance(self.inside, str):
                raise ValueError("inside must be left or right")
            object.__setattr__(self, "inside", InsideSide(self.inside))
        if self.length < MIN_LINE_LENGTH:
            raise ValueError("access line is degenerate")

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    def raw_side(self, x: float, y: float) -> float:
        return ((self.x2 - self.x1) * (y - self.y1) - (self.y2 - self.y1) * (x - self.x1)) / self.length

    def signed_distance(self, x: float, y: float) -> float:
        """Distance to the infinite line; positive on the inside."""
        return (1.0 if self.inside_side is InsideSide.RIGHT else -1.0) * self.raw_side(x, y)

    @property
    def inside_side(self) -> InsideSide:
        if self.inside is not None:
            return self.inside
        return InsideSide.RIGHT if self.raw_side(self.inside_x, self.inside_y) > 0 else InsideSide.LEFT

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
    "marker_queue_limit": (64, 4, 1024, "int"),               # overflow -> authoritative resync
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
        if self.line.inside is None:
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


MAX_FRAME_SIDE = 16384


class InvalidGeometry(ValueError):
    """Operator configuration rejected; ``field`` is a safe path (no values)."""

    def __init__(self, field: str) -> None:
        super().__init__("invalid_geometry")
        self.field = field


@dataclasses.dataclass(frozen=True)
class OperatorConfiguration:
    """A configuration drawn by the operator on a decoded frame of ``frame_width`` x
    ``frame_height`` pixels (coordinates normalized to that frame). Inside is left/right
    relative to P1 -> P2; there is no ``inside_point``."""

    frame_width: int
    frame_height: int
    config: PrivateParkingConfig

    def canonical(self) -> dict:
        line = self.config.line
        return {
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "line": {"p1": [line.x1, line.y1], "p2": [line.x2, line.y2]},
            "inside": line.inside_side.value,
            "zones": [{"name": zone.name, "rect": [zone.x1, zone.y1, zone.x2, zone.y2]}
                      for zone in self.config.zones],
        }


def _point(value: object, field: str) -> tuple[float, float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(_is_number(item) and 0.0 <= item <= 1.0 for item in value)):
        raise InvalidGeometry(field)
    return float(value[0]), float(value[1])


def build_operator_configuration(*, frame_width: object, frame_height: object, line: object,
                                 inside: object, zones: object,
                                 thresholds: PrivateParkingThresholds | None = None) -> OperatorConfiguration:
    """Validate an operator configuration (fail-closed, field path on error).

    ``line`` is ``{"p1": [x, y], "p2": [x, y]}``; ``zones`` is a list of
    ``{"name", "rect": [x1, y1, x2, y2]}``. Thresholds are not operator-editable:
    the template's thresholds or the defaults."""
    for field, side in (("frame_width", frame_width), ("frame_height", frame_height)):
        if type(side) is not int or not 1 <= side <= MAX_FRAME_SIDE:
            raise InvalidGeometry(field)
    if not isinstance(line, dict) or set(line) != {"p1", "p2"}:
        raise InvalidGeometry("line")
    (x1, y1), (x2, y2) = _point(line["p1"], "line.p1"), _point(line["p2"], "line.p2")
    if not isinstance(inside, str) or inside not in {side.value for side in InsideSide}:
        raise InvalidGeometry("inside")
    try:
        access_line = AccessLine(x1, y1, x2, y2, inside=InsideSide(inside))
    except ValueError:
        raise InvalidGeometry("line") from None
    if not isinstance(zones, list) or not 1 <= len(zones) <= MAX_ZONES:
        raise InvalidGeometry("zones")
    parsed: list[MotorZone] = []
    for index, zone in enumerate(zones):
        field = f"zones[{index}]"
        if not isinstance(zone, dict) or set(zone) != {"name", "rect"}:
            raise InvalidGeometry(field)
        name, rect = zone["name"], zone["rect"]
        if not isinstance(name, str) or _ZONE_NAME.fullmatch(name) is None:
            raise InvalidGeometry(f"{field}.name")
        if any(existing.name == name for existing in parsed):
            raise InvalidGeometry(f"{field}.name")
        if (not isinstance(rect, list) or len(rect) != 4
                or not all(_is_number(item) and 0.0 <= item <= 1.0 for item in rect)):
            raise InvalidGeometry(f"{field}.rect")
        try:
            parsed.append(MotorZone(name, *rect))
        except ValueError:
            raise InvalidGeometry(f"{field}.rect") from None
    result = OperatorConfiguration(frame_width, frame_height, PrivateParkingConfig(
        access_line, tuple(parsed), thresholds if thresholds is not None else make_thresholds()))
    if len(json.dumps(result.canonical()).encode("utf-8")) > MAX_CONFIG_BYTES:
        raise InvalidGeometry("configuration")
    return result


def operator_template(config: PrivateParkingConfig) -> PrivateParkingConfig:
    """A startup configuration as an unconfirmed operator template: ``inside_point``
    becomes ``inside`` by the sign of ``raw_side`` (the parser already rejected points
    too close to the line); zones and thresholds are kept."""
    line = config.line
    return PrivateParkingConfig(AccessLine(line.x1, line.y1, line.x2, line.y2, inside=line.inside_side),
                                config.zones, config.thresholds)


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


@dataclasses.dataclass(frozen=True, order=True)
class CandidateToken:
    """Authority for one prepared candidate. ``serial`` is never reused in a
    process, so a late call with an old token never matches a new candidate,
    even when a session id or event id is reused. ``episode_instance`` binds the
    token to the one episode instance it was issued for (episode ids may repeat;
    instances never do), so it can never seal or change another episode, and
    ``event_id`` binds it to its one event, so it can never seal, release or plan
    another event's evidence."""

    session_generation: int
    serial: int
    episode_instance: int | None = None
    event_id: str | None = None


class CandidateOutcome(str, enum.Enum):
    """Result of ``publish_candidate`` / ``abandon_candidate`` (and the tombstone kept
    for a closed preparing candidate)."""

    PUBLISHED = "PUBLISHED"            # claimable by the worker
    EXPIRED = "EXPIRED"                # deadline passed before publication
    ABANDONED = "ABANDONED"            # evidence failed; never claimable
    REJECTED_FULL = "REJECTED_FULL"    # candidate store was full at issue time
    STALE = "STALE"                    # token from another session generation
    UNKNOWN = "UNKNOWN"                # no record (never issued, or tombstone evicted)


class MarkerKind(str, enum.Enum):
    EPISODE_ENTERED = "EPISODE_ENTERED"
    EPISODE_AUTHORIZED = "EPISODE_AUTHORIZED"
    EPISODE_TRIGGERED = "EPISODE_TRIGGERED"
    EPISODE_ENDED = "EPISODE_ENDED"
    CANDIDATE_CLOSED = "CANDIDATE_CLOSED"        # preparing candidate closed without publication
    CANDIDATE_EXPIRED = "CANDIDATE_EXPIRED"      # published candidate expired unclaimed
    CANDIDATE_FINISHED = "CANDIDATE_FINISHED"    # claimed candidate completed or failed


@dataclasses.dataclass(frozen=True)
class EpisodeMarker:
    """Lifecycle fact for the evidence sampler. ``seq`` is strictly increasing per
    trigger session generation; ``keep_samples`` marks an ended episode whose
    candidate is still waiting for its seal. ``episode_instance`` identifies the
    episode instance (unique in the process); the sampler acts only on that
    instance, never on another one that reuses ``episode_id``."""

    seq: int
    kind: MarkerKind
    episode_id: str
    revision: int
    crossed_in_at: float
    token: CandidateToken | None = None
    keep_samples: bool = False
    episode_instance: int | None = None


@dataclasses.dataclass(frozen=True)
class EvidenceView:
    """Authoritative state after a marker overflow (replaces the lost markers).

    ``entered``: (episode_id, episode_instance, revision, crossed_in_at) of episodes
    that still need working samples; ``preparing``: (episode_id, token) of triggered
    episodes whose candidate waits for its seal (the token names the instance; keep
    samples, never re-begin); ``live_tokens``: every preparing, published or claimed
    token (other sealed evidence may go)."""

    entered: tuple[tuple[str, int, int, float], ...]
    preparing: tuple[tuple[str, CandidateToken], ...]
    live_tokens: frozenset[CandidateToken]


@dataclasses.dataclass(frozen=True)
class MarkerBatch:
    session_id: str | None
    session_generation: int
    upto_seq: int                      # every marker of this generation with seq <= upto_seq is covered
                                       # (a view takes its own newer seq)
    markers: tuple[EpisodeMarker, ...]
    view: EvidenceView | None = None   # set (and markers empty) after an overflow


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
    token: CandidateToken | None = None     # authority for seal/publish/abandon/release


@dataclasses.dataclass(frozen=True)
class PrivateParkingCandidate:
    """Evaluation candidate in the PARKING_MOTORCYCLE domain (never an assessment).

    The token stays outside ``evidence`` so it never reaches a provider payload."""

    event_id: str
    source_session_id: str
    domain: DomainName
    person_track_id: int
    object_track_id: int | None
    started_at: float            # crossing time (observation timeline)
    triggered_at: float          # approach confirmation (observation timeline)
    evidence: ProximityEvidence
    token: CandidateToken | None = None


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
    queued_candidates: int                   # published (claimable or claimed)
    counts: tuple[tuple[str, int], ...]
    preparing_candidates: int = 0            # waiting for seal/publication, never claimable
    queued_markers: int = 0
    resync_required: bool = False
    pending_token: CandidateToken | None = None   # identity of the claimed candidate (with pending_event_id)

    def count(self, name: str) -> int:
        return dict(self.counts)[name]
