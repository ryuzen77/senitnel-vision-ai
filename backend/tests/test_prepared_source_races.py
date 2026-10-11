"""P1a prepared-source store: gated interleavings (plan acceptance 4, 11 race, 12, 13).

Every interleaving is fixed by an Event gate inside the fake VideoService or the fake
filesystem; waits are bounded and every gate is released and every thread joined in
teardown. Polling only waits for a state a gate makes inevitable, always bounded.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.services.prepared_source import Outcome, SessionPhase, SourceError, SourceState
from tests.test_prepared_source import (  # noqa: F401 - fixture re-export
    WAIT,
    Gate,
    category,
    configured,
    env,
    op,
    operator_config,
    prepared,
)


def _wait_until(predicate, timeout: float = WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return predicate()


def _gated_play(env, store, gate_name: str):
    source_id, generation, revision = configured(store)
    gate = env.gate()
    setattr(env.video, gate_name, gate)
    thread, box = env.background(store.play, source_id, generation, revision, 1, op())
    assert gate.reached.wait(WAIT)
    return source_id, generation, revision, gate, thread, box


def _finish(thread, box):
    thread.join(WAIT)
    assert not thread.is_alive()
    return box


# --------------------------------------------------------------------------- before try_consume


def test_deadline_before_consume_prevents_any_reservation(env):
    store = env.store()
    source_id, generation, revision, gate, thread, box = _gated_play(env, store, "before_consume")
    current = store.snapshot()
    assert current.state is SourceState.PLAY_PENDING and current.last_attempt.outcome is Outcome.PENDING
    assert store.attempt(source_id, 1).outcome is Outcome.PENDING                 # gated start: PENDING
    assert category(store.save_configuration, source_id, generation, revision,
                    operator_config()) == "play_pending"
    env.clock.advance(59.0)
    store.tick()
    assert store.snapshot().state is SourceState.PLAY_PENDING
    env.clock.advance(1.0)
    store.tick()
    current = store.snapshot()
    assert current.state is SourceState.PREPARED and store.file_users() == 0
    assert current.last_attempt.outcome is Outcome.TIMED_OUT
    gate.release.set()
    record = _finish(thread, box)["result"]
    assert record.outcome is Outcome.TIMED_OUT and record.session_ref is None
    assert env.video.sessions == [] and env.video.cancelled == []                 # zero reservations
    assert store.count("stale_returns") == 1 and store.snapshot().state is SourceState.PREPARED


def test_delete_before_consume_cancels_and_deletes(env):
    store = env.store()
    source_id, generation, _revision, gate, thread, box = _gated_play(env, store, "before_consume")
    assert store.delete(source_id, generation) == "GONE"
    assert len(env.files.removed) == 1
    gate.release.set()
    record = _finish(thread, box)["result"]
    assert record.outcome is Outcome.CANCELLED and env.video.sessions == []
    assert store.snapshot() is None and store.admission_open()


def test_close_before_consume_leaves_no_late_session(env):
    store = env.store()
    _source_id, _generation, _revision, gate, thread, box = _gated_play(env, store, "before_consume")
    store.close()
    assert env.video.stops == 1 and len(env.files.removed) == 1
    gate.release.set()
    assert _finish(thread, box)["result"].outcome is Outcome.CANCELLED
    assert env.video.sessions == []


# --------------------------------------------------------------------------- after try_consume


def test_deadline_after_consume_cancels_that_lease_and_waits_for_clean_hook(env):
    store = env.store()
    source_id, generation, revision, gate, thread, box = _gated_play(env, store, "after_consume")
    env.clock.advance(60.0)
    store.tick()
    assert env.video.cancelled == ["lease-1"]
    current = store.snapshot()
    assert current.state is SourceState.ENDING and current.last_attempt.outcome is Outcome.TIMED_OUT
    assert (current.last_attempt.session_ref, current.last_attempt.session_id) == ("ref-1", "sid-1")
    assert category(store.play, source_id, generation, revision, 2, op()) == "session_cleanup_pending"
    assert category(store.create, op()) == "slot_busy"
    gate.release.set()
    record = _finish(thread, box)["result"]
    assert record.outcome is Outcome.TIMED_OUT                                    # write-once
    assert store.snapshot().state is SourceState.ENDING
    env.video.sessions[0].finish("cancelled", True)
    current = store.snapshot()
    assert current.state is SourceState.PREPARED and current.last_attempt.phase is SessionPhase.ENDED
    assert store.file_users() == 0


def test_delete_after_consume_deletes_only_after_clean_hook(env):
    store = env.store()
    source_id, generation, _revision, gate, thread, box = _gated_play(env, store, "after_consume")
    assert store.delete(source_id, generation) == "DELETING"
    assert env.video.cancelled == ["lease-1"] and env.files.calls == 0
    gate.release.set()
    assert _finish(thread, box)["result"].outcome is Outcome.CANCELLED
    assert env.files.calls == 0 and store.snapshot().state is SourceState.DELETING
    env.video.sessions[0].finish("cancelled", False)
    store.tick()
    assert env.files.calls == 0
    env.video.sessions[0].finish("cancelled", True)
    store.tick()
    assert len(env.files.removed) == 1 and store.snapshot() is None


def test_close_after_consume_keeps_the_file_while_the_ticket_holds_it(env):
    store = env.store()
    _source_id, _generation, _revision, gate, thread, box = _gated_play(env, store, "after_consume")
    store.close()
    assert env.video.cancelled == ["lease-1"] and env.video.stops == 1
    assert env.files.calls == 0 and store.snapshot().cleanup_pending
    gate.release.set()
    assert _finish(thread, box)["result"].outcome is Outcome.CANCELLED
    env.video.sessions[0].finish("cancelled", True)
    assert env.files.calls == 0 and store.snapshot().cleanup_pending             # startup sweep handles it


# --------------------------------------------------------------------------- the return of start


def test_return_gated_after_clean_eof_hook(env):
    store = env.store()
    env.video.hook_before_return = ("eof", True)
    source_id, generation, revision, gate, thread, box = _gated_play(env, store, "before_return")
    current = store.snapshot()
    assert current.state is SourceState.PREPARED and store.file_users() == 0
    assert current.last_attempt.outcome is Outcome.PENDING and current.last_attempt.phase is SessionPhase.ENDED
    assert category(store.play, source_id, generation, revision, 2, op()) == "play_in_progress"
    gate.release.set()
    record = _finish(thread, box)["result"]
    assert (record.outcome, record.phase, record.end_reason) == (Outcome.STARTED, SessionPhase.ENDED, "eof")
    assert store.snapshot().state is SourceState.PREPARED                         # never PLAYING


def test_return_gated_after_failed_cleanup_then_stop_retry(env):
    store = env.store()
    env.video.hook_before_return = ("stop", False)
    _source_id, _generation, _revision, gate, thread, box = _gated_play(env, store, "before_return")
    assert store.snapshot().state is SourceState.ENDING
    gate.release.set()
    record = _finish(thread, box)["result"]
    assert record.outcome is Outcome.STARTED and record.phase is SessionPhase.ENDING
    assert store.snapshot().state is SourceState.ENDING
    env.video.sessions[0].finish("stop", True)
    assert store.snapshot().state is SourceState.PREPARED and store.file_users() == 0


def test_return_gated_after_refusal_hook(env):
    store = env.store()
    env.video.refuse_after_consume = ("required_component_refused", True)
    source_id, generation, revision = configured(store)
    record = store.play(source_id, generation, revision, 1, op())
    assert record.outcome is Outcome.REFUSED and store.snapshot().state is SourceState.PREPARED
    assert store.snapshot().config_ready                                          # decision 6


def test_hook_from_another_thread_while_start_is_gated(env):
    store = env.store()
    _source_id, _generation, _revision, gate, thread, box = _gated_play(env, store, "before_return")
    hook_thread, _ = env.background(env.video.sessions[0].finish, "eof", True)
    hook_thread.join(WAIT)
    assert store.snapshot().state is SourceState.PREPARED
    gate.release.set()
    assert _finish(thread, box)["result"].phase is SessionPhase.ENDED
    assert store.snapshot().state is SourceState.PREPARED


# --------------------------------------------------------------------------- cleanup single flight


def test_cleanup_is_single_flight_under_concurrent_triggers(env):
    store = env.store()
    source_id, generation = prepared(store)
    env.files.fail_always = True
    env.files.gate = Gate()
    thread, box = env.background(store.delete, source_id, generation)
    assert env.files.gate.reached.wait(WAIT)
    env.clock.advance(3600.0)
    store.tick()                                                                  # sweeper tick
    assert store.delete(source_id, generation) == "DELETING"                      # explicit DELETE
    others = [env.background(store.tick)[0] for _ in range(4)]
    for other in others:
        other.join(WAIT)
    assert env.files.calls == 1 and env.files.max_active == 1
    env.files.gate.release.set()
    assert _finish(thread, box)["result"] == "DELETING"
    current = store.snapshot()
    assert current.state is SourceState.CLEANUP_PENDING and current.cleanup_attempts == 1


def test_shutdown_starts_no_attempt_beside_one_in_flight(env):
    store = env.store(shutdown_budget_seconds=0.05)
    source_id, generation = prepared(store)
    env.files.fail_always = True
    env.files.gate = Gate()
    thread, box = env.background(store.delete, source_id, generation)
    assert env.files.gate.reached.wait(WAIT)
    store.close()                                                                 # budget runs out
    assert env.files.calls == 1
    current = store.snapshot()
    assert current.cleanup_pending and current.state is SourceState.DELETING      # not success
    env.files.gate.release.set()
    _finish(thread, box)
    assert env.files.calls == 1 and store.snapshot().cleanup_attempts == 1
    assert category(store.create, op()) == "closed"


# --------------------------------------------------------------------------- reads versus expiry


def test_preview_read_versus_expiry_has_exactly_one_winner(env):
    for _ in range(50):
        store = env.store()
        source_id, generation = prepared(store)
        env.clock.advance(30 * 60)
        barrier = threading.Barrier(2, timeout=WAIT)
        outcome: dict = {}

        def read() -> None:
            barrier.wait()
            try:
                outcome["handle"] = store.begin_read(source_id, generation)
            except SourceError as error:
                outcome["error"] = error.category

        def expire() -> None:
            barrier.wait()
            store.tick()

        threads = [env.background(read)[0], env.background(expire)[0]]
        for thread in threads:
            thread.join(WAIT)
        if "handle" in outcome:                       # the reset won: no expiry, file kept
            current = store.snapshot()
            assert current.state is SourceState.PREPARED and current.expires_in_seconds == 1800.0
            assert env.files.calls == 0
            store.end_read(outcome["handle"])
            store.delete(source_id, generation)
        else:                                         # expiry won: no reset, deleted once
            assert outcome["error"] in {"expired", "source_gone"}
        assert _wait_until(lambda: store.snapshot() is None)
        env.files.calls = 0
        store.close()


# --------------------------------------------------------------------------- snapshot identity (13)


def test_current_snapshot_tuple_is_never_mixed(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    allowed = {(source_id, generation, revision, 1)}
    stop = threading.Event()
    seen: list = []

    def reader() -> None:
        while not stop.is_set():
            current = store.snapshot()
            seen.append(None if current is None else
                        (current.source_id, current.generation, current.config_revision, current.next_attempt))

    thread, _ = env.background(reader)
    for expected in range(revision + 1, revision + 30):
        assert store.save_configuration(source_id, generation, expected - 1, operator_config()) == expected
        allowed.add((source_id, generation, expected, 1))
    store.delete(source_id, generation)
    stop.set()
    thread.join(WAIT)
    assert seen and set(seen) <= allowed | {None}


def test_late_responses_carry_their_own_generation_and_attempt(env):
    store = env.store()
    source_id_a, generation_a, revision_a = configured(store)
    record_a = store.play(source_id_a, generation_a, revision_a, 1, op())
    env.video.sessions[0].finish("stop", True)
    store.delete(source_id_a, generation_a)
    source_id_b, generation_b = prepared(store)
    assert generation_b > generation_a and source_id_b != source_id_a
    assert category(store.save_configuration, source_id_a, generation_a, revision_a,
                    operator_config()) == "source_gone"
    assert category(store.attempt, source_id_a, 1) == "source_gone"
    assert record_a.attempt == 1 and record_a.session_ref == "ref-1"


# --------------------------------------------------------------------------- sweeper thread


def test_sweeper_thread_fires_deadlines_and_stops_on_close(env):
    store = env.store(sweeper_max_wait_seconds=0.01)
    store.create(op())
    store.start()
    env.clock.advance(600.0)
    store.wake()
    assert _wait_until(lambda: store.snapshot() is None)
    sweeper = store._sweeper
    store.close()
    assert sweeper is not None and not sweeper.is_alive()


# --------------------------------------------------------------------------- authoritative deadlines


def test_consume_after_the_play_deadline_is_refused_without_a_sweeper_pass(env):
    store = env.store()
    _source_id, _generation, _revision, gate, thread, box = _gated_play(env, store, "before_consume")
    env.clock.advance(60.0)                                                       # no tick
    gate.release.set()
    record = _finish(thread, box)["result"]
    assert record.outcome is Outcome.TIMED_OUT and record.session_ref is None
    assert env.video.sessions == [] and env.video.cancelled == []                 # zero reservations
    assert store.snapshot().state is SourceState.PREPARED and store.file_users() == 0


def test_late_return_after_the_play_deadline_does_not_revive(env):
    store = env.store()
    _source_id, _generation, _revision, gate, thread, box = _gated_play(env, store, "after_consume")
    env.clock.advance(60.0)                                                       # no tick
    gate.release.set()
    record = _finish(thread, box)["result"]
    assert record.outcome is Outcome.TIMED_OUT and env.video.cancelled == ["lease-1"]
    assert store.snapshot().state is SourceState.ENDING                           # never PLAYING
    env.video.sessions[0].finish("cancelled", True)
    assert store.snapshot().state is SourceState.PREPARED


# --------------------------------------------------------------------------- sweeper waits, no busy loop


def _counting_ticks(store):
    calls = {"n": 0}
    original = store.tick

    def tick() -> None:
        calls["n"] += 1
        original()

    store.tick = tick                                    # the sweeper loop calls self.tick()
    return calls


def test_sweeper_waits_for_a_reader_of_an_expired_file_then_cleanup_runs(env):
    store = env.store(sweeper_max_wait_seconds=0.01)
    source_id, generation = prepared(store)
    reader = store.begin_read(source_id, generation)
    ticks = _counting_ticks(store)
    store.start()
    env.clock.advance(30 * 60)
    store.wake()
    assert _wait_until(lambda: store._slot is not None and store._slot.voided)
    with store._lock:
        assert store._next_due() is None                 # used by a reader: nothing due
    before = ticks["n"]
    threading.Event().wait(0.1)                          # ten sweeper wait periods
    assert ticks["n"] == before and env.files.calls == 0
    store.end_read(reader)                               # the owner releases the file
    assert env.files.removed == [reader.path] and store.snapshot() is None


def test_sweeper_is_woken_by_the_clean_hook_of_a_deleted_session(env):
    store = env.store(sweeper_max_wait_seconds=0.01)
    source_id, generation, _revision, gate, thread, box = _gated_play(env, store, "after_consume")
    assert store.delete(source_id, generation) == "DELETING"
    gate.release.set()
    _finish(thread, box)
    ticks = _counting_ticks(store)
    store.start()
    threading.Event().wait(0.1)
    assert ticks["n"] == 0 and env.files.calls == 0      # the session still holds the file
    env.video.sessions[0].finish("cancelled", True)
    assert _wait_until(lambda: env.files.calls == 1)
    assert len(env.files.removed) == 1 and store.snapshot() is None


# --------------------------------------------------------------------------- consumed -> deadline -> hook -> return



@pytest.mark.parametrize("cleanup_ok", [True, False, None])
def test_hook_after_the_play_deadline_keeps_timed_out_and_owns_the_cancel(env, cleanup_ok):
    store = env.store()
    source_id, generation, revision, gate, thread, box = _gated_play(env, store, "after_consume")
    session = env.video.sessions[0]
    env.clock.advance(60.0)                                                       # no sweeper pass
    session.finish("eof", cleanup_ok)                                             # hook before the return
    assert env.video.cancelled == []                                              # never from the hook thread
    pending = [lease for _ticket, lease in store._deferred_cancels]
    assert pending == ([] if cleanup_ok is True else ["lease-1"])
    gate.release.set()
    record = _finish(thread, box)["result"]                                       # late return
    assert record.outcome is Outcome.TIMED_OUT                                    # not STARTED
    assert record.cleanup_ok is cleanup_ok and record.end_reason == "eof"
    assert env.video.cancelled == ([] if cleanup_ok is True else ["lease-1"])
    current = store.snapshot()
    assert category(store.create, op()) == "slot_busy"
    if cleanup_ok is True:
        assert record.phase is SessionPhase.ENDED and store.file_users() == 0
        assert current.state is SourceState.PREPARED and current.config_ready
        assert store.play(source_id, generation, revision, 2, op()).outcome is Outcome.STARTED
    else:
        assert record.phase is SessionPhase.ENDING and store.file_users() == 1     # ownership kept
        assert current.state is SourceState.ENDING and not current.config_ready
        assert category(store.play, source_id, generation, revision, 2, op()) == "session_cleanup_pending"
        assert category(store.delete, source_id, generation) == "session_cleanup_pending"
        store.tick()
        assert env.video.cancelled == ["lease-1"]                                 # sent once, not repeated
        session.finish("cancelled", True)
        current = store.snapshot()
        assert current.state is SourceState.PREPARED and current.last_attempt.outcome is Outcome.TIMED_OUT
        assert store.file_users() == 0 and env.video.cancelled == ["lease-1"]


@pytest.mark.parametrize("cleanup_ok", [True, False, None])
def test_hook_before_the_play_deadline_is_unchanged(env, cleanup_ok):
    store = env.store()
    source_id, generation, revision, gate, thread, box = _gated_play(env, store, "after_consume")
    env.clock.advance(59.0)
    env.video.sessions[0].finish("eof", cleanup_ok)                               # before the deadline
    env.clock.advance(1.0)                                                        # deadline passes later
    gate.release.set()
    record = _finish(thread, box)["result"]
    assert record.outcome is Outcome.STARTED and env.video.cancelled == []
    store.tick()
    assert env.video.cancelled == []
    if cleanup_ok is True:
        assert record.phase is SessionPhase.ENDED and store.snapshot().state is SourceState.PREPARED
        assert store.file_users() == 0
    else:
        assert record.phase is SessionPhase.ENDING and store.snapshot().state is SourceState.ENDING
        assert store.file_users() == 1
        assert category(store.play, source_id, generation, revision, 2, op()) == "session_cleanup_pending"


# --------------------------------------------------------------------------- close with a queued deletion


@pytest.mark.parametrize("delete_fails", [False, True])
def test_close_runs_a_deletion_claimed_but_not_yet_executed(env, delete_fails):
    store = env.store()
    source_id, generation, revision = configured(store)
    store.play(source_id, generation, revision, 1, op())
    session = env.video.sessions[0]
    session.finish("stop", True)                                                  # clean: PREPARED
    env.clock.advance(30 * 60)                                                    # TTL passed, no sweeper pass
    session.finish("stop", True)                                                  # late hook applies the expiry
    assert store.count("stale_hooks") == 1 and env.files.calls == 0
    with store._lock:
        queued = list(store._deferred_obligations)
    assert len(queued) == 1 and queued[0].in_flight                               # claimed, not executed
    env.files.fail_always = delete_fails
    store.close()
    assert env.files.calls == 1                                                   # the one final attempt
    assert not queued[0].in_flight
    if delete_fails:
        current = store.snapshot()
        assert current.cleanup_pending and current.cleanup_attempts == 1          # obligation kept
    else:
        assert store.snapshot() is None
    assert category(store.create, op()) == "closed"
