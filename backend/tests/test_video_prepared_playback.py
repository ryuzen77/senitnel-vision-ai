"""P1b prepared playback in the real VideoService (plan §16.2, §16.3 acceptance 1-10).

Fake decoder, scripted captures, recording sinks, a real HandoffTicket, the real
SessionConfigRegistry and, for the composed cases, the real PreparedSourceStore with
fake files and a fake control clock. No webcam, real video file, model, provider or
speaker. Gates are threading.Events with timeouts; polling only waits for a state a
gate makes inevitable, always bounded; every gate is released and every thread joined.
"""

from __future__ import annotations

import itertools
import pathlib
import threading
import time
from collections import deque

import numpy as np
import pytest

from app.models.private_parking import build_operator_configuration
from app.models.video import FramePacket, VideoState
from app.services import video_decoder
from app.services.prepared_source import (
    HandoffTicket,
    Outcome,
    PlaybackRefused,
    PreparedSourceStore,
    SessionPhase,
    SourceError,
    SourceState,
    TicketState,
)
from app.services.session_config_registry import BindingConflict, SessionConfigRegistry
from app.services.video_service import PreparedStartFailed, VideoService
from tests.test_prepared_source import FakeClock, FakeFiles

WAIT = 5.0
SIZE = (64, 48)
FRAME = np.zeros((48, 64, 3), dtype=np.uint8)
SMALL = np.zeros((48, 32, 3), dtype=np.uint8)
PATH = pathlib.Path("prepared") / "never-opened.mp4"
_tickets = itertools.count(1)


