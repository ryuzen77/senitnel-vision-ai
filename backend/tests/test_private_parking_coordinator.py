"""T3b coordinator and frame-sink tee. Fakes record the call order; scenarios with the
real trigger and sampler use synthetic tracks and frames. No video, model, network,
runtime wiring or speaker."""

from __future__ import annotations

import logging
import threading

import pytest

from app.models.event_frame import BufferedFrame
from app.models.private_parking import CandidateOutcome, CandidateToken, LocalProximityAlert, MarkerBatch
from app.models.private_parking_evidence import EvidenceFailure, SealResult
from app.services.private_parking_coordinator import (
    FrameSinkAdmissionError,
    FrameSinkTee,
    PrivateParkingCoordinator,
    SessionCleanupError,
)


def _alert(n: int, session: str = "s1") -> LocalProximityAlert:
    token = CandidateToken(1, n, 100 + n, f"ev{n}")
    return LocalProximityAlert(f"ev{n}", session, f"ep{n}", n, 50 + n, "z1", 10.0 + n, 100.0, token)


class FakeTrigger:
    def __init__(self, log: list) -> None:
        self.log = log
        self.alerts: list = []
        self.batches: list = []
        self.publish_result = CandidateOutcome.PUBLISHED
        self.publish_error: Exception | None = None
        self.pop_markers_error: Exception | None = None
        self.end_error: Exception | None = None

    def start_session(self, session_id):
        self.log.append(("trigger.start", session_id))

    def end_session(self, session_id):
        self.log.append(("trigger.end", session_id))
        if self.end_error is not None:
            raise self.end_error

    def observe(self, domain, tracks):
        self.log.append(("trigger.observe",))

    def pop_markers(self):
        self.log.append(("trigger.pop_markers",))
        if self.pop_markers_error is not None:
            raise self.pop_markers_error
        return self.batches.pop(0) if self.batches else MarkerBatch("s1", 1, 0, ())

    def pop_alerts(self):
        self.log.append(("trigger.pop_alerts",))
        alerts, self.alerts = tuple(self.alerts), []
        return alerts

    def publish_candidate(self, token):
        self.log.append(("trigger.publish", token.event_id))
        if self.publish_error is not None:
            raise self.publish_error
        return self.publish_result

    def abandon_candidate(self, token):
        self.log.append(("trigger.abandon", token.event_id))
        return CandidateOutcome.ABANDONED

    def request_resync(self):
        self.log.append(("trigger.request_resync",))


class FakeSampler:
    def __init__(self, log: list) -> None:
        self.log = log
        self.apply_results: list = []
        self.seal_failure: EvidenceFailure | None = None
        self.seal_error: Exception | None = None

    def apply_markers(self, batch):
        self.log.append(("sampler.apply", batch.view is not None))
        result = self.apply_results.pop(0) if self.apply_results else True
        if isinstance(result, Exception):
            raise result
        return result

    def seal(self, session_id, episode_id, event_id, triggered_at, *, token):
        self.log.append(("sampler.seal", session_id, episode_id, event_id, token))
        if self.seal_error is not None:
            raise self.seal_error
        if self.seal_failure is not None:
            return SealResult(None, self.seal_failure)
        return SealResult(object(), None)

    def release(self, event_id, *, token):
        self.log.append(("sampler.release", event_id, token))
        return True


class FakeGate:
    def __init__(self, log: list, error: Exception | None = None) -> None:
        self.log, self.error = log, error

    def offer_local(self, event_id, session_id):
        self.log.append(("gate.offer_local", event_id, session_id))
        if self.error is not None:
            raise self.error


def _rig(*, gate=True, gate_error=None, **kwargs):
    log: list = []
    trigger, sampler = FakeTrigger(log), FakeSampler(log)
    audio = FakeGate(log, gate_error) if gate else None
    return PrivateParkingCoordinator(trigger, sampler, audio_gate=audio, **kwargs), trigger, sampler, log


def _kinds(log):
    return [entry[0] for entry in log]


