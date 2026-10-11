"""P1a prepared-source store: sequential contracts (plan §8, acceptance 2-3, 5, 7-8, 11-12).

Fake clock, fake filesystem and a fake VideoService side only: no runtime wiring, no
real video, decoder, model, provider, webcam or speaker. Gated interleavings live in
``test_prepared_source_races.py`` and reuse these fakes.
"""

from __future__ import annotations

import itertools
import pathlib
import threading
import uuid

import pytest

from app.models.private_parking import build_operator_configuration
from app.services.prepared_source import (
    LocalSourceFiles,
    Outcome,
    PlaybackRefused,
    PreparedSourceStore,
    SessionPhase,
    SourceError,
    SourceState,
)

WAIT = 5.0
SIZE = (64, 48)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Gate:
    """``reached`` is set when a thread arrives; it waits (bounded) for ``release``."""

    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()

    def pass_through(self) -> None:
        self.reached.set()
        assert self.release.wait(WAIT), "gate was never released"


class FakeFiles:
    def __init__(self, leftovers: tuple[str, ...] = ()) -> None:
        self.leftovers = list(leftovers)
        self.removed: list[pathlib.Path] = []
        self.fail_next = 0
        self.fail_always = False
        self.list_error = False
        self.gate: Gate | None = None
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def remove(self, path: pathlib.Path) -> None:
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            gate = self.gate
        try:
            if gate is not None:
                gate.pass_through()
            with self._lock:
                if self.fail_always or self.fail_next > 0:
                    self.fail_next = max(0, self.fail_next - 1)
                    raise PermissionError("locked")
                self.removed.append(path)
        finally:
            with self._lock:
                self.active -= 1

    def list_files(self, directory: pathlib.Path) -> list[pathlib.Path]:
        if self.list_error:
            raise OSError("unavailable")
        return [directory / name for name in self.leftovers]


class _Session:
    def __init__(self, ticket, lease: str, on_finished) -> None:
        self.ticket, self.lease, self.on_finished = ticket, lease, on_finished

    def finish(self, end_reason: str, cleanup_ok: bool | None) -> None:
        self.on_finished(self.ticket.ticket_id, self.lease, end_reason, cleanup_ok)


class FakeVideo:
    """VideoService side of the handoff: consumes the ticket where the reservation
    would happen, records every reservation, ``cancel_session`` and ``stop``."""

    def __init__(self, session_ids=None) -> None:
        self._serial = itertools.count(1)
        self._session_ids = session_ids or (lambda n: f"sid-{n}")
        self.starts = 0
        self.sessions: list[_Session] = []
        self.cancelled: list[str] = []
        self.stops = 0
        self.configs: list = []
        self.before_consume: Gate | None = None
        self.after_consume: Gate | None = None
        self.before_return: Gate | None = None
        self.refuse_before_consume: str | None = None
        self.refuse_after_consume: tuple[str, bool | None] | None = None
        self.hook_before_return: tuple[str, bool | None] | None = None
        self.raise_error = False

    def start_prepared_file(self, path, *, ticket, session_config, expected_size, on_finished) -> bool:
        self.starts += 1
        if self.raise_error:
            raise RuntimeError("boom /secret/path")
        if self.refuse_before_consume is not None:
            raise PlaybackRefused(self.refuse_before_consume)
        if self.before_consume is not None:
            self.before_consume.pass_through()
        n = next(self._serial)
        lease = f"lease-{n}"
        if not ticket.try_consume(lease, f"ref-{n}", self._session_ids(n)):
            return False
        session = _Session(ticket, lease, on_finished)
        self.sessions.append(session)
        self.configs.append((session_config, expected_size))
        if self.after_consume is not None:
            self.after_consume.pass_through()
        if self.refuse_after_consume is not None:
            end_reason, cleanup_ok = self.refuse_after_consume
            session.finish("refused", cleanup_ok)
            raise PlaybackRefused(end_reason)
        if self.hook_before_return is not None:
            session.finish(*self.hook_before_return)
        if self.before_return is not None:
            self.before_return.pass_through()
        return True

    def cancel_session(self, lease: str) -> bool:
        self.cancelled.append(lease)
        return True

    def stop(self) -> None:
        self.stops += 1


