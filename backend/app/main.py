from contextlib import asynccontextmanager

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
    evaluation_worker,
    langflow_client,
    parking_candidate_trigger,
    scene_domain_classifier,
    shutdown_services,
    tracker_service,
    video_service,
)
from app.services.video_service import UPLOAD_DIR


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
        langflow_client=langflow_client,
        evaluation_budget=evaluation_budget,
        audio_alert=audio_alert_gate,
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