def wait_until(predicate, timeout: float = WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return predicate()


class Gate:
    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()

    def pass_through(self) -> None:
        self.reached.set()
        assert self.release.wait(WAIT), "gate never released"


class Capture:
    """Scripted capture: frames, "eof", a Gate (block in read), an Exception."""

    def __init__(self, script=(), *, opened: bool = True, get_error: Exception | None = None,
                 release_failures: int = 0) -> None:
        self.script = deque(script)
        self.opened = opened
        self.get_error = get_error
        self.release_failures = release_failures
        self.release_attempts = 0
        self.released = 0
        self.calls_before_adopt: list[str] = []

    def isOpened(self) -> bool:  # noqa: N802
        return self.opened

    def get(self, _prop: int) -> float:
        if self.get_error is not None:
            raise self.get_error
        return 1000.0                                    # fast pacing

    def read(self):
        while self.script:
            item = self.script.popleft()
            if isinstance(item, Gate):
                item.pass_through()
                continue
            if isinstance(item, Exception):
                raise item
            return (False, None) if isinstance(item, str) else (True, item)
        return False, None

    def release(self) -> None:
        self.release_attempts += 1
        if self.release_failures > 0:
            self.release_failures -= 1
            raise OSError("locked")
        self.released += 1


class Decoder:
    def __init__(self, capture: Capture | None = None, *, create_error: Exception | None = None) -> None:
        self.capture = capture or Capture([FRAME, "eof"])
        self.create_error = create_error
        self.create_gate: Gate | None = None
        self.probe_gate: Gate | None = None
        self.created = 0

    def create_capture(self, _path):
        self.created += 1
        if self.create_gate is not None:
            self.create_gate.pass_through()
        if self.create_error is not None:
            raise self.create_error
        return self.capture

    def probe(self, capture) -> float:
        if self.probe_gate is not None:
            self.probe_gate.pass_through()
        return video_decoder.probe(capture)


class Sink:
    accepts_lifecycle_lease = True

    def __init__(self, registry: SessionConfigRegistry | None = None) -> None:
        self.registry = registry
        self.starts: list[str] = []
        self.ends: list[str] = []
        self.offers: list[str] = []
        self.seen_config: list = []
        self.start_gate: Gate | None = None
        self.start_error: Exception | None = None

    def start_session(self, session_id: str, *, lease=None) -> None:
        if self.registry is not None:
            self.seen_config.append(self.registry.get(session_id))
        if self.start_gate is not None:
            self.start_gate.pass_through()
        if self.start_error is not None:
            raise self.start_error
        self.starts.append(session_id)

    def end_session(self, session_id: str, *, lease=None) -> None:
        self.ends.append(session_id)

    def offer(self, item, *, lease=None) -> None:
        self.offers.append(getattr(item, "session_id", None) or getattr(item, "source_session_id", None))


class Hooks:
    """Records end hooks; optional gate on the first call; can raise; can forward."""

    def __init__(self, target=None) -> None:
        self.calls: list[tuple] = []
        self.target = target
        self.first_gate: Gate | None = None
        self.raise_times = 0
        self._lock = threading.Lock()

    def __call__(self, ticket_id, lease, reason, ok) -> None:
        with self._lock:
            first = not self.calls
            self.calls.append((ticket_id, lease, reason, ok))
        if first and self.first_gate is not None:
            self.first_gate.pass_through()
        if self.raise_times > 0:
            self.raise_times -= 1
            raise RuntimeError("store unavailable /secret/path")
        if self.target is not None:
            self.target(ticket_id, lease, reason, ok)

    def oks(self) -> list:
        return [call[3] for call in self.calls]

    def reasons(self) -> list:
        return [call[2] for call in self.calls]


def config():
    return build_operator_configuration(
        frame_width=SIZE[0], frame_height=SIZE[1], line={"p1": [0.2, 0.5], "p2": [0.8, 0.5]},
        inside="right", zones=[{"name": "z1", "rect": [0.1, 0.1, 0.5, 0.5]}])


class Env:
    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.registry = SessionConfigRegistry()
        self.buffer = Sink()
        self.detector = Sink(self.registry)
        self.decoder = Decoder()
        self.hooks = Hooks()
        self.gates: list[Gate] = []
        self.threads: list[threading.Thread] = []
        self.videos: list[VideoService] = []
        self.stores: list[PreparedSourceStore] = []

    def video(self, **kwargs) -> VideoService:
        values = dict(event_frame_buffer=self.buffer, detector=self.detector,
                      capture_join_timeout_seconds=0.05, cleanup_wait_timeout_seconds=WAIT,
                      session_config_registry=self.registry, decoder=self.decoder)
        values.update(kwargs)
        video = VideoService(**values)
        self.videos.append(video)
        return video

    def gate(self) -> Gate:
        gate = Gate()
        self.gates.append(gate)
        return gate

    def background(self, target, *args, **kwargs):
        box: dict = {}

        def run() -> None:
            try:
                box["result"] = target(*args, **kwargs)
            except BaseException as error:  # noqa: BLE001 - inspected by the test
                box["error"] = error

        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread, box

    def start(self, video, ticket=None, *, hooks=None, background=False):
        ticket = ticket or HandoffTicket(next(_tickets))
        call = dict(ticket=ticket, session_config=config(), expected_size=SIZE,
                    on_finished=hooks or self.hooks)
        if background:
            thread, box = self.background(video.start_prepared_file, PATH, **call)
            return ticket, thread, box
        return ticket, video.start_prepared_file(PATH, **call)

    def teardown(self) -> None:
        for gate in self.gates:
            gate.release.set()
        for thread in self.threads:
            thread.join(WAIT)
        for store in self.stores:
            store.close()
        for video in self.videos:
            video.stop()
        for thread in self.threads:
            assert not thread.is_alive(), "test thread did not finish"


@pytest.fixture
def env(tmp_path):
    environment = Env(tmp_path)
    yield environment
    environment.teardown()


def finish(thread, box):
    thread.join(WAIT)
    assert not thread.is_alive()
    return box


def raised(box, kind, category=None):
    error = box.get("error")
    assert isinstance(error, kind), box
    if category is not None:
        assert error.category == category
    return error


# --------------------------------------------------------------------------- start, consume, bind


def test_prepared_start_binds_runs_and_ends_with_a_clean_eof_hook(env):
    env.decoder.capture = Capture([FRAME, FRAME, "eof"])
    video = env.video()
    ticket, started = env.start(video)
    assert started is True and ticket.state is TicketState.CONSUMED
    session_ref, session_id = ticket.identity()
    assert wait_until(lambda: env.hooks.calls)
    assert env.hooks.calls == [(ticket.ticket_id, ticket.lease, "eof", True)]
    assert env.detector.seen_config == [env.detector.seen_config[0]]
    assert env.detector.seen_config[0] is not None                     # bound before the sink started
    assert len(env.registry) == 0 and env.registry.count("unbound") == 1
    assert env.decoder.capture.released == 1
    assert env.detector.offers == [session_id, session_id] and env.buffer.offers == [session_id, session_id]
    assert wait_until(lambda: video.get_status().state is VideoState.IDLE)
    assert video.get_session_identity() == (VideoState.IDLE, None, None)
    assert session_ref and session_ref != session_id


def test_a_cancelled_ticket_reserves_nothing(env):
    video = env.video()
    ticket = HandoffTicket(next(_tickets))
    ticket.cancel()
    _ticket, started = env.start(video, ticket)
    assert started is False and env.decoder.created == 0 and env.detector.starts == []
    assert video.get_status().state is VideoState.IDLE and env.hooks.calls == []


def test_an_expired_ticket_reserves_nothing(env):
    clock = FakeClock()
    ticket = HandoffTicket(next(_tickets), deadline=clock() - 1, clock=clock)
    _ticket, started = env.start(env.video(), ticket)
    assert started is False and ticket.state is TicketState.CANCELLED and env.detector.starts == []


def test_busy_refuses_without_consuming(env):
    video = env.video()
    token = video.claim_start()                                          # a legacy reservation
    with pytest.raises(PlaybackRefused) as info:
        env.start(video)
    assert info.value.category == "video_busy"
    assert video.abort_start(token)


@pytest.mark.parametrize("kwargs", [dict(session_config_registry=None)])
def test_missing_registry_or_configuration_refuses_before_the_consume(env, kwargs):
    video = env.video(**kwargs)
    ticket = HandoffTicket(next(_tickets))
    with pytest.raises(PlaybackRefused) as info:
        video.start_prepared_file(PATH, ticket=ticket, session_config=config(), expected_size=SIZE,
                                  on_finished=env.hooks)
    assert info.value.category == "configuration_unavailable" and ticket.state is TicketState.OPEN
    video2 = env.video()
    for bad in (dict(session_config=None, expected_size=SIZE), dict(session_config=config(), expected_size=(64,)),
                dict(session_config=config(), expected_size=(64.0, 48))):
        with pytest.raises(PlaybackRefused):
            video2.start_prepared_file(PATH, ticket=ticket, on_finished=env.hooks, **bad)
    assert ticket.state is TicketState.OPEN and env.decoder.created == 0


def test_the_store_owns_the_file(env):
    video = env.video()
    env.start(video)
    assert wait_until(lambda: env.hooks.calls)
    assert video._current.upload_path is None                           # noqa: SLF001


# --------------------------------------------------------------------------- acceptance 1: capture ownership


@pytest.mark.parametrize("failure", ["constructor", "not_opened", "get_raises"])
def test_open_failures_after_the_consume(env, failure):
    if failure == "constructor":
        env.decoder.create_error = OSError("cannot open /secret/path")
    elif failure == "not_opened":
        env.decoder.capture = Capture(opened=False)
    else:
        env.decoder.capture = Capture(get_error=RuntimeError("probe"))
    video = env.video()
    with pytest.raises(PreparedStartFailed):
        env.start(video)
    assert env.hooks.reasons() == ["error"] and env.hooks.oks() == [True]
    assert env.decoder.capture.released == (0 if failure == "constructor" else 1)
    assert video.get_status().state is VideoState.ERROR and len(env.registry) == 0
    video.stop()
    assert video.get_status().state is VideoState.IDLE and len(env.hooks.calls) == 1


def test_a_failing_release_keeps_the_obligation_until_a_stop_retry(env):
    env.decoder.capture = Capture(opened=False, release_failures=1)
    video = env.video()
    with pytest.raises(PreparedStartFailed):
        env.start(video)
    assert env.hooks.oks() == [False] and env.decoder.capture.released == 0
    assert video.get_status().state is VideoState.ERROR
    video.stop()                                                         # explicit retry
    assert env.hooks.oks() == [False, True] and env.hooks.reasons() == ["error", "error"]
    assert env.decoder.capture.released == 1 and env.decoder.capture.release_attempts == 2
    assert video.get_status().state is VideoState.IDLE


@pytest.mark.parametrize("where", ["create", "probe"])
def test_stop_during_the_open_has_exactly_one_release_owner(env, where):
    env.decoder.capture = Capture([FRAME, "eof"])
    gate = env.gate()
    setattr(env.decoder, f"{where}_gate", gate)
    video = env.video()
    ticket, thread, box = env.start(video, background=True)
    assert gate.reached.wait(WAIT)
    video.stop()                                                         # deferred to the opener
    assert env.hooks.oks() == [None] and video.get_status().state is not VideoState.RUNNING
    gate.release.set()
    raised(finish(thread, box), PlaybackRefused, "stopped")
    assert wait_until(lambda: len(env.hooks.calls) == 2)
    assert env.hooks.oks() == [None, True] and env.hooks.reasons() == ["stop", "stop"]
    assert env.decoder.capture.released == 1 and env.detector.offers == []
    assert video._current.thread is None                                # noqa: SLF001 - never started
    assert video.get_status().state is VideoState.IDLE and len(env.registry) == 0


# --------------------------------------------------------------------------- acceptance 2: cancel atomicity


def test_cancel_before_bind_and_sink_start(env):
    video = env.video()
    video._lifecycle_lock.acquire()                                     # noqa: SLF001 - hold _start_sinks
    try:
        ticket, thread, box = env.start(video, background=True)
        assert wait_until(lambda: ticket.state is TicketState.CONSUMED)
        cancel_thread, cancel_box = env.background(video.cancel_session, ticket.lease)
        assert wait_until(lambda: video._current.released)              # noqa: SLF001 - recorded atomically
    finally:
        video._lifecycle_lock.release()                                 # noqa: SLF001
    assert finish(cancel_thread, cancel_box)["result"] is True
    raised(finish(thread, box), PlaybackRefused, "stopped")
    assert env.registry.count("bound") == 0 and env.detector.starts == [] and env.buffer.starts == []
    assert env.decoder.created == 0 and env.hooks.calls == [(ticket.ticket_id, ticket.lease, "cancelled", True)]
    assert video.get_status().state is VideoState.IDLE


def test_cancel_during_a_sink_start(env):
    env.detector.start_gate = env.gate()
    video = env.video()
    ticket, thread, box = env.start(video, background=True)
    assert env.detector.start_gate.reached.wait(WAIT)
    cancel_thread, cancel_box = env.background(video.cancel_session, ticket.lease)
    assert wait_until(lambda: video._current.released)                  # noqa: SLF001
    env.detector.start_gate.release.set()
    assert finish(cancel_thread, cancel_box)["result"] is True
    raised(finish(thread, box), PlaybackRefused, "stopped")
    assert env.registry.count("bound") == 1 and len(env.registry) == 0  # unbound by the cleanup
    assert env.hooks.reasons() == ["cancelled"] and env.hooks.oks() == [True]
    assert env.decoder.created == 0 and video.get_status().state is VideoState.IDLE


def test_cancel_after_running_ends_that_session_once(env):
    read_gate = env.gate()
    env.decoder.capture = Capture([FRAME, read_gate, FRAME, FRAME])
    video = env.video()
    ticket, started = env.start(video)
    assert started and read_gate.reached.wait(WAIT)
    state, ref, sid = video.get_session_identity()
    assert (state, (ref, sid)) == (VideoState.RUNNING, ticket.identity())
    offers_before = list(env.detector.offers)
    assert video.cancel_session(ticket.lease) is True                    # join times out: deferred
    assert env.hooks.oks() == [None] and env.hooks.reasons() == ["cancelled"]
    state, ref, sid = video.get_session_identity()
    assert state is VideoState.ERROR and (ref, sid) == ticket.identity()  # ids until IDLE
    read_gate.release.set()
    assert wait_until(lambda: len(env.hooks.calls) == 2)
    assert env.hooks.oks() == [None, True] and env.decoder.capture.released == 1
    assert env.detector.offers == offers_before                         # nothing after the cancel
    assert wait_until(lambda: video.get_status().state is VideoState.IDLE)
    assert video.get_session_identity() == (VideoState.IDLE, None, None)
    assert video.cancel_session(ticket.lease) is False


def test_cancel_with_another_lease_is_a_counted_no_op(env):
    read_gate = env.gate()
    env.decoder.capture = Capture([FRAME, read_gate, "eof"])
    video = env.video()
    env.start(video)
    assert read_gate.reached.wait(WAIT)
    assert video.cancel_session("other-lease") is False
    assert video.get_status().state is VideoState.RUNNING
    assert video.get_cleanup_diagnostics()["stale_cancels"] == 1
    read_gate.release.set()


def test_reused_session_id_waits_for_the_lifecycle_guard(env):
    read_gate = env.gate()
    env.decoder.capture = Capture([FRAME, read_gate, FRAME, FRAME])
    video = env.video(session_id_factory=lambda: "same")
    ticket_a, started = env.start(video)
    assert started and read_gate.reached.wait(WAIT)
    session_a = video._current                                          # noqa: SLF001
    assert video.cancel_session(ticket_a.lease)                          # deferred: not IDLE yet
    ticket_b = HandoffTicket(next(_tickets))
    with pytest.raises(PlaybackRefused) as info:                         # (ii) VideoService guard
        env.start(video, ticket_b)
    assert info.value.category == "video_busy" and ticket_b.state is TicketState.OPEN
    offers = list(env.detector.offers)
    read_gate.release.set()                                              # A's held frame: no publication
    assert wait_until(lambda: video.get_status().state is VideoState.IDLE)
    assert env.detector.offers == offers and env.hooks.oks() == [None, True]
    env.decoder.capture = Capture([FRAME, read_gate, "eof"])
    read_gate2 = env.gate()
    env.decoder.capture = Capture([FRAME, read_gate2, "eof"])
    _ticket, started = env.start(video, ticket_b)                         # only now: same id
    assert started and read_gate2.reached.wait(WAIT)
    assert ticket_b.identity()[1] == "same" and ticket_b.lease != ticket_a.lease
    assert video.cancel_session(ticket_a.lease) is False                  # late cancel of A
    packet = FramePacket("same", 99, 0.0, FRAME, b"jpeg")
    before = list(env.detector.offers)
    video._offer_consumers(packet, lease=session_a.token, session=session_a)   # noqa: SLF001
    assert env.detector.offers == before                                 # stale session object: no offer
    assert env.registry.get("same") is not None                          # B's binding untouched
    read_gate2.release.set()


# --------------------------------------------------------------------------- acceptance 3: geometry


def test_a_size_mismatch_ends_before_publication(env):
    env.decoder.capture = Capture([FRAME, FRAME, SMALL, FRAME, "eof"])
    video = env.video()
    env.start(video)
    # The runner writes the state after the hook returned: wait for the final state.
    assert wait_until(lambda: video.get_status().state is VideoState.ERROR)
    assert env.hooks.reasons() == ["frame_size_changed"] and env.hooks.oks() == [True]
    assert len(env.detector.offers) == 2 and len(env.buffer.offers) == 2


# --------------------------------------------------------------------------- acceptance 4/5: refusals and rollback


def test_a_required_refusal_rolls_back_with_a_refused_hook(env):
    env.detector.start_error = RuntimeError("no configuration")
    video = env.video(required_sinks={"detector"})
    with pytest.raises(PlaybackRefused) as info:
        env.start(video)
    assert info.value.category == "required_component_refused"
    assert env.hooks.reasons() == ["refused"] and env.hooks.oks() == [True]
    assert env.registry.count("bound") == 1 and len(env.registry) == 0 and env.decoder.created == 0


def test_a_bind_conflict_is_a_required_refusal(env):
    env.registry.bind("same", "older-lease", "older-config")
    video = env.video(session_id_factory=lambda: "same")
    with pytest.raises(PlaybackRefused) as info:
        env.start(video)
    assert info.value.category == "required_component_refused"
    assert env.detector.starts == [] and env.registry.get("same") == "older-config"
    assert env.hooks.reasons() == ["refused"]
    with pytest.raises(BindingConflict):
        env.registry.bind("same", "x", "y")


class _FailingThread:
    def __init__(self, *args, **kwargs) -> None:
        self.ident = None

    def start(self) -> None:
        raise RuntimeError("no thread")


@pytest.mark.parametrize("release_failures", [0, 1])
def test_thread_start_failure_after_the_consume(env, release_failures):
    env.decoder.capture = Capture([FRAME, "eof"], release_failures=release_failures)
    video = env.video(thread_factory=_FailingThread)
    with pytest.raises(PreparedStartFailed):
        env.start(video)
    assert env.hooks.reasons() == ["error"] and env.hooks.oks() == [release_failures == 0]
    if release_failures:
        video.stop()
        assert env.hooks.oks() == [False, True]
    assert env.decoder.capture.released == 1


# --------------------------------------------------------------------------- acceptance 10: handoff during the hook


@pytest.mark.parametrize("exit_before_hook_returns", [True, False])
def test_capture_handoff_while_the_hook_is_held(env, exit_before_hook_returns):
    read_gate = env.gate()
    env.decoder.capture = Capture([FRAME, read_gate, FRAME])
    env.hooks.first_gate = env.gate()
    video = env.video()
    _ticket, started = env.start(video)
    assert started and read_gate.reached.wait(WAIT)
    capture_thread = video._current.thread                               # noqa: SLF001
    stop_thread, stop_box = env.background(video.stop)
    assert env.hooks.first_gate.reached.wait(WAIT)                       # hook(None) held by the runner
    assert env.hooks.oks() == [None]
    if exit_before_hook_returns:
        read_gate.release.set()
        capture_thread.join(WAIT)
        assert not capture_thread.is_alive()                             # its exit path did not run
        assert env.decoder.capture.released == 0 and len(env.hooks.calls) == 1
        env.hooks.first_gate.release.set()
    else:
        env.hooks.first_gate.release.set()
        finish(stop_thread, stop_box)                                    # ownership given up: still in use
        assert env.decoder.capture.released == 0
        read_gate.release.set()
    finish(stop_thread, stop_box)
    assert wait_until(lambda: len(env.hooks.calls) == 2)
    assert env.hooks.oks() == [None, True] and env.decoder.capture.released == 1
    assert wait_until(lambda: video.get_status().state is VideoState.IDLE)
    assert len(env.hooks.calls) == 2


def test_opener_handoff_while_the_hook_is_held(env):
    env.decoder.create_gate = env.gate()
    env.hooks.first_gate = env.gate()
    video = env.video()
    _ticket, thread, box = env.start(video, background=True)
    assert env.decoder.create_gate.reached.wait(WAIT)
    stop_thread, stop_box = env.background(video.stop)
    assert env.hooks.first_gate.reached.wait(WAIT)                       # deferred to the opener
    env.decoder.create_gate.release.set()
    raised(finish(thread, box), PlaybackRefused, "stopped")              # the opener is done
    assert env.decoder.capture.released == 0 and len(env.hooks.calls) == 1
    env.hooks.first_gate.release.set()
    finish(stop_thread, stop_box)
    assert wait_until(lambda: len(env.hooks.calls) == 2)
    assert env.hooks.oks() == [None, True] and env.decoder.capture.released == 1


# --------------------------------------------------------------------------- composed with the real store


class Composition:
    def __init__(self, env: Env, **video_kwargs) -> None:
        self.clock = FakeClock()
        self.files = FakeFiles()
        self.hooks = env.hooks
        self.video = env.video(**video_kwargs)
        self.store = PreparedSourceStore(env.tmp_path / "prepared", video=self.video, files=self.files,
                                         clock=self.clock)
        env.stores.append(self.store)
        self.hooks.target = self.store.on_finished
        # The store passes its own on_finished; route it through the recording wrapper.
        original = self.video.start_prepared_file

        def start(path, *, ticket, session_config, expected_size, on_finished):
            return original(path, ticket=ticket, session_config=session_config,
                            expected_size=expected_size, on_finished=self.hooks)

        self.video.start_prepared_file = start
        snap = self.store.create("op-upload")
        handle = self.store.begin_write(snap.source_id, snap.generation, ".mp4")
        self.store.finish_write(handle, *SIZE)
        self.source_id, self.generation = snap.source_id, snap.generation
        self.revision = self.store.save_configuration(self.source_id, self.generation, None, config())

    def play(self, attempt, operation_id=None):
        return self.store.play(self.source_id, self.generation, self.revision, attempt,
                               operation_id or f"op-{attempt}")


def test_composed_play_eof_returns_the_source_to_prepared(env):
    env.decoder.capture = Capture([FRAME, FRAME, "eof"])
    comp = Composition(env)
    record = comp.play(1)
    assert record.outcome is Outcome.STARTED and record.session_ref and record.session_id
    assert wait_until(lambda: comp.store.snapshot().state is SourceState.PREPARED)
    last = comp.store.snapshot().last_attempt
    assert (last.phase, last.end_reason, last.cleanup_ok) == (SessionPhase.ENDED, "eof", True)
    assert comp.store.file_users() == 0 and comp.files.calls == 0       # Stop/EOF keeps the file


def test_composed_ending_source_never_reaches_a_new_reservation(env):
    read_gate = env.gate()
    env.decoder.capture = Capture([FRAME, read_gate, FRAME])
    comp = Composition(env)
    assert comp.play(1).outcome is Outcome.STARTED and read_gate.reached.wait(WAIT)
    comp.video.stop()                                                    # deferred: hook(None)
    current = comp.store.snapshot()
    assert current.state is SourceState.ENDING and current.next_attempt == 2
    with pytest.raises(SourceError) as info:                             # (i) refused before the CAS
        comp.play(2)
    assert info.value.category == "session_cleanup_pending"
    assert comp.store.snapshot().next_attempt == 2 and len(env.hooks.calls) == 1
    read_gate.release.set()
    assert wait_until(lambda: comp.store.snapshot().state is SourceState.PREPARED)
    assert comp.play(2).outcome is Outcome.STARTED


def test_composed_busy_video_uses_the_attempt_with_refused(env):
    comp = Composition(env)
    token = comp.video.claim_start()                                     # VideoService not IDLE
    record = comp.play(1)
    assert (record.outcome, record.reason) == (Outcome.REFUSED, "video_busy")
    assert comp.store.snapshot().next_attempt == 2 and comp.store.snapshot().state is SourceState.PREPARED
    assert comp.play(1, "op-1").attempt == 1                             # replay: same record
    comp.video.abort_start(token)


def test_composed_geometry_failure_with_a_failed_hook_delivery(env):
    env.decoder.capture = Capture([FRAME, SMALL, "eof"])
    comp = Composition(env)
    env.hooks.raise_times = 1                                            # first delivery fails
    comp.play(1)
    assert wait_until(lambda: comp.video.get_status().state is VideoState.ERROR)   # after the hook
    assert len(env.hooks.calls) == 1
    current = comp.store.snapshot()
    assert not current.config_ready and comp.store.file_users() == 1    # fail-closed: still held
    with pytest.raises(SourceError):
        comp.play(2)
    comp.video.stop()                                                    # Stop redelivers
    assert env.hooks.reasons() == ["frame_size_changed", "frame_size_changed"]
    current = comp.store.snapshot()
    assert current.state is SourceState.PREPARED and current.geometry_unstable and not current.config_ready
    with pytest.raises(SourceError) as info:
        comp.play(2)
    assert info.value.category == "source_geometry_unstable"


def test_composed_play_deadline_cancels_the_running_reservation(env):
    gate = env.gate()
    env.detector.start_gate = gate
    comp = Composition(env)
    thread, box = env.background(comp.play, 1)
    assert gate.reached.wait(WAIT)
    comp.clock.advance(60.0)
    tick_thread, _ = env.background(comp.store.tick)                     # cancel_session(lease)
    assert wait_until(lambda: comp.video._current.released)              # noqa: SLF001
    gate.release.set()
    tick_thread.join(WAIT)
    record = finish(thread, box)["result"]
    assert record.outcome is Outcome.TIMED_OUT
    assert wait_until(lambda: comp.store.snapshot().state is SourceState.PREPARED)
    assert env.hooks.reasons() == ["cancelled"] and comp.video.get_status().state is VideoState.IDLE


def test_composed_hook_after_store_close(env):
    read_gate = env.gate()
    env.decoder.capture = Capture([FRAME, read_gate, "eof"])
    comp = Composition(env, cleanup_wait_timeout_seconds=0.05)
    env.hooks.first_gate = env.gate()
    comp.play(1)
    assert read_gate.reached.wait(WAIT)
    read_gate.release.set()                                              # EOF: the capture thread cleans up
    assert env.hooks.first_gate.reached.wait(WAIT)                       # its hook is held
    comp.store.close()                                                   # waits only for its budget
    env.hooks.first_gate.release.set()
    assert wait_until(lambda: comp.video.get_status().state is VideoState.IDLE)
    assert comp.files.calls == 0 and comp.store.snapshot().cleanup_pending   # recorded, not deleted


def test_default_factory_gives_distinct_ids_per_reservation(env):
    video = env.video()                                                  # default session_id factory
    identities = []
    for _ in range(2):
        env.decoder.capture = Capture([FRAME, "eof"])
        ticket, started = env.start(video)
        assert started
        identities.append(ticket.identity())
        assert wait_until(lambda: video.get_status().state is VideoState.IDLE)
    (ref_a, sid_a), (ref_b, sid_b) = identities
    assert len({ref_a, sid_a, ref_b, sid_b}) == 4
    for value in (ref_a, sid_a, ref_b, sid_b):
        assert len(value) == 36 and value.count("-") == 4                # uuid4 text form


# --------------------------------------------------------------------------- binding rollback (P1b fix)


class FaultyRegistry(SessionConfigRegistry):
    """A real registry whose bind can store and THEN raise, and whose unbind can fail."""

    def __init__(self, *, bind_raises_after_store: bool = False, unbind_failures: int = 0) -> None:
        super().__init__()
        self.bind_raises_after_store = bind_raises_after_store
        self.unbind_failures = unbind_failures
        self.unbind_calls: list[tuple[str, str]] = []

    def bind(self, session_id, lease, config) -> None:
        super().bind(session_id, lease, config)
        if self.bind_raises_after_store:
            raise RuntimeError("bind failed after storing")

    def unbind(self, session_id, lease) -> bool:
        self.unbind_calls.append((session_id, lease))
        if self.unbind_failures > 0:
            self.unbind_failures -= 1
            raise OSError("registry unavailable")
        return super().unbind(session_id, lease)


def _use_registry(env, registry) -> None:
    env.registry = registry
    env.detector.registry = registry


def test_a_bind_that_stored_then_raised_is_rolled_back(env):
    _use_registry(env, FaultyRegistry(bind_raises_after_store=True))
    video = env.video()
    ticket = HandoffTicket(next(_tickets))
    with pytest.raises(PlaybackRefused) as info:
        env.start(video, ticket)
    assert info.value.category == "required_component_refused"
    assert len(env.registry) == 0                                        # the stored entry was removed
    assert env.registry.unbind_calls == [(ticket.identity()[1], ticket.lease)]
    assert env.hooks.reasons() == ["refused"] and env.hooks.oks() == [True]
    assert env.detector.starts == [] and env.decoder.created == 0


def test_a_failed_unbind_stays_pending_until_a_stop_retry(env):
    _use_registry(env, FaultyRegistry(unbind_failures=1))
    env.decoder.capture = Capture([FRAME, "eof"])
    comp = Composition(env)
    record = comp.play(1)
    assert record.outcome is Outcome.STARTED
    # ERROR is written after the hook returned (and the store applied it).
    assert wait_until(lambda: comp.video.get_status().state is VideoState.ERROR)   # no new reservation
    assert env.hooks.reasons() == ["eof"] and env.hooks.oks() == [False]   # not reported clean
    assert len(env.registry) == 1                                        # obligation kept
    current = comp.store.snapshot()
    assert current.state is SourceState.ENDING and comp.store.file_users() == 1
    with pytest.raises(SourceError) as info:
        comp.play(2)
    assert info.value.category == "session_cleanup_pending" and comp.store.snapshot().next_attempt == 2
    with pytest.raises(SourceError) as info:
        comp.store.create("op-other")
    assert info.value.category == "slot_busy"
    comp.video.stop()                                                    # explicit retry
    assert env.hooks.oks() == [False, True] and len(env.registry) == 0
    assert len(env.registry.unbind_calls) == 2
    assert comp.video.get_status().state is VideoState.IDLE
    assert comp.store.snapshot().state is SourceState.PREPARED and comp.store.file_users() == 0


def test_a_stale_rollback_never_removes_another_sessions_binding(env):
    _use_registry(env, FaultyRegistry())
    env.registry.bind("same", "lease-of-b", "config-of-b")               # another reservation's entry
    video = env.video(session_id_factory=lambda: "same")
    ticket = HandoffTicket(next(_tickets))
    with pytest.raises(PlaybackRefused):
        env.start(video, ticket)                                          # bind conflicts: refusal
    assert env.registry.unbind_calls == [("same", ticket.lease)]          # rollback with its own lease
    assert env.registry.get("same") == "config-of-b"                       # B's binding kept
    assert env.registry.count("unbind_ignored") == 1
    assert env.hooks.reasons() == ["refused"] and env.hooks.oks() == [True]
    video.stop()
    assert env.registry.get("same") == "config-of-b"
