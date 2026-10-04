# Engineering design conventions

How new backend code is shaped. Principles: [`design-docs/core-beliefs.md`](design-docs/core-beliefs.md).
Architecture: [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Service shape

- `from __future__ import annotations`; frozen dataclasses for internal contracts;
  Pydantic only at the HTTP boundary.
- Constructors validate every numeric parameter (type, finite, range, relations) and
  raise `ValueError`; config parsers return `None` on any violation (fail-closed).
- One `threading.Lock` (or `Condition`) per service; immutable snapshots via
  `get_snapshot()`; counters for every drop/reject path.
- Session lifecycle: `start_session(id)`, `end_session(id)` (only the matching id),
  `close()` idempotent; a session generation guards work done outside the lock.
- Inject `clock` (control timeline) and any external adapter; keep observation
  timestamps separate.
- Never call an adapter, device, HTTP, encoder or another service while holding the
  lock (documented exception: evaluation worker → trigger/budget/buffer/audio gate).

## VideoService concurrency rules (still enforced)

Carried over from the previous `AGENTS.md`; origin `phase1-video-input-plan.md` (historical).

- State machine `IDLE → STARTING → RUNNING → STOPPING → IDLE`, any state → `ERROR`;
  `POST /api/video/stop` is also the error reset.
- `_release_resources()` is idempotent (`_released` flag) and never sets `_state`;
  each caller sets its own post-cleanup state.
- `_packet` is guarded only by `_packet_cond`; never hold `_lock` while touching it.
- No self-join: cleanup skips `thread.join()` on the capture thread.
- Starts are atomic reservations (`_atomic_reserve()`, IDLE→STARTING under `_lock`);
  upload is two-phase (`claim_start()` → `start_file(path, token)`), and the start
  token blocks a racing `stop()` from reviving the service.
- File playback uses a monotonic deadline loop; the webcam goes to `ERROR` only after
  `WEBCAM_MAX_CONSECUTIVE_FAILURES` (5) failed reads.
- Upload guards: MIME + extension + OpenCV probe, 200 MB max, partial files deleted.
- Each `_atomic_reserve()` creates a new `session_id` and starts sessions on all
  pipeline consumers; `_release_resources()` ends them.

## Trigger shape (parking, retail, private parking)

`observe(domain_snapshot, tracking_snapshot)` with session/domain/monotonic checks;
`wait_for_candidate`, `wake_waiters`, `claim_next_candidate`, `complete`, `fail`,
`get_snapshot()` exposing `source_session_id` and `pending_event_id`. Candidates carry
`event_id`, `source_session_id`, `domain`, `person_track_id`, `object_track_id`
(`None` when there is no object), `started_at`, `triggered_at`, `evidence`.

## Evidence shape

Exactly five JPEG frames per bundle, strictly ordered, each within the plan deviation;
plans are created per domain by a strategy function. Never duplicate frames to fill a
bundle and never fall back to another domain's or mode's evidence.

## Change shape

Core (isolated files + fake tests + mutation checks) → review → commit → integration
(runtime/API/UI + fake integration tests) → review → commit. Live checks are separate
tasks with owner permission.
