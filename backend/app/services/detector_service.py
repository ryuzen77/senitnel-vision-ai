"""Single-worker, latest-frame-wins detector service.

The capture thread only calls :meth:`offer`, which overwrites a single pending
slot and returns immediately.  Model loading and inference always happen in
the dedicated worker, never in the capture or MJPEG paths.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from app.models.detection import Detection, DetectionResult
from app.models.video import FramePacket

logger = logging.getLogger(__name__)

InferenceRunner = Callable[[np.ndarray], list[Detection]]
ModelFactory = Callable[[], InferenceRunner]


def _default_model_factory() -> InferenceRunner:
    """Create the production YOLO runner in the worker on first use."""
    from ultralytics import YOLO

    weights = Path(__file__).resolve().parents[2] / "yolo26s.pt"
    model = YOLO(str(weights))

    def run(frame: np.ndarray) -> list[Detection]:
        result = model(frame, device=0, imgsz=640, verbose=False, save=False)[0]
        return [
            Detection(
                class_id=int(class_id),
                class_name=str(result.names[int(class_id)]),
                confidence=float(confidence),
                xyxy=tuple(float(value) for value in xyxy.tolist()),
            )
            for xyxy, confidence, class_id in zip(result.boxes.xyxy, result.boxes.conf, result.boxes.cls)
        ]

    return run


class DetectorService:
    """Own one reusable model and one asynchronous inference worker per process.

    This service assumes the application runs with ``uvicorn --workers 1``.
    ``model_factory`` exists for deterministic CPU-only tests.
    """

    def __init__(self, model_factory: ModelFactory | None = None) -> None:
        self._model_factory = model_factory or _default_model_factory
        self._runner: InferenceRunner | None = None
        self._cond = threading.Condition()
        self._active_session_id: str | None = None
        self._pending: FramePacket | None = None
        self._latest_result: DetectionResult | None = None
        self._detection_seq = 0
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="detector-worker", daemon=True)
        self._worker.start()

    def start_session(self, session_id: str) -> None:
        """Activate a new capture session without loading the model on the caller."""
        with self._cond:
            if self._closed:
                return
            self._active_session_id = session_id
            self._pending = None
            self._latest_result = None
            self._cond.notify_all()

    def offer(self, frame_packet: FramePacket) -> None:
        """Offer a frame without waiting for model loading or inference."""
        with self._cond:
            if self._closed or frame_packet.session_id != self._active_session_id:
                return
            self._pending = frame_packet
            self._cond.notify()

    def end_session(self, session_id: str) -> None:
        """Invalidate a session and discard its pending/latest data."""
        with self._cond:
            if session_id != self._active_session_id:
                return
            self._active_session_id = None
            self._pending = None
            self._latest_result = None
            self._cond.notify_all()

    def get_latest_result(self) -> DetectionResult | None:
        with self._cond:
            return self._latest_result

    def close(self) -> None:
        """Stop the worker.  Intended for application shutdown and tests."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._active_session_id = None
            self._pending = None
            self._latest_result = None
            self._cond.notify_all()
        self._worker.join(timeout=3.0)

    def _run(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                packet = self._pending
                self._pending = None

            if packet is None:
                continue

            try:
                if self._runner is None:
                    self._runner = self._model_factory()
                # The detector must never mutate VideoService's raw frame.
                detections = self._runner(packet.frame.copy())
            except Exception:
                logger.exception("Detector inference failed for frame %s", packet.seq)
                continue

            with self._cond:
                # Inference may have completed after stop or a new session.
                if self._closed or packet.session_id != self._active_session_id:
                    continue
                self._latest_result = DetectionResult(
                    detection_seq=self._detection_seq,
                    source_session_id=packet.session_id,
                    source_frame_seq=packet.seq,
                    source_timestamp=packet.ts,
                    detections=detections,
                )
                self._detection_seq += 1


# The application singleton is safe because the model is lazy-loaded in the worker.
detector_service = DetectorService()
