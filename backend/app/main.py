from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api import evaluation as evaluation_router
from app.api import domain as domain_router
from app.api import detections as detections_router
from app.api import video as video_router
from app.services.runtime import (
    audio_alert_gate,
    detector_service,
    event_frame_buffer,
    evaluation_budget,
    evaluation_client,
    evaluation_worker,
    parking_candidate_trigger,
    parking_wiring,
    scene_domain_classifier,
    shutdown_services,
    tracker_service,
    video_service,
)
from app.services.video_service import UPLOAD_DIR

# Uvicorn configures only its own loggers; the root logger stays at WARNING
# without a handler, so application INFO metadata was never printed. A fallback
# stderr handler is installed for exactly the two evaluation loggers
# (metadata-only lines: short IDs, domain, outcome/category, HTTP code,
# durations), and only when nothing configured their path. httpx/httpcore, the
# root logger and every other logger are left untouched; nothing is DEBUG.
#
# Any external choice wins, including deliberate silence: if the target or an
# ancestor is disabled, filtered, has a level, has a handler, or stops
# propagation (with or without handlers), or logging.disable() is active, no
# fallback is installed and these lines may stay hidden by that configuration.
EVALUATION_LOGGERS = (
    "app.services.evaluation_worker",
    "app.services.gemini_evaluation_client",
)


class _EvaluationFallbackHandler(logging.StreamHandler):
    """Marker type so a repeated call recognizes its own fallback."""


def _externally_configured(target: logging.Logger) -> bool:
    if logging.root.manager.disable != logging.NOTSET:
        return True                               # logging.disable(...) is in effect
    current: logging.Logger | None = target
    while current is not None:
        if current is logging.root:
            return bool(current.handlers or current.filters or current.disabled)
        if (current.disabled or current.handlers or current.filters
                or current.level != logging.NOTSET or not current.propagate):
            return True
        current = current.parent
    return False


def configure_evaluation_logging() -> None:
    """Idempotent fallback for the default Uvicorn logging configuration only."""
    for name in EVALUATION_LOGGERS:
        target = logging.getLogger(name)
        if any(isinstance(handler, _EvaluationFallbackHandler) for handler in target.handlers):
            continue                              # already installed by an earlier call
        if _externally_configured(target):
            continue                              # respect it, even if it silences INFO
        handler = _EvaluationFallbackHandler()    # stderr
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        target.addHandler(handler)
        target.setLevel(logging.INFO)
        target.propagate = False      # a root handler added later cannot duplicate lines


configure_evaluation_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    # ── Startup ──────────────────────────────────────────────────────────────
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    evaluation_worker.start()
    if audio_alert_gate is not None:
        audio_alert_gate.start()
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
    shutdown_services(
        video_service, scene_domain_classifier, detector_service,
        parking_candidate_trigger, tracker_service, event_frame_buffer,
        evaluation_worker=evaluation_worker,
        langflow_client=evaluation_client,  # the one active transport client
        evaluation_budget=evaluation_budget,
        audio_alert=audio_alert_gate,
        # Private parking (T3c); None in legacy and disabled modes.
        coordinator=parking_wiring.coordinator,
        frame_tee=parking_wiring.tee,
        sampler=parking_wiring.sampler,
    )


app = FastAPI(
    title="Sentinel Vision AI",
    description="Backend service for Sentinel Vision AI",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow local frontend dev servers
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(domain_router.router, prefix="/api")
app.include_router(detections_router.router, prefix="/api")
app.include_router(video_router.router, prefix="/api")
app.include_router(evaluation_router.router, prefix="/api")