# ── order (K6) and identity ──────────────────────────────────────────────────

def test_observe_order_markers_then_alerts_audio_first_then_markers_again() -> None:
    coordinator, trigger, _, log = _rig()
    trigger.alerts = [_alert(1)]
    coordinator.observe("domain", "tracks")
    assert _kinds(log) == ["trigger.observe", "trigger.pop_markers", "sampler.apply", "trigger.pop_alerts",
                           "gate.offer_local", "sampler.seal", "trigger.publish",
                           "trigger.pop_markers", "sampler.apply"]


def test_each_alert_is_prepared_with_its_own_identity_only() -> None:
    coordinator, trigger, _, log = _rig()
    first, second = _alert(1), _alert(2)
    trigger.alerts = [first, second]
    coordinator.observe("domain", "tracks")
    seals = [entry[1:] for entry in log if entry[0] == "sampler.seal"]
    assert seals == [("s1", "ep1", "ev1", first.token), ("s1", "ep2", "ev2", second.token)]
    assert [entry for entry in log if entry[0] == "trigger.publish"] == [("trigger.publish", "ev1"),
                                                                      ("trigger.publish", "ev2")]
    audio = [entry[1] for entry in log if entry[0] == "gate.offer_local"]
    assert audio == ["ev1", "ev2"] and all(log.index(("gate.offer_local", f"ev{n}", "s1")) <
                                           [i for i, e in enumerate(log) if e[0] == "sampler.seal"][n - 1]
                                           for n in (1, 2))


@pytest.mark.parametrize("case", ["seal_failure", "seal_error"])
def test_evidence_failure_abandons_and_never_publishes(case) -> None:
    coordinator, trigger, sampler, log = _rig()
    if case == "seal_failure":
        sampler.seal_failure = EvidenceFailure.INSUFFICIENT_JOURNEY
    else:
        sampler.seal_error = RuntimeError("boom")
    trigger.alerts = [_alert(1)]
    coordinator.observe("domain", "tracks")
    assert ("trigger.abandon", "ev1") in log and not any(e[0] == "trigger.publish" for e in log)
    assert ("gate.offer_local", "ev1", "s1") in log                    # audio already ran


@pytest.mark.parametrize("case", ["refused", "error"])
def test_publication_that_does_not_succeed_releases_the_evidence_with_the_own_token(case) -> None:
    coordinator, trigger, _, log = _rig()
    alert = _alert(1)
    if case == "refused":
        trigger.publish_result = CandidateOutcome.EXPIRED
    else:
        trigger.publish_error = RuntimeError("boom")
    trigger.alerts = [alert]
    coordinator.observe("domain", "tracks")
    assert ("sampler.release", "ev1", alert.token) in log


# ── audio optional (K3) ──────────────────────────────────────────────────────

def test_without_a_gate_evidence_is_still_prepared() -> None:
    coordinator, trigger, _, log = _rig(gate=False)
    trigger.alerts = [_alert(1)]
    coordinator.observe("domain", "tracks")
    assert ("trigger.publish", "ev1") in log and not any(e[0] == "gate.offer_local" for e in log)


def test_a_failing_gate_is_logged_sanitized_and_evidence_continues(caplog) -> None:
    coordinator, trigger, _, log = _rig(gate_error=RuntimeError("secret detail ev1"))
    trigger.alerts = [_alert(1)]
    with caplog.at_level(logging.WARNING):
        coordinator.observe("domain", "tracks")
    assert ("trigger.publish", "ev1") in log
    assert "local audio offer failed (RuntimeError)" in caplog.text
    assert "secret detail" not in caplog.text and "ev1" not in caplog.text
    assert coordinator.get_counts()["audio_failures"] == 1


# ── marker sync failure holds publication ────────────────────────────────────

