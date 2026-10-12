"""Wire models of ``/api/video/sources`` (private-parking plan §5, §18.2 item 6; P1c).

Frozen for the frontend (P1d): every 4xx/5xx body is ``{category, field?}`` (plus
``source_id``/``generation`` for ``503 cleanup_pending``); every Play that passed the CAS
answers ``200`` with an :class:`AttemptRecordModel`, ``REFUSED`` included. No path, file
name, configuration value, token or lease appears in any model here.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.services.prepared_source import AttemptRecord, CurrentSnapshot


class SessionModel(BaseModel):
    phase: str                       # NONE | RUNNING | ENDING | ENDED
    end_reason: str | None           # stop | eof | error | refused | cancelled | frame_size_changed
    cleanup_ok: bool | None


class AttemptRecordModel(BaseModel):
    attempt: int
    operation_id: str
    outcome: str                     # PENDING | STARTED | REFUSED | FAILED | TIMED_OUT | CANCELLED
    reason: str | None
    session_ref: str | None
    session_id: str | None
    session: SessionModel

    @classmethod
    def of(cls, record: AttemptRecord) -> "AttemptRecordModel":
        return cls(attempt=record.attempt, operation_id=record.operation_id, outcome=record.outcome.value,
                   reason=record.reason, session_ref=record.session_ref, session_id=record.session_id,
                   session=SessionModel(phase=record.phase.name, end_reason=record.end_reason,
                                        cleanup_ok=record.cleanup_ok))


class ConfigStateModel(BaseModel):
    revision: int
    ready: bool


class CleanupModel(BaseModel):
    pending: bool
    reason: str | None
    attempts: int
    capture: str | None              # CREATING | OWNED | RELEASE_PENDING | RELEASE_STALLED | null
    capture_attempts: int


class AnalysisModel(BaseModel):
    """Transient observation of VideoService, read outside the store lock. Never a
    readiness input: ``config.ready`` and the Play answer stay authoritative."""

    stop_required: bool
    state: str
    session_ref: str | None
    session_id: str | None


class CurrentModel(BaseModel):
    source_id: str | None
    generation: int | None
    state: str
    upload_operation_id: str | None
    frame_width: int | None
    frame_height: int | None
    preview_revision: int | None
    config: ConfigStateModel | None
    geometry_unstable: bool
    template_available: bool
    next_attempt: int | None
    last_attempt: AttemptRecordModel | None
    expires_in_seconds: float | None
    deadline_in_seconds: float | None
    cleanup: CleanupModel
    analysis: AnalysisModel | None

    @classmethod
    def of(cls, snap: CurrentSnapshot, *, template_available: bool,
           analysis: AnalysisModel | None) -> "CurrentModel":
        config = (ConfigStateModel(revision=snap.config_revision, ready=snap.config_ready)
                  if snap.config_revision is not None else None)
        return cls(source_id=snap.source_id, generation=snap.generation, state=snap.state.value,
                   upload_operation_id=snap.upload_operation_id, frame_width=snap.frame_width,
                   frame_height=snap.frame_height, preview_revision=snap.preview_revision, config=config,
                   geometry_unstable=snap.geometry_unstable, template_available=template_available,
                   next_attempt=snap.next_attempt,
                   last_attempt=AttemptRecordModel.of(snap.last_attempt) if snap.last_attempt else None,
                   expires_in_seconds=snap.expires_in_seconds, deadline_in_seconds=snap.deadline_in_seconds,
                   cleanup=CleanupModel(pending=snap.cleanup_pending, reason=snap.cleanup_reason,
                                        attempts=snap.cleanup_attempts, capture=snap.capture_state,
                                        capture_attempts=snap.capture_attempts),
                   analysis=analysis)


class CreatedModel(BaseModel):
    source_id: str
    generation: int
    state: str


class PreparedModel(BaseModel):
    state: str
    frame_width: int
    frame_height: int
    preview_revision: int


class LimitsModel(BaseModel):
    max_upload_bytes: int
    accepted_types: list[str]
    max_zones: int
    min_line_length: float
    min_zone_area: float
    max_configuration_bytes: int


class CapabilitiesModel(BaseModel):
    mode: str                        # legacy | private | disabled
    preparation_available: bool
    source_kinds: list[str]
    template_available: bool
    evaluation_enabled: bool
    limits: LimitsModel


class TemplateModel(BaseModel):
    line: dict
    inside: str
    zones: list[dict]
    frame_width: int
    frame_height: int
    confirmed: bool


class ConfigurationSavedModel(BaseModel):
    revision: int
    ready: bool
    canonical: dict


class DeletingModel(BaseModel):
    state: str


class CleanupRetryModel(BaseModel):
    cleanup: dict
