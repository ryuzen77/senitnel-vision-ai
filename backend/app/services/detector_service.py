"""Single-worker, latest-frame-wins detector service.

The capture thread only calls :meth:`offer`, which overwrites a single pending
slot and returns immediately.  Model loading and inference always happen in
the dedicated worker, never in the capture or MJPEG paths.

Session lifecycle (T3c0.1):

* Every admitted session gets a detector *generation* (strictly increasing, never
  reused, also for a reused session id) and, optionally, the owner's *lease*
  (VideoService's per-reservation token). ``offer``/``end_session`` act only when the
  session id AND the lease match the record, so a late call of an old operation never
  touches a newer session. ``lease=None`` keeps the id-only behaviour for legacy callers;
  the lease protects the lifecycle driven by its owner, not arbitrary direct calls.
* Cleanup obligations: each dependency (result consumer, tracker, parking trigger) is
  recorded BEFORE its ``start_session`` is called, so one that changes state and then
  raises is still ended. ``end_session`` tries every obligation still pending, keeps the
  failed ones and raises :class:`DetectorCleanupError`; a retry calls only those. A new
  session is refused while a cleanup is pending.
* Delivery versus invalidation: the worker holds ``_delivery_lock`` from the generation
  check through every consumer call; every lifecycle change takes ``_delivery_lock``
  before changing the active generation. A delivery therefore completes before an
  invalidation or is dropped after it; there is no proven bound on how long a lifecycle
  change waits for an in-flight delivery (consumers are in-memory by contract).
  Inference never runs under a detector lock.

Lock order: ``_lifecycle_lock`` -> ``_delivery_lock`` -> ``_cond``. The worker never takes
``_lifecycle_lock``; dependencies never call back into the detector. Errors and logs
carry dependency labels and exception types only, never ids or messages.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import ContextManager, Protocol

import numpy as np

from app.models.detection import Detection, DetectionResult
from app.models.domain import DomainSnapshot
from app.models.tracking import TrackingDetectionInput, TrackingFrame, TrackingSnapshot
from app.models.video import FramePacket

logger = logging.getLogger(__name__)

InferenceRunner = Callable[[np.ndarray], list[Detection]]
ModelFactory = Callable[[], InferenceRunner]


class DetectionResultConsumer(Protocol):
    """Receives only published results from the detector's active session."""

    def start_session(self, session_id: str) -> None: ...

    def observe(self, detection_result: DetectionResult) -> None: ...

    def end_session(self, session_id: str) -> None: ...


class TrackingConsumer(Protocol):
    """Receives post-validation CPU tracking observations for active sessions."""

    def start_session(self, session_id: str) -> None: ...

    def observe(self, frame: TrackingFrame) -> object: ...

    def end_session(self, session_id: str) -> None: ...

    def close(self) -> None: ...


class ParkingCandidateConsumer(Protocol):
    """Receives post-tracker metadata only; it never receives image data."""

    def start_session(self, session_id: str) -> None: ...

    def observe(self, domain_snapshot: DomainSnapshot, tracking_snapshot: TrackingSnapshot) -> None: ...

    def end_session(self, session_id: str) -> None: ...

    def close(self) -> None: ...


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


class DetectorAdmissionError(RuntimeError):
    """The detector refused a session start before touching any dependency (sanitized)."""


class DetectorStartError(RuntimeError):
    """A dependency did not start; every attempted dependency stays a cleanup obligation."""


class DetectorCleanupError(RuntimeError):
    """A dependency did not end its session; ``end_session`` retries only those (sanitized)."""


def _log_failure(label: str, step: str, error: BaseException) -> None:
    """Sanitized: dependency label, step and exception type only."""
    try:
        logger.warning("Detector %s %s failed (%s)", label, step, type(error).__name__)
    except Exception:
        pass


@dataclasses.dataclass(eq=False)
class _SessionRecord:
    session_id: str
    lease: object
    generation: int
    obligations: list[str] = dataclasses.field(default_factory=list)   # attempted, in start order
    remaining: set[str] | None = None                                  # set once the session ends


