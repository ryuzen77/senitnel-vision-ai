"""Bounded, session-scoped JPEG references and deterministic time selection."""

from __future__ import annotations

from collections import deque
import math
import threading
import time

from app.models.domain import DomainName
from app.models.event_frame import (
    BufferedFrame, BundleDecision, BundleStatus, EventFrameBufferSnapshot,
    EventFrameBundle, FrameSelectionPlan, SelectedFrame,
)


class EventFrameBuffer:
    """Capture-side offer only keeps existing immutable JPEG bytes in memory."""

    def __init__(self, *, retention_seconds: float = 6.0, max_frames: int = 240,
                 max_total_bytes: int = 32 * 1024 * 1024) -> None:
        if (not math.isfinite(retention_seconds) or retention_seconds <= 0
                or max_frames < 1 or max_total_bytes < 1):
            raise ValueError("buffer limits must be finite and positive")
        self._retention = retention_seconds
        self._max_frames = max_frames
        self._max_bytes = max_total_bytes
        self._condition = threading.Condition(threading.Lock())
        self._session_id: str | None = None
        self._ended_session_id: str | None = None
        self._frames: deque[BufferedFrame] = deque()
        self._total_bytes = 0
        self._last_seq: int | None = None
        self._last_timestamp: float | None = None
        self._closed = False

    def _clear(self) -> None:
        self._frames.clear()
        self._total_bytes = 0
        self._last_seq = None
        self._last_timestamp = None

    def start_session(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must be nonempty")
        with self._condition:
            if self._closed:
                return
            if self._session_id == session_id:
                return
            self._ended_session_id = self._session_id
            self._clear()
            self._session_id = session_id
            self._condition.notify_all()

    def offer(self, frame: BufferedFrame) -> bool:
        with self._condition:
            if (self._closed or frame.source_session_id != self._session_id
                    or not isinstance(frame.jpeg, bytes) or not frame.jpeg
                    or frame.mime_type != "image/jpeg" or len(frame.jpeg) > self._max_bytes
                    or not isinstance(frame.source_frame_seq, int) or frame.source_frame_seq < 0
                    or not isinstance(frame.frame_width, int) or frame.frame_width <= 0
                    or not isinstance(frame.frame_height, int) or frame.frame_height <= 0
                    or not isinstance(frame.source_timestamp, (float, int))
                    or not math.isfinite(frame.source_timestamp) or frame.source_timestamp < 0
                    or (self._last_seq is not None and frame.source_frame_seq <= self._last_seq)
                    or (self._last_timestamp is not None and frame.source_timestamp < self._last_timestamp)):
                return False
            self._frames.append(frame)
            self._total_bytes += len(frame.jpeg)
            self._last_seq = frame.source_frame_seq
            self._last_timestamp = frame.source_timestamp
            while (self._frames and (
                self._frames[0].source_timestamp < frame.source_timestamp - self._retention
                or len(self._frames) > self._max_frames
                or self._total_bytes > self._max_bytes
            )):
                self._total_bytes -= len(self._frames.popleft().jpeg)
            self._condition.notify_all()
            return True

    @staticmethod
    def _valid_plan(plan: FrameSelectionPlan) -> bool:
        if not isinstance(plan, FrameSelectionPlan):
            return False
        offsets = plan.target_offsets_seconds
        return bool(
            plan.event_id and plan.source_session_id
            and isinstance(plan.domain, DomainName) and plan.domain is not DomainName.UNKNOWN
            and isinstance(plan.triggered_at, (int, float)) and math.isfinite(plan.triggered_at)
            and plan.triggered_at >= 0
            and isinstance(plan.required_frame_count, int) and plan.required_frame_count > 0
            and isinstance(offsets, tuple) and len(offsets) == plan.required_frame_count
            and all(isinstance(offset, (int, float)) and math.isfinite(offset) for offset in offsets)
            and all(left < right for left, right in zip(offsets, offsets[1:]))
            and isinstance(plan.maximum_target_deviation_seconds, (int, float))
            and math.isfinite(plan.maximum_target_deviation_seconds)
            and plan.maximum_target_deviation_seconds >= 0
            and all(plan.triggered_at + offset >= 0 for offset in offsets)
        )

    def _build_locked(self, plan: FrameSelectionPlan) -> BundleDecision:
        if self._closed:
            return BundleDecision(BundleStatus.CLOSED)
        if not self._valid_plan(plan):
            return BundleDecision(BundleStatus.INVALID_PLAN)
        if plan.source_session_id != self._session_id:
            status = BundleStatus.ENDED if plan.source_session_id == self._ended_session_id else BundleStatus.SESSION_MISMATCH
            return BundleDecision(status)
        if not self._frames or self._frames[-1].source_timestamp < plan.triggered_at + plan.target_offsets_seconds[-1]:
            return BundleDecision(BundleStatus.WAITING_FOR_FUTURE_FRAME)
        tolerance = plan.maximum_target_deviation_seconds
        if self._frames[0].source_timestamp > plan.triggered_at + plan.target_offsets_seconds[0] + tolerance:
            return BundleDecision(BundleStatus.INSUFFICIENT_HISTORY)
        unused = set(range(len(self._frames)))
        selected: list[SelectedFrame] = []
        for offset in plan.target_offsets_seconds:
            target = plan.triggered_at + offset
            if not unused:
                return BundleDecision(BundleStatus.INSUFFICIENT_HISTORY)
            index = min(unused, key=lambda i: (
                abs(self._frames[i].source_timestamp - target),
                self._frames[i].source_timestamp, self._frames[i].source_frame_seq,
            ))
            frame = self._frames[index]
            deviation = abs(frame.source_timestamp - target)
            if deviation > tolerance:
                status = (BundleStatus.INSUFFICIENT_HISTORY
                          if frame.source_timestamp > target and self._frames[0].source_timestamp > target
                          else BundleStatus.TARGET_DEVIATION_EXCEEDED)
                return BundleDecision(status)
            unused.remove(index)
            selected.append(SelectedFrame(
                frame.source_frame_seq, frame.source_timestamp, offset,
                frame.source_timestamp - plan.triggered_at, deviation,
                frame.frame_width, frame.frame_height, frame.mime_type, frame.jpeg,
            ))
        bundle = EventFrameBundle(
            plan.event_id, plan.source_session_id, plan.domain, plan.triggered_at,
            tuple(selected), sum(len(frame.jpeg) for frame in selected),
        )
        return BundleDecision(BundleStatus.READY, bundle)

    def try_build_bundle(self, plan: FrameSelectionPlan) -> BundleDecision:
        with self._condition:
            return self._build_locked(plan)

    def wait_for_bundle(self, plan: FrameSelectionPlan, timeout_seconds: float) -> BundleDecision:
        if not isinstance(timeout_seconds, (float, int)) or not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while True:
                decision = self._build_locked(plan)
                if decision.status is not BundleStatus.WAITING_FOR_FUTURE_FRAME:
                    return decision
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return decision
                self._condition.wait(remaining)

    def get_snapshot(self) -> EventFrameBufferSnapshot:
        with self._condition:
            first = self._frames[0] if self._frames else None
            last = self._frames[-1] if self._frames else None
            return EventFrameBufferSnapshot(
                self._session_id, len(self._frames), self._total_bytes,
                first.source_frame_seq if first else None,
                last.source_frame_seq if last else None,
                first.source_timestamp if first else None,
                last.source_timestamp if last else None,
                self._closed,
            )

    def end_session(self, session_id: str) -> None:
        with self._condition:
            if self._session_id != session_id:
                return
            self._ended_session_id = session_id
            self._session_id = None
            self._clear()
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._session_id = None
            self._clear()
            self._condition.notify_all()
