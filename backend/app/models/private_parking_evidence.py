"""Contracts for per-episode evidence of the private-parking mode (RAM only).

Evidence is five distinct, ordered JPEG frames that follow the episode's
progress: one near the observed line crossing, three journey frames between
crossing and approach, one near the confirmed approach. Frames are chosen by
timestamps and explicit trigger markers only; nothing here recognises actions,
threat, authorization or identity.

Evidence only feeds the cloud evaluation. It is never a precondition for the
local warning. On T3, missing or expired private evidence must fail the cloud
evaluation for that event; it must NOT fall back to the time-offset bundle of
the legacy parking path.

All limits are provisional and uncalibrated.
"""

from __future__ import annotations

import dataclasses
import enum
import math

EVIDENCE_FRAME_COUNT = 5
# Plans derived from sealed evidence reproduce each actual timestamp as
# triggered_at + offset. Float rounding can make that sum differ from the
# timestamp in the last bits, so the plan carries this explicit tolerance and
# each frame reports its computed deviation (normally 0 or ~1e-15).
EVIDENCE_OFFSET_EPSILON = 1e-6


class EvidenceRole(str, enum.Enum):
    CROSSING = "CROSSING"
    JOURNEY = "JOURNEY"
    APPROACH = "APPROACH"


class EvidenceFailure(str, enum.Enum):
    CROSSING_ANCHOR_MISSING = "CROSSING_ANCHOR_MISSING"
    APPROACH_ANCHOR_MISSING = "APPROACH_ANCHOR_MISSING"
    INSUFFICIENT_JOURNEY = "INSUFFICIENT_JOURNEY"
    CAPACITY = "CAPACITY"
    EXPIRED = "EXPIRED"
    UNKNOWN_EPISODE = "UNKNOWN_EPISODE"


def _number(name: str, value: object, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number in [{low}, {high}]")
    return float(value)


def _integer(name: str, value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


@dataclasses.dataclass(frozen=True)
class EvidenceSamplerLimits:
    """Validated bounds. Byte limits cover JPEG bytes the sampler still references
    (each unique frame once), not process memory or encoding buffers elsewhere."""

    preroll_seconds: float = 3.0          # observation timeline
    preroll_max_frames: int = 120
    marker_tolerance_seconds: float = 0.3
    journey_capacity: int = 12            # thinned samples per episode (plus one "latest" slot)
    max_episodes: int = 16
    max_frame_bytes: int = 512 * 1024
    max_total_bytes: int = 24 * 1024 * 1024
    episode_max_age_seconds: float = 300.0   # control clock
    sealed_ttl_seconds: float = 30.0         # control clock
    max_sealed: int = 8
    max_failure_records: int = 32

    def __post_init__(self) -> None:
        _number("preroll_seconds", self.preroll_seconds, 0.5, 30.0)
        _integer("preroll_max_frames", self.preroll_max_frames, 2, 2000)
        _number("marker_tolerance_seconds", self.marker_tolerance_seconds, 0.0, 2.0)
        _integer("journey_capacity", self.journey_capacity, 4, 256)
        if self.journey_capacity % 2:
            raise ValueError("journey_capacity must be even (binary thinning)")
        _integer("max_episodes", self.max_episodes, 1, 256)
        _integer("max_frame_bytes", self.max_frame_bytes, 1, 16 * 1024 * 1024)
        _integer("max_total_bytes", self.max_total_bytes, 1, 512 * 1024 * 1024)
        if self.max_frame_bytes > self.max_total_bytes:
            raise ValueError("max_frame_bytes must not exceed max_total_bytes")
        _number("episode_max_age_seconds", self.episode_max_age_seconds, 1.0, 3600.0)
        _number("sealed_ttl_seconds", self.sealed_ttl_seconds, 0.1, 600.0)
        _integer("max_sealed", self.max_sealed, 1, 256)
        _integer("max_failure_records", self.max_failure_records, 1, 1024)
        if self.marker_tolerance_seconds >= self.preroll_seconds:
            raise ValueError("marker tolerance must be shorter than the pre-roll window")


@dataclasses.dataclass(frozen=True)
class EvidenceFrame:
    role: EvidenceRole
    source_frame_seq: int
    source_timestamp: float          # actual, never replaced by a target
    actual_offset_seconds: float     # source_timestamp - triggered_at
    frame_width: int
    frame_height: int
    jpeg: bytes = dataclasses.field(repr=False)


@dataclasses.dataclass(frozen=True)
class SealedEvidence:
    event_id: str
    source_session_id: str
    episode_id: str
    triggered_at: float
    frames: tuple[EvidenceFrame, ...]

    @property
    def total_jpeg_bytes(self) -> int:
        return sum(len(frame.jpeg) for frame in self.frames)


@dataclasses.dataclass(frozen=True)
class SealResult:
    evidence: SealedEvidence | None
    failure: EvidenceFailure | None


@dataclasses.dataclass(frozen=True)
class SamplerDiagnostic:
    """No images, no identities."""

    source_session_id: str | None
    active_episodes: int
    sealed: int
    failure_records: int
    held_frames: int
    held_bytes: int
    counts: tuple[tuple[str, int], ...]

    def count(self, name: str) -> int:
        return dict(self.counts)[name]