class Env:
    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp_path = tmp_path
        self.clock = FakeClock()
        self.files = FakeFiles()
        self.video = FakeVideo()
        self.stores: list[PreparedSourceStore] = []
        self.gates: list[Gate] = []
        self.threads: list[threading.Thread] = []

    def store(self, **kwargs) -> PreparedSourceStore:
        store = PreparedSourceStore(self.tmp_path / "prepared", video=self.video, files=self.files,
                                    clock=self.clock, **kwargs)
        self.stores.append(store)
        return store

    def gate(self) -> Gate:
        gate = Gate()
        self.gates.append(gate)
        return gate

    def background(self, target, *args):
        box: dict = {}

        def run() -> None:
            try:
                box["result"] = target(*args)
            except BaseException as error:          # noqa: BLE001 - inspected by the test
                box["error"] = error

        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread, box

    def teardown(self) -> None:
        for gate in self.gates:
            gate.release.set()
        if self.files.gate is not None:
            self.files.gate.release.set()
        for thread in self.threads:
            thread.join(WAIT)
            assert not thread.is_alive(), "test thread did not finish"
        for store in self.stores:
            store.close()


@pytest.fixture
def env(tmp_path):
    environment = Env(tmp_path)
    yield environment
    environment.teardown()


def op() -> str:
    return str(uuid.uuid4())


def operator_config(size=SIZE, **overrides):
    values = dict(frame_width=size[0], frame_height=size[1],
                  line={"p1": [0.2, 0.5], "p2": [0.8, 0.5]}, inside="right",
                  zones=[{"name": "z1", "rect": [0.1, 0.1, 0.5, 0.5]}])
    values.update(overrides)
    return build_operator_configuration(**values)


def prepared(store: PreparedSourceStore, size=SIZE):
    snap = store.create(op())
    handle = store.begin_write(snap.source_id, snap.generation, ".mp4")
    store.finish_write(handle, *size)
    return snap.source_id, snap.generation


def configured(store: PreparedSourceStore, size=SIZE):
    source_id, generation = prepared(store, size)
    revision = store.save_configuration(source_id, generation, None, operator_config(size))
    return source_id, generation, revision


def category(callable_, *args) -> str:
    with pytest.raises(SourceError) as info:
        callable_(*args)
    return info.value.category


def play(store, source_id, generation, revision, operation_id=None):
    snap = store.snapshot()
    return store.play(source_id, generation, revision, snap.next_attempt, operation_id or op())


# --------------------------------------------------------------------------- upload


def test_create_write_prepare_and_snapshot(env):
    store = env.store()
    operation_id = op()
    snap = store.create(operation_id)
    assert snap.state is SourceState.AWAITING_FILE and snap.deadline_in_seconds == 600.0
    assert snap.upload_operation_id == operation_id and snap.next_attempt == 1
    assert store.create(operation_id).source_id == snap.source_id      # same operation: same slot
    assert category(store.create, op()) == "slot_busy"
    handle = store.begin_write(snap.source_id, snap.generation, ".mp4")
    assert store.snapshot().state is SourceState.WRITING and store.snapshot().deadline_in_seconds == 300.0
    assert handle.path.parent == env.tmp_path / "prepared" and snap.source_id not in handle.path.name
    prepared_file = store.finish_write(handle, 64, 48)
    assert prepared_file.state is SourceState.PREPARED and prepared_file.preview_revision == 1
    current = store.snapshot()
    assert (current.frame_width, current.frame_height, current.expires_in_seconds) == (64, 48, 1800.0)
    assert current.config_ready is False and current.deadline_in_seconds is None
    assert not store.admission_open()


def test_write_errors(env):
    store = env.store()
    snap = store.create(op())
    assert category(store.begin_write, snap.source_id, snap.generation, ".avi") == "unsupported_type"
    assert category(store.begin_write, snap.source_id, snap.generation + 1, ".mp4") == "generation_mismatch"
    assert category(store.begin_write, "other", snap.generation, ".mp4") == "unknown_source"
    store.begin_write(snap.source_id, snap.generation, ".webm")
    assert category(store.begin_write, snap.source_id, snap.generation, ".mp4") == "upload_not_expected"