@pytest.mark.parametrize("failure", ["apply_raises", "apply_false", "pop_raises"])
def test_a_marker_sync_failure_holds_publication_until_a_view_is_applied(failure) -> None:
    coordinator, trigger, sampler, log = _rig()
    if failure == "apply_raises":
        sampler.apply_results = [RuntimeError("apply failed")]
    elif failure == "apply_false":
        sampler.apply_results = [False]
    else:
        trigger.pop_markers_error = RuntimeError("pop failed")
    trigger.alerts = [_alert(1)]
    coordinator.observe("domain", "tracks")
    trigger.pop_markers_error = None
    assert ("gate.offer_local", "ev1", "s1") in log                    # audio still ran
    assert not any(e[0] in ("sampler.seal", "trigger.publish") for e in log)
    assert ("trigger.request_resync",) in log
    assert coordinator.get_counts()["held_alerts"] == 1
    log.clear()
    coordinator.observe("domain", "tracks")                            # a normal batch is not proof
    assert not any(e[0] in ("sampler.seal", "trigger.publish") for e in log)
    assert ("trigger.request_resync",) in log
    log.clear()
    trigger.batches = [MarkerBatch("s1", 1, 9, (), view=object())]     # the authoritative view
    coordinator.observe("domain", "tracks")
    assert ("trigger.publish", "ev1") in log
    counts = coordinator.get_counts()
    assert counts["held_alerts"] == 0 and counts["resyncs_applied"] == 1 and counts["resync_needed"] == 0


def test_held_alerts_are_bounded_and_dropped_ones_are_never_published() -> None:
    coordinator, trigger, sampler, log = _rig(max_held_alerts=2)
    sampler.apply_results = [False, False]
    trigger.alerts = [_alert(1), _alert(2), _alert(3)]
    coordinator.observe("domain", "tracks")
    assert coordinator.get_counts()["held_dropped"] == 1
    trigger.batches = [MarkerBatch("s1", 1, 9, (), view=object())]
    log.clear()
    coordinator.observe("domain", "tracks")
    assert [e[1] for e in log if e[0] == "trigger.publish"] == ["ev2", "ev3"]


def test_a_new_session_voids_held_alerts() -> None:
    coordinator, trigger, sampler, log = _rig()
    sampler.apply_results = [False, False]
    trigger.alerts = [_alert(1)]
    coordinator.observe("domain", "tracks")
    coordinator.start_session("s2")
    trigger.batches = [MarkerBatch("s2", 2, 1, (), view=object())]
    log.clear()
    coordinator.observe("domain", "tracks")
    assert not any(e[0] in ("sampler.seal", "trigger.publish") for e in log)


# ── close (K4) ───────────────────────────────────────────────────────────────

def test_close_stops_new_work_is_idempotent_and_never_closes_dependencies() -> None:
    coordinator, trigger, _, log = _rig()
    trigger.close = lambda: log.append(("trigger.close",))
    coordinator.close()
    coordinator.close()
    trigger.alerts = [_alert(1)]
    coordinator.observe("domain", "tracks")
    coordinator.start_session("s2")
    coordinator.end_session("s1")                                      # cleanup is still forwarded
    assert log == [("trigger.end", "s1")]
    assert coordinator.get_counts()["observations_after_close"] == 1


def test_a_trigger_that_does_not_end_the_session_is_reported_and_retried() -> None:
    coordinator, trigger, _, log = _rig()
    trigger.end_error = RuntimeError("secret detail s1")
    with pytest.raises(SessionCleanupError) as raised:
        coordinator.end_session("s1")
    assert "s1" not in str(raised.value) and raised.value.__suppress_context__
    trigger.end_error = None
    coordinator.end_session("s1")                                      # the retry ends it
    assert log == [("trigger.end", "s1"), ("trigger.end", "s1")]


def test_constructor_rejects_incomplete_dependencies() -> None:
    log: list = []
    with pytest.raises(TypeError):
        PrivateParkingCoordinator(object(), FakeSampler(log))
    with pytest.raises(TypeError):
        PrivateParkingCoordinator(FakeTrigger(log), FakeSampler(log), audio_gate=object())
    with pytest.raises(ValueError):
        PrivateParkingCoordinator(FakeTrigger(log), FakeSampler(log), max_held_alerts=0)


