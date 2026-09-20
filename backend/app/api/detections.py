"""Read-only API for the latest completed detector result."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.models.detection import DetectionResponse, DetectionResultResponse
from app.services.detector_service import detector_service

router = APIRouter(prefix="/detections", tags=["detections"])


@router.get(
    "/latest",
    response_model=DetectionResultResponse,
    responses={204: {"description": "No active detection result is available."}},
)
async def get_latest_detection() -> DetectionResultResponse | Response:
    """Return the latest result without scheduling or running inference."""
    result = detector_service.get_latest_result()
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return DetectionResultResponse(
        detection_seq=result.detection_seq,
        source_session_id=result.source_session_id,
        source_frame_seq=result.source_frame_seq,
        source_timestamp=result.source_timestamp,
        detections=[
            DetectionResponse(
                class_id=detection.class_id,
                class_name=detection.class_name,
                confidence=detection.confidence,
                xyxy=detection.xyxy,
            )
            for detection in result.detections
        ],
    )
