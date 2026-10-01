"""Authenticated callback boundary for the process-local evaluation worker."""

from __future__ import annotations

import hmac
import os
import time
from typing import Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from app.models.evaluation import (
    CallbackDisposition, CallbackSubjectProfile, EvaluationAssessmentResponse,
    EvaluationCallback, EvaluationCallbackPayload, EvaluationCurrentResponse,
    EvaluationCurrentView, EvaluationLatestResultResponse,
)


MAX_CALLBACK_BODY_BYTES = 16 * 1024
router = APIRouter(prefix="/evaluation", tags=["evaluation"])


class CallbackConsumer(Protocol):
    """Accept only the current pending event/session, without I/O under its lock.

    ACCEPTED publishes the first validated result. DUPLICATE applies only to an
    identical replay in the same active session; it must not publish again.
    A conflicting replay, stale session, or missing/expired pending event is
    REJECTED. The consumer must never let a later callback overwrite a result.
    """

    def accept_callback(self, callback: EvaluationCallback, now: float) -> CallbackDisposition: ...


class CurrentEvaluationReader(Protocol):
    def get_current_view(self) -> EvaluationCurrentView: ...


class CallbackReceipt(BaseModel):
    status: Literal["accepted", "duplicate"]


def get_callback_consumer() -> CallbackConsumer | None:
    """Return the one process-local worker only while evaluation is enabled."""
    from app.services.runtime import evaluation_enabled, evaluation_worker

    return evaluation_worker if evaluation_enabled else None


def get_callback_token() -> str:
    return os.getenv("SENTINEL_CALLBACK_TOKEN", "")


def get_current_evaluation_reader() -> CurrentEvaluationReader | None:
    """Use the callback worker singleton, including when dispatch is disabled."""
    try:
        from app.services.runtime import evaluation_worker
    except Exception:
        return None
    return evaluation_worker


@router.get("/current", response_model=EvaluationCurrentResponse)
async def get_current_evaluation(
    reader: CurrentEvaluationReader | None = Depends(get_current_evaluation_reader),
) -> EvaluationCurrentResponse:
    if reader is None:
        raise HTTPException(status_code=503, detail="Evaluation status unavailable")
    try:
        view = await run_in_threadpool(reader.get_current_view)
        latest = None
        if view.latest_result is not None:
            record = view.latest_result
            assessment = record.assessment
            assessment.validate()
            profile = assessment.subject_profile
            latest = EvaluationLatestResultResponse(
                event_id=record.event_id,
                source_session_id=record.source_session_id,
                assessment=EvaluationAssessmentResponse(
                    threatScore=assessment.threat_score,
                    status=assessment.status,
                    reasoning=assessment.reasoning,
                    suspectProfile=CallbackSubjectProfile(
                        personDetected=profile.person_detected,
                        clothing=profile.clothing,
                        observedAction=profile.observed_action,
                        visibleObjects=profile.visible_objects,
                        distinguishingFeatures=profile.distinguishing_features,
                    ),
                ),
            )
        return EvaluationCurrentResponse(
            enabled=view.enabled,
            source_session_id=view.source_session_id,
            process_state=view.process_state,
            active_event_id=view.active_event_id,
            latest_result=latest,
        )
    except Exception:
        raise HTTPException(status_code=503, detail="Evaluation status unavailable") from None


@router.post("/callback", response_model=CallbackReceipt)
async def receive_evaluation_callback(
    request: Request,
    consumer: CallbackConsumer | None = Depends(get_callback_consumer),
    expected_token: str = Depends(get_callback_token),
) -> CallbackReceipt:
    """Validate and forward metadata only; never infer or publish a result here.

    Authentication is a static shared token, not a body HMAC. Deployment must
    use HTTPS and a long random token; pending-event correlation handles replay.
    """
    if not expected_token:
        raise HTTPException(status_code=503, detail="Callback not configured")
    supplied_token = request.headers.get("x-sentinel-callback-token", "")
    if not hmac.compare_digest(supplied_token.encode("utf-8"), expected_token.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Unauthorized callback")
    if consumer is None:
        raise HTTPException(status_code=503, detail="Callback receiver unavailable")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="JSON body required")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_CALLBACK_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Callback body too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid content length") from None

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_CALLBACK_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Callback body too large")
        body.extend(chunk)
    try:
        callback = EvaluationCallbackPayload.model_validate_json(bytes(body)).to_callback()
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid callback payload") from None

    try:
        disposition = await run_in_threadpool(consumer.accept_callback, callback, time.monotonic())
    except Exception:
        raise HTTPException(status_code=503, detail="Callback receiver unavailable") from None
    if disposition is CallbackDisposition.ACCEPTED:
        return CallbackReceipt(status="accepted")
    if disposition is CallbackDisposition.DUPLICATE:
        return CallbackReceipt(status="duplicate")
    raise HTTPException(status_code=409, detail="Callback not active")
