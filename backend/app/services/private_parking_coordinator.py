"""Private-parking coordinator and frame-sink tee (T3b; not wired into runtime yet).

``PrivateParkingCoordinator`` takes the parking-trigger slot of DetectorService
(``start_session``/``observe``/``end_session``/``close``) and runs on the detector
thread. Per observation, in this order (bounded: two marker drains):

  1. ``trigger.observe(...)``
  2. drain the trigger's markers and apply them to the sampler
  3. for each popped alert: local audio offer FIRST (optional gate), then seal the
     evidence and publish — or abandon on an evidence failure, or release the sealed
     evidence when publication did not succeed
  4. drain/apply markers once more (closures created in step 3)

Every call passes the alert's OWN identity: ``(event_id, episode_id, token)``, where
the token itself names the episode instance and the event. Nothing is mixed between
alerts; the trigger and sampler also refuse a mixed or stale identity.

Marker synchronization: if a drain or apply fails, the sampler may hold working samples
that no longer match the trigger. Publication of new candidates is then held (their
alerts still get the local audio offer) and an authoritative resync view is requested;
held alerts are sealed/published only after a view was applied. A held candidate whose
deadline passes simply expires (never published); the hold queue is bounded.

No generation of its own: stale work is refused by the token and by the trigger,
sampler and gate session checks. No lock is held while calling a dependency; the
coordinator's own lock guards only its small state. ``close()`` stops new work and is
idempotent; it never closes a dependency (runtime owns their shutdown). Logs carry only
step names and exception types, never ids, images or payloads.

``FrameSinkTee`` takes the event-frame-buffer slot of VideoService and forwards
``offer``/``start_session``/``end_session`` to every sink (e.g. EventFrameBuffer and the
evidence sampler), isolating failures per sink. It does no I/O, HTTP or inference
itself; each call takes the sinks' own locks, so it can wait for their (bounded,
in-memory) critical sections.

Cleanup failures are reported, never hidden: ``end_session`` still tries every sink,
then raises ``SessionCleanupError`` when one did not end, so VideoService keeps the step
pending (ERROR, a new session refused) and retries it on the next explicit Stop. Both
wrappers remember what is still pending, so a retry ends only the dependencies that
failed. The error carries no ids or messages of the underlying failure.

Tee obligations are bounded and never evicted. The tee owns at most one ACTIVE session
(started, not yet ended) plus the sessions whose cleanup is still pending; together they
never exceed ``max_pending_cleanups``. ``start_session`` reserves the slot the session's
cleanup may need, so a failed end always fits. When no slot is free - or another session
is still active, or the same id still has cleanup pending - ``start_session`` raises
``FrameSinkAdmissionError`` BEFORE any sink is touched. ``end_session`` for an active or
pending session always runs, whatever the capacity; a successful cleanup frees its slot.
``end_session`` for a session the tee never started is a no-op (no obligation).

Leases (T3c0.1): the tee accepts the owner's per-operation lease
(``start_session``/``end_session(..., lease=...)``) and acts on an end only when the id
AND the lease match its record, so a late end of an old operation never touches a newer
session with the same id. ``lease=None`` keeps the id-only behaviour. After ``close()``
both wrappers refuse ``start_session`` with a sanitized admission error (never a silent
return); ``end_session`` stays available so recorded cleanup can still finish.
"""

from __future__ import annotations

from collections import deque
import logging
import threading

from app.models.private_parking import CandidateOutcome, LocalProximityAlert

logger = logging.getLogger(__name__)

MAX_HELD_ALERTS = 32
MAX_PENDING_CLEANUPS = 8


class SessionCleanupError(RuntimeError):
    """A wrapped dependency did not end its session; retry ``end_session`` (sanitized)."""


class FrameSinkAdmissionError(RuntimeError):
    """The tee refused a new session before touching any sink (sanitized: no ids)."""


class SessionAdmissionError(RuntimeError):
    """The coordinator refused a session start (closed, or the trigger refused it);
    sanitized, no ids."""


def _log_failure(component: str, step: str, error: BaseException) -> None:
    """Sanitized: step name and exception type only (no message, ids or payloads)."""
    try:
        logger.warning("%s %s failed (%s)", component, step, type(error).__name__)
    except Exception:
        pass


