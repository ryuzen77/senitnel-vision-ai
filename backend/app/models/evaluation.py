"""Internal, metadata-safe contracts for local evaluation orchestration."""

from __future__ import annotations

import dataclasses
import enum

from app.models.domain import DomainName
from app.models.event_frame import EventFrameBundle


class ThreatStatus(str, enum.Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclasses.dataclass(frozen=True)
class SubjectProfile:
    person_detected: bool
    clothing: str
    observed_action: str
    visible_objects: tuple[str, ...]
    distinguishing_features: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class ThreatAssessment:
    threat_score: int
    status: ThreatStatus
    reasoning: str
    subject_profile: SubjectProfile

    def validate(self) -> None:
        if type(self.threat_score) is not int or not 0 <= self.threat_score <= 100:
            raise ValueError("threat_score must be an integer from 0 to 100")
        expected = (ThreatStatus.NORMAL if self.threat_score <= 34 else
                    ThreatStatus.WARNING if self.threat_score <= 64 else ThreatStatus.CRITICAL)
        if self.status is not expected:
            raise ValueError("status is inconsistent with threat_score")
        if not isinstance(self.reasoning, str) or not self.reasoning.strip():
            raise ValueError("reasoning must be nonempty")
        profile = self.subject_profile
        if not isinstance(profile, SubjectProfile) or type(profile.person_detected) is not bool:
            raise ValueError("subject_profile.person_detected must be bool")
        if not isinstance(profile.clothing, str) or not isinstance(profile.observed_action, str):
            raise ValueError("subject_profile descriptions must be strings")
        for name in ("visible_objects", "distinguishing_features"):
            value = getattr(profile, name)
            if not isinstance(value, tuple) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"subject_profile.{name} must be tuple[str, ...]")


@dataclasses.dataclass(frozen=True)
class EvaluationRequest:
    event_id: str
    source_session_id: str
    domain: DomainName
    started_at: float
    triggered_at: float
    person_track_id: int
    object_track_id: int
    evidence: object
    frame_bundle: EventFrameBundle = dataclasses.field(repr=False)


@dataclasses.dataclass(frozen=True)
class EvaluationRecord:
    evaluation_seq: int
    event_id: str
    source_session_id: str
    domain: DomainName
    requested_at: float
    completed_at: float
    assessment: ThreatAssessment
    frame_source_sequences: tuple[int, ...]
    client_name: str


class EvaluationFailure(str, enum.Enum):
    NO_STRATEGY = "NO_STRATEGY"
    BUDGET_DENIED = "BUDGET_DENIED"
    BUNDLE_TIMEOUT = "BUNDLE_TIMEOUT"
    BUNDLE_INVALID = "BUNDLE_INVALID"
    STALE_SESSION = "STALE_SESSION"
    CLIENT_ERROR = "CLIENT_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    CLOSED = "CLOSED"


@dataclasses.dataclass(frozen=True)
class WorkerSnapshot:
    running: bool
    closed: bool
    active_event_id: str | None
    latest_event_id: str | None
    published_count: int
    discarded_stale_count: int
    failure_counts: tuple[tuple[EvaluationFailure, int], ...]
    last_failure: EvaluationFailure | None
    last_budget_denial_reason: str | None
