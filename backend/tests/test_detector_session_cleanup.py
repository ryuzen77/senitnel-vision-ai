"""T3c0.1 — DetectorService session lifecycle: generation-bound jobs, delivery versus
invalidation, cleanup obligations with retry, leases, implicit switch and close.

Fake model, fake dependencies and deterministic ``threading.Event`` gates only: no GPU,
model weights, video, network or speaker. Every wait is bounded and every helper thread
is joined and asserted finished.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from app.models.video import FramePacket
from app.services.detector_service import (
    DetectorAdmissionError,
    DetectorCleanupError,
    DetectorService,
    DetectorStartError,
)
from app.services.private_parking_coordinator import PrivateParkingCoordinator
from tests.test_private_parking_coordinator import FakeSampler, FakeTrigger

WAIT = 5.0


def _packet(session_id: str, seq: int) -> FramePacket:
    frame = np.full((2, 2, 3), seq, dtype=np.uint8)
    return FramePacket(session_id=session_id, seq=seq, ts=float(seq), frame=frame, jpeg=b"jpeg")


def _wait_for(predicate, timeout: float = WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _thread(target, *args) -> threading.Thread:
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


def _joined(*threads: threading.Thread) -> bool:
    for thread in threads:
        thread.join(WAIT)
    return not any(thread.is_alive() for thread in threads)


class Dep:
    """A detector dependency (result consumer, tracker or parking trigger)."""

    def __init__(self, name: str, log: list) -> None:
        self.name, self.log = name, log
        self.active: str | None = None
        self.mutate_then_raise = False
        self.end_errors = 0
        self.start_gate: threading.Event | None = None
        self.end_gate: threading.Event | None = None
        self.observe_gate: threading.Event | None = None
        self.in_start, self.in_end, self.in_observe = threading.Event(), threading.Event(), threading.Event()

    def start_session(self, session_id: str) -> None:
        self.log.append((self.name, "start", session_id))
        self.in_start.set()
        if self.start_gate is not None:
            assert self.start_gate.wait(WAIT)
        self.active = session_id
        if self.mutate_then_raise:
            raise RuntimeError(f"start failed for {session_id}")

    def end_session(self, session_id: str) -> None:
        self.log.append((self.name, "end", session_id))
        self.in_end.set()
        if self.end_gate is not None:
            assert self.end_gate.wait(WAIT)
        if self.end_errors:
            self.end_errors -= 1
            raise RuntimeError(f"end failed for {session_id}")
        if self.active == session_id:
            self.active = None

    def observe(self, *args):
        item = args[-1]
        self.log.append((self.name, "observe", item.source_frame_seq))
        self.in_observe.set()
        if self.observe_gate is not None:
            assert self.observe_gate.wait(WAIT)
        return ()

    def close(self) -> None:
        pass


class Rig:
    def __init__(self, *, deps: tuple[str, ...] = ("result consumer", "tracker", "parking trigger"),
                 parking_trigger=None) -> None:
        self.log: list = []
        self.deps = {name: Dep(name, self.log) for name in deps}
        self.model_gates: dict[int, threading.Event] = {}
        self.model_entered: dict[int, threading.Event] = {}

        def runner(frame):
            value = int(frame[0, 0, 0])
            if value in self.model_gates:
                self.model_entered[value].set()
                assert self.model_gates[value].wait(WAIT)
            return []

        self.detector = DetectorService(
            model_factory=lambda: runner,
            result_consumer=self.deps.get("result consumer"),
            tracker=self.deps.get("tracker"),
            parking_trigger=parking_trigger if parking_trigger is not None else self.deps.get("parking trigger"),
            domain_snapshot_provider=lambda: "domain",
        )

    def hold_inference(self, seq: int) -> threading.Event:
        self.model_gates[seq], self.model_entered[seq] = threading.Event(), threading.Event()
        return self.model_gates[seq]

    def calls(self, kind: str) -> list:
        return [entry for entry in self.log if entry[1] == kind]

    def diagnostics(self) -> dict:
        return self.detector.get_lifecycle_diagnostics()

    def close(self) -> None:
        for gate in self.model_gates.values():
            gate.set()
        for dep in self.deps.values():
            for gate in (dep.start_gate, dep.end_gate, dep.observe_gate):
                if gate is not None:
                    gate.set()
        self.detector.close()
        assert not self.detector._worker.is_alive()          # noqa: SLF001


@pytest.fixture
def rigs():
    created: list[Rig] = []

    def make(**kwargs) -> Rig:
        rig = Rig(**kwargs)
        created.append(rig)
        return rig

    yield make
    for rig in created:
        rig.close()


# ── 1. stale inference never reaches a newer session with the same id ────────

def test_a_held_inference_of_a_is_never_delivered_to_b_with_the_same_id(rigs) -> None:
    rig = rigs()
    release = rig.hold_inference(1)
    rig.detector.start_session("same", lease="op-a")
    rig.detector.offer(_packet("same", 1), lease="op-a")
    assert rig.model_entered[1].wait(WAIT)                               # A's inference in flight
    rig.detector.end_session("same", lease="op-a")
    rig.detector.start_session("same", lease="op-b")                     # same id, new operation
    release.set()
    assert _wait_for(lambda: rig.diagnostics()["stale_results"] == 1)
    assert rig.detector.get_latest_result() is None
    assert rig.calls("observe") == []                                     # no consumer saw A's result
    rig.detector.offer(_packet("same", 2), lease="op-a")                  # old lease: refused
    rig.detector.offer(_packet("same", 3), lease="op-b")
    assert _wait_for(lambda: len(rig.calls("observe")) == 3)              # consumer, tracker, trigger
    assert {entry[2] for entry in rig.calls("observe")} == {3}
    assert rig.detector.get_latest_result().source_frame_seq == 3


# ── 2. delivery versus invalidation: mutual exclusion ────────────────────────

def test_end_waits_for_an_in_flight_delivery_before_start_b_runs(rigs) -> None:
    rig = rigs()
    consumer = rig.deps["result consumer"]
    consumer.observe_gate = threading.Event()
    rig.detector.start_session("same", lease="op-a")
    rig.detector.offer(_packet("same", 1), lease="op-a")
    assert consumer.in_observe.wait(WAIT)                                 # delivery A, after the check
    ender = _thread(lambda: rig.detector.end_session("same", lease="op-a"))
    # deterministic proof that end A already holds the lifecycle lock and waits for the
    # delivery lock (held by the worker) before start B is issued
    assert _wait_for(lambda: rig.detector._lifecycle_lock.locked())     # noqa: SLF001
    assert rig.detector._delivery_lock.locked()                         # noqa: SLF001
    assert rig.calls("end") == []
    starter = _thread(lambda: rig.detector.start_session("same", lease="op-b"))
    consumer.observe_gate.set()
    assert _joined(ender, starter)
    sequence = [(entry[0], entry[1]) for entry in rig.log]
    first_end = sequence.index(("result consumer", "end"))
    assert sequence.index(("parking trigger", "observe")) < first_end    # A's delivery completed first
    assert sequence[-3:] == [("result consumer", "start"), ("tracker", "start"), ("parking trigger", "start")]
    observed_after_b = rig.log[len(rig.log) - 3:]
    assert all(entry[1] == "start" for entry in observed_after_b)        # B's state holds nothing from A
    assert rig.detector.get_latest_result() is None
    assert rig.diagnostics()["active_sessions"] == 1


# ── 3. partial start, several cleanup failures, staged retry ─────────────────

def test_partial_start_obligations_are_exactly_the_attempted_dependencies(rigs) -> None:
    rig = rigs()
    consumer, tracker, trigger = rig.deps["result consumer"], rig.deps["tracker"], rig.deps["parking trigger"]
    tracker.mutate_then_raise = True
    with pytest.raises(DetectorStartError) as started:
        rig.detector.start_session("s1", lease="op")
    assert "s1" not in str(started.value) and started.value.__suppress_context__
    assert tracker.active == "s1" and trigger.active is None and ("parking trigger", "start", "s1") not in rig.log
    consumer.end_errors = tracker.end_errors = 1
    with pytest.raises(DetectorCleanupError) as ended:
        rig.detector.end_session("s1", lease="op")
    assert "s1" not in str(ended.value)
    assert rig.calls("end") == [("result consumer", "end", "s1"), ("tracker", "end", "s1")]
    snapshot = rig.diagnostics()
    assert (snapshot["pending_dependency_ends"], snapshot["pending_result_consumer"], snapshot["pending_tracker"],
            snapshot["pending_parking_trigger"]) == (2, 1, 1, 0)
    log_size = len(rig.log)
    with pytest.raises(DetectorAdmissionError):
        rig.detector.start_session("s2", lease="op-2")                     # refused while pending
    assert len(rig.log) == log_size                                       # no dependency touched
    tracker.end_errors = 1
    rig.log.clear()
    with pytest.raises(DetectorCleanupError):
        rig.detector.end_session("s1", lease="op")                         # retry #1
    assert rig.calls("end") == [("result consumer", "end", "s1"), ("tracker", "end", "s1")]
    rig.log.clear()
    rig.detector.end_session("s1", lease="op")                             # retry #2: only the tracker
    assert rig.calls("end") == [("tracker", "end", "s1")]
    assert tracker.active is None and consumer.active is None
    assert rig.diagnostics()["pending_sessions"] == 0
    tracker.mutate_then_raise = False
    rig.detector.start_session("s2", lease="op-2")                         # admitted again


def test_implicit_switch_with_a_gated_end_and_a_failing_switch(rigs) -> None:
    rig = rigs()
    tracker = rig.deps["tracker"]
    rig.detector.start_session("one")
    tracker.end_gate = threading.Event()
    switcher = _thread(lambda: rig.detector.start_session("two"))
    assert tracker.in_end.wait(WAIT)                                      # switch holds the lifecycle
    assert rig.detector.get_latest_result() is None                       # state reads stay available
    rig.detector.offer(_packet("one", 1))                                 # never blocks
    tracker.end_gate.set()
    assert _joined(switcher)
    assert rig.deps["parking trigger"].active == "two"
    tracker.end_gate, tracker.end_errors = None, 1
    with pytest.raises(DetectorAdmissionError):
        rig.detector.start_session("three")                               # A's end failed: B refused
    assert rig.deps["parking trigger"].active is None and ("parking trigger", "start", "three") not in rig.log
    rig.detector.end_session("two")                                        # retry
    rig.detector.start_session("three")


def test_close_during_a_delivery_with_a_raising_end_returns_and_joins(rigs) -> None:
    rig = rigs()
    consumer, trigger = rig.deps["result consumer"], rig.deps["parking trigger"]
    consumer.observe_gate = threading.Event()
    rig.detector.start_session("s1", lease="op")
    rig.detector.offer(_packet("s1", 1), lease="op")
    assert consumer.in_observe.wait(WAIT)
    trigger.end_errors = 1
    closer = _thread(rig.detector.close)
    assert _wait_for(lambda: rig.detector._closed)                      # noqa: SLF001
    consumer.observe_gate.set()
    assert _joined(closer)
    assert not rig.detector._worker.is_alive()                           # noqa: SLF001
    snapshot = rig.diagnostics()
    assert snapshot["close_failures"] == 1 and snapshot["pending_parking_trigger"] == 1
    rig.detector.end_session("s1", lease="op")                             # retry after close
    assert rig.diagnostics()["pending_sessions"] == 0 and trigger.active is None


# ── 4. closed start is a refusal; end/retry stays available ──────────────────

def test_closed_start_is_refused_and_forwarded_as_a_refusal(rigs) -> None:
    rig = rigs()
    rig.detector.close()
    with pytest.raises(DetectorAdmissionError) as refused:
        rig.detector.start_session("s1")
    assert "s1" not in str(refused.value) and rig.log == []
    log: list = []
    coordinator = PrivateParkingCoordinator(FakeTrigger(log), FakeSampler(log))
    coordinator.close()
    wrapped = rigs(deps=("result consumer", "tracker"), parking_trigger=coordinator)
    with pytest.raises(DetectorStartError):
        wrapped.detector.start_session("s1", lease="op")                   # coordinator refused
    assert log == []
    wrapped.detector.end_session("s1", lease="op")                         # cleanup still runs
    assert ("trigger.end", "s1") in log and wrapped.diagnostics()["pending_sessions"] == 0


# ── 5. start versus close at the record boundary ─────────────────────────────

def test_close_during_a_start_ends_what_was_recorded_and_nothing_else(rigs) -> None:
    rig = rigs()
    consumer = rig.deps["result consumer"]
    consumer.start_gate = threading.Event()
    errors: list = []

    def start():
        try:
            rig.detector.start_session("s1", lease="op")
        except DetectorStartError as error:
            errors.append(error)

    starter = _thread(start)
    assert consumer.in_start.wait(WAIT)                                   # recorded, start running
    closer = _thread(rig.detector.close)
    assert _wait_for(lambda: rig.detector._closed)                      # noqa: SLF001
    consumer.start_gate.set()
    assert _joined(starter, closer)
    assert len(errors) == 1
    assert ("tracker", "start", "s1") not in rig.log and ("parking trigger", "start", "s1") not in rig.log
    assert rig.calls("end") == [("result consumer", "end", "s1")]        # only the recorded one
    assert not rig.detector._worker.is_alive()                           # noqa: SLF001
    with pytest.raises(DetectorAdmissionError):
        rig.detector.start_session("s2", lease="op-2")


# ── 6. late lease-bound end never touches B ──────────────────────────────────

def test_a_late_end_with_the_old_lease_never_touches_b(rigs) -> None:
    rig = rigs()
    rig.detector.start_session("same", lease="op-a")
    rig.detector.end_session("same", lease="op-a")
    rig.detector.start_session("same", lease="op-b")
    rig.log.clear()
    rig.detector.end_session("same", lease="op-a")                         # late end of A
    rig.detector.end_session("same")                                       # id-only caller
    assert rig.log == [] and rig.diagnostics()["stale_ends"] == 2
    assert all(dep.active == "same" for dep in rig.deps.values())
    with pytest.raises(DetectorAdmissionError):
        rig.detector.start_session("same", lease="op-c")                   # same id still active
    rig.detector.end_session("same", lease="op-b")
    assert all(dep.active is None for dep in rig.deps.values())


def test_errors_and_logs_are_sanitized(rigs, caplog) -> None:
    rig = rigs()
    rig.deps["tracker"].end_errors = 1
    rig.detector.start_session("secret-session", lease="secret-lease")
    with pytest.raises(DetectorCleanupError) as raised:
        rig.detector.end_session("secret-session", lease="secret-lease")
    assert "secret" not in str(raised.value)
    assert "secret" not in caplog.text and "Detector tracker end_session failed (RuntimeError)" in caplog.text
    assert not any(isinstance(value, str) for value in rig.diagnostics().values())
    rig.detector.end_session("secret-session", lease="secret-lease")