# ── tee ──────────────────────────────────────────────────────────────────────

class Sink:
    def __init__(self, name, log, *, fail=False):
        self.name, self.log, self.fail = name, log, fail
        self.frames: list = []
        self.end_failures = 0

    def offer(self, frame):
        self.log.append((self.name, "offer"))
        if self.fail:
            raise RuntimeError("sink failed")
        self.frames.append(frame)
        return True

    def start_session(self, session_id):
        self.log.append((self.name, "start", session_id))
        if self.fail:
            raise RuntimeError("sink failed")

    def end_session(self, session_id):
        self.log.append((self.name, "end", session_id))
        if self.fail or self.end_failures:
            self.end_failures = max(0, self.end_failures - 1)
            raise RuntimeError("sink failed")


def test_tee_forwards_the_same_frame_and_sessions_and_isolates_a_failing_sink(caplog) -> None:
    log: list = []
    failing, healthy = Sink("buffer", log, fail=True), Sink("sampler", log)
    tee = FrameSinkTee(failing, healthy)
    frame = BufferedFrame("s1", 1, 1.0, 64, 48, b"\xff\xd8x\xff\xd9")
    with caplog.at_level(logging.WARNING):
        tee.start_session("s1")
        assert tee.offer(frame) is True
        with pytest.raises(SessionCleanupError):
            tee.end_session("s1")                                      # reported, never a success
    assert healthy.frames == [frame] and healthy.frames[0] is frame
    assert log == [("buffer", "start", "s1"), ("sampler", "start", "s1"), ("buffer", "offer"),
                   ("sampler", "offer"), ("sampler", "end", "s1"), ("buffer", "end", "s1")]
    assert "frame sink offer failed (RuntimeError)" in caplog.text


def test_tee_tries_every_sink_then_retries_only_the_failed_ones() -> None:
    log: list = []
    first, second, third = Sink("a", log), Sink("b", log), Sink("c", log)
    first.end_failures = third.end_failures = 1
    tee = FrameSinkTee(first, second, third)
    tee.start_session("s1")
    log.clear()
    with pytest.raises(SessionCleanupError) as raised:
        tee.end_session("s1")
    assert log == [("c", "end", "s1"), ("b", "end", "s1"), ("a", "end", "s1")]   # all tried
    assert "s1" not in str(raised.value) and tee.pending_cleanups() == 1
    third.end_failures = 1                                             # c fails once more
    log.clear()
    with pytest.raises(SessionCleanupError):
        tee.end_session("s1")
    assert log == [("c", "end", "s1"), ("a", "end", "s1")]             # b is not ended twice
    log.clear()
    tee.end_session("s1")
    assert log == [("c", "end", "s1")] and tee.pending_cleanups() == 0


def test_tee_close_stops_frames_and_starts_but_forwards_ends() -> None:
    log: list = []
    tee = FrameSinkTee(Sink("buffer", log), Sink("sampler", log))
    tee.start_session("s1")
    log.clear()
    tee.close()
    tee.close()
    assert tee.offer(object()) is False
    tee.start_session("s2")                                            # ignored after close
    tee.end_session("s1")                                              # cleanup still forwarded
    assert log == [("sampler", "end", "s1"), ("buffer", "end", "s1")]
    tee.end_session("s2")                                              # never started: no obligation
    assert len(log) == 2


def _fill(tee: FrameSinkTee, failing: "Sink", n: int) -> None:
    """Start and fail to end ``n`` sessions (one failing sink each time)."""
    for k in range(n):
        tee.start_session(f"s{k}")
        failing.end_failures = 1
        with pytest.raises(SessionCleanupError):
            tee.end_session(f"s{k}")


