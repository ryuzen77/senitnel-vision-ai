"""Read-only API for the current session domain classification."""

from __future__ import annotations

from fastapi import APIRouter

from app.models.domain import DomainSnapshotResponse
from app.services.domain_service import domain_service

router = APIRouter(prefix="/domain", tags=["domain"])


@router.get("/current", response_model=DomainSnapshotResponse)
async def get_current_domain() -> DomainSnapshotResponse:
    """Return the singleton classifier snapshot without changing its state."""
    snapshot = domain_service.get_snapshot()
    return DomainSnapshotResponse(
        source_session_id=snapshot.source_session_id,
        state=snapshot.state,
        domain=snapshot.domain,
        observation_count=snapshot.observation_count,
        evidence_hits=snapshot.evidence_hits,
        confidence=snapshot.confidence,
        locked=snapshot.locked,
        last_detection_seq=snapshot.last_detection_seq,
    )
