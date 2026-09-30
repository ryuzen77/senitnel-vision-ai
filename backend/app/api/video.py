"""
Sentinel Vision AI — Video API router

Five endpoints:
  POST /api/video/webcam/start
  POST /api/video/upload
  POST /api/video/stop
  GET  /api/video/status
  GET  /api/video/stream

All business logic is delegated to VideoService via its public interface only.
No private attributes (_packet_cond, etc.) are accessed here.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

import cv2
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.models.video import VideoStartResponse, VideoState, VideoStatusResponse
from app.services.video_service import UPLOAD_DIR, video_service

router = APIRouter(prefix="/video", tags=["video"])

logger = logging.getLogger(__name__)

_ALLOWED_MIME_TYPES = {"video/mp4", "video/webm"}
_ALLOWED_EXTENSIONS = {".mp4", ".webm"}
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB
_CHUNK_SIZE = 1024 * 1024  # 1 MB read chunks


# ---------------------------------------------------------------------------
# POST /api/video/webcam/start
# ---------------------------------------------------------------------------


@router.post("/webcam/start", response_model=VideoStartResponse)
async def start_webcam() -> VideoStartResponse:
    # start_webcam() atomically reserves the slot and returns False if not IDLE.
    # This avoids the non-atomic get_status() → start race.
    acquired = video_service.start_webcam()
    if not acquired:
        status = video_service.get_status()
        raise HTTPException(
            status_code=409,
            detail=f"Cannot start: service is {status.state}",
        )
    status = video_service.get_status()
    return VideoStartResponse(
        state=status.state,
        message="Webcam capture started" if status.state == VideoState.RUNNING else f"Start failed: {status.error}",
    )


# ---------------------------------------------------------------------------
# POST /api/video/upload
# ---------------------------------------------------------------------------


@router.post("/upload", response_model=VideoStartResponse)
async def upload_video(file: UploadFile) -> VideoStartResponse:
    # FastAPI/Starlette has already received and parsed the multipart body here;
    # backend elapsed timing does not include the browser-to-server transfer.
    handler_entered_at = time.monotonic()
    # ── 1. Atomic reservation BEFORE any file I/O ──────────────────────────
    token = video_service.claim_start()
    if token is None:
        raise HTTPException(
            status_code=409,
            detail="Cannot upload: service is not IDLE",
        )
    video_service.record_startup_milestone(
        "upload_handler_entered", token=token, at=handler_entered_at,
    )

    # From this point the service is STARTING and we are the sole owner.
    # All failure paths MUST guarantee:
    #   • The temporary file is removed if it exists.
    #   • The probe capture is released if it was created.
    #   • The service returns to IDLE via video_service.stop().
    # Ownership handoff: before start_file() succeeds, the handler owns cleanup.
    # After start_file() succeeds, VideoService owns the file.

    saved_path: Path | None = None
    file_owned_by_service = False  # flipped to True after successful start_file()

    try:
        # ── 2. Validate MIME type ───────────────────────────────────────────
        content_type = (file.content_type or "").lower()
        if content_type not in _ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported MIME type: {content_type}. Allowed: mp4, webm",
            )

        # ── 3. Validate file extension ──────────────────────────────────────
        original_name = file.filename or ""
        ext = Path(original_name).suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file extension: {ext}. Allowed: .mp4, .webm",
            )

        # ── 4. Chunked write with size limit ─────────────────────────────────
        saved_path = UPLOAD_DIR / f"{uuid.uuid4()}{ext}"
        total_bytes = 0

        try:
            with saved_path.open("wb") as fh:
                while True:
                    chunk = await file.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > _MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=422,
                            detail="File exceeds 200 MB limit",
                        )
                    fh.write(chunk)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Upload write error") from exc
        video_service.record_startup_milestone("upload_copy_completed", token=token)

        # ── 5. Probe: verify the file can be opened by OpenCV ───────────────
        probe: cv2.VideoCapture | None = None
        try:
            probe = cv2.VideoCapture(str(saved_path))
            opened = probe.isOpened()
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="Probe failed unexpectedly",
            ) from exc
        finally:
            # Always release the probe capture — even if isOpened() raises.
            if probe is not None:
                probe.release()
            video_service.record_startup_milestone("first_opencv_probe_completed", token=token)

        if not opened:
            raise HTTPException(
                status_code=422,
                detail="File cannot be opened as a valid video",
            )

        # ── 6. Hand off to service (already STARTING) ─────────────────────
        try:
            video_service.start_file(saved_path, token)
        except RuntimeError:
            # Token mismatch: stop() raced the upload; service is already IDLE.
            # We own the file — delete it and return 409.
            raise HTTPException(
                status_code=409,
                detail="Upload aborted: stop was called during upload",
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Service start error") from exc

        # Handoff succeeded — VideoService now owns saved_path.
        file_owned_by_service = True

        status = video_service.get_status()
        return VideoStartResponse(
            state=status.state,
            message=(
                f"Video '{original_name}' started"
                if status.state == VideoState.RUNNING
                else f"Start failed: {status.error}"
            ),
        )

    except HTTPException:
        # Roll back: stop the service (returns to IDLE) and delete the file
        # if we still own it.
        video_service.stop()
        if saved_path is not None and not file_owned_by_service:
            try:
                saved_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    except Exception:
        # Unexpected exception — roll back without leaking filesystem paths.
        logger.exception("Unexpected error in upload_video")
        video_service.stop()
        if saved_path is not None and not file_owned_by_service:
            try:
                saved_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# POST /api/video/stop
# ---------------------------------------------------------------------------


@router.post("/stop", response_model=VideoStatusResponse)
async def stop_video() -> VideoStatusResponse:
    video_service.stop()  # Idempotent; also recovers ERROR → IDLE
    return video_service.get_status()


# ---------------------------------------------------------------------------
# GET /api/video/status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=VideoStatusResponse)
async def get_status() -> VideoStatusResponse:
    return video_service.get_status()


# ---------------------------------------------------------------------------
# GET /api/video/stream  — MJPEG streaming
# ---------------------------------------------------------------------------


@router.get("/stream")
async def stream_video() -> StreamingResponse:
    status = video_service.get_status()
    if status.state != VideoState.RUNNING:
        raise HTTPException(
            status_code=503,
            detail="Video stream is not available (service not RUNNING)",
        )

    async def generate():  # noqa: ANN202
        last_seq = -1
        loop = asyncio.get_event_loop()

        try:
            while True:
                # Offload blocking wait to the default thread pool so the
                # event loop remains free.
                packet = await loop.run_in_executor(
                    None,
                    video_service.wait_for_packet,
                    last_seq,
                    2.0,
                )

                if packet is None:
                    # Timeout or service stopped.
                    svc_state = video_service.get_status().state
                    if svc_state != VideoState.RUNNING:
                        break
                    continue  # Spurious wake — keep waiting

                if packet.seq == last_seq:
                    # No new frame within the timeout window.
                    svc_state = video_service.get_status().state
                    if svc_state != VideoState.RUNNING:
                        break
                    continue

                last_seq = packet.seq
                chunk = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + packet.jpeg
                    + b"\r\n"
                )
                video_service.record_startup_milestone(
                    "first_mjpeg_packet_yielded", session_id=packet.session_id,
                )
                yield chunk

        except (GeneratorExit, asyncio.CancelledError):
            # Client disconnected — exit silently.
            return
        except Exception:
            # Unexpected error — log it but do not propagate to avoid
            # crashing the streaming response.
            logger.exception("Unexpected error in stream generator")
            return

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )
