"""Prepared-source store for the private-parking configuration flow (P1 plan, phase P1a).

One slot holds at most one prepared video file. The store owns the file from the first
write until a successful deletion; nothing else deletes it. Not wired into the runtime
yet (P1c); VideoService's side of the handoff is P1b.

Contracts (plan §8):

* Identities: ``source_id`` (uuid4), ``generation`` (strictly increasing per process),
  ``config_revision`` (per generation), ``attempt`` (CAS from ``next_attempt``) and the
  client ``operation_id``, bound to exactly one attempt for the whole generation. The
  binding set is never evicted while the generation lives (cap ``max_attempts``); only
  full attempt records are evicted (newest ``retained_attempts`` kept).
* Handoff ticket: single-use authority between the store and VideoService. Exactly one
  transition leaves OPEN: ``try_consume`` (VideoService, inside its reservation section)
  or ``cancel`` (store: deadline, DELETE, close). The ticket lock is a leaf.
* File users: the writer until it returns, preview readers, and the ticket of an
  executed attempt from the pin until it is cancelled while still open or a hook with
  ``cleanup_ok=True`` arrives for the lease recorded at consume. The file is deleted only
  when no user remains; until then the deletion is an obligation owned by the store.
* Outcome is write-once; the session phase only moves forward; the slot state follows
  from both (``PLAYING`` only for ``STARTED`` + ``RUNNING``).
* Deadlines are authoritative: every operation that accepts, extends or completes work
  first applies the deadlines and the idle TTL due at the control clock, under the store
  lock, so a late request or return never revives a voided source; the ticket refuses a
  consume after its deadline. Deadlines void an operation at once and never kill a
  thread. Expiry is not cleanup success. Deletion is single-flight per obligation, retried every
  ``cleanup_retry_seconds`` up to ``cleanup_attempt_limit`` attempts, then
  ``CLEANUP_STALLED`` (obligation kept, admission closed; an explicit DELETE runs one
  more attempt). Startup deletes leftovers of the dedicated directory; a leftover it
  cannot delete is an obligation like any other. ``close()`` closes admission for good
  and runs at most one final attempt per obligation without users or attempt in flight.
* Lock order: ``store lock -> ticket lock``; the store never calls VideoService, the
  filesystem or a decoder under its lock, and VideoService calls ``on_finished`` outside
  its own locks.

Durations are the owner's initial defaults (plan §0.1), not calibrated values. Logs are
generic: no path, file name, configuration value or image data.
"""

from __future__ import annotations

import dataclasses
import enum
import functools
import itertools
import logging
import os
import pathlib
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Callable, Protocol

from app.models.private_parking import MAX_FRAME_SIDE, OperatorConfiguration

logger = logging.getLogger(__name__)

AWAITING_FILE_SECONDS = 600.0
WRITING_SECONDS = 300.0
PLAY_PENDING_SECONDS = 60.0
IDLE_TTL_SECONDS = 1800.0
RETAINED_ATTEMPTS = 8
MAX_ATTEMPTS = 256
CLEANUP_RETRY_SECONDS = 60.0
CLEANUP_ATTEMPT_LIMIT = 10
SHUTDOWN_BUDGET_SECONDS = 5.0
ACCEPTED_SUFFIXES = (".mp4", ".webm")
_GONE_MEMORY = 8          # recently voided source ids answered with ``source_gone``


class SourceState(str, enum.Enum):
    AWAITING_FILE = "AWAITING_FILE"
    WRITING = "WRITING"
    PREPARED = "PREPARED"
    PLAY_PENDING = "PLAY_PENDING"
    PLAYING = "PLAYING"
    ENDING = "ENDING"
    DELETING = "DELETING"
    CLEANUP_PENDING = "CLEANUP_PENDING"
    CLEANUP_STALLED = "CLEANUP_STALLED"


