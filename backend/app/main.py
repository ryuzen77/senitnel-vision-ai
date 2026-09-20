from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api import video as video_router
from app.services.detector_service import detector_service
from app.services.video_service import UPLOAD_DIR, video_service


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    # ── Startup ──────────────────────────────────────────────────────────────
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
    video_service.stop()
    detector_service.close()


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
app.include_router(video_router.router, prefix="/api")