def test_full_cleanup_capacity_keeps_every_old_obligation() -> None:
    log: list = []
    healthy, failing = Sink("buffer", log), Sink("sampler", log)
    tee = FrameSinkTee(healthy, failing, max_pending_cleanups=3)
    _fill(tee, failing, 3)
    assert tee.pending_cleanups() == 3
    with pytest.raises(FrameSinkAdmissionError):
        tee.start_session("s9")
    assert tee.pending_cleanups() == 3                                 # nothing evicted
    log.clear()
    for k in range(3):                                                 # every old session still ends
        tee.end_session(f"s{k}")
    assert log == [("sampler", "end", "s0"), ("sampler", "end", "s1"), ("sampler", "end", "s2")]
    assert tee.pending_cleanups() == 0


def test_an_extra_session_is_refused_before_any_sink_starts() -> None:
    log: list = []
    healthy, failing = Sink("buffer", log), Sink("sampler", log)
    tee = FrameSinkTee(healthy, failing, max_pending_cleanups=2)
    _fill(tee, failing, 2)
    log.clear()
    for session in ("s9", "s0"):                                       # capacity full / same id pending
        with pytest.raises(FrameSinkAdmissionError) as raised:
            tee.start_session(session)
        assert session not in str(raised.value) and raised.value.__cause__ is None
    assert log == []                                                   # no sink was touched
    tee.end_session("s0")                                              # free one slot
    tee.start_session("s9")
    log.clear()
    with pytest.raises(FrameSinkAdmissionError):
        tee.start_session("s10")                                       # another session still active
    assert log == []
    counts = tee.get_diagnostics()
    assert (counts["starts_refused_capacity"], counts["starts_refused_pending"],
            counts["starts_refused_active"]) == (1, 1, 1)


def test_a_retry_never_ends_a_sink_that_already_ended() -> None:
    log: list = []
    first, second = Sink("buffer", log), Sink("sampler", log)
    tee = FrameSinkTee(first, second, max_pending_cleanups=1)
    _fill(tee, second, 1)                                              # at full capacity
    log.clear()
    second.end_failures = 1
    with pytest.raises(SessionCleanupError):
        tee.end_session("s0")                                          # retry runs at full capacity
    tee.end_session("s0")
    assert log == [("sampler", "end", "s0"), ("sampler", "end", "s0")]  # the buffer is never ended again


def test_a_successful_cleanup_frees_its_slot() -> None:
    log: list = []
    healthy, failing = Sink("buffer", log), Sink("sampler", log)
    tee = FrameSinkTee(healthy, failing, max_pending_cleanups=1)
    _fill(tee, failing, 1)
    with pytest.raises(FrameSinkAdmissionError):
        tee.start_session("s1")
    tee.end_session("s0")
    assert tee.get_diagnostics()["free_slots"] == 1
    log.clear()
    tee.start_session("s1")                                            # admitted again
    assert log == [("buffer", "start", "s1"), ("sampler", "start", "s1")]
    tee.end_session("s1")
    assert tee.get_diagnostics()["free_slots"] == 1


def test_diagnostics_count_every_open_obligation_without_ids() -> None:
    log: list = []
    sinks = [Sink(name, log) for name in ("a", "b", "c")]
    tee = FrameSinkTee(*sinks, max_pending_cleanups=4)
    tee.start_session("s0")
    sinks[0].end_failures = sinks[2].end_failures = 1
    with pytest.raises(SessionCleanupError):
        tee.end_session("s0")                                          # two sinks still to end
    tee.start_session("s1")
    sinks[1].end_failures = 1
    with pytest.raises(SessionCleanupError):
        tee.end_session("s1")                                          # one sink still to end
    tee.start_session("s2")                                            # active
    counts = tee.get_diagnostics()
    assert (counts["active_sessions"], counts["pending_sessions"], counts["pending_sink_ends"],
            counts["free_slots"], counts["capacity"], counts["end_failures"]) == (1, 2, 3, 1, 4, 2)
    assert not any(isinstance(value, str) for value in counts.values())
    tee.end_session("s0")
    tee.end_session("s1")
    tee.end_session("s2")
    counts = tee.get_diagnostics()
    assert (counts["active_sessions"], counts["pending_sessions"], counts["pending_sink_ends"],
            counts["free_slots"], counts["cleanups_completed"]) == (0, 0, 0, 4, 3)


