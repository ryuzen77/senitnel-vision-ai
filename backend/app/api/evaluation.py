"""Authenticated callback boundary; coordinator wiring is a later phase."""

from __future__ import annotations

import hmac
import os
import time
from typing import Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from app.models.evaluation import CallbackDisposition, EvaluationCallback, EvaluationCallbackPayload


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


class CallbackReceipt(BaseModel):
    status: Literal["accepted", "duplicate"]


def get_callback_consumer() -> CallbackConsumer | None:
    """No production consumer exists yet; tests inject a fake via Depends."""
    return None


def get_callback_token() -> str:
    return os.getenv("SENTINEL_CALLBACK_TOKEN", "")


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
