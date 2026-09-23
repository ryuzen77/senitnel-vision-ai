"""Production composition root for the single-worker application process."""

from __future__ import annotations

import threading
import time

from app.services.clip_scene_runner import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_REVISION,
    make_clip_scene_runner_factory,
)
from app.services.detector_service import DetectorService
from app.services.domain_service import domain_service
from app.services.event_frame_buffer import EventFrameBuffer
from app.services.scene_domain_classifier import SceneDomainClassifier
from app.services.parking_candidate_trigger import ParkingCandidateTrigger
from app.services.tracker_service import TrackerService
from app.services.video_service import VideoService

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
video_service = VideoService(
    detector=detector_service,
    scene_classifier=scene_domain_classifier,
    event_frame_buffer=event_frame_buffer,
)


def shutdown_services(video, scene, detector, parking_trigger, tracker, frame_buffer=None) -> None:  # noqa: ANN001
    """Shutdown order that prevents tracker observations after closure."""
    video.stop()
    scene.close()
    detector.close()
    parking_trigger.close()
    tracker.close()
    if frame_buffer is not None:
        frame_buffer.close()
