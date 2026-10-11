# P1 — Private parking: file upload → preview → line/zone configuration → Play

Status: **rev 4.3 — P1a completed (core only, §15.4); P1 as a whole is not complete:
P1b-P1d (VideoService, API/runtime incl. the leftover recovery action, UI) remain and are
not authorized.** Author: Claude
(implementing agent), 2026-10-11, from source read at `a45c986`. Review history: rev 1
(`2fc0a08`) BLOCKED (five gaps), rev 2 (`53e27c1`) BLOCKED (R1-R4), rev 3 (`413c5ae`)
BLOCKED (R5-R7), all by Codex read-only, recorded in
the frontend plan (`private-parking-configuration-ui.md`, draft lokal, belum dipublikasikan) §11-§14. Rev 4 closes R5-R7 with
a single-use handoff ticket (§8.3) and generation-scoped operation IDs (§8.2); Codex
review of rev 4 (`c6e9ee4`), rev 4.1 (`087c771`) and rev 4.2 (`2ade927`): PASS WITH
LIMITATIONS. Rev 4.2 adds
D7-D9 (§13): bounded cleanup retries, the identity scope statement, acceptance 11-13 and
the implementation phases (§14). Rev 4.3 records the owner's parameter decisions (§0.1)
and aligns shutdown and startup cleanup (D10-D12, §13). No source changed, no runtime run.

## 0. PM decisions (approved by the owner, 2026-10-11) — unchanged

1. Phase 1 is video files only; webcam/CCTV preview deferred.
2. The new flow exists only in private mode; legacy and retail stay as they are.
3. An operator configuration is required before Play; the startup file is only a
   template that must be confirmed and validated for the selected source.
4. Private mode may open preview/editor without a valid startup file; analysis stays
   off until a valid operator configuration exists.
5. Replacing a source needs an explicit delete of the old one; no silent replace.
6. A refused Play keeps the source and its configuration.
7. Stop ends analysis; the source is kept until deleted or expired.
8. The inside side is left/right relative to P1 → P2; no offset-based `inside_point`.

**Technical proposals, not PM decisions:** preview = first decodable frame; Play plays
from the start.

### 0.1 PM parameter decisions (owner, 2026-10-11) — initial defaults, not calibration

The owner adopted the proposed values below as **initial defaults**. They are not the
result of measurement or calibration; changing them later is a PM decision, and the
reasons in §8.4 remain the technical rationale, not evidence.

| Parameter | Default |
|---|---|
| `AWAITING_FILE` deadline | 10 min |
| `WRITING` deadline | 5 min |
| `PLAY_PENDING` deadline | 60 s |
| `PREPARED` idle TTL (reset rule D5) | 30 min |
| Full attempt records retained per generation | 8 |
| Attempt cap per generation (ID binding) | 256 |
| Cleanup retry interval | 60 s |
| Cleanup retry limit before `CLEANUP_STALLED` | 10 attempts |

### 0.2 PM decisions after the P1a review (owner, 2026-10-12)

1. **`REFUSED` after an accepted CAS.** Once a Play passed the attempt CAS, a refusal
   (also one before any reservation, e.g. VideoService busy) uses up that attempt: the
   final outcome `REFUSED` with its reason is stored write-once, a replay with the same
   `(attempt, operation_id)` returns the same record, and nothing retries automatically.
   The operator's next Play uses the next attempt. P1c must freeze the wire response for
   this case (§14). This confirms P1a implementation decision 3 (§15).
2. **Recovery for a stalled startup leftover.** P1c adds an operator action that retries
   cleanup without a `source_id` (planned in §14, P1c). P1a adds no endpoint.

## 1. Goal

In private mode an operator uploads a video file, sees a still preview, draws the access
line (P1 → P2, inside left/right) and the motor zones, saves, and only then starts
analysis. Preview never starts analysis, dispatch or audio. Analysis uses exactly the
saved configuration of exactly that source, on frames whose geometry equals the preview.

## 2. Scope / non-scope

Exact files of the implementation phase (after authorization):

| Path | Contract carried |
|---|---|
| new `backend/app/services/prepared_source.py` | Store: one slot, states, handoff ticket, Play attempt records (CAS, generation-wide `operation_id` binding, bounded record retention, attempt cap), deadlines, sweeper, file-user tracking, cleanup obligations, session-end handling |
| new `backend/app/services/session_config_registry.py` | Lease-bound binding of a configuration to one analysis session instance |
| new `backend/app/services/video_decoder.py` | Shared file opener (same `cv2` backend/properties) for preview and playback; first-frame preview decode |
| new `backend/app/api/sources.py` | Endpoints of §5 |
| new `backend/app/models/sources.py` | Wire models, attempt/outcome/session phases, error categories |
| `backend/app/models/private_parking.py` | `inside: left/right`; operator-configuration parser; template conversion |
| `backend/app/services/private_parking_trigger.py` | Configuration and thresholds per session from the registry; refusal without a binding |
| `backend/app/services/private_parking_coordinator.py` | Re-raise a trigger start refusal (today swallowed) |
| `backend/app/services/video_service.py` | `start_prepared_file` consuming the handoff ticket inside the reservation section, binding inside the start section, store-owned file, per-frame geometry check, public `session_ref`, session-finished hook (also for start rollback), `cancel_session(lease)` |
| `backend/app/api/video.py`, `backend/app/models/video.py` | Additive `session_ref` in status; private mode: legacy start routes `409 private_flow_required` before reservation |
| `backend/app/services/runtime.py`, `backend/app/main.py`, `backend/.env.example` | Private wiring without a valid startup file; registry/store/decoder wiring; router; shutdown order; template wording |
| new tests | `test_prepared_source.py`, `test_prepared_source_races.py`, `test_sources_api.py`, `test_session_config_registry.py`, `test_private_session_config.py`, `test_inside_side_geometry.py`, `test_video_prepared_playback.py` |
| adapted tests | `test_private_parking_runtime_wiring.py` (invalid startup ≠ disabled), `test_private_parking_evaluation_enable.py` (wiring), `test_private_parking_trigger.py` (side, per-session config), `test_private_parking_coordinator.py` (start refusal), `test_private_parking_contracts_integration.py` and `test_private_parking_evidence_sampler.py` (config type), `test_video_session_lifecycle.py`, `test_video_required_sinks.py`, `test_video_service.py` (status field, hook), `test_video_api.py`, `test_video_upload_rollback.py` (private-mode refusal of legacy routes), `test_evaluation_runtime_integration.py` (module composition) |
| this plan; after implementation `ARCHITECTURE.md`, `docs/RELIABILITY.md`, `PLAN.md` | Contracts and status |

Non-scope: frontend (`private-parking-configuration-ui.md`, draft lokal, belum dipublikasikan); webcam/CCTV preview;
gesture; camera health (`camera-health-monitoring.md`, draft lokal, belum dipublikasikan); local voice asset; retail;
legacy-mode behaviour; persistence across restarts; threshold editing; multi-camera;
seek. No new dependency.

## 3. Dependencies

T3c `d075207`, T3c0.1 `e50d9e2` (leases, detector cleanup), T3c0.2 `d479357` (required
sinks, `abort_start`, single-owner capture/upload cleanup), T3d `1771d6b`, F1 `a45c986`.
Reused limits: upload ≤ 200 MiB MP4/WebM and its probe; config JSON ≤ 4096 bytes; line
length ≥ 0.05; 1-8 zones named `[a-z0-9_-]{1,32}`, axis-aligned, area ≥ 0.005; thresholds
from `THRESHOLD_SPECS`.

## 4. Identities and capability

| Identity | Issued by | Meaning | Never |
|---|---|---|---|
| `mode` | process (`SENTINEL_PARKING_MODE`) | `legacy` / `private` / `disabled` | changes at runtime |
| `source_id` | store, `uuid4` | one prepared source | reused (also across restarts) |
| `generation` | store, strictly increasing per process | content version of the slot; voids older preview/config/attempts | reused within a process |
| `config_revision` | store, increasing per generation | accepted configuration | carried to another generation |
| `attempt` | store, increasing per generation (CAS) | one Play attempt | executed twice; re-accepted after a newer attempt |
| `operation_id` | client `uuid4` | client correlation of one upload or one Play attempt; bound to exactly one `attempt` for the whole generation (§8.2) | bound to two attempts; accepted again after eviction |
| `ticket` | store, one per executed attempt | single-use handoff authority between store and VideoService; carries the correlation identity before any callback (§8.3) | consumed twice; revived after cancel |
| `lease` | VideoService reservation token | authority of one analysis session instance (internal) | exposed, reused |
| `session_ref` | VideoService, `uuid4` per reservation | public, non-reusable name of that instance | equal to a reusable `session_id` |

`GET /api/video/sources/capabilities` (all modes): `{mode, preparation_available,
source_kinds: ["file"], template_available, evaluation_enabled, limits: {max_upload_bytes,
accepted_types, max_zones, min_line_length, min_zone_area}}`.