def test_tee_requires_complete_sinks() -> None:
    with pytest.raises(ValueError):
        FrameSinkTee()
    with pytest.raises(TypeError):
        FrameSinkTee(object())
    with pytest.raises(ValueError):
        FrameSinkTee(Sink("a", []), max_pending_cleanups=0)


def test_tee_in_the_video_buffer_slot_starts_first_and_ends_last(monkeypatch) -> None:
    from app.models.video import VideoState
    from app.services.event_frame_buffer import EventFrameBuffer
    from app.services.private_parking_evidence_sampler import EpisodeEvidenceSampler
    from app.services.video_service import VideoService
    from tests.conftest import FakeCapture

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_a, **_k: FakeCapture(opened=True))
    log: list = []
    buffer, sampler = EventFrameBuffer(), EpisodeEvidenceSampler()

    class Worker:
        def start_session(self, session_id):
            log.append(("worker", "start"))

        def end_session(self, session_id):
            log.append(("worker", "end"))

    class Recording(FrameSinkTee):
        def start_session(self, session_id):
            log.append(("tee", "start"))
            super().start_session(session_id)

        def end_session(self, session_id):
            log.append(("tee", "end"))
            super().end_session(session_id)

    tee = Recording(buffer, sampler)
    service = VideoService(event_frame_buffer=tee, evaluation_worker=Worker())
    try:
        assert service.start_webcam()
        deadline = threading.Event()
        for _ in range(500):
            if sampler.get_snapshot().held_frames and buffer.get_snapshot().source_session_id:
                break
            deadline.wait(0.01)
        assert sampler.get_snapshot().held_frames > 0
    finally:
        service.stop()
    assert service.get_status().state is VideoState.IDLE
    assert log == [("tee", "start"), ("worker", "start"), ("worker", "end"), ("tee", "end")]
    assert sampler.get_snapshot().held_bytes == 0 and sampler.get_snapshot().source_session_id is None


def test_a_tee_sink_that_fails_to_end_a_fails_the_video_cleanup_until_stop_retries(monkeypatch) -> None:
    from app.models.video import VideoState
    from app.services.event_frame_buffer import EventFrameBuffer
    from app.services.video_service import VideoService
    from tests.conftest import FakeCapture

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture",
                        lambda *_a, **_k: FakeCapture(opened=True))
    log: list = []

    class FailingSampler(Sink):
        """Fails before clearing its session (it would still hold A's evidence)."""

        def __init__(self) -> None:
            super().__init__("sampler", log)
            self.active = None

        def start_session(self, session_id):
            super().start_session(session_id)
            self.active = session_id

        def end_session(self, session_id):
            super().end_session(session_id)                            # raises while end_failures > 0
            if self.active == session_id:
                self.active = None

    buffer, sampler = EventFrameBuffer(), FailingSampler()
    sampler.end_failures = 1
    service = VideoService(event_frame_buffer=FrameSinkTee(buffer, sampler))
    try:
        assert service.start_webcam()
        session_a = service._current                                   # noqa: SLF001
        service.stop()
        assert service.get_status().state is VideoState.ERROR
        assert session_a.cleanup_done.is_set() and session_a.cleanup_ok is False
        assert sampler.active == session_a.session_id                  # the sink still holds A
        assert buffer.get_snapshot().source_session_id is None         # the other sink did end
        assert not service.start_webcam()                              # B refused
        log.clear()
        service.stop()                                                 # explicit Stop retries
        assert log == [("sampler", "end", session_a.session_id)]       # only the failed sink
        assert session_a.cleanup_ok is True and service.get_status().state is VideoState.IDLE
        assert sampler.active is None
        assert service.start_webcam()                                  # B accepted
    finally:
        service.stop()
    assert service.get_status().state is VideoState.IDLE
