"""Nonblocking early-frame scene-domain sampler with an injected runner.

The production CLIP runner is intentionally not part of this module yet.  The
capture thread only offers packets; copying and score aggregation happen in a
dedicated worker after all five samples have been selected.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import threading
from collections.abc import Callable, Sequence

import numpy as np

from app.models.domain import DomainName
from app.models.video import FramePacket
from app.services.domain_service import DomainService

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class SceneFrameScores:
    """Raw, threshold-free cosine scores returned for one BGR frame."""

    parking_score: float
    retail_score: float


SceneRunner = Callable[[Sequence[np.ndarray]], Sequence[SceneFrameScores]]
SceneRunnerFactory = Callable[[], SceneRunner]
Clock = Callable[[], float]


class SceneDomainClassifier:
    """Sample five early frames and make at most one scene decision per session."""

    def __init__(
        self,
        domain_service: DomainService,
        runner_factory: SceneRunnerFactory,
        clock: Clock,
        target_elapsed_seconds: Sequence[float] = (1, 2, 3, 4, 5),
        min_mean_top_score: float = 0.26,
        min_consistency: float = 0.80,
        min_mean_margin: float = 0.025,
        scene_model: str = "scene-runner",
    ) -> None:
        targets = tuple(float(target) for target in target_elapsed_seconds)
        if not targets or any(not math.isfinite(target) or target <= 0 for target in targets):
            raise ValueError("target_elapsed_seconds must contain positive finite values")
        if tuple(sorted(targets)) != targets or len(set(targets)) != len(targets):
            raise ValueError("target_elapsed_seconds must be strictly increasing")
        self._domain_service = domain_service
        self._runner_factory = runner_factory
        self._clock = clock
        self._targets = targets
        self._min_mean_top_score = min_mean_top_score
        self._min_consistency = min_consistency
        self._min_mean_margin = min_mean_margin
        self._scene_model = scene_model
        self._cond = threading.Condition()
        self._active_session_id: str | None = None
        self._started_at: float | None = None
        self._next_target_index = 0
        self._last_packet_seq: int | None = None
        self._samples: list[FramePacket] = []
        self._batch: tuple[str, tuple[FramePacket, ...]] | None = None
        self._batch_scheduled = False
        self._runner: SceneRunner | None = None
        self._last_error: Exception | None = None
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="scene-domain-worker", daemon=True)
        self._worker.start()

    def start_session(self, session_id: str) -> None:
        """Reset sampler state without creating a runner or copying frames."""
        with self._cond:
            if self._closed:
                return
            self._active_session_id = session_id
            self._started_at = self._clock()
            self._next_target_index = 0
            self._last_packet_seq = None
            self._samples.clear()
            self._batch = None
            self._batch_scheduled = False
            self._last_error = None
            self._cond.notify_all()

    def offer(self, packet: FramePacket) -> None:
        """Select at most one packet in constant time; never copy or infer here."""
        with self._cond:
            if (
                self._closed
                or packet.session_id != self._active_session_id
                or self._batch_scheduled
                or self._started_at is None
                or (self._last_packet_seq is not None and packet.seq <= self._last_packet_seq)
            ):
                return
            self._last_packet_seq = packet.seq
            if packet.ts - self._started_at < self._targets[self._next_target_index]:
                return

            self._samples.append(packet)
            self._next_target_index += 1
            if self._next_target_index == len(self._targets):
                self._batch = (packet.session_id, tuple(self._samples))
                self._samples.clear()
                self._batch_scheduled = True
                self._cond.notify_all()

    def end_session(self, session_id: str) -> None:
        """Discard pending samples; an in-flight worker result will be rejected."""
        with self._cond:
            if session_id != self._active_session_id:
                return
            self._active_session_id = None
            self._started_at = None
            self._next_target_index = 0
            self._last_packet_seq = None
            self._samples.clear()
            self._batch = None
            self._batch_scheduled = False
            self._cond.notify_all()

    def close(self) -> None:
        """Wake and stop the worker.  Idempotent for tests and shutdown."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._active_session_id = None
            self._samples.clear()
            self._batch = None
            self._cond.notify_all()
        self._worker.join(timeout=3.0)

    def _session_is_active(self, session_id: str) -> bool:
        with self._cond:
            return not self._closed and self._active_session_id == session_id

    def _run(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._closed or self._batch is not None)
                if self._closed:
                    return
                batch = self._batch
                self._batch = None

            if batch is None:
                continue
            session_id, packets = batch
            if not self._session_is_active(session_id):
                continue
            snapshot = self._domain_service.get_snapshot()
            if snapshot.locked or snapshot.source_session_id != session_id:
                continue

            try:
                # The sole point where raw capture frames are copied for scene work.
                frames = [packet.frame.copy() for packet in packets]
                if not self._session_is_active(session_id):
                    continue
                if self._domain_service.get_snapshot().locked:
                    continue
                if self._runner is None:
                    self._runner = self._runner_factory()
                scores = tuple(self._runner(frames))
                decision = self._aggregate(scores)
            except Exception as exc:
                logger.exception("Scene-domain runner failed for session %s", session_id)
                with self._cond:
                    self._last_error = exc
                continue

            if not self._session_is_active(session_id):
                continue
            self._domain_service.apply_scene_decision(
                session_id=session_id,
                domain=decision.domain,
                scene_model=self._scene_model,
                mean_top_score=decision.mean_top_score,
                consistency=decision.consistency,
                mean_margin=decision.mean_margin,
                sample_count=len(scores),
            )

    def _aggregate(self, scores: Sequence[SceneFrameScores]) -> "SceneDecision":
        if len(scores) != len(self._targets):
            raise ValueError("runner must return one score pair per sampled frame")
        parking_values = [score.parking_score for score in scores]
        retail_values = [score.retail_score for score in scores]
        if any(not math.isfinite(value) for value in [*parking_values, *retail_values]):
            raise ValueError("runner returned non-finite scene scores")

        parking_votes = sum(parking > retail for parking, retail in zip(parking_values, retail_values))
        retail_votes = sum(retail > parking for parking, retail in zip(parking_values, retail_values))
        if parking_votes > retail_votes:
            top_domain = DomainName.PARKING_MOTORCYCLE
            winning_votes = parking_votes
        elif retail_votes > parking_votes:
            top_domain = DomainName.RETAIL_STORE
            winning_votes = retail_votes
        else:
            top_domain = DomainName.UNKNOWN
            winning_votes = 0

        mean_top_score = sum(max(parking, retail) for parking, retail in zip(parking_values, retail_values)) / len(scores)
        mean_margin = sum(abs(parking - retail) for parking, retail in zip(parking_values, retail_values)) / len(scores)
        consistency = winning_votes / len(scores)
        accepted = (
            top_domain is not DomainName.UNKNOWN
            and mean_top_score >= self._min_mean_top_score
            and consistency >= self._min_consistency
            and mean_margin >= self._min_mean_margin
        )
        return SceneDecision(
            domain=top_domain if accepted else DomainName.UNKNOWN,
            mean_top_score=mean_top_score,
            mean_margin=mean_margin,
            consistency=consistency,
        )


@dataclasses.dataclass(frozen=True)
class SceneDecision:
    domain: DomainName
    mean_top_score: float
    mean_margin: float
    consistency: float
