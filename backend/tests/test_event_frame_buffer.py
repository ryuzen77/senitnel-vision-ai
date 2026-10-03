"""Synthetic JPEG-only tests; no capture, codec, model, file, or network."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import threading

import pytest

from app.models.domain import DomainName
from app.models.event_frame import (
    BufferedFrame, BundleStatus, FrameSelectionPlan, make_parking_frame_selection_plan,
    make_retail_frame_selection_plan,
)
from app.services.event_frame_buffer import EventFrameBuffer


def _frame(seq: int, ts: float, *, session: str = "one", jpeg: bytes | None = None) -> BufferedFrame:
    return BufferedFrame(session, seq, ts, 640, 480, jpeg if jpeg is not None else bytes([seq % 255]) * 10)


def _plan(*, trigger: float = 10.0, session: str = "one", domain: DomainName = DomainName.PARKING_MOTORCYCLE,
          offsets: tuple[float, ...] = (-2.0, -1.0, -0.4, 0.0, 0.4), tolerance: float = 0.3) -> FrameSelectionPlan:
    return FrameSelectionPlan("event", session, domain, trigger, offsets, tolerance, len(offsets))


def _ready_buffer() -> EventFrameBuffer:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    for seq, timestamp in enumerate((8.0, 9.0, 9.6, 10.0, 10.4), 1):
        assert buffer.offer(_frame(seq, timestamp))
    return buffer


def test_offer_adds_count_bytes_and_reuses_immutable_jpeg_reference() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    jpeg = b"synthetic-jpeg"
    assert buffer.offer(_frame(1, 1, jpeg=jpeg))
    snapshot = buffer.get_snapshot()
    assert (snapshot.frame_count, snapshot.total_jpeg_bytes) == (1, len(jpeg))
    assert buffer._frames[0].jpeg is jpeg
    assert jpeg.hex() not in repr(snapshot) and "synthetic-jpeg" not in repr(snapshot)
    with pytest.raises(FrozenInstanceError):
        snapshot.total_jpeg_bytes = 0


def test_wrong_session_duplicate_out_of_order_and_backward_time_rejected() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    assert not buffer.offer(_frame(1, 1, session="other"))
    assert buffer.offer(_frame(1, 1))
    assert not buffer.offer(_frame(1, 2))
    assert not buffer.offer(_frame(0, 2))
    assert not buffer.offer(_frame(2, .5))
    assert buffer.get_snapshot().frame_count == 1


@pytest.mark.parametrize("bad", [b"", bytearray(b"x")])
def test_invalid_jpeg_rejected(bad) -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    assert not buffer.offer(_frame(1, 1, jpeg=bad))


def test_retention_prunes_oldest_and_exact_boundary_is_kept() -> None:
    buffer = EventFrameBuffer(retention_seconds=2); buffer.start_session("one")
    for seq, ts in enumerate((1, 2, 3, 4), 1):
        assert buffer.offer(_frame(seq, ts))
    snap = buffer.get_snapshot()
    assert (snap.frame_count, snap.total_jpeg_bytes, snap.oldest_source_timestamp) == (3, 30, 2)


def test_frame_count_and_byte_budget_prune_oldest() -> None:
    count_buffer = EventFrameBuffer(max_frames=2); count_buffer.start_session("one")
    byte_buffer = EventFrameBuffer(max_total_bytes=20); byte_buffer.start_session("one")
    for seq in range(1, 4):
        assert count_buffer.offer(_frame(seq, seq))
        assert byte_buffer.offer(_frame(seq, seq))
    assert count_buffer.get_snapshot().oldest_source_frame_seq == 2
    assert byte_buffer.get_snapshot().oldest_source_frame_seq == 2
    assert count_buffer.get_snapshot().total_jpeg_bytes == byte_buffer.get_snapshot().total_jpeg_bytes == 20


def test_oversized_single_jpeg_rejected_without_evicting_valid_frame() -> None:
    buffer = EventFrameBuffer(max_total_bytes=10); buffer.start_session("one")
    assert buffer.offer(_frame(1, 1, jpeg=b"small"))
    assert not buffer.offer(_frame(2, 2, jpeg=b"x" * 11))
    assert buffer.get_snapshot().total_jpeg_bytes == 5
    assert buffer.offer(_frame(2, 2, jpeg=b"medium"))
    assert buffer.get_snapshot().total_jpeg_bytes == 6


def test_session_reset_end_and_close_clear_all_jpeg_references() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    buffer.offer(_frame(1, 1))
    buffer.start_session("two")
    assert buffer.get_snapshot().frame_count == buffer.get_snapshot().total_jpeg_bytes == 0
    assert buffer.try_build_bundle(_plan(session="one")).status is BundleStatus.ENDED
    assert buffer.offer(_frame(1, 1, session="two"))
    buffer.end_session("two"); buffer.end_session("two")
    assert buffer.get_snapshot().total_jpeg_bytes == 0
    assert buffer.try_build_bundle(_plan(session="two")).status is BundleStatus.ENDED
    buffer.close(); buffer.close()
    assert buffer.get_snapshot().closed and not buffer.offer(_frame(2, 2, session="two"))
    assert buffer.try_build_bundle(_plan(session="two")).status is BundleStatus.CLOSED


def test_exact_five_targets_select_ready_unique_jpegs_in_target_order() -> None:
    buffer = _ready_buffer()
    decision = buffer.try_build_bundle(_plan())
    assert decision.status is BundleStatus.READY and decision.bundle is not None
    bundle = decision.bundle
    assert [frame.source_frame_seq for frame in bundle.frames] == [1, 2, 3, 4, 5]
    assert len({frame.source_frame_seq for frame in bundle.frames}) == 5
    assert bundle.total_jpeg_bytes == 50
    assert all(frame.deviation_seconds == 0 for frame in bundle.frames)
    assert all(frame.mime_type == "image/jpeg" for frame in bundle.frames)
    assert "jpeg=" not in repr(bundle) and "jpeg=" not in repr(bundle.frames[0])


def test_nearest_unique_selection_and_deterministic_tie_break() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    for seq, ts in ((1, 9.9), (2, 10.0), (3, 10.1), (4, 10.5)):
        buffer.offer(_frame(seq, ts))
    decision = buffer.try_build_bundle(_plan(offsets=(0.0, .05, .5), tolerance=.2))
    assert decision.status is BundleStatus.READY
    assert [frame.source_frame_seq for frame in decision.bundle.frames] == [2, 3, 4]
    assert decision.bundle.frames[1].deviation_seconds == pytest.approx(.05)
    tie_buffer = EventFrameBuffer(); tie_buffer.start_session("one")
    for seq, ts in ((1, 9.875), (2, 10.125), (3, 10.5)):
        tie_buffer.offer(_frame(seq, ts))
    tied = tie_buffer.try_build_bundle(_plan(offsets=(0.0, .5), tolerance=.2))
    assert tied.status is BundleStatus.READY
    assert [frame.source_frame_seq for frame in tied.bundle.frames] == [1, 3]


def test_one_frame_cannot_fill_two_targets() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    buffer.offer(_frame(1, 10.0)); buffer.offer(_frame(2, 10.4))
    decision = buffer.try_build_bundle(_plan(offsets=(0.0, .05, .4), tolerance=.3))
    assert decision.status is BundleStatus.TARGET_DEVIATION_EXCEEDED
    assert decision.bundle is None


def test_future_frame_required_and_waiter_wakes_on_offer() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    for seq, ts in enumerate((8, 9, 9.6, 10), 1): buffer.offer(_frame(seq, ts))
    plan = _plan()
    assert buffer.try_build_bundle(plan).status is BundleStatus.WAITING_FOR_FUTURE_FRAME
    result = []
    worker = threading.Thread(target=lambda: result.append(buffer.wait_for_bundle(plan, .5)))
    worker.start()
    assert buffer.offer(_frame(5, 10.4))
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert result[0].status is BundleStatus.READY


def test_wait_timeout_and_end_wakeup_are_clean() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    assert buffer.wait_for_bundle(_plan(), 0).status is BundleStatus.WAITING_FOR_FUTURE_FRAME
    result = []
    worker = threading.Thread(target=lambda: result.append(buffer.wait_for_bundle(_plan(), 1)))
    worker.start()
    buffer.end_session("one")
    worker.join(timeout=1)
    assert not worker.is_alive() and result[0].status is BundleStatus.ENDED


def test_close_wakes_waiter_without_fabricating_future_frame() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    result = []
    worker = threading.Thread(target=lambda: result.append(buffer.wait_for_bundle(_plan(), 1)))
    worker.start()
    buffer.close()
    worker.join(timeout=1)
    assert not worker.is_alive() and result[0].status is BundleStatus.CLOSED
    assert result[0].bundle is None


def test_missing_pre_history_never_duplicates_later_frame() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    for seq, ts in enumerate((9, 9.6, 10, 10.4), 1): buffer.offer(_frame(seq, ts))
    decision = buffer.try_build_bundle(_plan())
    assert decision.status is BundleStatus.INSUFFICIENT_HISTORY and decision.bundle is None


def test_target_deviation_exceeded_when_gap_inside_history() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    for seq, ts in enumerate((8, 9, 9.6, 10.4), 1): buffer.offer(_frame(seq, ts))
    assert buffer.try_build_bundle(_plan()).status is BundleStatus.TARGET_DEVIATION_EXCEEDED


def test_unknown_and_malformed_plan_rejected_but_retail_generic_plan_allowed() -> None:
    buffer = _ready_buffer()
    assert buffer.try_build_bundle(_plan(domain=DomainName.UNKNOWN)).status is BundleStatus.INVALID_PLAN
    assert buffer.try_build_bundle(replace(_plan(), required_frame_count=4)).status is BundleStatus.INVALID_PLAN
    assert buffer.try_build_bundle(_plan(session="other")).status is BundleStatus.SESSION_MISMATCH
    assert buffer.try_build_bundle(_plan(domain=DomainName.RETAIL_STORE)).status is BundleStatus.READY


def test_parking_factory_has_exact_five_offsets() -> None:
    plan = make_parking_frame_selection_plan("event", "one", 10)
    assert plan.domain is DomainName.PARKING_MOTORCYCLE
    assert plan.target_offsets_seconds == (-2.0, -1.0, -.4, 0.0, .4)
    assert plan.required_frame_count == 5 and plan.maximum_target_deviation_seconds == .3


def test_retail_factory_is_separate_with_exact_five_offsets() -> None:
    plan = make_retail_frame_selection_plan("event", "one", 10)
    assert plan.domain is DomainName.RETAIL_STORE
    assert plan.target_offsets_seconds == (-2.0, -1.0, -.4, 0.0, .4)
    assert plan.required_frame_count == 5 and plan.maximum_target_deviation_seconds == .3
    assert make_retail_frame_selection_plan is not make_parking_frame_selection_plan


def test_retail_plan_selects_exactly_five_frames_when_available() -> None:
    buffer = EventFrameBuffer(); buffer.start_session("one")
    for seq in range(36):                                      # 7.0 .. 10.5 s at 10 fps
        assert buffer.offer(_frame(seq, 7.0 + seq / 10))
    decision = buffer.try_build_bundle(make_retail_frame_selection_plan("event", "one", 10.0))
    assert decision.status is BundleStatus.READY
    frames = decision.bundle.frames
    assert decision.bundle.domain is DomainName.RETAIL_STORE and len(frames) == 5
    assert tuple(frame.target_offset_seconds for frame in frames) == (-2.0, -1.0, -.4, 0.0, .4)
    assert all(frame.deviation_seconds <= .3 for frame in frames)


def test_concurrent_offer_and_snapshot_read_remain_consistent() -> None:
    buffer = EventFrameBuffer(max_frames=20, max_total_bytes=200); buffer.start_session("one")
    errors = []
    def writer() -> None:
        for seq in range(100):
            if not buffer.offer(_frame(seq, float(seq))): errors.append(seq)
    worker = threading.Thread(target=writer)
    worker.start()
    while worker.is_alive():
        snap = buffer.get_snapshot()
        assert 0 <= snap.frame_count <= 20
        assert snap.total_jpeg_bytes == snap.frame_count * 10
    worker.join(timeout=1)
    assert not errors
    assert buffer.get_snapshot().frame_count <= 20