## 5. Endpoints (proposal; `/api/video/sources`, mutations private mode only)

| Method / path | Request | Success | Errors (category only) |
|---|---|---|---|
| `GET /capabilities` | — | `200` §4 | — |
| `POST /` | `{operation_id}` | `201 {source_id, generation, state: "AWAITING_FILE"}` | `409 slot_busy`, `cleanup_pending`, `private_mode_inactive` |
| `PUT /{id}/file?generation=g` | multipart `file` | `200 {state: "PREPARED", frame_width, frame_height, preview_revision}` | `404 unknown_source`; `409 generation_mismatch` / `cancelled` / `deadline_exceeded`; `422 unsupported_type` / `too_large` / `not_decodable`; `500 write_failed` |
| `GET /current` | — | `200` §5.1 or `204` | — |
| `GET /{id}/preview?generation=g&preview_revision=r` | — | `200 image/jpeg` (native decoded size) | `404`; `409 generation_mismatch` / `expired` |
| `GET /{id}/template?generation=g` | — | `200 {line, inside, zones, frame_width, frame_height, confirmed: false}` or `204` | `404`; `409` |
| `PUT /{id}/configuration` | `{generation, expected_revision \| null, frame_width, frame_height, line: {p1, p2}, inside, zones}` | `200 {revision, ready: true, canonical}` | `404`; `409 generation_mismatch` / `revision_conflict` / `frame_size_mismatch` / `play_pending` / `playing` / `session_cleanup_pending` / `source_geometry_unstable`; `422 invalid_geometry` + field path |
| `DELETE /{id}?generation=g` | — | `202 {state: "DELETING"}` (also during `PLAY_PENDING`, §8.3 step 7); `204` if already gone | `404`; `409 generation_mismatch` / `playing` / `session_cleanup_pending` |
| `POST /{id}/play` | `{generation, config_revision, attempt, operation_id}` | `200` attempt record §5.2 | `404`; `409 generation_mismatch` / `revision_stale` / `config_not_ready` / `attempt_superseded` / `attempt_conflict` / `operation_reused` / `attempt_limit` / `play_in_progress` / `session_cleanup_pending` / `video_busy` |
| `GET /{id}/play/{attempt}` | — | `200` attempt record | `404 unknown_source`; `410 outcome_evicted` / `source_gone` |

Bodies of `4xx` carry only `{category, field?}`. `POST /api/video/stop` ends analysis.
In private mode the legacy start routes answer `409 private_flow_required` before any
reservation, write or probe; in legacy mode every `/sources` mutation answers `409
private_mode_inactive` and old routes are unchanged. `GET /api/video/status` gains the
additive `session_ref` and `session_id` of the current reservation, read in one
`V._lock` snapshot together with `state` (D6, §5.3).

### 5.1 `GET /current`

`{source_id, generation, state, upload_operation_id, frame_width, frame_height,
preview_revision, config: {revision, ready} | null, template_available, next_attempt,
last_attempt: <attempt record> | null, expires_in_seconds | null, deadline_in_seconds |
null, cleanup: {pending, reason} }`. `ready` is false while the session of the last
attempt is not cleaned up.

### 5.2 Attempt record

`{attempt, operation_id, outcome: "PENDING" | "STARTED" | "REFUSED" | "FAILED" |
"TIMED_OUT" | "CANCELLED", reason | null, session_ref | null, session_id | null,
session: {phase: "NONE" |
"RUNNING" | "ENDING" | "ENDED", end_reason: "stop" | "eof" | "error" | "refused" |
"cancelled" | null, cleanup_ok: bool | null}}`. The outcome is write-once; the session
phase only moves forward (§8.3). `TIMED_OUT`/`CANCELLED` with `session.phase` other than
`NONE` means a reservation had already been made and is being or was cleaned up.
An immediate EOF reads `outcome: STARTED, session.phase: ENDED, end_reason: eof`; a start
still blocked reads `PENDING`.

### 5.3 Identity across endpoints (D6, proposal)

| Identity | Where it appears | Frontend use |
|---|---|---|
| `source_id` + `generation` | `/current`, every `/sources` request and response | binds preview, template, configuration and Play to one prepared content version; any change voids drafts |
| `config_revision` | `/current.config`, PUT answer, Play request | Play only with the accepted revision; a stale one → `409 revision_stale` |
| `attempt` + `operation_id` | `/current.next_attempt`, Play request, attempt record | one manual Play intent; lookup by `attempt`; never a new UUID to resolve an unknown outcome |
| `session_ref` | attempt record, `/api/video/status` | the analysis session instance this attempt started (never reused) |
| `session_id` | attempt record, `/api/video/status`, existing `source_session_id` on detection, domain and evaluation results | joins results to the instance: accept a result only if its `source_session_id` equals the `session_id` of a status snapshot whose `session_ref` equals the attempt's `session_ref` |
| `lease` | internal only | never exposed |

**Scope of the result join (D8).** Two different protections exist and must not be
confused:

- *Internal authority* — lease, ticket, generation, attempt CAS and the lease-bound
  registry (§7.2, §8.2-§8.3). These protect the backend's own state and do **not** depend on
  `session_id` being unique; they hold with the reusing test factory (acceptance 6).
- *Result join for the frontend* — `source_session_id == status.session_id` under a
  matching `session_ref`. This is only as strong as the uniqueness of `session_id`.
  **Precondition:** in the composed runtime `session_id` comes from the default factory
  (`uuid4` per reservation, `VideoService._session_id_factory`); no environment value or
  API input can replace it. Acceptance 13 tests this precondition. With an injected
  reusing factory the join is **not** claimed safe: a stale result carrying the reused id
  is indistinguishable, because result payloads carry no instance authority.

What is atomic: one `GET /api/video/status` response (`state`, `session_ref`,
`session_id` read in one `V._lock` section) and one `GET /current` response (store
fields read in one store-lock section). **Nothing is atomic across endpoints:** status,
`/current`, attempt lookups and detection/domain/evaluation results are read at different
times and may describe different moments; the frontend must treat each as a separate
observation and keep its epoch/abort/render guard. A session switch is visible only after
the next status read. Detection, domain and evaluation schemas stay unchanged in P1.

## 6. Geometry (preview = every analysed frame)

- Coordinates normalized to the **decoded frame**, origin top-left, x right, y down,
  [0, 1]. Preview = first decodable frame at native size (no own resize/crop/rotation).
- **One decoder:** `video_decoder.open_file(path)` is the only opener for preview and
  playback (same backend and properties). The generation stores `frame_width`/
  `frame_height`.
- **Inside side:** `raw_side(Q) = ((x2 − x1)(qy − y1) − (y2 − y1)(qx − x1)) / |P1P2|`
  (existing `AccessLine.raw_side`). Viewed from P1 towards P2 on screen, **left** ⇔
  `raw_side < 0`, **right** ⇔ `raw_side > 0` (P1 = (0, 0), P2 = (1, 0): a point above the
  line on screen gives `raw_side < 0` → left). Reversing P1/P2 flips left/right. The
  trigger's sign is `+1` for right, `−1` for left. No `inside_point` and no point-distance
  rule for operator configurations; line length ≥ 0.05 stays.
- **Template:** a startup file's `inside_point` becomes `inside` by the sign of
  `raw_side(inside_point)` (points within `2 × line_hysteresis` or < 0.02 are rejected as
  today); its thresholds become session thresholds. Returned `confirmed: false`; never used
  until saved by `PUT /configuration` for this generation.