class PrivateParkingCoordinator:
    def __init__(self, trigger, sampler, *, audio_gate=None, max_held_alerts: int = MAX_HELD_ALERTS) -> None:
        for name, dependency, methods in (
            ("trigger", trigger, ("start_session", "end_session", "observe", "pop_markers", "pop_alerts",
                                  "publish_candidate", "abandon_candidate", "request_resync")),
            ("sampler", sampler, ("apply_markers", "seal", "release")),
        ):
            if not all(callable(getattr(dependency, method, None)) for method in methods):
                raise TypeError(f"{name} does not provide the coordinator contract")
        if audio_gate is not None and not callable(getattr(audio_gate, "offer_local", None)):
            raise TypeError("audio_gate must provide offer_local")
        if type(max_held_alerts) is not int or not 1 <= max_held_alerts <= 1024:
            raise ValueError("max_held_alerts must be an integer in [1, 1024]")
        self._trigger = trigger
        self._sampler = sampler
        self._gate = audio_gate
        self._max_held = max_held_alerts
        self._lock = threading.Lock()            # guards the fields below only
        self._closed = False
        self._resync_needed = False
        self._held: deque[LocalProximityAlert] = deque()
        self._counts = dict.fromkeys((
            "observations", "observations_after_close", "alerts", "audio_offers", "audio_failures",
            "seals_failed", "published", "publish_refused", "abandoned", "released",
            "marker_sync_failures", "resyncs_requested", "resyncs_applied", "alerts_held",
            "held_released", "held_dropped", "step_failures", "starts_refused_closed",
        ), 0)

    # ── lifecycle (DetectorService parking-trigger slot) ──────────────────────

    def start_session(self, session_id: str) -> None:
        """Raises ``SessionAdmissionError`` after close() (never a silent return)."""
        with self._lock:
            if self._closed:
                self._counts["starts_refused_closed"] += 1
                raise SessionAdmissionError("private parking coordinator is closed")
            self._resync_needed = False
            self._held.clear()                   # held alerts of an older session are void
        if self._call("trigger", "start_session", self._trigger.start_session, session_id) is _FAILED:
            # Re-raised (P1b): the required detector refuses and VideoService rolls back,
            # so a session never runs without its configuration.
            raise SessionAdmissionError("private parking trigger refused the session") from None

    def end_session(self, session_id: str) -> None:
        """Forwarded even after close() (cleanup is never refused). Raises
        ``SessionCleanupError`` when the trigger did not end the session; calling again
        retries it (the trigger is the only dependency this ends)."""
        with self._lock:
            self._held = deque(alert for alert in self._held if alert.source_session_id != session_id)
        if self._call("trigger", "end_session", self._trigger.end_session, session_id) is _FAILED:
            raise SessionCleanupError("private parking trigger did not end the session") from None

    def close(self) -> None:
        """Stop accepting new work; idempotent; dependencies stay open (runtime closes them)."""
        with self._lock:
            self._closed = True
            self._held.clear()

    # ── observation (detector thread) ─────────────────────────────────────────

    def observe(self, domain_snapshot, tracking_snapshot) -> None:
        with self._lock:
            if self._closed:
                self._counts["observations_after_close"] += 1
                return
            self._counts["observations"] += 1
        self._call("trigger", "observe", self._trigger.observe, domain_snapshot, tracking_snapshot)
        synced = self._sync_markers()
        alerts = self._call("trigger", "pop_alerts", self._trigger.pop_alerts)
        if alerts is _FAILED or alerts is None:
            alerts = ()
        if synced:
            for alert in self._take_held():      # alerts held during an earlier failure
                self._prepare(alert)
        for alert in alerts:
            if not isinstance(alert, LocalProximityAlert):
                continue
            self._bump("alerts")
            self._offer_audio(alert)             # audio first, independent of evidence
            if synced:
                self._prepare(alert)
            else:
                self._hold(alert)
        self._sync_markers()                     # closures created while preparing (once)

    # ── steps ─────────────────────────────────────────────────────────────────

    def _sync_markers(self) -> bool:
        """Drain and apply one marker batch. Returns True only when the sampler is known
        to be in sync: the batch was applied and, after an earlier failure, it was an
        authoritative view."""
        with self._lock:
            resync_needed = self._resync_needed
        try:
            batch = self._trigger.pop_markers()
            applied = self._sampler.apply_markers(batch)
        except Exception as error:
            _log_failure("private parking coordinator", "marker sync", error)
            applied, batch = False, None
        in_sync = bool(applied) and (not resync_needed or getattr(batch, "view", None) is not None)
        if in_sync:
            if resync_needed:
                self._bump("resyncs_applied")
            with self._lock:
                self._resync_needed = False
            return True
        if not resync_needed:
            self._bump("marker_sync_failures")
        with self._lock:
            self._resync_needed = True
        if self._call("trigger", "request_resync", self._trigger.request_resync) is not _FAILED:
            self._bump("resyncs_requested")
        return False

    def _offer_audio(self, alert: LocalProximityAlert) -> None:
        if self._gate is None:
            return
        try:
            self._gate.offer_local(alert.event_id, alert.source_session_id)
        except Exception as error:
            self._bump("audio_failures")
            _log_failure("private parking coordinator", "local audio offer", error)
        else:
            self._bump("audio_offers")

    def _prepare(self, alert: LocalProximityAlert) -> None:
        """Seal with the alert's own identity, then publish, abandon or release."""
        event_id, episode_id, token = alert.event_id, alert.episode_id, alert.token
        result = self._call("sampler", "seal", self._sampler.seal, alert.source_session_id, episode_id,
                            event_id, alert.triggered_at, token=token)
        if result is _FAILED or getattr(result, "evidence", None) is None:
            self._bump("seals_failed")
            if self._call("trigger", "abandon_candidate", self._trigger.abandon_candidate, token) \
                    is CandidateOutcome.ABANDONED:
                self._bump("abandoned")
            return
        outcome = self._call("trigger", "publish_candidate", self._trigger.publish_candidate, token)
        if outcome is CandidateOutcome.PUBLISHED:
            self._bump("published")
            return
        self._bump("publish_refused")
        if self._call("sampler", "release", self._sampler.release, event_id, token=token) is True:
            self._bump("released")

    def _hold(self, alert: LocalProximityAlert) -> None:
        with self._lock:
            if len(self._held) >= self._max_held:
                self._held.popleft()             # its candidate expires unpublished
                self._counts["held_dropped"] += 1
            self._held.append(alert)
            self._counts["alerts_held"] += 1

    def _take_held(self) -> tuple[LocalProximityAlert, ...]:
        with self._lock:
            held = tuple(self._held)
            self._held.clear()
            self._counts["held_released"] += len(held)
        return held

    def _call(self, component: str, step: str, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:
            self._bump("step_failures")
            _log_failure(f"private parking {component}", step, error)
            return _FAILED

    def _bump(self, name: str) -> None:
        with self._lock:
            self._counts[name] += 1

    def get_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts, held_alerts=len(self._held), resync_needed=int(self._resync_needed),
                        closed=int(self._closed))