def test_delete_during_write_cancels_and_writer_deletes_partial_once(env):
    store = env.store()
    snap = store.create(op())
    handle = store.begin_write(snap.source_id, snap.generation, ".mp4")
    assert store.delete(snap.source_id, snap.generation) == "DELETING"
    assert handle.cancelled.is_set()
    assert env.files.calls == 0                                     # the writer still uses the file
    assert category(store.create, op()) == "cleanup_pending"
    assert category(store.finish_write, handle, 64, 48) == "cancelled"
    assert env.files.removed == [handle.path] and env.files.calls == 1
    assert store.snapshot() is None and store.admission_open()
    assert category(store.begin_read, snap.source_id, snap.generation) == "source_gone"


def test_failed_write_voids_source_and_deletes_partial(env):
    store = env.store()
    snap = store.create(op())
    handle = store.begin_write(snap.source_id, snap.generation, ".mp4")
    store.fail_write(handle, "not_decodable")
    assert env.files.removed == [handle.path]
    assert store.snapshot() is None
    assert category(store.finish_write, handle, 64, 48) == "write_finished"


def test_awaiting_file_deadline_frees_slot_without_deletion(env):
    store = env.store()
    snap = store.create(op())
    env.clock.advance(599.0)
    store.tick()
    assert store.snapshot().state is SourceState.AWAITING_FILE
    env.clock.advance(1.0)
    store.tick()
    assert store.snapshot() is None and env.files.calls == 0
    assert store.delete(snap.source_id, snap.generation) == "GONE"
    assert category(store.play, snap.source_id, snap.generation, 1, 1, op()) == "source_gone"
    assert store.create(op()).generation == snap.generation + 1


def test_writing_deadline_voids_and_holds_slot_until_writer_returns(env):
    store = env.store()
    snap = store.create(op())
    env.clock.advance(100.0)
    handle = store.begin_write(snap.source_id, snap.generation, ".mp4")
    env.clock.advance(299.0)
    store.tick()
    assert store.snapshot().state is SourceState.WRITING           # 5 min from the write start
    env.clock.advance(1.0)
    store.tick()
    assert handle.cancelled.is_set() and store.snapshot().state is SourceState.DELETING
    assert category(store.create, op()) == "cleanup_pending"
    assert category(store.finish_write, handle, 64, 48) == "deadline_exceeded"
    assert env.files.removed == [handle.path] and store.snapshot() is None


# --------------------------------------------------------------------------- configuration


def test_configuration_rules(env):
    store = env.store()
    snap = store.create(op())
    assert category(store.save_configuration, snap.source_id, snap.generation, None,
                    operator_config()) == "not_prepared"
    source_id, generation = snap.source_id, snap.generation
    store.finish_write(store.begin_write(source_id, generation, ".mp4"), *SIZE)
    assert category(store.save_configuration, source_id, generation, None,
                    operator_config((32, 48))) == "frame_size_mismatch"
    assert category(store.save_configuration, source_id, generation, 3, operator_config()) == "revision_conflict"
    assert category(store.save_configuration, source_id, generation + 1, None,
                    operator_config()) == "generation_mismatch"
    assert store.save_configuration(source_id, generation, None, operator_config()) == 1
    assert category(store.save_configuration, source_id, generation, None, operator_config()) == "revision_conflict"
    assert store.save_configuration(source_id, generation, 1, operator_config()) == 2
    current = store.snapshot()
    assert current.config_revision == 2 and current.config_ready


# --------------------------------------------------------------------------- attempts