class Outcome(str, enum.Enum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    REFUSED = "REFUSED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class SessionPhase(int, enum.Enum):
    NONE = 0
    RUNNING = 1
    ENDING = 2
    ENDED = 3


class TicketState(str, enum.Enum):
    OPEN = "OPEN"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"


class CancelResult(str, enum.Enum):
    OPEN_CANCELLED = "OPEN_CANCELLED"        # no reservation can exist any more
    CONSUMED = "CONSUMED"                    # a reservation exists; end it by its lease
    ALREADY_CANCELLED = "ALREADY_CANCELLED"


class SourceError(Exception):
    """A refused request. ``category`` is a fixed, safe string (wire category in P1c)."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class PlaybackRefused(Exception):
    """Raised by ``start_prepared_file`` for a refusal (outcome ``REFUSED``)."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class HandoffTicket:
    """Single-use handoff between the store and VideoService. Leaf lock: no other lock
    is taken and no I/O happens under it. The hook summary is bounded (latest hook and
    a count), so repeated cleanup retries add no state."""

    def __init__(self, ticket_id: int, deadline: float | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        self.ticket_id = ticket_id
        self._deadline = deadline
        self._clock = clock
        self._lock = threading.Lock()
        self._state = TicketState.OPEN
        self._lease: str | None = None
        self._session_ref: str | None = None
        self._session_id: str | None = None
        self._hooks = 0
        self._last_hook: tuple[str, bool | None] | None = None

    def try_consume(self, lease: str, session_ref: str, session_id: str) -> bool:
        """False when cancelled or past its deadline (then cancelled here: the deadline is
        authoritative even before the sweeper runs)."""
        with self._lock:
            if self._state is not TicketState.OPEN:
                return False
            if self._deadline is not None and self._clock is not None and self._clock() >= self._deadline:
                self._state = TicketState.CANCELLED
                return False
            self._state = TicketState.CONSUMED
            self._lease, self._session_ref, self._session_id = lease, session_ref, session_id
            return True

    def cancel(self) -> tuple[CancelResult, str | None]:
        with self._lock:
            if self._state is TicketState.OPEN:
                self._state = TicketState.CANCELLED
                return CancelResult.OPEN_CANCELLED, None
            if self._state is TicketState.CONSUMED:
                return CancelResult.CONSUMED, self._lease
            return CancelResult.ALREADY_CANCELLED, None

    @property
    def state(self) -> TicketState:
        with self._lock:
            return self._state

    @property
    def lease(self) -> str | None:
        with self._lock:
            return self._lease

    def identity(self) -> tuple[str | None, str | None]:
        """``(session_ref, session_id)`` recorded at consume (never changed later)."""
        with self._lock:
            return self._session_ref, self._session_id

    def record_hook(self, end_reason: str, cleanup_ok: bool | None) -> None:
        with self._lock:
            self._hooks += 1
            self._last_hook = (end_reason, cleanup_ok)

    def hook_summary(self) -> tuple[int, tuple[str, bool | None] | None]:
        with self._lock:
            return self._hooks, self._last_hook


class PreparedPlayback(Protocol):
    """VideoService side (P1b). ``start_prepared_file`` consumes ``ticket`` inside its
    reservation section and returns False (stale, nothing reserved) when the ticket was
    already cancelled; it raises ``PlaybackRefused`` for a refusal. ``on_finished`` may be
    called before it returns."""

    def start_prepared_file(self, path: pathlib.Path, *, ticket: HandoffTicket,
                            session_config: OperatorConfiguration, expected_size: tuple[int, int],
                            on_finished: Callable[[int, str, str, bool | None], None]) -> bool: ...

    def cancel_session(self, lease: str) -> bool: ...

    def stop(self) -> None: ...


class SourceFiles(Protocol):
    def remove(self, path: pathlib.Path) -> None: ...        # FileNotFoundError counts as removed

    def list_files(self, directory: pathlib.Path) -> list[pathlib.Path]: ...


class LocalSourceFiles:
    """Regular files directly inside the dedicated directory; no recursion, no links."""

    def remove(self, path: pathlib.Path) -> None:
        os.remove(path)

    def list_files(self, directory: pathlib.Path) -> list[pathlib.Path]:
        directory.mkdir(parents=True, exist_ok=True)
        with os.scandir(directory) as entries:
            return [pathlib.Path(entry.path) for entry in entries if entry.is_file(follow_symlinks=False)]


@dataclasses.dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    operation_id: str
    outcome: Outcome
    reason: str | None
    session_ref: str | None
    session_id: str | None
    phase: SessionPhase
    end_reason: str | None
    cleanup_ok: bool | None


@dataclasses.dataclass(frozen=True)
class CurrentSnapshot:
    """One store-lock read. Source fields are None when only startup leftovers remain."""

    source_id: str | None
    generation: int | None
    state: SourceState
    upload_operation_id: str | None
    frame_width: int | None
    frame_height: int | None
    preview_revision: int | None
    config_revision: int | None
    config_ready: bool
    geometry_unstable: bool
    next_attempt: int | None
    last_attempt: AttemptRecord | None
    expires_in_seconds: float | None
    deadline_in_seconds: float | None
    cleanup_pending: bool
    cleanup_reason: str | None
    cleanup_attempts: int


@dataclasses.dataclass(frozen=True)
class PreparedFile:
    state: SourceState
    frame_width: int
    frame_height: int
    preview_revision: int


@dataclasses.dataclass(eq=False)
class _Run:
    attempt: int
    operation_id: str
    ticket: HandoffTicket
    source: "_Source"
    outcome: Outcome = Outcome.PENDING
    reason: str | None = None
    phase: SessionPhase = SessionPhase.NONE
    end_reason: str | None = None
    cleanup_ok: bool | None = None
    holds_file: bool = True


@dataclasses.dataclass(eq=False)
class _Obligation:
    path: pathlib.Path
    source: "_Source | None"
    reason: str
    attempts: int = 0
    next_at: float = 0.0
    in_flight: bool = False
    stalled: bool = False


@dataclasses.dataclass(eq=False)
class WriteHandle:
    """The writer's authority for one upload. ``path`` is where it writes; ``cancelled``
    is set when the operation is voided (DELETE, deadline, close). The writer stops at
    its next check and returns through ``finish_write`` / ``fail_write``."""

    source: "_Source"
    path: pathlib.Path
    cancelled: threading.Event = dataclasses.field(default_factory=threading.Event)
    done: bool = False


@dataclasses.dataclass(eq=False)
class ReadHandle:
    source: "_Source"
    path: pathlib.Path
    done: bool = False


@dataclasses.dataclass(eq=False)
class _Source:
    source_id: str
    generation: int
    upload_operation_id: str
    state: SourceState
    deadline: float | None
    expires_at: float | None = None
    path: pathlib.Path | None = None
    writer: WriteHandle | None = None
    readers: int = 0
    holding: set = dataclasses.field(default_factory=set)
    frame_width: int | None = None
    frame_height: int | None = None
    config: OperatorConfiguration | None = None
    config_revision: int = 0
    geometry_unstable: bool = False
    next_attempt: int = 1
    bindings: dict = dataclasses.field(default_factory=dict)
    records: OrderedDict = dataclasses.field(default_factory=OrderedDict)
    active: _Run | None = None
    voided: bool = False
    void_reason: str | None = None
    obligation: _Obligation | None = None

    def users(self) -> int:
        return (self.writer is not None) + self.readers + len(self.holding)


def _is_int(value: object) -> bool:
    return type(value) is int


def _draining(method):
    """Run deferred deletions/cancels outside the lock after the method, also when it
    raises (a refused request may still have expired the source)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        finally:
            self._drain()
    return wrapper


class PreparedSourceStore:
    def __init__(self, directory: pathlib.Path, *, video: PreparedPlayback,
                 files: SourceFiles | None = None, clock: Callable[[], float] = time.monotonic,
                 source_id_factory: Callable[[], str] | None = None,
                 awaiting_file_seconds: float = AWAITING_FILE_SECONDS,
                 writing_seconds: float = WRITING_SECONDS,
                 play_pending_seconds: float = PLAY_PENDING_SECONDS,
                 idle_ttl_seconds: float = IDLE_TTL_SECONDS,
                 retained_attempts: int = RETAINED_ATTEMPTS, max_attempts: int = MAX_ATTEMPTS,
                 cleanup_retry_seconds: float = CLEANUP_RETRY_SECONDS,
                 cleanup_attempt_limit: int = CLEANUP_ATTEMPT_LIMIT,
                 shutdown_budget_seconds: float = SHUTDOWN_BUDGET_SECONDS,
                 sweeper_max_wait_seconds: float = 1.0) -> None:
        self._directory = pathlib.Path(directory)
        self._video = video
        self._files = files if files is not None else LocalSourceFiles()
        self._clock = clock
        self._new_source_id = source_id_factory or (lambda: str(uuid.uuid4()))
        self._awaiting_file_seconds = awaiting_file_seconds
        self._writing_seconds = writing_seconds
        self._play_pending_seconds = play_pending_seconds
        self._idle_ttl_seconds = idle_ttl_seconds
        self._retained_attempts = retained_attempts
        self._max_attempts = max_attempts
        self._cleanup_retry_seconds = cleanup_retry_seconds
        self._cleanup_attempt_limit = cleanup_attempt_limit
        self._shutdown_budget_seconds = shutdown_budget_seconds
        self._sweeper_max_wait = sweeper_max_wait_seconds
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._generations = itertools.count(1)
        self._ticket_ids = itertools.count(1)
        self._slot: _Source | None = None
        self._obligations: list[_Obligation] = []
        self._tickets: dict[int, _Run] = {}        # tracked from the pin until the file use ends
        self._gone: deque[str] = deque(maxlen=_GONE_MEMORY)
        self._closed = False
        self._storage_failed = False
        self._sweeper: threading.Thread | None = None
        self._deferred_obligations: list[_Obligation] = []   # claimed under the lock, run after it
        self._deferred_cancels: list[tuple[int, str]] = []   # (ticket_id, lease) to cancel after it
        self._counts = {name: 0 for name in (
            "stale_hooks", "stale_returns", "cancel_session_calls", "cleanup_attempts",
            "cleanup_failures", "startup_leftovers")}
        self._startup_sweep()

    # ------------------------------------------------------------------ helpers

    def _now(self) -> float:
        return float(self._clock())

    def _log(self, message: str) -> None:
        logger.warning("Prepared source: %s", message)

    def count(self, name: str) -> int:
        with self._lock:
            return self._counts[name]

    def _startup_sweep(self) -> None:
        try:
            leftovers = self._files.list_files(self._directory)
        except Exception:                                   # noqa: BLE001 - never raise from startup
            self._storage_failed = True
            self._log("prepared-source directory is unavailable; admission closed")
            return
        claimed = []
        with self._lock:
            for path in leftovers:
                obligation = _Obligation(path=path, source=None, reason="startup_leftover")
                obligation.in_flight = True
                self._obligations.append(obligation)
                claimed.append(obligation)
                self._counts["startup_leftovers"] += 1
        for obligation in claimed:
            self._attempt_delete(obligation)

    def _due_locked(self) -> None:
        """Caller holds the lock. Apply the slot's deadline or idle TTL due at the control
        clock; deletions and session cancels it causes are deferred to ``_drain``."""
        source = self._slot
        if self._closed or source is None or source.voided:
            return
        now = self._now()
        if source.deadline is not None and source.deadline <= now:
            if source.state is SourceState.AWAITING_FILE:
                self._deferred_obligations += self._void(source, "expired")
            elif source.state is SourceState.WRITING:
                self._deferred_obligations += self._void(source, "deadline_exceeded")
            elif source.state is SourceState.PLAY_PENDING:
                source.deadline = None
                self._deferred_cancels += self._cancel_active(source, Outcome.TIMED_OUT)
        elif (source.state is SourceState.PREPARED and source.expires_at is not None
              and source.expires_at <= now):
            self._deferred_obligations += self._void(source, "expired")

    def _drain(self) -> None:
        """Outside the lock: run deletions/cancels deferred by ``_due_locked``."""
        with self._lock:
            obligations, cancels = self._deferred_obligations, self._deferred_cancels
            self._deferred_obligations, self._deferred_cancels = [], []
        if obligations or cancels:
            self._run(obligations, cancels)

    def _live(self, source_id: str, generation: int | None) -> _Source:
        """Caller holds the lock. The current, not voided source of that generation, after
        applying due deadlines (the caller's public method drains in ``finally``)."""
        self._due_locked()
        source = self._slot
        if source is None or source.source_id != source_id:
            if source_id in self._gone:
                raise SourceError("source_gone")
            raise SourceError("unknown_source")
        if generation is not None and generation != source.generation:
            raise SourceError("generation_mismatch")
        if source.voided:
            raise SourceError("expired" if source.void_reason == "expired" else "source_gone")
        return source

    def _reset_ttl(self, source: _Source) -> None:
        if source.state is SourceState.PREPARED and not source.voided:
            source.expires_at = self._now() + self._idle_ttl_seconds

    def _void(self, source: _Source, reason: str) -> list[_Obligation]:
        """Caller holds the lock. Void the generation; returns claimed obligations."""
        source.voided = True
        source.void_reason = reason
        source.deadline = None
        source.expires_at = None
        source.state = SourceState.DELETING
        self._gone.append(source.source_id)
        if source.writer is not None:
            source.writer.cancelled.set()
        if source.path is None:                              # nothing written: slot is free
            if self._slot is source:
                self._slot = None
            self._cond.notify_all()
            return []
        source.obligation = _Obligation(path=source.path, source=source, reason=reason,
                                        next_at=self._now())
        self._obligations.append(source.obligation)
        self._cond.notify_all()
        return self._claim(source.obligation)

    def _claim(self, obligation: _Obligation | None) -> list[_Obligation]:
        """Caller holds the lock. Single flight: at most one attempt per obligation."""
        if (obligation is None or obligation.in_flight or obligation not in self._obligations
                or (obligation.source is not None and obligation.source.users() > 0)):
            return []
        obligation.in_flight = True
        return [obligation]

    def _attempt_delete(self, obligation: _Obligation) -> None:
        """Outside the lock; the caller claimed ``obligation`` (in_flight set)."""
        try:
            self._files.remove(obligation.path)
            removed = True
        except FileNotFoundError:
            removed = True
        except Exception:                                   # noqa: BLE001 - kept as an obligation
            removed = False
        with self._lock:
            self._counts["cleanup_attempts"] += 1
            obligation.in_flight = False
            if removed:
                if obligation in self._obligations:
                    self._obligations.remove(obligation)
                if obligation.source is not None and self._slot is obligation.source:
                    self._slot = None
            else:
                self._counts["cleanup_failures"] += 1
                obligation.attempts += 1
                if obligation.attempts >= self._cleanup_attempt_limit:
                    obligation.stalled = True
                else:
                    obligation.next_at = self._now() + self._cleanup_retry_seconds
            self._cond.notify_all()
        if not removed:
            self._log("file deletion failed; obligation kept")

    def _run(self, obligations: list[_Obligation], cancels: list[tuple[int, str]]) -> None:
        """Outside the lock: end consumed sessions by their lease, then delete files. A
        cancel is sent only while its ticket still holds the file under that lease: once a
        clean hook proved the cleanup, it is dropped (never repeated)."""
        with self._lock:
            leases = []
            for ticket_id, lease in cancels:
                run = self._tickets.get(ticket_id)
                if run is not None and run.holds_file and run.ticket.lease == lease:
                    leases.append(lease)
                    self._counts["cancel_session_calls"] += 1
        for lease in leases:
            try:
                self._video.cancel_session(lease)
            except Exception:                               # noqa: BLE001 - the hook decides
                self._log("session cancel failed")
        for obligation in obligations:
            self._attempt_delete(obligation)

    def _release_run(self, run: _Run) -> None:
        """Caller holds the lock. The run's file use ends; a voided source's deletion
        becomes due (run by the caller's thread or the next sweeper pass)."""
        run.holds_file = False
        self._tickets.pop(run.ticket.ticket_id, None)
        run.source.holding.discard(run)
        if run.source.voided and run.source.users() == 0 and run.source.obligation is not None:
            run.source.obligation.next_at = min(run.source.obligation.next_at, self._now())
            self._cond.notify_all()

    def _settle(self, source: _Source) -> None:
        """Caller holds the lock. Slot state follows outcome and session phase."""
        run = source.active
        if source.voided or run is None:
            return
        if not run.holds_file:
            source.active = None
            source.deadline = None
            if source.state in (SourceState.PLAY_PENDING, SourceState.PLAYING, SourceState.ENDING):
                source.state = SourceState.PREPARED
                source.expires_at = self._now() + self._idle_ttl_seconds
        elif run.outcome is Outcome.PENDING and run.phase is SessionPhase.NONE:
            source.state = SourceState.PLAY_PENDING
        elif run.outcome is Outcome.STARTED and run.phase is SessionPhase.RUNNING:
            source.state, source.deadline = SourceState.PLAYING, None
        else:
            source.state, source.deadline = SourceState.ENDING, None

    def _cancel_active(self, source: _Source, outcome: Outcome) -> list[tuple[int, str]]:
        """Caller holds the lock. Cancel the active ticket (§8.3 step 7); returns the
        ``(ticket_id, lease)`` whose consumed reservation must be ended outside the lock."""
        run = source.active
        if run is None or not run.holds_file:
            return []
        result, lease = run.ticket.cancel()
        if run.outcome is Outcome.PENDING:
            run.outcome = outcome
        if result is not CancelResult.CONSUMED:          # no reservation can exist
            self._release_run(run)
            self._settle(source)
            return []
        self._settle(source)
        return [(run.ticket.ticket_id, lease)] if lease is not None else []

    def _record(self, run: _Run) -> AttemptRecord:
        session_ref, session_id = run.ticket.identity()
        return AttemptRecord(run.attempt, run.operation_id, run.outcome, run.reason, session_ref,
                             session_id, run.phase, run.end_reason, run.cleanup_ok)

    # ------------------------------------------------------------------ upload

    @_draining
    def create(self, operation_id: str) -> CurrentSnapshot:
        if not isinstance(operation_id, str) or not operation_id:
            raise SourceError("invalid_operation_id")
        with self._lock:
            if self._closed:
                raise SourceError("closed")
            self._due_locked()
            if self._storage_failed:
                raise SourceError("storage_unavailable")
            source = self._slot
            if source is not None:
                if not source.voided and source.upload_operation_id == operation_id:
                    return self._snapshot_locked()
                raise SourceError("cleanup_pending" if source.voided else "slot_busy")
            if self._obligations:
                raise SourceError("cleanup_pending")
            self._slot = _Source(source_id=self._new_source_id(), generation=next(self._generations),
                                 upload_operation_id=operation_id, state=SourceState.AWAITING_FILE,
                                 deadline=self._now() + self._awaiting_file_seconds)
            return self._snapshot_locked()

    @_draining
    def begin_write(self, source_id: str, generation: int, suffix: str) -> WriteHandle:
        if suffix not in ACCEPTED_SUFFIXES:
            raise SourceError("unsupported_type")
        with self._lock:
            if self._closed:
                raise SourceError("closed")
            source = self._live(source_id, generation)
            if source.state is not SourceState.AWAITING_FILE:
                raise SourceError("upload_not_expected")
            source.path = self._directory / f"{uuid.uuid4().hex}{suffix}"     # never reused, no client name
            source.writer = WriteHandle(source=source, path=source.path)
            source.state = SourceState.WRITING
            source.deadline = self._now() + self._writing_seconds
            return source.writer

    @_draining
    def finish_write(self, handle: WriteHandle, frame_width: int, frame_height: int) -> PreparedFile:
        """The writer published a probed file. The WRITING deadline is checked first: a
        voided operation answers its category, never becomes PREPARED, and the writer
        thread deletes the partial file (through the single-flight path)."""
        if not all(type(side) is int and 1 <= side <= MAX_FRAME_SIDE for side in (frame_width, frame_height)):
            self.fail_write(handle, "not_decodable")
            raise SourceError("not_decodable")
        claimed: list[_Obligation] = []
        with self._lock:
            self._due_locked()
            source = self._end_writer(handle)
            if source.voided:
                category = source.void_reason
                claimed = self._claim(source.obligation)
            else:
                source.state = SourceState.PREPARED
                source.deadline = None
                source.frame_width, source.frame_height = frame_width, frame_height
                source.expires_at = self._now() + self._idle_ttl_seconds
                return PreparedFile(source.state, frame_width, frame_height, 1)
        self._run(claimed, [])
        raise SourceError("deadline_exceeded" if category == "deadline_exceeded" else "cancelled")

    @_draining
    def fail_write(self, handle: WriteHandle, category: str) -> None:
        """Write/probe failed: the source is voided and the partial file deleted once."""
        with self._lock:
            self._due_locked()
            source = self._end_writer(handle)
            claimed = self._void(source, category) if not source.voided else self._claim(source.obligation)
        self._run(claimed, [])

    def _end_writer(self, handle: WriteHandle) -> _Source:
        if handle.done:
            raise SourceError("write_finished")
        handle.done = True
        source = handle.source
        if source.writer is handle:
            source.writer = None
        if source.voided and source.users() == 0 and source.obligation is not None:
            source.obligation.next_at = min(source.obligation.next_at, self._now())
            self._cond.notify_all()
        return source

    # ------------------------------------------------------------------ reads

    @_draining
    def begin_read(self, source_id: str, generation: int) -> ReadHandle:
        """Preview read: a file user around the decode; resets the idle TTL (D5)."""
        with self._lock:
            source = self._live(source_id, generation)
            if source.state in (SourceState.AWAITING_FILE, SourceState.WRITING):
                raise SourceError("not_prepared")
            source.readers += 1
            self._reset_ttl(source)
            return ReadHandle(source=source, path=source.path)

    def end_read(self, handle: ReadHandle) -> None:
        with self._lock:
            if handle.done:
                return
            handle.done = True
            source = handle.source
            source.readers -= 1
            claimed = self._claim(source.obligation) if source.voided else []
            if source.voided and source.users() == 0:
                self._cond.notify_all()                     # a waiting sweeper may act now
        self._run(claimed, [])

    @_draining
    def touch_template(self, source_id: str, generation: int) -> tuple[int, int]:
        """Template read: resets the idle TTL (D5); returns the generation's frame size."""
        with self._lock:
            source = self._live(source_id, generation)
            if source.frame_width is None:
                raise SourceError("not_prepared")
            self._reset_ttl(source)
            return source.frame_width, source.frame_height

    # ------------------------------------------------------------------ configuration

    @_draining
    def save_configuration(self, source_id: str, generation: int, expected_revision: int | None,
                           configuration: OperatorConfiguration) -> int:
        if not isinstance(configuration, OperatorConfiguration):
            raise SourceError("invalid_geometry")
        if expected_revision is not None and not _is_int(expected_revision):
            raise SourceError("invalid_request")
        with self._lock:
            source = self._live(source_id, generation)
            if source.geometry_unstable:
                raise SourceError("source_geometry_unstable")
            state_refusals = {SourceState.PLAY_PENDING: "play_pending", SourceState.PLAYING: "playing",
                              SourceState.ENDING: "session_cleanup_pending",
                              SourceState.AWAITING_FILE: "not_prepared", SourceState.WRITING: "not_prepared"}
            if source.state in state_refusals:
                raise SourceError(state_refusals[source.state])
            if (expected_revision or 0) != source.config_revision:
                raise SourceError("revision_conflict")
            if (configuration.frame_width, configuration.frame_height) != (source.frame_width, source.frame_height):
                raise SourceError("frame_size_mismatch")
            source.config = configuration
            source.config_revision += 1
            self._reset_ttl(source)
            return source.config_revision

    # ------------------------------------------------------------------ play

    @_draining
    def play(self, source_id: str, generation: int, config_revision: int, attempt: int,
             operation_id: str) -> AttemptRecord:
        if not (_is_int(config_revision) and _is_int(attempt) and isinstance(operation_id, str) and operation_id):
            raise SourceError("invalid_request")
        with self._lock:
            if self._closed:
                raise SourceError("closed")
            source = self._live(source_id, generation)
            bound = source.bindings.get(operation_id)
            if bound is not None:
                if bound != attempt:
                    raise SourceError("operation_reused")
                run = source.records.get(attempt)
                if run is None:
                    raise SourceError("outcome_evicted")
                return self._record(run)
            if attempt < source.next_attempt:
                raise SourceError("attempt_superseded")
            if attempt > source.next_attempt:
                raise SourceError("attempt_conflict")
            if len(source.bindings) >= self._max_attempts:
                raise SourceError("attempt_limit")
            if source.geometry_unstable:
                raise SourceError("source_geometry_unstable")
            if source.state is SourceState.ENDING:
                raise SourceError("session_cleanup_pending")
            last = next(reversed(source.records.values()), None)
            if source.state in (SourceState.PLAY_PENDING, SourceState.PLAYING) or (
                    last is not None and last.outcome is Outcome.PENDING):
                raise SourceError("play_in_progress")
            if source.state is not SourceState.PREPARED or source.config is None:
                raise SourceError("config_not_ready")
            if config_revision != source.config_revision:
                raise SourceError("revision_stale")
            # Linearization point: bind, CAS, pin generation and configuration.
            source.bindings[operation_id] = attempt
            source.next_attempt += 1
            source.deadline = self._now() + self._play_pending_seconds
            run = _Run(attempt=attempt, operation_id=operation_id, source=source,
                       ticket=HandoffTicket(next(self._ticket_ids), source.deadline, self._clock))
            source.records[attempt] = run
            while len(source.records) > self._retained_attempts:
                source.records.popitem(last=False)
            source.active = run
            source.holding.add(run)
            self._tickets[run.ticket.ticket_id] = run
            source.state = SourceState.PLAY_PENDING
            source.expires_at = None
            path, config = source.path, source.config
            size = (source.frame_width, source.frame_height)
        failure: tuple[Outcome, str] | None = None
        consumed = False
        try:
            consumed = self._video.start_prepared_file(path, ticket=run.ticket, session_config=config,
                                                       expected_size=size, on_finished=self.on_finished)
        except PlaybackRefused as error:
            failure = (Outcome.REFUSED, error.category if isinstance(error.category, str) else "refused")
        except Exception:                                   # noqa: BLE001 - outcome FAILED, sanitized
            failure = (Outcome.FAILED, "start_failed")
            self._log("prepared playback start failed")
        claimed: list[_Obligation] = []
        with self._lock:
            self._due_locked()                        # a return after the deadline cannot revive
            if failure is not None:
                if run.outcome is Outcome.PENDING:
                    run.outcome, run.reason = failure
                if run.ticket.cancel()[0] is not CancelResult.CONSUMED and run.holds_file:
                    self._release_run(run)            # refused before any reservation
            elif consumed and run.ticket.state is TicketState.CONSUMED:
                if run.outcome is Outcome.PENDING:
                    run.outcome = Outcome.STARTED
                if run.phase is SessionPhase.NONE:
                    run.phase = SessionPhase.RUNNING
            else:
                self._counts["stale_returns"] += 1
                if run.ticket.cancel()[0] is not CancelResult.CONSUMED and run.holds_file:
                    if run.outcome is Outcome.PENDING:
                        run.outcome, run.reason = Outcome.FAILED, "start_failed"
                    self._release_run(run)
            self._settle(source)
            if source.voided and source.obligation is not None:
                claimed = self._claim(source.obligation)
            record = self._record(run)
        self._run(claimed, [])
        return record

    def on_finished(self, ticket_id: int, lease: str, end_reason: str, cleanup_ok: bool | None) -> None:
        """Session-finished hook (VideoService cleanup runner, outside its locks). Applied
        only for a tracked ticket whose consume recorded this lease; stale hooks of an
        older instance are counted and ignored.

        Deadlines due at the control clock are applied first, so a hook arriving after the
        ``PLAY_PENDING`` deadline cannot clear that deadline: the outcome becomes
        ``TIMED_OUT`` (write-once) and the hook only records the session's cleanup. The
        cancel of that lease is scheduled, not run here (this thread is VideoService's
        cleanup runner): a clean hook drops it, a failed or unknown cleanup keeps it, and
        the sweeper or the next store call sends it outside the lock."""
        with self._lock:
            self._due_locked()
            run = self._tickets.get(ticket_id)
            if run is None or run.ticket.state is not TicketState.CONSUMED or run.ticket.lease != lease:
                self._counts["stale_hooks"] += 1
                return
            run.ticket.record_hook(end_reason, cleanup_ok)
            run.end_reason = end_reason
            if cleanup_ok is True:
                run.phase = SessionPhase.ENDED
                run.cleanup_ok = True
                self._release_run(run)
                self._deferred_cancels = [item for item in self._deferred_cancels if item[0] != ticket_id]
            else:
                run.cleanup_ok = cleanup_ok
                if run.phase < SessionPhase.ENDING:
                    run.phase = SessionPhase.ENDING
            self._settle(run.source)
            if self._deferred_cancels or self._deferred_obligations:
                self._cond.notify_all()                     # the sweeper sends them

    def report_geometry_unstable(self, ticket_id: int, lease: str) -> None:
        """A decoded frame of this session differed from the generation's size (P1b)."""
        with self._lock:
            run = self._tickets.get(ticket_id)
            if run is None or run.ticket.lease != lease:
                self._counts["stale_hooks"] += 1
                return
            if not run.source.voided:
                run.source.geometry_unstable = True

    @_draining
    def attempt(self, source_id: str, attempt: int) -> AttemptRecord:
        with self._lock:
            source = self._live(source_id, None)
            run = source.records.get(attempt)
            if run is not None:
                return self._record(run)
            if attempt in source.bindings.values():
                raise SourceError("outcome_evicted")
            raise SourceError("unknown_attempt")

    # ------------------------------------------------------------------ delete / expiry

    @_draining
    def delete(self, source_id: str, generation: int) -> str:
        """``"DELETING"`` (deletion pending) or ``"GONE"`` (nothing left), read after the
        attempt this call may have run."""
        cancels: list[tuple[int, str]] = []
        with self._lock:
            self._due_locked()
            source = self._slot
            if source is None or source.source_id != source_id:
                if source_id in self._gone:
                    return "GONE"
                raise SourceError("unknown_source")
            if generation != source.generation:
                raise SourceError("generation_mismatch")
            if source.voided:
                obligation = source.obligation
                claimed = self._claim(obligation) if obligation is not None and obligation.stalled else []
            else:
                if source.state is SourceState.PLAYING:
                    raise SourceError("playing")
                if source.state is SourceState.ENDING:
                    raise SourceError("session_cleanup_pending")
                cancels = self._cancel_active(source, Outcome.CANCELLED)
                claimed = self._void(source, "cancelled")
        self._run(claimed, cancels)
        self._drain()
        with self._lock:
            return "GONE" if self._slot is not source else "DELETING"

    @_draining
    def tick(self) -> None:
        """One sweeper pass at the current clock: deadlines, idle TTL, due retries."""
        with self._lock:
            if self._closed:
                return
            self._due_locked()
            now = self._now()
            for obligation in list(self._obligations):
                if not obligation.stalled and obligation.next_at <= now:
                    self._deferred_obligations += self._claim(obligation)

    def _next_due(self) -> float | None:
        """Caller holds the lock. Only work that can run: an obligation still used by a
        reader, writer or session, in flight or stalled has no due time; its release (or
        the attempt's end) notifies the condition instead, so the sweeper never spins.
        Deferred work (a cancel scheduled by a hook) is due now; a pass drains it."""
        if self._deferred_obligations or self._deferred_cancels:
            return self._now()
        times = [obligation.next_at for obligation in self._obligations
                 if not obligation.stalled and not obligation.in_flight
                 and (obligation.source is None or obligation.source.users() == 0)]
        source = self._slot
        if source is not None and not source.voided:
            times += [t for t in (source.deadline, source.expires_at if source.state is SourceState.PREPARED
                                  else None) if t is not None]
        return min(times) if times else None

    def wake(self) -> None:
        """Wake the sweeper (tests advance a fake clock, then wake it)."""
        with self._lock:
            self._cond.notify_all()

    def start(self) -> None:
        with self._lock:
            if self._sweeper is not None or self._closed:
                return
            self._sweeper = threading.Thread(target=self._sweep_loop, name="prepared-source-sweeper",
                                             daemon=True)
            self._sweeper.start()

    def _sweep_loop(self) -> None:
        while True:
            with self._lock:
                if self._closed:
                    return
                due = self._next_due()
                wait = self._sweeper_max_wait if due is None else due - self._now()
                if wait > 0:
                    self._cond.wait(timeout=min(wait, self._sweeper_max_wait))
                    continue
            try:
                self.tick()
            except Exception:                               # noqa: BLE001 - the sweeper never dies
                self._log("sweeper pass failed")

    # ------------------------------------------------------------------ shutdown

    def close(self) -> None:
        """Close admission for good, cancel the open ticket, stop the sweeper and the
        video session, then run at most one final deletion attempt per obligation that
        has no users and no attempt in flight. Waiting is bounded by the shutdown budget;
        a budget that runs out is not cleanup success. Never raises for a failed deletion."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            source = self._slot
            cancels = self._deferred_cancels
            self._deferred_cancels = []
            # Deletions already claimed (in_flight) but still queued for a drain that may
            # never come (the sweeper stops): close runs them as their one final attempt.
            final: list[_Obligation] = self._deferred_obligations
            self._deferred_obligations = []
            if source is not None and not source.voided:
                if source.state is SourceState.PLAY_PENDING:
                    cancels += self._cancel_active(source, Outcome.CANCELLED)
                final += self._void(source, "closed")   # claimed only without file users
            self._cond.notify_all()
            sweeper = self._sweeper
        if sweeper is not None:
            sweeper.join(timeout=self._shutdown_budget_seconds)
        self._run([], cancels)
        try:
            self._video.stop()
        except Exception:                                   # noqa: BLE001
            self._log("video stop failed during shutdown")
        with self._lock:
            for obligation in self._obligations:
                final += self._claim(obligation)
        workers = [threading.Thread(target=self._attempt_delete, args=(obligation,),
                                    name="prepared-source-final-delete", daemon=True) for obligation in final]
        for worker in workers:
            worker.start()
        deadline = time.monotonic() + self._shutdown_budget_seconds
        with self._lock:
            while any(obligation.in_flight for obligation in self._obligations):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=remaining)
            left = len(self._obligations)
        if left:
            self._log(f"{left} cleanup obligation(s) remain after shutdown; startup cleanup handles them")

    # ------------------------------------------------------------------ snapshot

    def admission_open(self) -> bool:
        with self._lock:
            return (not self._closed and not self._storage_failed and self._slot is None
                    and not self._obligations)

    @_draining
    def snapshot(self) -> CurrentSnapshot | None:
        with self._lock:
            self._due_locked()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> CurrentSnapshot | None:
        now = self._now()
        source = self._slot
        obligation = source.obligation if source is not None else None
        if obligation is None and self._obligations:
            obligation = self._obligations[0]
        if source is None and obligation is None:
            return None
        if obligation is not None and (source is None or source.voided):
            if obligation.stalled:
                state = SourceState.CLEANUP_STALLED
            elif obligation.attempts > 0:
                state = SourceState.CLEANUP_PENDING
            else:
                state = SourceState.DELETING
        else:
            state = source.state
        if source is None:
            return CurrentSnapshot(None, None, state, None, None, None, None, None, False, False, None, None,
                                   None, None, True, obligation.reason, obligation.attempts)
        last = next(reversed(source.records.values()), None)
        ready = (source.config is not None and not source.geometry_unstable and not source.voided
                 and source.state is SourceState.PREPARED)
        return CurrentSnapshot(
            source.source_id, source.generation, state, source.upload_operation_id,
            source.frame_width, source.frame_height, 1 if source.frame_width is not None else None,
            source.config_revision or None, ready, source.geometry_unstable,
            source.next_attempt, self._record(last) if last is not None else None,
            max(0.0, source.expires_at - now) if source.expires_at is not None else None,
            max(0.0, source.deadline - now) if source.deadline is not None else None,
            obligation is not None or bool(self._obligations),
            obligation.reason if obligation is not None else None,
            obligation.attempts if obligation is not None else 0)

    def ticket_summary(self, ticket_id: int) -> tuple[int, tuple[str, bool | None] | None] | None:
        with self._lock:
            run = self._tickets.get(ticket_id)
            return None if run is None else run.ticket.hook_summary()

    def file_users(self) -> int:
        with self._lock:
            return 0 if self._slot is None else self._slot.users()