class _Failed:
    """Sentinel for a dependency call that raised (never equal to a real result)."""

    def __repr__(self) -> str:
        return "<failed>"


_FAILED = _Failed()


class FrameSinkTee:
    """Forwards frames and session calls to every sink, isolating failures per sink.

    Public use (VideoService event-frame-buffer slot): ``start_session`` -> ``offer``... ->
    ``end_session`` for one session at a time; ``end_session`` may be repeated until it
    stops raising. Obligations (active session + sessions with sinks still to end) are
    bounded by ``max_pending_cleanups`` and never dropped; see the module docstring."""

    accepts_lifecycle_lease = True

    def __init__(self, *sinks, max_pending_cleanups: int = MAX_PENDING_CLEANUPS) -> None:
        if not sinks:
            raise ValueError("at least one sink is required")
        for sink in sinks:
            if not all(callable(getattr(sink, method, None)) for method in ("offer", "start_session", "end_session")):
                raise TypeError("every sink must provide offer, start_session and end_session")
        if type(max_pending_cleanups) is not int or not 1 <= max_pending_cleanups <= 64:
            raise ValueError("max_pending_cleanups must be an integer in [1, 64]")
        self._sinks = tuple(sinks)
        self._capacity = max_pending_cleanups
        # Serializes start/end (sink calls included) so two cleanups of one session never
        # race; offer() never takes it. Order: _lifecycle_lock -> _lock; sinks are called
        # with _lifecycle_lock only.
        self._lifecycle_lock = threading.Lock()
        self._lock = threading.Lock()            # guards the fields below
        self._closed = False
        self._active: tuple[str, object] | None = None      # (session id, lease) started, not ended
        # session id -> (lease, sinks still to end); a session id is never active and pending
        self._pending_end: dict[str, tuple[object, frozenset[int]]] = {}
        self._counts = dict.fromkeys(("starts_refused_capacity", "starts_refused_active",
                                      "starts_refused_pending", "starts_refused_closed", "end_failures",
                                      "cleanups_completed", "stale_ends"), 0)

    def offer(self, frame) -> bool:
        """True when at least one sink accepted the frame. Each sink gets the same object."""
        with self._lock:
            if self._closed:
                return False
        accepted = False
        for sink in self._sinks:
            try:
                accepted = bool(sink.offer(frame)) or accepted
            except Exception as error:
                _log_failure("frame sink", "offer", error)
        return accepted

    def start_session(self, session_id: str, *, lease: object = None) -> None:
        """Admit a new session or raise ``FrameSinkAdmissionError`` before any sink is
        called (also after close())."""
        with self._lifecycle_lock:
            with self._lock:
                reason = None
                if self._closed:
                    reason, counter = "the tee is closed", "starts_refused_closed"
                elif self._active is not None:
                    reason, counter = "another session is still active", "starts_refused_active"
                elif session_id in self._pending_end:
                    reason, counter = "cleanup of this session is still pending", "starts_refused_pending"
                elif len(self._pending_end) >= self._capacity:
                    reason, counter = "cleanup capacity is full", "starts_refused_capacity"
                if reason is not None:
                    self._counts[counter] += 1
                else:
                    self._active = (session_id, lease)   # reserves the slot its cleanup may need
            if reason is not None:
                raise FrameSinkAdmissionError(f"frame sink tee refused a new session: {reason}")
            for sink in self._sinks:
                try:
                    sink.start_session(session_id)
                except Exception as error:
                    _log_failure("frame sink", "start_session", error)

    def end_session(self, session_id: str, *, lease: object = None) -> None:
        """Reverse order; forwarded even after close() (cleanup is never refused, also at
        full capacity). Every sink still to end is tried; if any failed, raises
        ``SessionCleanupError`` and keeps exactly those sinks pending, so a later call
        retries only them. No-op (counted ``stale_ends``) unless the id AND the lease
        match an active or pending record."""
        with self._lifecycle_lock:
            with self._lock:
                pending = self._pending_end.get(session_id)
                if pending is not None and pending[0] == lease:
                    targets = pending[1]
                elif self._active == (session_id, lease):
                    targets = frozenset(range(len(self._sinks)))
                else:
                    self._counts["stale_ends"] += 1
                    return
            failed = set()
            for index in reversed(range(len(self._sinks))):
                if index not in targets:
                    continue
                try:
                    self._sinks[index].end_session(session_id)
                except Exception as error:
                    failed.add(index)
                    _log_failure("frame sink", "end_session", error)
            with self._lock:
                if self._active == (session_id, lease):
                    self._active = None          # its reserved slot moves to _pending_end if needed
                if failed:
                    self._pending_end[session_id] = (lease, frozenset(failed))
                    self._counts["end_failures"] += 1
                else:
                    self._pending_end.pop(session_id, None)
                    self._counts["cleanups_completed"] += 1
        if failed:
            raise SessionCleanupError(f"{len(failed)} frame sink(s) did not end the session") from None

    def pending_cleanups(self) -> int:
        """Number of sessions with at least one sink still to end."""
        with self._lock:
            return len(self._pending_end)

    def get_diagnostics(self) -> dict[str, int]:
        """Counts only (no session ids): every open obligation and the refusal counters."""
        with self._lock:
            active = int(self._active is not None)
            return dict(self._counts, capacity=self._capacity, active_sessions=active,
                        pending_sessions=len(self._pending_end),
                        pending_sink_ends=sum(len(sinks) for _lease, sinks in self._pending_end.values()),
                        free_slots=self._capacity - len(self._pending_end) - active,
                        closed=int(self._closed))

    def close(self) -> None:
        """Stop forwarding new frames; later starts are refused; idempotent; sinks stay
        open and every recorded cleanup can still run."""
        with self._lock:
            self._closed = True