def test_attempts_replay_supersede_conflict_and_reuse(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    op_a = op()
    record_a = store.play(source_id, generation, revision, 1, op_a)
    assert record_a.outcome is Outcome.STARTED and record_a.phase is SessionPhase.RUNNING
    assert (record_a.session_ref, record_a.session_id) == ("ref-1", "sid-1")
    assert store.snapshot().state is SourceState.PLAYING and store.snapshot().next_attempt == 2
    assert category(store.play, source_id, generation, revision, 2, op()) == "play_in_progress"
    env.video.sessions[0].finish("stop", True)
    assert store.snapshot().state is SourceState.PREPARED
    record_b = store.play(source_id, generation, revision, 2, op())
    assert record_b.outcome is Outcome.STARTED and env.video.starts == 2
    replay = store.play(source_id, generation, revision, 1, op_a)          # lost ACK replay of A
    assert replay.attempt == 1 and replay.phase is SessionPhase.ENDED and env.video.starts == 2
    env.video.sessions[1].finish("stop", True)
    assert category(store.play, source_id, generation, revision, 3, op_a) == "operation_reused"
    assert category(store.play, source_id, generation, revision, 1, op()) == "attempt_superseded"
    assert category(store.play, source_id, generation, revision, 4, op()) == "attempt_conflict"
    assert env.video.starts == 2


def test_replay_after_eviction_and_reuse_after_eviction(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    op_a = op()
    store.play(source_id, generation, revision, 1, op_a)
    env.video.sessions[-1].finish("stop", True)
    for attempt in range(2, 10):                                     # 9 attempts: A's record evicted
        store.play(source_id, generation, revision, attempt, op())
        env.video.sessions[-1].finish("eof", True)
    assert category(store.play, source_id, generation, revision, 1, op_a) == "outcome_evicted"
    assert category(store.attempt, source_id, 1) == "outcome_evicted"
    assert category(store.play, source_id, generation, revision, 10, op_a) == "operation_reused"
    assert store.attempt(source_id, 9).outcome is Outcome.STARTED
    assert category(store.attempt, source_id, 42) == "unknown_attempt"
    assert env.video.starts == 9


def test_attempt_limit(env):
    store = env.store(max_attempts=3)
    source_id, generation, revision = configured(store)
    for attempt in (1, 2, 3):
        store.play(source_id, generation, revision, attempt, op())
        env.video.sessions[-1].finish("stop", True)
    assert category(store.play, source_id, generation, revision, 4, op()) == "attempt_limit"
    assert env.video.starts == 3


def test_play_requires_accepted_configuration_revision(env):
    store = env.store()
    source_id, generation = prepared(store)
    assert category(store.play, source_id, generation, 0, 1, op()) == "config_not_ready"
    revision = store.save_configuration(source_id, generation, None, operator_config())
    assert category(store.play, source_id, generation, revision + 1, 1, op()) == "revision_stale"
    assert env.video.starts == 0
    record = store.play(source_id, generation, revision, 1, op())
    config, expected = env.video.configs[0]
    assert record.outcome is Outcome.STARTED and expected == SIZE and config.config.line.inside.value == "right"


def test_refusal_before_reservation_keeps_source_and_configuration(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    env.video.refuse_before_consume = "video_busy"
    record = store.play(source_id, generation, revision, 1, op())
    assert (record.outcome, record.reason, record.phase) == (Outcome.REFUSED, "video_busy", SessionPhase.NONE)
    current = store.snapshot()
    assert current.state is SourceState.PREPARED and current.config_ready and store.file_users() == 0
    env.video.refuse_before_consume = None
    assert store.play(source_id, generation, revision, 2, op()).outcome is Outcome.STARTED


def test_refusal_rollback_clean_and_failed(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    env.video.refuse_after_consume = ("required_component_refused", True)
    record = store.play(source_id, generation, revision, 1, op())
    assert record.outcome is Outcome.REFUSED and record.phase is SessionPhase.ENDED
    assert store.snapshot().state is SourceState.PREPARED and store.file_users() == 0
    env.video.refuse_after_consume = ("required_component_refused", False)    # rollback failed
    record = store.play(source_id, generation, revision, 2, op())
    assert record.outcome is Outcome.REFUSED and record.phase is SessionPhase.ENDING
    current = store.snapshot()
    assert current.state is SourceState.ENDING and not current.config_ready
    assert category(store.play, source_id, generation, revision, 3, op()) == "session_cleanup_pending"
    assert category(store.delete, source_id, generation) == "session_cleanup_pending"
    assert category(store.create, op()) == "slot_busy"
    env.video.sessions[-1].finish("stop", True)                                # a Stop retry cleaned up
    current = store.snapshot()
    assert current.state is SourceState.PREPARED and current.config_ready and current.config_revision == revision


def test_start_exception_is_failed_and_sanitized(env, caplog):
    store = env.store()
    source_id, generation, revision = configured(store)
    env.video.raise_error = True
    record = store.play(source_id, generation, revision, 1, op())
    assert (record.outcome, record.reason) == (Outcome.FAILED, "start_failed")
    assert store.snapshot().state is SourceState.PREPARED and store.file_users() == 0
    assert "secret" not in caplog.text and "boom" not in caplog.text


def test_immediate_eof_hook_before_return(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    env.video.hook_before_return = ("eof", True)
    record = store.play(source_id, generation, revision, 1, op())
    assert (record.outcome, record.phase, record.end_reason, record.cleanup_ok) == (
        Outcome.STARTED, SessionPhase.ENDED, "eof", True)
    assert store.snapshot().state is SourceState.PREPARED and store.file_users() == 0


def test_cleanup_false_keeps_ending_and_blocks_expiry_until_clean_hook(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    store.play(source_id, generation, revision, 1, op())
    session = env.video.sessions[0]
    session.finish("stop", False)
    assert store.snapshot().state is SourceState.ENDING and not store.snapshot().config_ready
    assert category(store.delete, source_id, generation) == "session_cleanup_pending"
    env.clock.advance(7200.0)
    store.tick()
    assert store.snapshot().state is SourceState.ENDING and env.files.calls == 0
    session.finish("stop", None)
    assert store.snapshot().state is SourceState.ENDING
    session.finish("stop", True)
    current = store.snapshot()
    assert current.state is SourceState.PREPARED and current.config_ready
    assert current.expires_in_seconds == 1800.0                          # ENDING -> PREPARED resets
    assert current.last_attempt.phase is SessionPhase.ENDED


def test_hook_summary_is_bounded_and_keeps_obligation(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    store.play(source_id, generation, revision, 1, op())
    session = env.video.sessions[0]
    attributes = set(vars(session.ticket))
    for _ in range(100):
        session.finish("stop", False)
    assert store.ticket_summary(session.ticket.ticket_id) == (100, ("stop", False))
    assert set(vars(session.ticket)) == attributes
    assert all(not isinstance(value, (list, dict, set)) for value in vars(session.ticket).values())
    assert store.snapshot().state is SourceState.ENDING and store.file_users() == 1
    session.finish("stop", True)
    assert store.snapshot().state is SourceState.PREPARED and store.ticket_summary(session.ticket.ticket_id) is None


def test_stale_hook_of_a_does_not_change_b_with_reused_session_id(env):
    env.video = FakeVideo(session_ids=lambda n: "same-session")
    store = env.store()
    source_id, generation, revision = configured(store)
    store.play(source_id, generation, revision, 1, op())
    session_a = env.video.sessions[0]
    session_a.finish("stop", True)
    record_b = store.play(source_id, generation, revision, 2, op())
    session_b = env.video.sessions[1]
    session_a.finish("stop", True)                                        # late hook of A
    session_a.on_finished(session_b.ticket.ticket_id, session_a.lease, "stop", True)   # B's ticket, A's lease
    assert store.count("stale_hooks") == 2
    assert store.snapshot().state is SourceState.PLAYING
    assert store.attempt(source_id, 2) == record_b
    assert record_b.session_id == "same-session" and record_b.session_ref == "ref-2"


def test_identity_fixed_at_consume(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    record = store.play(source_id, generation, revision, 1, op())
    env.video.sessions[0].finish("eof", True)
    later = store.attempt(source_id, 1)
    assert (later.session_ref, later.session_id) == (record.session_ref, record.session_id)


def test_lookup_after_delete_is_source_gone(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    store.play(source_id, generation, revision, 1, op())
    env.video.sessions[0].finish("stop", True)
    assert store.delete(source_id, generation) == "GONE"
    assert category(store.attempt, source_id, 1) == "source_gone"
    assert len(env.files.removed) == 1


def test_geometry_unstable_generation_cannot_be_reopened(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    store.play(source_id, generation, revision, 1, op())
    session = env.video.sessions[0]
    store.report_geometry_unstable(session.ticket.ticket_id, session.lease)
    session.finish("error", True)
    current = store.snapshot()
    assert current.state is SourceState.PREPARED and current.geometry_unstable and not current.config_ready
    assert category(store.play, source_id, generation, revision, 2, op()) == "source_geometry_unstable"
    assert category(store.save_configuration, source_id, generation, revision,
                    operator_config()) == "source_geometry_unstable"
    assert store.begin_read(source_id, generation) is not None             # still readable (decision 6)
    store.report_geometry_unstable(session.ticket.ticket_id, "lease-other")
    assert store.count("stale_hooks") == 1


# --------------------------------------------------------------------------- idle TTL (D5)


def test_idle_ttl_reset_by_actions_not_by_polling(env):
    store = env.store()
    source_id, generation = prepared(store)
    env.clock.advance(29 * 60)
    revision = store.save_configuration(source_id, generation, None, operator_config())
    env.clock.advance(29 * 60)
    store.end_read(store.begin_read(source_id, generation))
    env.clock.advance(29 * 60)
    assert store.touch_template(source_id, generation) == SIZE
    store.play(source_id, generation, revision, 1, op())
    env.video.sessions[0].finish("stop", True)                            # ENDING -> PREPARED: reset
    for _ in range(30 * 60 - 1):                                          # polling every second
        env.clock.advance(1.0)
        store.snapshot()
        store.attempt(source_id, 1)
        store.tick()
    assert store.snapshot().state is SourceState.PREPARED
    env.clock.advance(1.0)                                                # exactly 30 min idle
    store.tick()
    assert store.snapshot() is None and len(env.files.removed) == 1
    assert category(store.begin_read, source_id, generation) == "source_gone"


def test_rejected_requests_do_not_reset_ttl(env):
    store = env.store()
    source_id, generation = prepared(store)
    env.clock.advance(29 * 60)
    category(store.save_configuration, source_id, generation, 5, operator_config())
    category(store.save_configuration, source_id, generation, None, operator_config((10, 10)))
    category(store.save_configuration, source_id, generation + 1, None, operator_config())
    category(store.begin_read, source_id, generation + 1)
    category(store.touch_template, "other", generation)
    category(store.play, source_id, generation, 1, 1, op())
    env.clock.advance(60.0)
    store.tick()
    assert store.snapshot() is None


def test_requests_for_an_old_generation_do_not_reset_the_new_one(env):
    store = env.store()
    old_id, old_generation = prepared(store)
    store.delete(old_id, old_generation)
    source_id, generation = prepared(store)
    env.clock.advance(29 * 60)
    for call in (lambda: store.begin_read(old_id, old_generation),
                 lambda: store.touch_template(old_id, old_generation),
                 lambda: store.save_configuration(old_id, old_generation, None, operator_config())):
        with pytest.raises(SourceError):
            call()
    env.clock.advance(60.0)
    store.tick()
    assert store.snapshot() is None


def test_expired_preview_read_answers_expired(env):
    store = env.store()
    source_id, generation = prepared(store)
    env.files.fail_always = True                                          # keep the voided slot visible
    env.clock.advance(30 * 60)
    store.tick()
    assert category(store.begin_read, source_id, generation) == "expired"
    assert category(store.touch_template, source_id, generation) == "expired"


def test_expiry_waits_for_an_active_reader(env):
    store = env.store()
    source_id, generation = prepared(store)
    reader = store.begin_read(source_id, generation)
    env.clock.advance(30 * 60)
    store.tick()
    current = store.snapshot()
    assert current.state is SourceState.DELETING and env.files.calls == 0
    assert category(store.begin_read, source_id, generation) == "expired"
    store.end_read(reader)
    assert env.files.removed == [reader.path] and store.snapshot() is None


# --------------------------------------------------------------------------- bounded cleanup (D7)


def test_cleanup_retries_are_spaced_then_stall_and_keep_the_obligation(env):
    store = env.store()
    source_id, generation = prepared(store)
    env.files.fail_always = True
    assert store.delete(source_id, generation) == "DELETING"
    current = store.snapshot()
    assert current.state is SourceState.CLEANUP_PENDING and current.cleanup_attempts == 1
    store.tick()
    assert env.files.calls == 1                                           # not due yet
    for expected in range(2, 11):
        env.clock.advance(59.0)
        store.tick()
        assert env.files.calls == expected - 1
        env.clock.advance(1.0)
        store.tick()
        assert env.files.calls == expected
    current = store.snapshot()
    assert current.state is SourceState.CLEANUP_STALLED and current.cleanup_pending
    assert current.cleanup_attempts == 10 and current.cleanup_reason == "cancelled"
    env.clock.advance(3600.0)
    store.tick()
    assert env.files.calls == 10                                          # no further automatic attempt
    assert category(store.create, op()) == "cleanup_pending"
    assert store.delete(source_id, generation) == "DELETING"              # explicit: one more attempt
    assert env.files.calls == 11 and store.snapshot().state is SourceState.CLEANUP_STALLED
    env.files.fail_always = False
    assert store.delete(source_id, generation) == "GONE"
    assert env.files.calls == 12 and store.snapshot() is None and store.admission_open()


def test_cleanup_success_after_failures_frees_slot(env):
    store = env.store()
    source_id, generation = prepared(store)
    env.files.fail_next = 2
    store.delete(source_id, generation)
    env.clock.advance(60.0)
    store.tick()
    assert store.snapshot().cleanup_attempts == 2
    env.clock.advance(60.0)
    store.tick()
    assert store.snapshot() is None and env.files.calls == 3
    assert store.delete(source_id, generation) == "GONE"


def test_pending_cleanup_delete_does_not_stack_an_attempt(env):
    store = env.store()
    source_id, generation = prepared(store)
    env.files.fail_always = True
    store.delete(source_id, generation)
    assert store.delete(source_id, generation) == "DELETING"
    assert env.files.calls == 1                                           # retries follow the schedule


# --------------------------------------------------------------------------- startup (D12)


def test_startup_removes_leftovers_and_opens_admission(env):
    env.files.leftovers = ["old.mp4", "older.webm"]
    store = env.store()
    assert [path.name for path in env.files.removed] == ["old.mp4", "older.webm"]
    assert store.admission_open() and store.snapshot() is None


def test_startup_undeletable_leftover_keeps_obligation_and_closes_admission(env):
    env.files.leftovers = ["old.mp4"]
    env.files.fail_always = True
    store = env.store()
    current = store.snapshot()
    assert current.source_id is None and current.state is SourceState.CLEANUP_PENDING
    assert current.cleanup_attempts == 1 and current.cleanup_reason == "startup_leftover"
    assert category(store.create, op()) == "cleanup_pending"
    for _ in range(9):
        env.clock.advance(60.0)
        store.tick()
    current = store.snapshot()
    assert current.state is SourceState.CLEANUP_STALLED and env.files.calls == 10
    assert not store.admission_open()


def test_startup_touches_only_the_dedicated_directory(tmp_path):
    directory = tmp_path / "prepared"
    (directory / "nested").mkdir(parents=True)
    (directory / "leftover.mp4").write_bytes(b"x")
    (directory / "nested" / "keep.mp4").write_bytes(b"x")
    (tmp_path / "outside.mp4").write_bytes(b"x")
    store = PreparedSourceStore(directory, video=FakeVideo(), files=LocalSourceFiles(), clock=FakeClock())
    try:
        assert not (directory / "leftover.mp4").exists()
        assert (directory / "nested" / "keep.mp4").exists() and (tmp_path / "outside.mp4").exists()
        assert store.admission_open()
    finally:
        store.close()


def test_unavailable_directory_closes_admission_without_raising(env):
    env.files.list_error = True
    store = env.store()
    assert category(store.create, op()) == "storage_unavailable"


# --------------------------------------------------------------------------- shutdown (D11)


def test_shutdown_final_attempt_failing_keeps_obligation(env):
    store = env.store()
    source_id, generation = prepared(store)
    env.files.fail_always = True
    store.delete(source_id, generation)
    assert env.files.calls == 1
    store.close()
    assert env.files.calls == 2                                           # exactly one final attempt
    current = store.snapshot()
    assert current.cleanup_pending and current.cleanup_attempts == 2
    assert category(store.create, op()) == "closed"
    store.close()
    assert env.files.calls == 2


def test_shutdown_final_attempt_for_a_stalled_obligation(env):
    store = env.store(cleanup_attempt_limit=2)
    source_id, generation = prepared(store)
    env.files.fail_always = True
    store.delete(source_id, generation)
    env.clock.advance(60.0)
    store.tick()
    assert store.snapshot().state is SourceState.CLEANUP_STALLED and env.files.calls == 2
    env.files.fail_always = False
    store.close()
    assert env.files.calls == 3 and store.snapshot() is None
    assert category(store.create, op()) == "closed" and not store.admission_open()


def test_shutdown_deletes_an_unused_prepared_source(env):
    store = env.store()
    prepared(store)
    store.close()
    assert len(env.files.removed) == 1 and env.video.stops == 1 and store.snapshot() is None


def test_shutdown_makes_no_attempt_while_file_users_remain(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    store.play(source_id, generation, revision, 1, op())
    store.close()
    assert env.video.stops == 1 and env.files.calls == 0
    assert store.snapshot().cleanup_pending
    env.video.sessions[0].finish("stop", True)                            # late clean hook: recorded
    store.tick()
    assert env.files.calls == 0 and store.snapshot().cleanup_pending      # no loop after close
    assert category(store.play, source_id, generation, revision, 2, op()) == "closed"


# --------------------------------------------------------------------------- authoritative deadlines


def test_save_preview_template_and_play_after_ttl_without_a_sweeper_pass(env):
    store = env.store()
    source_id, generation, revision = configured(store)
    env.files.fail_always = True                                          # keep the voided slot visible
    env.clock.advance(30 * 60)                                            # no tick
    assert category(store.save_configuration, source_id, generation, revision,
                    operator_config()) == "expired"
    assert category(store.play, source_id, generation, revision, 1, op()) == "expired"
    assert category(store.begin_read, source_id, generation) == "expired"
    assert category(store.touch_template, source_id, generation) == "expired"
    assert env.video.starts == 0 and env.video.sessions == []            # zero reservations
    current = store.snapshot()
    assert current.state is SourceState.CLEANUP_PENDING and current.cleanup_reason == "expired"
    assert env.files.calls == 1                                           # one attempt, from the request
    env.files.fail_always = False
    env.clock.advance(60.0)
    store.tick()
    assert store.snapshot() is None


def test_finish_write_after_deadline_without_a_sweeper_pass(env):
    store = env.store()
    snap = store.create(op())
    handle = store.begin_write(snap.source_id, snap.generation, ".mp4")
    env.clock.advance(300.0)                                              # no tick
    env.files.fail_always = True
    assert category(store.finish_write, handle, 64, 48) == "deadline_exceeded"
    assert handle.cancelled.is_set() and env.files.calls == 1             # the writer tried once
    current = store.snapshot()
    assert current.state is SourceState.CLEANUP_PENDING and current.frame_width is None
    assert current.cleanup_reason == "deadline_exceeded"
    assert category(store.create, op()) == "cleanup_pending"              # ownership kept
    assert category(store.begin_read, snap.source_id, snap.generation) == "source_gone"
    env.files.fail_always = False
    env.clock.advance(60.0)
    store.tick()
    assert env.files.removed == [handle.path] and store.snapshot() is None


def test_awaiting_file_deadline_applies_on_admission_without_a_sweeper_pass(env):
    store = env.store()
    snap = store.create(op())
    env.clock.advance(600.0)
    assert category(store.begin_write, snap.source_id, snap.generation, ".mp4") == "source_gone"
    assert store.create(op()).generation == snap.generation + 1


# --------------------------------------------------------------------------- extreme numbers


@pytest.mark.parametrize("value", [10 ** 400, -10 ** 400])
def test_store_numeric_parameters_with_extreme_integers(env, value):
    store = env.store()
    source_id, generation, revision = configured(store)
    expected = "attempt_conflict" if value > 0 else "attempt_superseded"
    assert category(store.play, source_id, generation, revision, value, op()) == expected
    assert category(store.play, source_id, generation, value, 1, op()) == "revision_stale"
    assert category(store.save_configuration, source_id, generation, value,
                    operator_config()) == "revision_conflict"
    assert category(store.play, source_id, generation, revision, 1.0, op()) == "invalid_request"
    assert category(store.play, source_id, generation, True, 1, op()) == "invalid_request"
    assert category(store.save_configuration, source_id, generation, True,
                    operator_config()) == "invalid_request"
    assert env.video.starts == 0
    snap_store = env.store()
    snap = snap_store.create(op())
    handle = snap_store.begin_write(snap.source_id, snap.generation, ".mp4")
    assert category(snap_store.finish_write, handle, value, 48) == "not_decodable"
    assert snap_store.snapshot() is None