- **Every frame (R4):** `PUT /configuration` must carry the generation's size (`409
  frame_size_mismatch`); Play re-checks. During analysis VideoService compares **each
  decoded frame's size** with the expected size **before** publishing it to the packet slot
  and before offering it to the tee, detector or scene classifier. The first mismatch ends
  the session through the existing capture-error path (ERROR "frame size changed"); the
  mismatching frame and any later frame never reach the sampler, trigger, worker or
  provider. The store then marks the **generation** `geometry_unstable`: configuration
  `ready: false`, Play `409 source_geometry_unstable`, `PUT /configuration` cannot reopen
  it (re-saving the same size would repeat the mismatch; the file itself is not uniform).
  The source and configuration stay readable (decision 6); the remedy is an explicit
  delete and a new upload (e.g. a re-encoded file). Coordinates are never adjusted
  silently. `preview_revision` is constant (`1`) per generation in P1; it is reserved for
  a later preview refresh. Proposal, not a PM decision.
- **Detection limit (D3):** P1 checks frame **size** only. A change of view with the same
  size — a different crop, pan, zoom, rotation within the frame, an edited or spliced file,
  or for a later camera phase a moved camera — passes every check, and analysis then uses
  line and zones that no longer match the scene (wrong or missing warnings). P1 makes no
  claim that a valid configuration matches the physical scene. Detecting this depends on
  the camera-health work (C1H, `camera-health-monitoring.md`, draft lokal, belum dipublikasikan; signal "camera
  shifted": comparison against a reference captured at configuration time); it is a
  prerequisite before any real CCTV use, not part of P1.
- **Letterboxing** is a UI concern (contain transform in the frontend plan); the API sees
  only decoded-frame coordinates. The PUT answer echoes the stored canonical geometry.

## 7. Runtime changes (the static startup composition does not support this)

1. **Mode wiring:** in `private` mode `build_parking_wiring` always builds the private
   objects; the startup file is an optional template (invalid → `template_available: false`
   + generic warning, not the disabled path). `DisabledParkingTrigger` stays for an invalid
   mode value only.
2. **Registry (R3):** `bind(session_id, lease, config)`, `get(session_id)`,
   `unbind(session_id, lease)`. Entries carry the lease; `bind` refuses when an entry for
   that `session_id` exists; `unbind` with another lease is a no-op (counted). Bind, the
   sink starts (whose trigger `start_session` calls `get`) and unbind on the end path all
   run inside VideoService's `_lifecycle_lock`; that serialization makes `get` return the
   binding of the reservation being started, never an older one with the same id.
3. **Trigger:** `PrivateParkingTrigger(default_config=None, session_config=registry.get)`
   uses the bound configuration and its thresholds per session; none → raises
   `PrivateParkingAdmissionError` (sanitized).
4. **Coordinator:** re-raises a trigger start refusal, so the required detector refuses and
   VideoService answers `503 required_component_refused` — never a session without
   configuration.
5. **VideoService:** `start_prepared_file(path, *, ticket, session_config, expected_size,
   on_finished) -> (lease, session_ref)`:
   - inside the existing reservation section of `_atomic_reserve` (under `V._lock`, the
     same section that moves IDLE → STARTING and creates token and session id), it calls
     `ticket.try_consume(lease, session_ref)`; if that fails (ticket cancelled) no
     reservation exists and the call returns `stale` — no sink, capture or bind ran;
   - inside the start section (`_lifecycle_lock`) it binds `(session_id, lease, config)`
     first, then starts sinks; unbind with the same lease in `_end_sinks`;
   - the file is opened through `video_decoder` and **not** attached as `upload_path`
     (the store owns the file; Stop keeps it); the capture is released by the session's
     existing single-owner cleanup (deferred while a read is active);
   - per-frame size check (§6);
   - `on_finished(ticket_id, lease, end_reason, cleanup_ok)` is called by the cleanup
     runner after every finished cleanup attempt of a consumed ticket (outside VideoService
     locks), including start refusal rollback (`end_reason: "refused"`) and Stop retries;
     it may arrive **before** `start_prepared_file` returns;
   - `cancel_session(lease)`: STARTING → the existing `abort_start(token)` rollback;
     RUNNING → a stop of only that instance; any other lease → no-op (counted). Used by
     the store when a consumed ticket is cancelled.
6. **Legacy routes in private mode** refuse before reservation (§5).

## 8. Store lifecycle

### 8.1 States and file users

`EMPTY → AWAITING_FILE → WRITING → PREPARED → PLAY_PENDING → PLAYING → ENDING →
PREPARED`; `PREPARED → DELETING → EMPTY`; any of `AWAITING_FILE`/`WRITING`/`PREPARED`
→ `EXPIRED`/`CANCELLED` → `DELETING`; `DELETING → CLEANUP_PENDING → EMPTY`;
`CLEANUP_PENDING → CLEANUP_STALLED` after the retry limit, `→ EMPTY` only on a successful
deletion (D7). At startup a leftover file whose deletion fails enters `CLEANUP_PENDING`
directly (D12).

File users counted by the store: the writer (until it returns), preview readers (refcount
around each decode, outside the lock), and the **ticket** of an executed attempt — from
the pin (before any VideoService call) until either the ticket is cancelled while still
open (no reservation can exist) or `on_finished(..., cleanup_ok=True)` arrives for the
lease the ticket recorded. There is no window between reservation and registration. **The file is deleted only when no
user remains**; until then the deletion is a recorded obligation owned by the store.

### 8.2 Play attempts (R1)

- `/current` exposes `next_attempt`. A Play executes only if `attempt == next_attempt`
  (compare-and-set under the store lock) and no attempt is `PENDING`/`STARTED` with a live
  session; then `next_attempt` increments and `PLAY_PENDING(attempt, operation_id)` pins
  generation and configuration (the linearization point).
- Identity: `(generation, attempt)` is the Play identity; `operation_id` is bound to
  exactly one attempt for the whole generation. Decision order under the store lock:
  1. `operation_id` already bound in this generation: to the same `attempt` → return
     its record (or `410 outcome_evicted` if the record was evicted); to another attempt →
     `409 operation_reused`, nothing runs;
  2. `attempt < next_attempt` → `409 attempt_superseded`; `attempt > next_attempt` → `409
     attempt_conflict`;
  3. otherwise CAS: bind the id, `next_attempt += 1`, pin.
  **An old attempt is never executed again and an old `operation_id` never executes a
  new attempt**, retained or not.
- Bounded retention that enforces this: the store keeps two structures per generation —
  (a) full attempt records for the **8 most recent** attempts (outcome, session sub-record)
  and (b) the **binding set** `operation_id → attempt` for **every** attempt of the
  generation, never evicted while the generation lives. (b) is bounded by a cap of **256
  attempts per generation** (16-byte UUID + int each, ≈ 6 KiB); at the cap Play answers
  `409 attempt_limit` and the operator deletes and re-uploads. Eviction drops only (a),
  so step 1 still refuses reuse after eviction. Numbers are the PM initial defaults (§0.1).
- Scope: the binding set dies with the generation; an old request carries its old
  `generation` and answers `409 generation_mismatch` before step 1. A client reusing a
  UUID across generations is a client bug the server does not detect (UUIDv4 collisions
  are not a design case). `POST /` (upload) is deduplicated only against the current slot's
  `upload_operation_id`; a replayed `POST /` after delete may create an empty
  `AWAITING_FILE` that expires (10 min) without effect.
- Delete/expiry voids the generation: lookups answer `410 source_gone`.
- `PENDING` while `start_prepared_file` runs; terminal outcomes `STARTED`, `REFUSED`,
  `FAILED`, `TIMED_OUT`, `CANCELLED`. The session sub-record follows the hooks (§5.2, §8.3).

### 8.3 Handoff ticket and session end (R2, R5, R6)

**Ticket.** An in-memory object with its own leaf lock (never held while taking another
lock, no I/O under it) and three states: `OPEN → CONSUMED(lease, session_ref)` or `OPEN →
CANCELLED`, plus a **bounded hook summary** (D1): the latest hook (`end_reason`,
`cleanup_ok`) and a hook count — no per-hook history, so repeated cleanup retries add no
state; an outstanding cleanup obligation is never dropped by summarizing. Exactly one
transition out of `OPEN`
happens; that transition is the **linearization point** between the store and VideoService:

- `try_consume(lease, session_ref)` — VideoService, inside its reservation section;
- `cancel()` — store (deadline, `DELETE`, close); returns `OPEN_CANCELLED` or
  `CONSUMED(lease)`.

If `cancel()` wins, `try_consume` fails and no reservation, bind, sink, capture or
dispatch can follow (R5). If `try_consume` wins, the reservation exists and the store's
cancel learns its lease; that reservation is then ended through its own token (`cancel_
session(lease)` → `abort_start(token)` while STARTING, lease-bound stop while RUNNING).
Invalidation (cancel) is immediate; cleanup completes later and is reported by the hook.

**Steps.**

1. Under the store lock: identity checks and CAS (§8.2), create the ticket, register it as
   file user, record `PENDING`, state `PLAY_PENDING`. Release the lock.
2. Outside any store lock: `start_prepared_file(..., ticket=t, on_finished=store.hook)`.
3. Hooks (any time after `try_consume`, possibly before step 2 returns): `store.hook(
   ticket_id, lease, end_reason, cleanup_ok)` under the store lock: the ticket and its lease
   are already known (recorded at consume), so the hook is applied, never dropped as
   unknown. The store tracks every consumed ticket until its clean hook, also after its
   generation was voided by delete/close, so a late clean hook still releases that file
   use. A hook whose `(ticket_id, lease)` matches no tracked ticket, or whose lease differs
   from the one recorded at consume, is ignored and counted (stale A after B).
4. Return of step 2, under the store lock — merged monotonically:
   - outcome is **write-once**: `PENDING → STARTED | REFUSED | FAILED | TIMED_OUT`; a later
     writer never overwrites it (a cancel that won first records `TIMED_OUT` or
     `CANCELLED`; the late return then only fills `session_ref`);
   - session phase only moves forward: `NONE → RUNNING → ENDING → ENDED`; a successful
     return sets `RUNNING` only while the phase is still `NONE` (a hook may already have
     moved it to `ENDING`/`ENDED`);
   - slot state is derived, not set by the return: `PLAYING` only if the outcome is
     `STARTED` **and** the session phase is `RUNNING`; a return after an EOF/Stop/refusal
     hook leaves the slot `ENDING` or `PREPARED` as the hooks decided.
5. Session end: Stop/EOF/error → phase `ENDING`. Only a hook with `cleanup_ok=True` for the
   ticket's lease sets `ENDED`, releases the ticket's file use and returns the slot to
   `PREPARED` (configuration kept). `cleanup_ok=False`/`null` keeps `ENDING`: no Play, no
   delete, no expiry, admission closed; the next explicit Stop retries and the next hook
   decides.
6. Refused start: VideoService's own rollback calls the hook (`refused`, `cleanup_ok`); the
   return raises → outcome `REFUSED` (decision 6: source and configuration kept). A failed
   rollback stays `ENDING` until a clean hook.
7. Cancel paths (deadline, `DELETE` while `PLAY_PENDING`, close) under the store lock call
   `ticket.cancel()`:
   - `OPEN_CANCELLED` → outcome `TIMED_OUT` (deadline) or `CANCELLED` (delete/close),
     file use released immediately, slot back to `PREPARED` (deadline) or on to
     `DELETING` (delete/close); the blocked start call later returns `stale` and changes
     nothing;
   - `CONSUMED(lease)` → outcome `TIMED_OUT`/`CANCELLED` is recorded only if still `PENDING`,
     slot `ENDING` (deadline) or `DELETING` (delete/close); after releasing the store lock
     the store calls `cancel_session(lease)`; the file stays in use until the clean hook.
     `DELETE` answers `202 DELETING` and the file is deleted only after that hook; close
     records the obligation. `PUT /configuration` during `PLAY_PENDING` stays `409
     play_pending`.
<a id="84-deadlines-idle-ttl-and-cleanup-proposals"></a>

### 8.4 Deadlines, idle TTL and cleanup (PM initial defaults, §0.1)

All values are the owner's initial defaults (§0.1), not measurements or calibration; the
reason column is the technical rationale behind the original proposal.
Deadlines bound one operation; the idle TTL bounds an unused source; retention bounds
memory; the cleanup retry bounds how often a failing deletion is retried.

| Kind | Applies to | Starts / resets | Default | On expiry | Capacity | Reason |
|---|---|---|---|---|---|---|
| Operation deadline | `AWAITING_FILE` | starts at `POST /`; no reset | 10 min | `EXPIRED`, nothing to delete; slot free | 1 slot | operator picks a file within minutes; frees a slot left by a closed tab |
| Operation deadline | `WRITING` (upload copy, probe, preview decode) | starts when the PUT handler begins; no reset | 5 min | operation `deadline_exceeded`, cancel flag; the writer owns and deletes its partial file when it returns; slot held until then | 1 writer; ≤ 200 MiB | local copy of 200 MiB + probe + one decode takes seconds on a local disk; 5 min tolerates a slow disk without leaving a hung write unbounded |
| Operation deadline | `PLAY_PENDING` | starts at the pin; no reset | 60 s | `ticket.cancel()` (§8.3 step 7) | 1 ticket | a start is reservation + sink starts (detector/CLIP model load, lazy); one minute covers a cold model load; longer hides a hang |
| Idle TTL | `PREPARED` source | starts at `→ PREPARED`; reset by any `/sources` mutation, preview or template read and `ENDING → PREPARED` (D5); **not** by `GET /current` polling | 30 min | `EXPIRED → DELETING` | 1 file ≤ 200 MiB | a configuration session (draw, save, Play, Stop, adjust) fits in 30 min of inactivity; bounds disk use after a closed tab |
| — | `PLAYING`, `ENDING` | — | none | waits for analysis end and clean hook | — | expiry must never delete a file in use |
| Outcome retention | full attempt records | per generation, newest kept | 8 records | older record → `410 outcome_evicted`; cannot execute | 8 records | operator Plays are manual; 8 covers several Play/Stop cycles for reconciliation after lost ACKs |
| ID binding after eviction | `operation_id → attempt` set | per generation; never evicted while the generation lives | cap 256 attempts | at the cap Play → `409 attempt_limit` (delete and re-upload) | 256 × (UUID + int), raw ≈ 6 KiB before Python overhead | enforces "no ID reuse" after eviction with bounded memory; 256 manual Plays on one source exceed a working day of tests |
| Cleanup retry interval | `CLEANUP_PENDING` deletion | after each failed delete | 60 s | retry; admission (`POST /`) stays closed | 1 obligation per file | transient locks (antivirus/indexer on Windows) clear within a minute; avoids a busy loop |
| Cleanup retry limit (D7) | automatic deletion retries | count per obligation; reset only by success | 10 attempts (≈ 10 min) | `CLEANUP_STALLED`: automatic retries stop, **obligation kept**, admission stays closed; an explicit `DELETE` runs one more attempt; shutdown runs at most one more (D11); a failed startup sweep keeps the obligation (D12) | 1 attempt in flight | a lock lasting 10 min is not transient; stops endless log/IO churn without forgetting the file |

Ownership rule (D2, replaces the earlier "never-returning start blocks the slot"): a
deadline voids the operation at once and never kills a thread. The slot stays blocked
only while a resource is still owned: a writer that has not returned, or a **consumed**
ticket whose clean hook has not arrived. An `OPEN_CANCELLED` ticket cannot reserve, so it
releases its file use at once and does not block the slot; its start call may still
return later as `stale` without effect. A writer or consumed session that never finishes
is a recorded limitation; restart clears it (startup deletes leftovers in the dedicated
prepared-source directory; a leftover it cannot delete stays an obligation, D12).

One store daemon thread waits on a condition until the earliest deadline (fake clock and
condition in tests) and never holds the store lock while deleting or calling VideoService.
Expiry ≠ cleanup success: expiry voids the generation; deletion is a separate step that
needs zero file users.

**Cleanup retries (D7).** Deletion is single-flight: the obligation carries an
`in_flight` flag set under the store lock before the filesystem call (made outside the
lock) and cleared with the result; a sweeper tick, `DELETE` or shutdown that finds it set
does not start another attempt (no stacked retries). Failures increment the count and
schedule the next attempt; after the limit the state is `CLEANUP_STALLED` (still blocks
`POST /`, still listed in `/current.cleanup`). Success removes the obligation and frees the
slot. Session cleanup (VideoService) is not retried by the store: it stays operator-driven
through Stop (§8.3 step 5).

**Startup cleanup (D12).** At construction the store lists the dedicated prepared-source
directory (never any other path) and deletes each leftover file through the same
single-flight path. A file it cannot delete becomes a `CLEANUP_PENDING` obligation with a
fresh attempt count (this first try counts as attempt 1): it is listed in
`/current.cleanup`, `POST /` answers `409` while it exists, and it follows the normal
retry interval and limit (then `CLEANUP_STALLED`). Admission opens only when no
obligation remains. A failed startup deletion never raises out of construction.

### 8.5 Lock order and shutdown

Lock order: `V._lifecycle_lock → V._lock → ticket lock`, and `store lock → ticket lock`.
The ticket lock is a leaf; the store lock is never taken by VideoService while it holds
its own locks (hooks run outside VideoService locks), and the store never calls
VideoService, the filesystem or `cv2` under its lock. No cycle exists.

**Shutdown (D11).** Store `close()` closes admission permanently (every later `POST /`,
PUT and Play is refused), cancels the open ticket (no late session), stops the sweeper,
then calls `video.stop()`; hooks are recorded. For each obligation — `CLEANUP_PENDING` or
`CLEANUP_STALLED` — with **zero file users and no attempt in flight**, close runs **at
most one** final deletion attempt, through the same single-flight flag. It never starts a
second attempt beside one already in flight, never loops, and never resets the attempt
count. The shutdown budget bounds only how long close waits for that attempt: a budget
that runs out is **not** cleanup success — the obligation stays recorded (logged
generically, without paths of other directories) and the startup sweep handles the file
next time (D12). A failed final attempt likewise keeps the obligation, and admission
stays closed. Close never raises because of a failed deletion.

## 9. Acceptance (deterministic: fakes, fake clock, `threading.Event` gates, deferred responses)

1. **Preview isolation:** spies show no reservation, sink start, detector/CLIP/worker
   call, gate offer or dispatch during upload, preview, template and configuration.
2. **Upload/cancel:** gate write and probe before the ACK; `DELETE` → cancel flag, partial
   file deleted once by the writer, `PUT` `409 cancelled`; `POST /` for B `409` until the
   deletion succeeded; the stale A response/error does not publish A; repeat with the gate
   before and after publishing PREPARED.
3. **Attempts (R1, R7):** A → STARTED, Stop, clean → B → STARTED → replay A (same attempt
   and op) returns A's record and starts nothing; replay A after eviction → `410
   outcome_evicted`, nothing started; op-A with the next valid attempt → `409
   operation_reused`, before **and** after A's record was evicted (fill 8+ attempts);
   stale `attempt` → `409 attempt_superseded`; attempt ahead → `409 attempt_conflict`;
   cap reached → `409 attempt_limit`; gated start: `GET /play/{attempt}` = `PENDING`; lost
   ACK + immediate EOF → `STARTED`, `session.phase = ENDED`, `end_reason = eof`; lookup
   after delete/expiry → `410 source_gone`; one VideoService start per executed attempt.
4. **Handoff races (R5, R6), each with an `Event` gate inside the fake reservation path:**
   - gate **after the store pin, before `try_consume`**; fire the deadline (fake clock),
     `DELETE` or close; release → `try_consume` fails, zero reservations, binds, sink
     starts, captures and dispatches; outcome `TIMED_OUT`/`CANCELLED`; file use released;
   - gate **after `try_consume`, before sink start**; fire the deadline/close; release →
     `cancel_session(lease)` rolls back through `abort_start(token)` of that lease only;
     slot `ENDING` until the clean hook; B cannot start meanwhile;
   - gate **the return** of `start_prepared_file` after an immediate EOF / Stop / refusal
     hook (`cleanup_ok=True`) already ran → record `STARTED + ENDED` (or `REFUSED`), slot
     `PREPARED` (not `PLAYING`), file-user count 0; repeat with hook `cleanup_ok=False` then a
     Stop retry hook `True` → `ENDING` then `PREPARED`;
   - pin then `PUT /configuration` → `409 play_pending`; pin then `DELETE` with the ticket
     open → `CANCELLED`, deletion proceeds; with the ticket consumed → `202 DELETING`,
     `cancel_session(lease)`, file deleted only after the clean hook; never `PLAYING`;
   - stale hook of A (lease A, forced same `session_id`) after B's pin → B unchanged;
   - close in each gate → no late session, obligations recorded, no file deleted while a
     ticket holds it; all gates released and threads joined in teardown (no sleep).
5. **Cleanup ≠ end (R2):** Stop with waiter timeout, capture release failure, sink end
   failure, read still active → `ENDING`, `ready` false, Play/DELETE `409
   session_cleanup_pending`, `POST /` `409`; a later Stop with `cleanup_ok=True` → `PREPARED`
   with the same configuration; refused Play with a failed rollback stays `ENDING`; expiry
   never runs during `ENDING`; the file is never deleted while the session or a preview
   reader uses it.
6. **Instance authority (R3):** forced `session_id` reuse (factory) for A and B: a late
   bind/unbind/`on_finished` of A (lease A) does not change B's binding, thresholds,
   attempt record or slot state; `bind` for an id that is still bound is refused; a start
   without a binding is refused (`503`).
7. **Geometry (R4):** fake capture with matching first frames then a size change → the
   session ends (ERROR "frame size changed") before that frame reaches tee, detector,
   trigger or worker (spies: zero offers, alerts, dispatches from it); generation
   `geometry_unstable`: `ready: false`, Play `409 source_geometry_unstable`, re-save does
   not reopen it, DELETE allowed; horizontal/vertical/diagonal lines with
   left/right match the trigger's signed distance; reversed P1/P2 flips sides; 0/1 edges;
   template conversion incl. too-close rejection and non-default `line_hysteresis`;
   non-finite/out-of-range/degenerate/too-small/too-many/duplicate → `422`; size mismatch at
   PUT and Play → `409`.
8. **TTL/deadlines without calls:** fake clock advanced with no API call and no frame →
   `AWAITING_FILE`, `WRITING`, `PREPARED` and `PLAY_PENDING` deadlines fire from the sweeper;
   deletion fails once/repeatedly → `CLEANUP_PENDING`, retries, admission closed, success →
   `EMPTY`; a never-returning gated writer keeps the slot blocked and marked.
9. **Stale configuration:** gate `PUT /configuration` of A; delete A; new source B (same file
   name, forced same `session_id`) → A's late ACK/revision/preview does not make B ready;
   B's template still needs confirmation.
10. **Legacy/retail and sanitization:** legacy mode unchanged; private-mode legacy routes
    `409 private_flow_required` before write/probe/reservation; no path, file name,
    configuration value, JPEG/base64 or provider body in logs or error bodies.
11. **Idle TTL reset (D5), fake clock:** each successful `PUT /configuration`, preview read
    and template read at t = 29 min moves expiry to t + 30 min; `GET /current`, `GET /play/
    {attempt}`, `GET /capabilities` and `/api/video/status` polled every second do **not**
    (expiry at exactly 30 min of no resetting action); rejected requests (`4xx`, wrong
    `generation`, stale revision) and requests for an old generation do not reset the current
    one; `WRITING`/`PLAY_PENDING` deadlines never reset. Race: gate a preview read after its
    generation check and fire expiry, then release → exactly one winner under the store
    lock: either the reset was applied first (no expiry) or expiry won (the read answers
    `409 expired` and does not reset; the decode already in progress keeps its file-user
    count, so the file is deleted only after it returns).
12. **Bounded cleanup retries (D7, D11, D12):** fake filesystem failing N times: one
    attempt in flight at any moment (gate the delete; sweeper ticks, `DELETE` and shutdown
    during the gate start no second attempt); attempts spaced by the fake clock; after 10
    failures → `CLEANUP_STALLED`, obligation still in `/current.cleanup`, `POST /` still
    `409`, no further automatic attempt; explicit `DELETE` → one attempt; success →
    `EMPTY`. Shutdown: (a) pending or stalled obligation, no users, nothing in flight →
    exactly one attempt; failing → obligation kept, admission closed, no exception;
    succeeding → obligation removed, admission still closed (store closed); (b) attempt
    already in flight (gated) → close starts none, and a shutdown budget that runs out
    while it is gated leaves the obligation recorded (not success); (c) file users remain
    → no attempt. Startup (fake directory): a removable leftover is deleted and `POST /`
    is admitted; an undeletable leftover → `CLEANUP_PENDING` with attempt count 1,
    `POST /` → `409`, retried on the fake clock, `CLEANUP_STALLED` after the limit, no
    exception from construction; only files in the dedicated directory are touched. Hook
    summary (D1): 100 cleanup-false hooks keep a constant-size summary, the obligation
    stays until a true hook.
13. **Identity fields and snapshots (D6, D8):** wire models: status carries `state`,
    `session_ref`, `session_id` from one locked read (gate between reservation and sink
    start: a status read returns STARTING with both new ids or IDLE with neither, never a
    mix of A's ref with B's id); attempt record gets `session_ref`/`session_id` at consume
    and never changes them; `/current` fields come from one store snapshot (gate a `PUT
    /configuration` and a `DELETE` around a `/current` read: the answer shows either the
    old or the new `(source_id, generation, revision, next_attempt)` tuple, never a mix).
    Interleaved responses: gated `GET /preview` and `PUT /configuration` of generation A
    answered after B exists carry A's `generation` (the client can discard them); a late
    attempt record of A carries A's `attempt`/`operation_id`/`session_ref`. Precondition:
    the composed runtime uses the default `session_id` factory (two reservations → distinct
    `uuid4` ids; no env/API path sets the factory). No test asserts a cross-endpoint atomic
    snapshot or safety of results under a reusing factory.

## 10. Risks (high → low)

- **H1** Configuration applied to the wrong session instance → wrong/no warnings
  (acceptance 3, 6, 9).
- **H2** Reviewed T1/T3 contracts change (per-session trigger config, coordinator start
  refusal, VideoService start/hook/per-frame check) — regressions across private-parking
  and video suites (adapted test list in §2).
- **H3** A session or file outliving its owner after timeouts (stuck writer/start, failed
  cleanup) blocks the slot; bounded but needs operator Stop/restart.
- **H4** Geometry: same-size view changes are not detected (§6 detection limit; depends
  on C1H camera health); orientation handling relies on one decoder for preview and
  playback.
- **M1** Sweeper thread and hook add concurrency surface (fake-clock tests required).
- **M2** Browser → server transfer cannot be aborted before the handler; only our work is
  cancelled.
- **M3** Private mode refuses the legacy start routes (mode-specific behaviour change; the
  UI must use the new flow). Consequence of decisions 1 and 3: **webcam analysis is
  unavailable in private mode** until a webcam preview phase exists (no operator
  configuration can be made for it). For the PM to acknowledge; not a new decision here.
- **L1** All durations and the retention size are PM initial defaults (§0.1), not
  calibrated.
- **L2** A valid geometry does not prove the physical camera view is right.

## 11. Open technical points

1. Calibration of the §0.1 defaults (decided by the PM as initial values; any later
   change is a PM decision).
2. `DELETE` async (`202`) vs sync (`204`) when nothing is in use.
3. Whether `PUT /configuration` may rename zones or only validate names.
4. Preview frame: first decodable (proposal) vs operator-chosen time (needs seek).
5. Cross-endpoint identity: proposal D6 (§5.3) joins results by `session_id` through a
   status snapshot that also carries `session_ref`, without changing result schemas.
   Alternative: add `session_ref` to detection/domain/evaluation responses (more files).
6. Wire details of the two P1c items of §0.2 (status codes, body fields, route name) —
   frozen in the P1c review, proposals in §14.

## 12. Verification (planned)

Serial, fakes only, unique `--basetemp`: new test files, adapted tests, then
`test_video_*`, `test_private_parking_*`, `test_detector_*`, runtime integration and the
full backend suite; `pip check`; `git diff --check`; control-byte scan. No server, real
video, webcam, provider, TTS or speaker.

## 13. Review status

Rev 1 `2fc0a08` BLOCKED; rev 2 `53e27c1` BLOCKED (R1-R4); rev 3 `413c5ae` BLOCKED (R5-R7);
rev 4 (this file) closes R5 (ticket linearization), R6 (correlation recorded at consume,
monotonic merge) and R7 (generation-wide `operation_id` binding with a cap); awaiting
Codex re-review. Codex: rev 4 `c6e9ee4` PASS WITH LIMITATIONS (R5-R7 closed in design;
acceptance not run).

**Rev 4.1 delta for targeted re-review** (clarifications; D5 and D6 change the contract):

| # | Section | Change | Contract effect |
|---|---|---|---|
| D1 | §8.3 | Ticket keeps a bounded hook summary instead of an append-only log | none (bounds state; obligations kept) |
| D2 | §8.4 | Ownership rule: only a non-returned writer or a consumed ticket blocks the slot; `OPEN_CANCELLED` releases at once | aligns §8.4 with §8.3 step 7 (editorial) |
| D3 | §6, §10 H4 | Same-size view change stated as a P1 detection limit with the C1H dependency | none (limit made explicit) |
| D4 | §8.4 | Table of every duration/retention with start/reset, expiry, capacity and reason | none (values unchanged, still proposals) |
| D5 | §8.4 | Idle TTL reset by mutations and preview/template reads, not by `/current` polling | **changes** when a source expires |
| D6 | §5, §5.2, §5.3, §11.5 | Status and attempt record carry `session_id` next to `session_ref`; result join rule | **additive** API fields; resolves open point 5 as a proposal |

Codex: rev 4.1 `087c771` PASS WITH LIMITATIONS (D1-D6, no new blocker).

**Rev 4.2 delta for targeted re-review:**

| # | Section | Change | Contract effect |
|---|---|---|---|
| D7 | §8.4, §8.5 | Single-flight deletion, retry limit 10 → `CLEANUP_STALLED` (obligation kept, admission closed, explicit `DELETE` retries once), one final shutdown attempt | **changes** retry behaviour (was unbounded) and adds a state |
| D8 | §5.3 | Internal authority vs result join separated; join precondition (default factory) and the non-claims (no cross-endpoint atomicity, no safety with reused ids) | none (scope made explicit; replaces an overclaim in rev 4.1) |
| D9 | §9.11-§9.13, §14 | Acceptance for TTL reset, bounded retries, identity snapshots; implementation phases | none (acceptance and plan structure) |

Codex: rev 4.2 `2ade927` PASS WITH LIMITATIONS (D7-D9, no blocker; low clarification:
shutdown expectation for a stalled obligation differed between §8.5 and §9.12 — closed
by D11).

**Rev 4.3 delta for targeted re-review** (owner instruction, 2026-10-11):

| # | Section | Change | Contract effect |
|---|---|---|---|
| D10 | §0.1, §8.2, §8.4, §10 L1, §11.1 | Owner's parameter decisions recorded as initial defaults, not calibration | none (values unchanged; status changes from proposal to PM default) |
| D11 | §8.4, §8.5, §9.12 | Shutdown: at most one final attempt per obligation (pending or stalled) with no users and nothing in flight; no parallel attempt, no retry loop, admission closed permanently, budget exhaustion ≠ success, failure keeps the obligation. Resolves the rev 4.2 conflict between §8.5 (one final attempt) and §9.12 (no attempt for a stalled obligation) | **changes** shutdown for stalled obligations |
| D12 | §8.1, §8.4, §9.12 | Failed startup deletion becomes a `CLEANUP_PENDING` obligation (counted, retried, stalls after the limit); admission stays closed until no obligation remains | **changes** startup (was an unspecified "sweep removes leftovers") |

Implementation: P1a only, per §14, after this delta review reports no blocker.

## 14. Implementation phases (each needs its own authorization)

Authorization (owner, 2026-10-11): **P1a** may be implemented once the rev 4.3 delta
review has no blocker, without further approval, using fakes only (no runtime wiring,
provider, real video or device; no stage/commit/push). **P1b, P1c and P1d are not
authorized.**

High risk first; every phase ends with tests, report and review before the next starts.

| Phase | Scope (files) | Acceptance | Depends on |
|---|---|---|---|
| **P1a — core store and ownership** | new `prepared_source.py` (store, states, ticket, attempt CAS, ID binding set and cap, record retention, deadlines, sweeper, file users, single-flight cleanup); new `session_config_registry.py`; `models/private_parking.py` (inside side, operator config, template conversion); tests `test_prepared_source.py`, `test_prepared_source_races.py`, `test_session_config_registry.py`, `test_inside_side_geometry.py` | §9.3, §9.4 and §9.5 against a fake VideoService (ticket consume/cancel, hook before return, fake hooks); §9.6 registry part; §9.7 geometry part; §9.8, §9.9 store part, §9.11, §9.12; §9.13 `/current` snapshot | HEAD `a45c986` only; no runtime change, nothing wired |
| **P1b — VideoService lifecycle and analysis contracts** | `video_service.py` (`start_prepared_file`, consume in the reservation section, hook incl. refusal rollback, `cancel_session`, per-frame size check, status snapshot with `session_ref`/`session_id`); new `video_decoder.py`; `private_parking_trigger.py` (per-session config); `private_parking_coordinator.py` (re-raise); adapted tests `test_video_session_lifecycle.py`, `test_video_required_sinks.py`, `test_video_service.py`, `test_private_parking_trigger.py`, `test_private_parking_coordinator.py`, `test_private_parking_contracts_integration.py`, `test_private_parking_evidence_sampler.py`; new `test_video_prepared_playback.py`, `test_private_session_config.py` | §9.4 with the real VideoService and fake capture/sinks; §9.5; §9.6; §9.7 frame part; §9.13 status snapshot and factory precondition; legacy start paths unchanged | P1a (ticket, registry, config model) |
| **P1c — runtime and API** | new `api/sources.py`, `models/sources.py`; `api/video.py`, `models/video.py` (status fields, private-mode refusal of legacy starts); `runtime.py`, `main.py`, `.env.example`; new `test_sources_api.py`; adapted `test_private_parking_runtime_wiring.py`, `test_private_parking_evaluation_enable.py`, `test_video_api.py`, `test_video_upload_rollback.py`, `test_evaluation_runtime_integration.py` | §9.1, §9.2, §9.3 over HTTP, §9.9, §9.10, §9.11 over HTTP, §9.13 wire models; full backend suite; then docs (`ARCHITECTURE.md`, `docs/RELIABILITY.md`, `PLAN.md`) | P1b |
| **P1d — UI** (frontend plan `private-parking-configuration-ui.md`, draft lokal, belum dipublikasikan; Codex) | frontend files of that plan | that plan's §9 against fakes of the P1c contract | P1c contract frozen (fakes may start after P1c review) |

**P1c additions from the PM decisions of §0.2 (proposals; not authorized yet):**

- *Refused Play response (§0.2.1).* `POST /{id}/play` answers `200` with the attempt record
  for every attempt that passed the CAS, including `outcome: "REFUSED"` and its
  `reason` category (e.g. `video_busy`, `required_component_refused`); `409` categories stay
  reserved for requests refused **before** the CAS (identity, revision, attempt, state).
  `GET /play/{attempt}` and a replay of the same `(attempt, operation_id)` return the
  identical record; `/current.next_attempt` has advanced; the server never retries. P1c
  acceptance: busy and required-component refusals → `200 REFUSED`, replay identical, one
  VideoService start per attempt, the next Play needs the next attempt, wire model frozen
  in `models/sources.py` and reviewed before P1d.
- *Cleanup recovery without a source ID (§0.2.2).* Proposal `POST
  /api/video/sources/cleanup/retry` (private mode, no body): the store method
  `retry_cleanup()` runs **one** attempt for each obligation that is `CLEANUP_STALLED`,
  has no file users and no attempt in flight, through the existing single-flight flag and
  store lock — never a second attempt beside one in flight, no retry loop, no reset of the
  attempt count. It acts only on obligations the store itself recorded (its own sources
  and files found by the startup sweep of the dedicated directory); it takes no path,
  file name or pattern from the request and never lists or touches another directory.
  Answer: `202 {cleanup: {pending, stalled, attempted}}` (counts only, no paths); `204`
  when no obligation remains; `409 private_mode_inactive` / `closed`. A `CLEANUP_PENDING`
  obligation keeps its schedule (no extra attempt), as with `DELETE`. Admission opens only
  when no obligation remains. P1c acceptance: a stalled leftover with the fake filesystem
  failing then succeeding; concurrent retry/DELETE/sweeper under a delete gate → one
  attempt; no path in body or log; a file outside the directory is never touched;
  shutdown and startup rules unchanged.

Risks per phase: P1a carries the concurrency design (ticket, CAS, cleanup) without
touching reviewed runtime code; P1b changes reviewed T1/T3 contracts (highest regression
risk, adapted tests listed); P1c changes mode-specific behaviour (legacy routes in private
mode) and composition; P1d depends on a frozen contract. No phase needs a server, real
video, provider, webcam, TTS or speaker.

## 15. P1a implementation record (Claude, 2026-10-11) — awaiting independent review

Authorization: owner, after Codex PASS on the rev 4.3 delta (blob `0006938`). Preflight:
HEAD `a45c986`, nothing staged, no backend file modified before the change; the
uncommitted documentation of other authors was left untouched.

**Files (P1a scope, nothing wired):**

| File | Content |
|---|---|
| new `backend/app/services/prepared_source.py` | `PreparedSourceStore` (one slot, states, deadlines, idle TTL with the D5 reset rule, attempt CAS, generation-wide `operation_id` binding with cap, record retention, file users, single-flight bounded cleanup, startup sweep D12, `close()` D11, sweeper thread, `/current` snapshot under one lock); `HandoffTicket` (leaf lock, bounded hook summary); `PreparedPlayback` and `SourceFiles` protocols; `LocalSourceFiles` |
| new `backend/app/services/session_config_registry.py` | Lease-bound `bind`/`get`/`unbind`; refusal of a still-bound id; counted no-op for another lease |
| `backend/app/models/private_parking.py` | Additive: `InsideSide`; `AccessLine` takes either `inside_point` (startup file, unchanged) or `inside=` left/right (exactly one); `OperatorConfiguration`, `build_operator_configuration` (`InvalidGeometry.field`), `operator_template` |
| new tests | `test_prepared_source.py` (37), `test_prepared_source_races.py` (16), `test_session_config_registry.py` (8), `test_inside_side_geometry.py` (37) |

**Implementer verification** (Windows, fakes only):

- `pytest` of the four new files: 98 passed; repeated 30 times (store, races,
  registry): 0 failures.
- The private-parking regressions (trigger, contracts integration, runtime wiring):
  passed unchanged.
- The full backend suite: `pytest backend/tests -q --basetemp <unique>` → 1307 passed,
  1 warning (Starlette deprecation, pre-existing).
- `pip check` → no broken requirements; `git diff --check` → clean; the new files have
  no control bytes or trailing whitespace and end with a final LF.
- No server, provider, real video, decoder, webcam, TTS or speaker; nothing staged.

**Acceptance coverage at store level:**

| Acceptance | Covered by | Not yet |
|---|---|---|
| §9.2 upload/cancel | delete while writing, writer deletes partial once, `POST` B `409` until deletion, failed write | HTTP gates (P1c) |
| §9.3 attempts | replay, supersede, conflict, reuse before/after eviction, cap, gated `PENDING`, lost ACK + immediate EOF, `source_gone` | HTTP |
| §9.4 handoff races | gates before/after `try_consume` and on the return, with deadline, `DELETE` and close; refusal rollback; stale hook A after B | real VideoService (P1b) |
| §9.5 cleanup ≠ end | `cleanup_ok` False/None keeps `ENDING`, blocks Play/DELETE/admission/expiry; Stop retry hook | real Stop paths (P1b) |
| §9.6 instance authority | registry with reused id; stale hook with forced same `session_id` | bind inside `_lifecycle_lock`, trigger refusal (P1b) |
| §9.7 geometry | sides on horizontal/vertical/diagonal lines, reverse flip, 0/1 edges, template conversion with non-default hysteresis, invalid fields; store marks `geometry_unstable` | per-frame check in VideoService (P1b) |
| §9.8, §9.11, §9.12 | deadlines and TTL without calls, reset rule, rejected/old-generation requests, preview-versus-expiry race (Barrier, 50 rounds), single flight under concurrent triggers, stall after 10, explicit DELETE, startup D12, shutdown D11 (a), (b), (c) | HTTP polling endpoints (P1c) |
| §9.13 | `/current` tuple never mixed under concurrent saves/delete; attempt ids fixed at consume; late A responses carry A's identity | status snapshot and factory precondition (P1b/P1c) |

**Implementation decisions for the reviewer** (plan text unchanged where not stated):

1. `try_consume(lease, session_ref, session_id)` also records `session_id` so the attempt
   record can carry it (D6); §7.5 names only `(lease, session_ref)`.
2. A size mismatch is reported by `report_geometry_unstable(ticket_id, lease)` before the
   end hook (same lease check as hooks); P1b calls it from the per-frame check.
3. A refusal before any reservation (e.g. VideoService busy) is recorded as attempt
   outcome `REFUSED` with that reason, not answered as `409 video_busy` before the CAS: the
   store cannot know it without starting. The attempt number is used. **Confirmed by the
   PM (§0.2.1);** P1c freezes the wire answer.
4. A failed write/probe voids the source (reason recorded); the operator starts with
   `POST /` again.
5. A clean hook for a voided source only makes its deletion due; the sweeper or the next
   request deletes it, so VideoService's cleanup runner never waits on file I/O.
6. Startup leftovers have no `source_id`: `/current` shows them with empty source fields
   and the cleanup state; an explicit `DELETE` cannot address them (no id), so a stalled
   leftover waits for the final attempt at `close()` or the next startup. **PM (§0.2.2):**
   P1c adds an operator recovery action (planned in §14); none in P1a.
7. The shutdown budget is wall-clock time (`time.monotonic`), not the injected control
   clock; the tests use a 50 ms budget with a held gate.
8. File names in the dedicated directory are fresh `uuid4().hex` plus the suffix, never
   the client file name or `source_id`.

**Limitations (unchanged by P1a):**

- Nothing is wired: the runtime, API, VideoService and the trigger are untouched.
- The durations are the PM initial defaults, not calibrated.
- A writer or consumed session that never returns keeps the slot blocked until restart.
- Same-size view changes are not detected (§6).

### 15.1 Fixes for the Codex review of P1a (owner instruction, same scope)

| Finding | Fix | Regression tests |
|---|---|---|
| F1 Deadlines were applied only by the sweeper, so a late request or return could act on an expired source | `_due_locked()` applies the slot's deadline or idle TTL at the control clock under the store lock at the start of every operation that accepts, extends or completes work (`create`, `begin_write`, `finish_write`, `fail_write`, preview/template reads, save, Play and its return merge, attempt lookup, `delete`, `snapshot`, `tick`); deletions and session cancels it causes run after the lock is released (`_drain` in `finally`). The handoff ticket carries the `PLAY_PENDING` deadline: `try_consume` at or after it cancels the ticket and refuses, so no reservation can follow. A ticket cancelled this way is released like `OPEN_CANCELLED`. Cleanup still waits for readers and writers | save, Play, preview and template after the TTL without a sweeper pass → `409 expired`, zero starts and reservations; finish after the `WRITING` deadline → `deadline_exceeded`, never `PREPARED`, obligation kept and admission closed when the deletion fails; consume after the Play deadline → refused, zero reservations; a late return after the deadline → `TIMED_OUT`, `cancel_session(lease)`, never `PLAYING`; begin-write after the `AWAITING_FILE` deadline |
| F2 An obligation still in use kept a due time of "now", so a running sweeper spun | `_next_due()` ignores obligations with file users (and, as before, in-flight or stalled ones); releasing the last user (reader end, writer return, clean hook) sets the due time and notifies the condition. No polling added | a reader holds an expired file: zero sweeper passes during ten wait periods, deletion after the reader ends; a clean hook of a deleted session wakes the sweeper, which deletes the file |
| F3 Integers too large for a float raised `OverflowError` through validation | `_is_number` treats such integers as not a number, so the boundary fails closed with `InvalidGeometry(field)` / `ValueError` / parser `None`; frame sizes are bounded by `MAX_FRAME_SIDE` in the store; Play and save reject non-integer counters (`invalid_request`). No broad exception handler was added: an unrelated construction error (wrong thresholds object) still propagates as itself | ±10^400 and ±10^20 on line points, zone rectangles and frame sizes → field errors; on `AccessLine`, thresholds (float and int kinds) and the startup parser → controlled failure; on attempt, revision and frame size in the store → `attempt_conflict` / `attempt_superseded` / `revision_stale` / `revision_conflict` / `not_decodable` |

Implementer check of the regressions: each group was also run against the previous
behaviour restored by a scratch pytest plugin (outside the repository; no source change),
and fails there: F1 5 tests, F2 2 tests, F3 8 tests (the ±10^20 cases pass in both
versions, as expected).

Results after the fixes:

- The four P1a files: 132 passed. Store, races and registry repeated 30 times: 0 failures.
- The full backend suite: 1341 passed, 1 warning (Starlette deprecation, pre-existing).
- `pip check` clean; `git diff --check` clean; the new and changed files have no control
  bytes or trailing whitespace and end with a final LF. Nothing staged, no server,
  provider, real video or device.

Remaining limitation from F2: the two sweeper tests use a short real-time window (0.1 s)
to show that no pass happens while a user holds the file; the fix itself is deterministic
(`_next_due()` is None, asserted directly).

**PM decisions recorded for P1a (owner, 2026-10-12; detail in §0.2):**

- `REFUSED` after the CAS was accepted is a final outcome and the attempt is used up; a
  replay returns the same record and nothing retries. This is the implemented behaviour
  (decision 3 above); P1c freezes its wire response.
- Recovery of a stalled startup leftover without a `source_id` is a P1c obligation (an
  operator retry action, serialized, store-owned files only; §14). P1a adds no endpoint.

### 15.2 Fix: consumed ticket → deadline passes → hook → late return (owner instruction)

Gap: the hook did not apply due deadlines. With the ticket consumed and the `PLAY_PENDING`
deadline passed but no sweeper pass yet, a hook settled the slot and cleared the deadline,
and the late return then wrote `STARTED`.

Fix (`prepared_source.py`):

- `on_finished` first applies the deadlines due at the control clock under the store lock,
  before the hook changes the phase or clears the deadline. The outcome is `TIMED_OUT`
  (write-once); the hook then only records the session's cleanup (`phase`, `end_reason`,
  `cleanup_ok`). The outcome of the operation and the cleanup status are separate fields:
  a later clean hook does not change `TIMED_OUT`, and a late return cannot write `STARTED`.
- The cancel of that lease is scheduled as `(ticket_id, lease)`, never run in the hook's
  thread (VideoService's cleanup runner). A clean hook (`cleanup_ok=True`) drops it; with
  `False`/`None` it stays and is sent once, outside the lock, by the next store call or a
  sweeper pass (deferred work is due now in `_next_due`, and the hook notifies the
  condition). Before sending, each cancel is checked under the lock: only while that
  ticket still holds the file under that lease, so a cancel is never repeated after the
  cleanup was proven. `False`/`None` keep the file use, `ENDING`, Play/DELETE `409
  session_cleanup_pending` and admission closed until a clean hook.

Deterministic tests (no sweeper thread; `test_prepared_source_races.py`):

| Case | Expected |
|---|---|
| Hook after the deadline, `cleanup_ok=True`, before the return | outcome `TIMED_OUT`, phase `ENDED`, no cancel sent or scheduled, file use released, `PREPARED`, `POST /` still `slot_busy`, the next Play starts |
| Same with `False` / `None` | outcome `TIMED_OUT`, phase `ENDING`, nothing sent from the hook thread, one cancel scheduled and sent once after the return (not repeated by a later pass), file use kept, `ENDING`, Play/DELETE `session_cleanup_pending`; a later clean hook → `PREPARED`, outcome still `TIMED_OUT` |
| Comparison: hook before the deadline (`True`/`False`/`None`), deadline passes later | unchanged valid behaviour: outcome `STARTED`, no cancel; `True` → `PREPARED`, otherwise `ENDING` with the file use kept |

Implementer checks: the three after-deadline cases fail against the previous hook
(restored by a scratch pytest plugin outside the repository) and pass now; the three
comparison cases pass in both. P1a files 138 passed; store, races and registry repeated
30 times: 0 failures; full backend suite 1347 passed, 1 warning (Starlette deprecation,
pre-existing); `pip check` and `git diff --check` clean; no control bytes or trailing
whitespace; nothing staged; no server, provider, real video or device.

### 15.3 Fix: shutdown skipped a queued deletion (Codex re-review, BLOCKED)

Codex re-review of §15.2 (backend plan `e9df23f`, `prepared_source.py` `b203266`):
the deadline finding is closed (cleanup `True`/`False`/`None` reproduced: `TIMED_OUT`
kept, cancel outside the hook thread, no repeated cancel). **New blocker:** `close()`
took the deferred cancels but not the deferred deletions. A deletion claimed under the
lock (`in_flight=True`) by a hook that applied an expiry, but not yet run (hooks never
drain; the sweeper was held), was skipped by close as "in flight"; the sweeper then exited
on `closed`. Result: zero delete calls, the obligation left queued and `in_flight` with no
worker. Admission stayed closed and the obligation was kept, but the final shutdown
attempt (D11) never ran.

Fix: under the same lock that sets `closed`, `close()` also takes the queued deletions and
runs them as their one final attempt, together with the obligations it claims itself
(`final += _void(...)` instead of overwriting the list). A deletion already taken by a
running drain is truly in flight and is only waited for within the shutdown budget, as
before. After `closed`, no new deletion is queued (`_due_locked` and `tick` stop), so
nothing can be stranded behind close.

Regression test (`test_prepared_source_races.py`, deterministic, no sweeper thread): a
clean session, the idle TTL passes, a late hook applies the expiry and queues the claimed
deletion (asserted: queued, `in_flight`, zero delete calls), then `close()`:

| Variant | Expected |
|---|---|
| deletion succeeds | exactly one delete call, `in_flight` cleared, slot empty, `POST /` → `closed` |
| deletion fails | exactly one delete call, `in_flight` cleared, obligation kept (`cleanup_attempts` 1), `POST /` → `closed`, no exception |

Implementer checks: both variants failed before the fix (zero delete calls; close waited
the full 5 s budget for the stranded flag) and pass after it. P1a files 140 passed;
store, races and registry repeated 30 times: 0 failures; full backend suite 1349 passed,
1 warning (Starlette deprecation, pre-existing); `pip check` and `git diff --check`
clean; no control bytes or trailing whitespace; nothing staged; no server, provider,
real video or device.

### 15.4 Closure of P1a — core only (owner instruction, 2026-10-12)

**Review verdict: PASS WITH LIMITATIONS** — Codex, through the owner/PM, on the §15.3
state (backend plan `519956c`, `prepared_source.py` `6578a2b`, race tests `4c184eb`).
Reviewer results of the final round, relayed by Codex through the owner/PM: P1a target
tests 140 passed, the full backend suite 1349 passed. Earlier round (§15.2), also relayed:
the deadline finding closed (cleanup `True`/`False`/`None` reproduced), 138 target and
1347 full-suite tests passed with one deprecation warning, `pip check`, diff, whitespace,
newline and control-byte checks passed. The 30-fold repetition and the mutation runs
against restored old behaviour are **implementer evidence** (Claude), not reviewer
results.

Hash check before the commit (full blob hashes, unchanged since the reviewed round):

| Path | Blob |
|---|---|
| `backend/app/services/prepared_source.py` | reviewed `6578a2bd6ea2612db6d7fb5a0b33ebcfd501e3c1`; committed `60f6ee9c5df93e67e131910b84d503dce73998bb` (see below) |
| `backend/app/services/session_config_registry.py` | `8911852a9cf92147d1d9eb2df79fc0b1d78cf77b` |
| `backend/app/models/private_parking.py` | `afe885fcdb8beb7a3cfd33b9dded88c83d1e538e` |
| `backend/tests/test_prepared_source.py` | `1c1228d489462488b2e3a2c62f4c31fb199ffcb5` |
| `backend/tests/test_prepared_source_races.py` | `4c184eb79ec99a739b9bf27ad8c53ea971885d04` |
| `backend/tests/test_session_config_registry.py` | `95e190966921ffcca3f050c393b49c5752ecde08` |
| `backend/tests/test_inside_side_geometry.py` | `adbb29a31c0866d90f96744bca115ea3b0e2ff02` |

The reviewer-quoted hashes cover the plan, the store and the race tests; the other four
files were last changed in earlier reviewed rounds (§15, §15.1) and not since. One
change after the review, found by `git diff --cached --check`: `prepared_source.py` ended
with an extra blank line; only that final empty line was removed (verified: the reviewed
blob equals the committed content plus one newline), hence the second hash.
Implementer result after that change (Claude, not a reviewer result): full backend suite
1349 passed, 1 warning (Starlette deprecation, pre-existing).

**Scope limits of this closure:** core store, ownership, registry and geometry model
only, tested with fakes. Nothing is wired: no VideoService change (P1b), no API, runtime
or `.env.example` (P1c), no UI (P1d). Acceptance parts needing the real VideoService,
HTTP or the composed runtime (§15 coverage table, "Not yet" column) remain open. The
startup-leftover recovery action is P1c work (§0.2.2). Durations are PM initial defaults,
not calibrated. Same-size view changes are not detected. Two sweeper tests use a 0.1 s
real-time window. Documents shared with other authors (`ARCHITECTURE.md`, `PLAN.md`,
reliability) are not updated in this commit; they follow at the end of P1c (§14).
