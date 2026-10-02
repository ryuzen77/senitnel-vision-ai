"""Production composition root for the single-worker application process."""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time

from app.models.evaluation import EvaluationRequest
from app.services.clip_scene_runner import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_REVISION,
    make_clip_scene_runner_factory,
)
from app.services.detector_service import DetectorService
from app.services.domain_service import domain_service
from app.services.evaluation_budget import EvaluationBudget
from app.services.evaluation_worker import MAX_DISPATCHES_LIMIT, EvaluationWorker
from app.services.event_frame_buffer import EventFrameBuffer
from app.services.langflow_evaluation_client import LangflowDispatchRejected, LangflowEvaluationClient
from app.services.scene_domain_classifier import SceneDomainClassifier
from app.services.parking_candidate_trigger import ParkingCandidateTrigger
from app.services.tracker_service import TrackerService
from app.services.video_service import VideoService

logger = logging.getLogger(__name__)


class _UnavailableDispatcher:
    """Never called: the disabled worker does not claim candidates or budget."""

    name = "evaluation-disabled"

    def dispatch(self, request: EvaluationRequest) -> None:
        raise RuntimeError("External evaluation is disabled")


def _create_langflow_client() -> LangflowEvaluationClient | None:
    if not os.getenv("SENTINEL_LANGFLOW_URL", "").strip():
        return None
    try:
        return LangflowEvaluationClient()
    except LangflowDispatchRejected:
        logger.warning("Langflow evaluation disabled: invalid transport configuration")
        return None


def _external_evaluation_enabled(client: LangflowEvaluationClient | None) -> bool:
    url_configured = bool(os.getenv("SENTINEL_LANGFLOW_URL", "").strip())
    token_configured = bool(os.getenv("SENTINEL_CALLBACK_TOKEN", "").strip())
    if url_configured and not token_configured:
        logger.warning("External evaluation disabled: callback token is missing")
    elif token_configured and not url_configured:
        logger.warning("External evaluation disabled: Langflow URL is missing")
    return client is not None and token_configured


def _evaluation_callback_timeout_seconds() -> float | None:
    raw = os.getenv("SENTINEL_EVALUATION_CALLBACK_TIMEOUT_SECONDS", "45")
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        timeout = None
    if timeout is None or not math.isfinite(timeout) or not 0 < timeout <= 60:
        logger.warning("External evaluation disabled: invalid callback timeout configuration")
        return None
    return timeout


def _evaluation_max_dispatches() -> tuple[bool, int | None]:
    """Return (valid, limit). Unset means unlimited; invalid disables evaluation.

    The limit counts dispatch attempts for the lifetime of this process only.
    """
    raw = os.getenv("SENTINEL_EVALUATION_MAX_DISPATCHES")
    if raw is None:
        return True, None
    if (re.fullmatch(r"[0-9]{1,4}", raw) is None
            or not 1 <= int(raw) <= MAX_DISPATCHES_LIMIT):
        logger.warning("External evaluation disabled: invalid dispatch limit configuration")
        return False, None
    return True, int(raw)

# This is the one shared production GPU gate.  It is injected, never looked up
# by worker modules, so capture/MJPEG paths cannot acquire it accidentally.
shared_cuda_gate = threading.Lock()

tracker_service = TrackerService()
parking_candidate_trigger = ParkingCandidateTrigger()
detector_service = DetectorService(
    result_consumer=domain_service,
    tracker=tracker_service,
    parking_trigger=parking_candidate_trigger,
    domain_snapshot_provider=domain_service.get_snapshot,
    cuda_gate=shared_cuda_gate,
)
scene_domain_classifier = SceneDomainClassifier(
    domain_service=domain_service,
    runner_factory=make_clip_scene_runner_factory(shared_cuda_gate),
    clock=time.monotonic,
    scene_model=f"{DEFAULT_MODEL_ID}@{DEFAULT_MODEL_REVISION}",
)
event_frame_buffer = EventFrameBuffer()
evaluation_callback_timeout_seconds = _evaluation_callback_timeout_seconds()
effective_callback_timeout_seconds = evaluation_callback_timeout_seconds or 45.0
evaluation_budget = EvaluationBudget(
    pending_timeout_seconds=effective_callback_timeout_seconds + 15,
)
langflow_client = _create_langflow_client()
evaluation_max_dispatches_valid, evaluation_max_dispatches = _evaluation_max_dispatches()
# A callback token is required before spending an external evaluation request.
evaluation_enabled = (_external_evaluation_enabled(langflow_client)
                      and evaluation_callback_timeout_seconds is not None
                      and evaluation_max_dispatches_valid)
evaluation_worker = EvaluationWorker(
    parking_candidate_trigger, evaluation_budget, event_frame_buffer,
    langflow_client if langflow_client is not None else _UnavailableDispatcher(),
    callback_timeout_seconds=effective_callback_timeout_seconds,
    enabled=evaluation_enabled,
    max_dispatches=evaluation_max_dispatches,
)
video_service = VideoService(
    detector=detector_service,
    scene_classifier=scene_domain_classifier,
    event_frame_buffer=event_frame_buffer,
    evaluation_worker=evaluation_worker,
)


def shutdown_services(
    video, scene, detector, parking_trigger, tracker, frame_buffer=None,
    *, evaluation_worker=None, langflow_client=None, evaluation_budget=None,
) -> None:  # noqa: ANN001
    """Stop the worker before its client; in-flight request errors stay isolated."""
    video.stop()
    if evaluation_worker is not None:
        evaluation_worker.close()
    if langflow_client is not None:
        langflow_client.close()
    scene.close()
    detector.close()
    parking_trigger.close()
    tracker.close()
    if frame_buffer is not None:
        frame_buffer.close()
    if evaluation_budget is not None:
        evaluation_budget.close()
