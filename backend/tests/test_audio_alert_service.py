"""Gate v3 audio alert tests with fake devices, clocks and gates; never a real speaker."""

from __future__ import annotations

import logging
import sys
import threading
import time
import types
import wave

import pytest

from app.models.evaluation import ThreatStatus
from app.services.audio_alert_service import (
    AudioAlertGate, WinsoundDevice, probe_wav_duration,
)

CRITICAL, WARNING, NORMAL = ThreatStatus.CRITICAL, ThreatStatus.WARNING, ThreatStatus.NORMAL
PATH = "C:/outside/alert.wav"


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeDevice:
    def __init__(self, *, start_gate: threading.Event | None = None,
                 start_error: Exception | None = None, stop_error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.in_start = threading.Event()
        self.start_gate = start_gate
        self.start_error = start_error
        self.stop_error = stop_error

    def start(self, path: str) -> None:
        self.calls.append("start")
        self.in_start.set()
        if self.start_gate is not None:
            assert self.start_gate.wait(5)
        if self.start_error is not None:
            raise self.start_error

    def stop(self) -> None:
        self.calls.append("stop")
        if self.stop_error is not None:
            raise self.stop_error


def _gate(device: FakeDevice | None = None, *, clock: FakeClock | None = None,
          duration: float = 0.01, cooldown: float = 30.0, join: float = 2.0):
    device = device or FakeDevice()
    clock = clock or FakeClock()
    gate = AudioAlertGate(device, PATH, duration, cooldown_seconds=cooldown, clock=clock,
                          join_timeout_seconds=join)
    gate.apply_lifecycle(1, "A")
    return gate, device, clock


def _pause_after_grant(gate: AudioAlertGate):
    """Hold the device thread between GRANTED and the start mark."""
    paused, resume = threading.Event(), threading.Event()
    original = gate._execute

    def held(attempt):
        paused.set()
        assert resume.wait(5)
        original(attempt)

    gate._execute = held
    return paused, resume


def _in_thread(target, *args) -> threading.Thread:
    thread = threading.Thread(target=target, args=args)
    thread.start()
    return thread


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()


# ── configuration of the gate ────────────────────────────────────────────────

@pytest.mark.parametrize("kwargs", [
    {"duration_seconds": 0}, {"duration_seconds": 8.01}, {"duration_seconds": True},
    {"cooldown_seconds": 9.9}, {"cooldown_seconds": 120.1}, {"cooldown_seconds": float("nan")},
    {"join_timeout_seconds": 2.01}, {"join_timeout_seconds": -1}, {"wav_path": ""},
])
def test_constructor_rejects_invalid_values(kwargs) -> None:
    arguments = {"device": FakeDevice(), "wav_path": "x.wav", "duration_seconds": 4.0} | kwargs
    with pytest.raises(ValueError):
        AudioAlertGate(arguments.pop("device"), arguments.pop("wav_path"),
                       arguments.pop("duration_seconds"), **arguments)


def test_constructor_requires_start_and_stop() -> None:
    class StartOnly:
        def start(self, path): ...
    with pytest.raises(TypeError):
        AudioAlertGate(StartOnly(), "x.wav", 4.0)


# ── control plane rules ──────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [NORMAL, WARNING])
def test_only_critical_is_a_candidate(status) -> None:
    gate, device, _ = _gate()
    gate.offer(1, "e1", "A", status)
    assert not gate.run_once(0)
    assert device.calls == [] and gate.get_snapshot().dropped_not_critical == 1


def test_critical_plays_once_with_start_then_stop() -> None:
    gate, device, _ = _gate()
    gate.offer(1, "e1", "A", CRITICAL)
    assert gate.run_once(0)
    assert device.calls == ["start", "stop"]       # every started attempt ends with stop
    snapshot = gate.get_snapshot()
    assert (snapshot.granted, snapshot.completed, snapshot.attempt_state) == (1, 1, None)
    gate.offer(1, "e1", "A", CRITICAL)             # replay of the same event
    assert not gate.run_once(0)
    assert device.calls == ["start", "stop"] and gate.get_snapshot().dropped_duplicate == 1


@pytest.mark.parametrize("epoch,session", [(0, "A"), (2, "A"), (1, "B"), (1, None)])
def test_offer_never_creates_or_changes_lifecycle(epoch, session) -> None:
    gate, device, _ = _gate()
    gate.offer(epoch, "e1", session, CRITICAL)
    snapshot = gate.get_snapshot()
    assert (snapshot.epoch, snapshot.session_short_id) == (1, "A")
    assert snapshot.dropped_stale == 1 and snapshot.queued_event_short_id is None
    assert not gate.run_once(0) and device.calls == []


def test_lifecycle_epoch_is_monotonic() -> None:
    gate, _, _ = _gate()
    gate.apply_lifecycle(3, "C")
    gate.apply_lifecycle(2, "B")                   # older transition delivered late: ignored
    gate.apply_lifecycle(3, "X")                   # same epoch: ignored
    snapshot = gate.get_snapshot()
    assert (snapshot.epoch, snapshot.session_short_id) == (3, "C")


def test_first_wins_and_cooldown_drop_instead_of_defer() -> None:
    gate, device, clock = _gate(cooldown=30)
    gate.offer(1, "e1", "A", CRITICAL)
    gate.offer(1, "e2", "A", CRITICAL)             # slot taken: newer one dropped
    assert gate.run_once(0)
    clock.now += 29.9
    gate.offer(1, "e3", "A", CRITICAL)             # cooldown: dropped, never scheduled later
    clock.now += 0.2
    assert not gate.run_once(0)
    gate.offer(1, "e4", "A", CRITICAL)
    assert gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert (snapshot.dropped_busy, snapshot.dropped_cooldown, snapshot.granted) == (1, 1, 2)
    assert device.calls == ["start", "stop", "start", "stop"]


def test_concurrent_offers_overflow_stays_bounded() -> None:
    gate, device, _ = _gate()
    barrier = threading.Barrier(16)

    def offer(index: int) -> None:
        barrier.wait(2)
        gate.offer(1, f"e{index % 8}", "A", CRITICAL)

    threads = [_in_thread(offer, index) for index in range(16)]
    for thread in threads:
        thread.join(2)
    snapshot = gate.get_snapshot()
    assert snapshot.queued_event_short_id is not None
    assert snapshot.offered_critical == 16
    assert snapshot.dropped_busy + snapshot.dropped_duplicate == 15
    assert gate.run_once(0) and not gate.run_once(0)
    assert device.calls == ["start", "stop"]


def test_recent_event_guard_is_bounded() -> None:
    gate, _, clock = _gate(cooldown=10)
    for index in range(100):
        gate.offer(1, f"e{index}", "A", CRITICAL)
        assert gate.run_once(0)
        clock.now += 10
    assert len(gate._recent_events) == 64


# ── invalidation versus grant ────────────────────────────────────────────────

@pytest.mark.parametrize("invalidate", ["end", "new-session", "close"])
def test_invalidation_before_grant_means_no_grant(invalidate) -> None:
    gate, device, _ = _gate()
    gate.offer(1, "e1", "A", CRITICAL)
    {"end": lambda: gate.apply_lifecycle(2, None),
     "new-session": lambda: gate.apply_lifecycle(2, "B"),
     "close": gate.close}[invalidate]()
    assert not gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert device.calls == [] and snapshot.granted == 0 and snapshot.dropped_stale == 1


def test_grant_before_invalidation_but_before_start_mark_never_touches_device() -> None:
    gate, device, _ = _gate()
    paused, resume = _pause_after_grant(gate)
    gate.offer(1, "e1", "A", CRITICAL)
    runner = _in_thread(gate.run_once, 0)
    assert paused.wait(2)
    assert gate.get_snapshot().attempt_state == "GRANTED"
    invalidation = _in_thread(gate.apply_lifecycle, 2, "B")
    invalidation.join(1)
    assert not invalidation.is_alive()             # returned while the device thread is held
    resume.set()
    runner.join(2)
    snapshot = gate.get_snapshot()
    assert device.calls == []
    assert (snapshot.granted, snapshot.cancelled_before_start, snapshot.attempt_state) == (1, 1, None)


def test_invalidation_during_device_start_is_in_flight_then_stopped() -> None:
    start_gate = threading.Event()
    gate, device, _ = _gate(FakeDevice(start_gate=start_gate), duration=5.0)
    gate.offer(1, "e1", "A", CRITICAL)
    runner = _in_thread(gate.run_once, 0)
    assert device.in_start.wait(2)
    invalidation = _in_thread(gate.apply_lifecycle, 2, None)
    invalidation.join(1)
    assert not invalidation.is_alive()             # never waits for the device start
    start_gate.set()
    runner.join(2)
    assert not runner.is_alive()
    assert device.calls == ["start", "stop"]
    assert gate.get_snapshot().stopped == 1


def test_invalidation_while_in_flight_signals_stop_promptly() -> None:
    gate, device, _ = _gate(duration=5.0)
    gate.offer(1, "e1", "A", CRITICAL)
    runner = _in_thread(gate.run_once, 0)
    assert _wait_until(lambda: gate.get_snapshot().attempt_state == "IN_FLIGHT")
    gate.apply_lifecycle(2, "B")
    runner.join(2)
    assert not runner.is_alive()                    # did not wait the 5 s policy bound
    assert device.calls == ["start", "stop"] and gate.get_snapshot().stopped == 1


def test_old_cancel_never_reaches_a_new_attempt() -> None:
    gate, device, clock = _gate(duration=5.0)
    gate.offer(1, "e1", "A", CRITICAL)
    first = _in_thread(gate.run_once, 0)
    assert _wait_until(lambda: gate.get_snapshot().attempt_state == "IN_FLIGHT")
    old_attempt = gate._attempt
    gate.apply_lifecycle(2, "B")
    first.join(2)
    assert old_attempt.cancel.is_set()
    clock.now += 31
    gate._duration = 0.01                          # short second clip for the test
    gate.offer(2, "e2", "B", CRITICAL)
    assert gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert (snapshot.stopped, snapshot.completed) == (1, 1)   # second attempt was not cancelled
    assert device.calls == ["start", "stop", "start", "stop"]


# Defence in depth: the grant re-checks what offer checked. Through the public API an
# invalidation always clears the slot and a fault can only arise during an attempt, so
# these white-box tests plant the slot directly to keep the re-checks from regressing.

def test_grant_rechecks_epoch_of_a_planted_slot() -> None:
    from app.services.audio_alert_service import _Candidate

    gate, device, _ = _gate()
    gate.apply_lifecycle(2, "A")
    gate._slot = _Candidate("e1", 1)
    assert gate.run_once(0)                        # the slot is taken, then refused
    assert device.calls == [] and gate.get_snapshot().granted == 0


def test_grant_rechecks_device_fault_of_a_planted_slot() -> None:
    from app.services.audio_alert_service import _Candidate

    gate, device, _ = _gate()
    gate._device_fault = True
    gate._slot = _Candidate("e1", 1)
    assert gate.run_once(0)                        # the slot is taken, then refused
    assert device.calls == [] and gate.get_snapshot().granted == 0


# ── lifecycle fault: fail closed ─────────────────────────────────────────────

def test_fail_closed_drops_queued_slot_and_blocks_every_later_grant() -> None:
    gate, device, clock = _gate()
    gate.offer(1, "e1", "A", CRITICAL)
    gate.fail_closed()
    assert not gate.run_once(0)
    clock.now += 60
    gate.apply_lifecycle(2, "B")                   # ignored: the gate stays closed
    gate.offer(2, "e2", "B", CRITICAL)
    gate.offer(1, "e3", "A", CRITICAL)
    assert not gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert device.calls == [] and snapshot.granted == 0
    assert snapshot.closed and snapshot.lifecycle_fault and snapshot.dropped_stale == 3
    assert (snapshot.epoch, snapshot.session_short_id) == (1, "A")


def test_fail_closed_cancels_a_granted_attempt_before_its_start() -> None:
    gate, device, _ = _gate()
    paused, resume = _pause_after_grant(gate)
    gate.offer(1, "e1", "A", CRITICAL)
    runner = _in_thread(gate.run_once, 0)
    assert paused.wait(2)
    gate.fail_closed()
    resume.set()
    runner.join(2)
    assert device.calls == [] and gate.get_snapshot().cancelled_before_start == 1


def test_fail_closed_signals_the_in_flight_attempt_without_waiting_for_the_device() -> None:
    start_gate = threading.Event()
    gate, device, _ = _gate(FakeDevice(start_gate=start_gate), duration=5.0)
    gate.offer(1, "e1", "A", CRITICAL)
    runner = _in_thread(gate.run_once, 0)
    assert device.in_start.wait(2)
    closing = _in_thread(gate.fail_closed)
    closing.join(1)
    assert not closing.is_alive()                   # returned while device.start is held
    start_gate.set()
    runner.join(2)
    assert not runner.is_alive()                    # cancel cut the 5 s clip wait short
    assert device.calls == ["start", "stop"] and gate.get_snapshot().stopped == 1


def test_close_without_a_started_thread_reports_stopped() -> None:
    gate, _, _ = _gate()
    assert gate.close() is True and gate.close() is True


# ── device failures ──────────────────────────────────────────────────────────

def test_start_with_effect_then_failure_gets_best_effort_stop_and_no_retry() -> None:
    gate, device, clock = _gate(FakeDevice(start_error=RuntimeError("driver error after start")))
    gate.offer(1, "e1", "A", CRITICAL)
    assert gate.run_once(0)
    assert device.calls == ["start", "stop"]
    snapshot = gate.get_snapshot()
    assert (snapshot.failed, snapshot.device_fault) == (1, False)
    clock.now += 60
    gate.offer(1, "e1", "A", CRITICAL)             # same event: never retried
    assert not gate.run_once(0)
    assert device.calls == ["start", "stop"]


def test_failed_stop_enters_device_fault_and_blocks_new_grants(caplog) -> None:
    gate, device, clock = _gate(FakeDevice(stop_error=RuntimeError("stop failed")))
    gate.offer(1, "e1", "A", CRITICAL)
    with caplog.at_level(logging.WARNING):
        assert gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert snapshot.device_fault and snapshot.stop_failures == 1 and snapshot.failed == 1
    assert "audio disabled until restart" in caplog.text
    device.stop_error = None
    for epoch, session in ((1, "A"), (2, "B")):
        clock.now += 60
        gate.apply_lifecycle(epoch, session)
        gate.offer(epoch, f"later-{epoch}", session, CRITICAL)
        assert not gate.run_once(0)
    assert device.calls == ["start", "stop"]
    assert gate.get_snapshot().dropped_device_fault == 2


# ── stalled device: control plane stays responsive ───────────────────────────

def test_stalled_device_start_keeps_lifecycle_offer_and_snapshot_responsive(caplog) -> None:
    start_gate = threading.Event()
    clock = FakeClock()
    gate, device, _ = _gate(FakeDevice(start_gate=start_gate), clock=clock, join=0.2)
    gate.start()
    try:
        gate.offer(1, "e1", "A", CRITICAL)
        assert device.in_start.wait(2)
        calls = [
            _in_thread(gate.apply_lifecycle, 2, "B"),
            _in_thread(gate.offer, 2, "e2", "B", CRITICAL),
            _in_thread(gate.get_snapshot),
        ]
        for call in calls:
            call.join(1)
            assert not call.is_alive()
        snapshot = gate.get_snapshot()
        assert snapshot.attempt_state == "STARTING" and not snapshot.start_stalled
        clock.now += 2.1
        assert gate.get_snapshot().start_stalled    # indicator only
        assert gate.get_snapshot().dropped_cooldown == 1   # cooldown starts at grant
        with caplog.at_level(logging.WARNING):
            assert gate.close() is False             # bounded join, reported as stuck
        assert "did not stop in time" in caplog.text
        assert gate.close() is False                 # still stuck: never reported as stopped
    finally:
        start_gate.set()
    assert _wait_until(lambda: not gate.get_snapshot().running)
    assert gate.close() is True                      # the thread has really finished now
    assert device.calls == ["start", "stop"]


def test_background_thread_close_is_bounded_and_idempotent() -> None:
    gate, device, _ = _gate(duration=5.0)
    gate.start()
    gate.start()
    gate.offer(1, "e1", "A", CRITICAL)
    assert _wait_until(lambda: gate.get_snapshot().attempt_state == "IN_FLIGHT")
    assert gate.close() is True                     # cancel wakes the clip wait; thread exits
    assert gate.close() is True
    assert device.calls == ["start", "stop"]
    gate.offer(1, "e2", "A", CRITICAL)
    gate.start()
    snapshot = gate.get_snapshot()
    assert snapshot.closed and not snapshot.running and snapshot.queued_event_short_id is None


# ── production device and asset probe ────────────────────────────────────────

class _FakeWinsound(types.ModuleType):
    SND_FILENAME, SND_ASYNC, SND_NODEFAULT = 0x20000, 0x1, 0x2

    def __init__(self) -> None:
        super().__init__("winsound")
        self.calls: list[tuple[object, int]] = []

    def PlaySound(self, sound, flags) -> None:  # noqa: N802
        self.calls.append((sound, flags))


def test_winsound_device_start_and_stop(monkeypatch) -> None:
    fake = _FakeWinsound()
    monkeypatch.setitem(sys.modules, "winsound", fake)
    device = WinsoundDevice()
    device.start(PATH)
    device.stop()
    assert fake.calls == [(PATH, fake.SND_FILENAME | fake.SND_ASYNC | fake.SND_NODEFAULT), (None, 0)]


def _write_wav(path, seconds: float, rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(b"\x00\x00" * int(seconds * rate))


def test_probe_wav_duration_reads_header_only(tmp_path) -> None:
    good = tmp_path / "alert.wav"
    _write_wav(good, 2.5)
    assert probe_wav_duration(good) == pytest.approx(2.5)
    empty = tmp_path / "empty.wav"
    _write_wav(empty, 0)
    assert probe_wav_duration(empty) is None
    text = tmp_path / "not.wav"
    text.write_text("not audio")
    assert probe_wav_duration(text) is None
    assert probe_wav_duration(tmp_path / "missing.wav") is None


# ── local access warning (T3a1): separate clip, grant-based dedup ────────────

LOCAL_PATH = "C:/outside/local-warning.wav"


class PathDevice(FakeDevice):
    """Records which clip each start used."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.paths: list[str] = []

    def start(self, path: str) -> None:
        self.paths.append(path)
        super().start(path)


def _local_gate(device: FakeDevice | None = None, *, clock: FakeClock | None = None, local: bool = True,
                duration: float = 0.01, cooldown: float = 30.0):
    device = device or PathDevice()
    clock = clock or FakeClock()
    extra = {"local_wav_path": LOCAL_PATH, "local_duration_seconds": duration} if local else {}
    gate = AudioAlertGate(device, PATH, duration, cooldown_seconds=cooldown, clock=clock,
                          join_timeout_seconds=2.0, **extra)
    gate.apply_lifecycle(1, "A")
    return gate, device, clock


@pytest.mark.parametrize("kwargs", [
    {"local_wav_path": LOCAL_PATH},                                   # duration missing
    {"local_duration_seconds": 1.0},                                  # path missing
    {"local_wav_path": "", "local_duration_seconds": 1.0},
    {"local_wav_path": "x.wav", "local_duration_seconds": 1.0},       # same clip as CRITICAL
    {"local_wav_path": "./x.wav", "local_duration_seconds": 1.0},     # same clip, other spelling
    {"local_wav_path": LOCAL_PATH, "local_duration_seconds": 0},
    {"local_wav_path": LOCAL_PATH, "local_duration_seconds": 8.01},
    {"local_wav_path": LOCAL_PATH, "local_duration_seconds": True},
    {"local_wav_path": LOCAL_PATH, "local_duration_seconds": float("nan")},
])
def test_local_clip_configuration_is_validated(kwargs) -> None:
    with pytest.raises(ValueError):
        AudioAlertGate(FakeDevice(), "x.wav", 4.0, **kwargs)


def test_local_and_critical_clips_are_never_swapped() -> None:
    gate, device, clock = _local_gate()
    gate.offer_local("e1", "A")
    assert gate.run_once(0)
    clock.now += 31
    gate.offer(1, "e2", "A", CRITICAL)
    assert gate.run_once(0)
    clock.now += 31
    gate.offer_local("e3", "A")
    assert gate.run_once(0)
    assert device.paths == [LOCAL_PATH, PATH, LOCAL_PATH]
    snapshot = gate.get_snapshot()
    assert (snapshot.granted, snapshot.granted_local, snapshot.local_clip_configured) == (3, 2, True)


def test_snapshot_reports_the_clip_kind_of_the_attempt() -> None:
    gate, _, _ = _local_gate(duration=5.0)
    gate.offer_local("e1", "A")
    runner = _in_thread(gate.run_once, 0)
    assert _wait_until(lambda: gate.get_snapshot().attempt_state == "IN_FLIGHT")
    assert gate.get_snapshot().attempt_clip == "LOCAL"
    gate.apply_lifecycle(2, None)                  # Stop cuts the local clip promptly
    runner.join(2)
    assert not runner.is_alive() and gate.get_snapshot().stopped == 1


@pytest.mark.parametrize("session", [None, "B"])
def test_local_offer_from_another_session_is_stale(session) -> None:
    gate, device, _ = _local_gate()
    gate.offer_local("e1", session)
    assert not gate.run_once(0)
    assert device.paths == [] and gate.get_snapshot().dropped_stale == 1


def test_pending_local_offer_dies_when_the_lifecycle_moves_on() -> None:
    gate, device, _ = _local_gate()
    gate.offer_local("e1", "A")                    # queued, not yet granted
    gate.apply_lifecycle(2, "B")                   # Stop / new session
    gate.offer_local("e1", "A")                    # late offer from the old session
    assert not gate.run_once(0)
    assert device.paths == [] and gate.get_snapshot().dropped_stale == 2


def test_planted_local_slot_with_an_old_epoch_is_refused_at_grant() -> None:
    from app.services.audio_alert_service import AlertClip, _Candidate

    gate, device, _ = _local_gate()
    gate.apply_lifecycle(2, "A")                   # same session id, new lifecycle epoch
    gate._slot = _Candidate("e1", 1, AlertClip.LOCAL)
    assert gate.run_once(0)
    assert device.paths == [] and gate.get_snapshot().granted == 0


def test_missing_local_clip_disables_local_audio_and_lets_one_critical_through() -> None:
    gate, device, _ = _local_gate(local=False)
    gate.offer_local("e1", "A")
    assert not gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert snapshot.dropped_no_local_clip == 1 and not snapshot.local_clip_configured
    gate.offer(1, "e1", "A", CRITICAL)             # rejected before grant: not remembered
    assert gate.run_once(0)
    gate.offer(1, "e1", "A", CRITICAL)
    assert not gate.run_once(0)
    assert device.paths == [PATH]                  # once, with the CRITICAL clip, never a fallback
    assert gate.get_snapshot().dropped_duplicate == 1


@pytest.mark.parametrize("start_error", [None, RuntimeError("driver error")])
def test_local_grant_blocks_a_later_critical_for_the_same_event(start_error) -> None:
    gate, device, clock = _local_gate(PathDevice(start_error=start_error))
    gate.offer_local("e1", "A")
    assert gate.run_once(0)
    clock.now += 60                                # cooldown over: only dedup can refuse it
    gate.offer(1, "e1", "A", CRITICAL)
    assert not gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert device.paths == [LOCAL_PATH] and snapshot.granted == 1 and snapshot.dropped_duplicate == 1


def test_queued_local_offer_absorbs_a_critical_for_the_same_event() -> None:
    gate, device, _ = _local_gate()
    gate.offer_local("e1", "A")
    gate.offer(1, "e1", "A", CRITICAL)             # same event already in the slot
    assert gate.run_once(0) and not gate.run_once(0)
    assert device.paths == [LOCAL_PATH] and gate.get_snapshot().dropped_duplicate == 1


def test_shared_slot_and_cooldown_drop_other_events_with_reasons() -> None:
    gate, device, clock = _local_gate()
    gate.offer_local("e1", "A")
    gate.offer(1, "e2", "A", CRITICAL)             # slot busy: dropped, never queued
    assert gate.run_once(0)
    clock.now += 10
    gate.offer_local("e3", "A")                    # inside the shared cooldown
    gate.offer(1, "e4", "A", CRITICAL)
    assert not gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert (snapshot.dropped_busy, snapshot.dropped_cooldown) == (1, 2)
    assert device.paths == [LOCAL_PATH]


def test_rejected_local_offer_never_bypasses_cooldown_or_fault() -> None:
    gate, device, clock = _local_gate(PathDevice(stop_error=RuntimeError("stop failed")), local=True)
    gate.offer(1, "e1", "A", CRITICAL)
    assert gate.run_once(0)                        # granted; stop fails -> device fault
    clock.now += 60
    gate.offer_local("e2", "A")                    # rejected: device fault
    gate.offer(1, "e2", "A", CRITICAL)             # rejected too: the fault still holds
    assert not gate.run_once(0)
    assert gate.get_snapshot().dropped_device_fault == 2 and device.paths == [PATH]

    gate, device, clock = _local_gate(local=False)
    gate.offer(1, "e1", "A", CRITICAL)
    assert gate.run_once(0)
    clock.now += 5
    gate.offer_local("e2", "A")                    # rejected: no local clip (stores nothing)
    gate.offer(1, "e2", "A", CRITICAL)             # still inside the cooldown of e1
    assert not gate.run_once(0)
    snapshot = gate.get_snapshot()
    assert (snapshot.dropped_no_local_clip, snapshot.dropped_cooldown) == (1, 1)
    clock.now += 26
    gate.offer(1, "e2", "A", CRITICAL)             # after the cooldown: one grant for e2
    assert gate.run_once(0) and device.paths == [PATH, PATH]


def test_local_offer_after_fail_closed_is_refused() -> None:
    gate, device, _ = _local_gate()
    gate.fail_closed()
    gate.offer_local("e1", "A")
    assert not gate.run_once(0) and device.paths == []