class DetectorService:
    """Own one reusable model and one asynchronous inference worker per process.

    This service assumes the application runs with ``uvicorn --workers 1``.
    ``model_factory`` exists for deterministic CPU-only tests.
    """

    accepts_lifecycle_lease = True

    def __init__(
        self,
        model_factory: ModelFactory | None = None,
        result_consumer: DetectionResultConsumer | None = None,
        tracker: TrackingConsumer | None = None,
        parking_trigger: ParkingCandidateConsumer | None = None,
        domain_snapshot_provider: Callable[[], DomainSnapshot] | None = None,
        cuda_gate: ContextManager[None] | None = None,
    ) -> None:
        self._model_factory = model_factory or _default_model_factory
        self._result_consumer = result_consumer
        self._tracker = tracker
        self._parking_trigger = parking_trigger
        self._domain_snapshot_provider = domain_snapshot_provider
        # Production injects the application-wide gate.  This per-instance
        # fallback keeps isolated CPU-only unit tests independent.
        self._cuda_gate = cuda_gate or threading.Lock()
        self._runner: InferenceRunner | None = None
        # Lock order: _lifecycle_lock -> _delivery_lock -> _cond (see module docstring).
        self._lifecycle_lock = threading.Lock()   # start/end/close; held while dependencies are called
        self._delivery_lock = threading.Lock()    # one delivery vs. one generation change
        self._cond = threading.Condition()        # guards the fields below
        self._active: _SessionRecord | None = None
        self._ending: _SessionRecord | None = None     # at most one session with pending cleanup
        self._generation = 0
        self._job: tuple[FramePacket, int] | None = None
        self._latest_result: DetectionResult | None = None
        self._detection_seq = 0
        self._closed = False
        self._counts = dict.fromkeys((
            "start_failures", "end_failures", "stale_ends", "stale_results", "cleanups_completed",
            "starts_refused", "close_failures",
        ), 0)
        self._worker = threading.Thread(target=self._run, name="detector-worker", daemon=True)
        self._worker.start()

    def _dependencies(self) -> tuple[tuple[str, object], ...]:
        return tuple((label, dependency) for label, dependency in (
            ("result consumer", self._result_consumer), ("tracker", self._tracker),
            ("parking trigger", self._parking_trigger),
        ) if dependency is not None)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start_session(self, session_id: str, *, lease: object = None) -> None:
        """Activate a new capture session without loading the model on the caller.

        Raises :class:`DetectorAdmissionError` before any dependency call when closed,
        when a cleanup is pending, or when this session is already active; raises
        :class:`DetectorStartError` when a dependency did not start (the attempted ones
        stay obligations of this session). A different active session is ended first
        (legacy implicit switch); if that cleanup fails, the new start is refused.
        """
        with self._lifecycle_lock:
            with self._cond:
                closed, previous = self._closed, self._active
            if closed:
                self._refuse("detector is closed")
            if previous is not None and previous.session_id != session_id:
                try:
                    self._end_record_locked(previous)
                except DetectorCleanupError:
                    self._refuse("cleanup of the previous session is still pending")
            with self._cond:
                if self._ending is not None:
                    reason = "cleanup of an earlier session is still pending"
                elif self._active is not None:
                    reason = "this session is already active"
                else:
                    reason = None
            if reason is not None:
                self._refuse(reason)
            with self._delivery_lock:
                with self._cond:
                    self._generation += 1
                    record = _SessionRecord(session_id, lease, self._generation)
                    self._active = record
                    self._job = None
                    self._latest_result = None
                    self._cond.notify_all()
            for label, dependency in self._dependencies():
                with self._cond:
                    if self._closed:
                        self._counts["start_failures"] += 1
                        raise DetectorStartError("detector closed during the start") from None
                    record.obligations.append(label)        # recorded BEFORE the call
                try:
                    dependency.start_session(session_id)
                except Exception as error:
                    with self._cond:
                        self._counts["start_failures"] += 1
                    _log_failure(label, "start_session", error)
                    raise DetectorStartError(f"detector {label} did not start the session") from None

    def _refuse(self, reason: str) -> None:
        with self._cond:
            self._counts["starts_refused"] += 1
        raise DetectorAdmissionError(f"detector refused a new session: {reason}") from None

    def offer(self, frame_packet: FramePacket, *, lease: object = None) -> None:
        """Offer a frame without waiting for model loading or inference."""
        with self._cond:
            record = self._active
            if (self._closed or record is None or frame_packet.session_id != record.session_id
                    or lease != record.lease):
                return
            self._job = (frame_packet, record.generation)
            self._cond.notify()

    def end_session(self, session_id: str, *, lease: object = None) -> None:
        """End the matching active session, or retry the dependencies still pending for
        it. No-op (counted ``stale_ends``) when nothing matches the id AND the lease.
        Raises :class:`DetectorCleanupError` while a dependency did not end."""
        with self._lifecycle_lock:
            with self._cond:
                record = next((candidate for candidate in (self._active, self._ending)
                               if candidate is not None and candidate.session_id == session_id
                               and candidate.lease == lease), None)
                if record is None:
                    self._counts["stale_ends"] += 1
                    return
            self._end_record_locked(record)

    def _end_record_locked(self, record: _SessionRecord) -> None:
        """Caller holds _lifecycle_lock. Invalidate ``record`` if active (atomic with
        respect to a delivery), then try every obligation still pending."""
        with self._delivery_lock:
            with self._cond:
                if record is self._active:
                    self._active = None
                    self._job = None
                    self._latest_result = None
                    record.remaining = set(record.obligations)
                    self._cond.notify_all()
                targets = [label for label in record.obligations if label in record.remaining]
        dependencies = dict(self._dependencies())
        for label in targets:
            try:
                dependencies[label].end_session(record.session_id)
            except Exception as error:
                _log_failure(label, "end_session", error)
            else:
                with self._cond:
                    record.remaining.discard(label)
        with self._cond:
            remaining = len(record.remaining)
            if remaining:
                self._ending = record
                self._counts["end_failures"] += 1
            else:
                if self._ending is record:
                    self._ending = None
                self._counts["cleanups_completed"] += 1
        if remaining:
            raise DetectorCleanupError(f"{remaining} detector dependency end(s) failed") from None

    def get_latest_result(self) -> DetectionResult | None:
        with self._cond:
            return self._latest_result

    def get_lifecycle_diagnostics(self) -> dict[str, int]:
        """Counts and fixed labels only (no session ids or leases)."""
        with self._cond:
            pending = self._ending.remaining if self._ending is not None else set()
            per_label = {f"pending_{label.replace(' ', '_')}": int(label in pending)
                         for label, _dependency in self._dependencies()}
            return dict(self._counts, active_sessions=int(self._active is not None),
                        pending_sessions=int(self._ending is not None),
                        pending_dependency_ends=len(pending), closed=int(self._closed), **per_label)

    def close(self) -> None:
        """Stop the worker.  Intended for application shutdown and tests.

        (1) closed + worker notified first, so a raising dependency cannot keep the
        worker alive; (2) every recorded obligation is tried once (failures stay pending
        for a later ``end_session`` retry; never raises); (3) join with no lock held.
        """
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._cond.notify_all()
        with self._lifecycle_lock:
            with self._cond:
                records = [record for record in (self._active, self._ending) if record is not None]
            for record in records:
                try:
                    self._end_record_locked(record)
                except DetectorCleanupError:
                    with self._cond:
                        self._counts["close_failures"] += 1
        self._worker.join(timeout=3.0)

    # ── worker ────────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._closed or self._job is not None)
                if self._closed:
                    return
                packet, generation = self._job
                self._job = None

            try:
                # Only lazy loading and inference use the CUDA gate.  Waiting,
                # packet state and publishing results remain outside it.
                with self._cuda_gate:
                    if self._runner is None:
                        self._runner = self._model_factory()
                    # The detector must never mutate VideoService's raw frame.
                    detections = self._runner(packet.frame.copy())
            except Exception:
                logger.exception("Detector inference failed for frame %s", packet.seq)
                continue

            # Delivery: the generation check and every consumer call form one unit with
            # respect to invalidation (which takes _delivery_lock before changing it).
            with self._delivery_lock:
                with self._cond:
                    record = self._active
                    if self._closed or record is None or record.generation != generation:
                        self._counts["stale_results"] += 1
                        continue
                    result = DetectionResult(
                        detection_seq=self._detection_seq,
                        source_session_id=packet.session_id,
                        source_frame_seq=packet.seq,
                        source_timestamp=packet.ts,
                        detections=detections,
                    )
                    self._latest_result = result
                    self._detection_seq += 1
                self._deliver(packet, result)

    def _deliver(self, packet: FramePacket, result: DetectionResult) -> None:
        """Caller holds _delivery_lock. Consumers are in-memory and never call back."""
        if self._result_consumer is not None:
            try:
                self._result_consumer.observe(result)
            except Exception:
                logger.exception("Detector result consumer failed for frame %s", packet.seq)
        if self._tracker is None:
            return
        try:
            frame_height, frame_width = packet.frame.shape[:2]
            tracking_frame = TrackingFrame(
                source_session_id=result.source_session_id,
                source_frame_seq=result.source_frame_seq,
                source_timestamp=result.source_timestamp,
                frame_width=frame_width,
                frame_height=frame_height,
                detections=tuple(
                    TrackingDetectionInput(
                        class_id=detection.class_id,
                        class_name=detection.class_name,
                        confidence=detection.confidence,
                        xyxy=detection.xyxy,
                    )
                    for detection in result.detections
                ),
            )
            tracked = self._tracker.observe(tracking_frame)
        except Exception:
            # Tracking is optional; keep published detection/domain/video alive.
            logger.exception("Tracker observe failed for frame %s", packet.seq)
            return
        if self._parking_trigger is None or self._domain_snapshot_provider is None:
            return
        try:
            # This runs after DomainService.observe(result) and tracker
            # association, outside the detector state and CUDA locks.
            domain_snapshot = self._domain_snapshot_provider()
        except Exception:
            logger.exception("Domain snapshot provider failed for frame %s", packet.seq)
            return
        try:
            self._parking_trigger.observe(
                domain_snapshot,
                TrackingSnapshot(
                    source_session_id=result.source_session_id,
                    source_frame_seq=result.source_frame_seq,
                    source_timestamp=result.source_timestamp,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    tracks=tuple(tracked),
                ),
            )
        except Exception:
            # Candidate selection is optional and cannot impact
            # detection, tracking, domain state, or video capture.
            logger.exception("Parking candidate trigger failed for frame %s", packet.seq)


def __getattr__(name: str):  # noqa: ANN202
    """Compatibility export; production ownership lives in ``runtime``."""
    if name == "detector_service":
        from app.services.runtime import detector_service

        return detector_service
    raise AttributeError(name)
