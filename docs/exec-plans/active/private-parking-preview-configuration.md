# P1 — Private parking: file upload → preview → line/zone configuration → Play

Status: **rev 4.3 — P1a completed (core only, §15.4); P1b completed — lifecycle core
verified with fakes (§17.2); P1c completed — runtime/API integration verified with fakes
(§19.3, wire contract frozen for P1d); P1d (UI) completed in `67a04fb`, frontend verified
with API fakes (frontend plan §23). Real video (cloud and audio off): an agent local
replay through the API on `67a04fb`, and an owner UI test of Play, Stop, delete, line and
ROI only (report baseline `67a04fb`; commit of the running app not recorded), both in
`completed/history.md`. The zone-editor fix that test called for is in
`8a6b8e5` (frontend plan §24; fakes and independent review, no real-video re-test). P1 as
a whole stays open until the owner closes it; private-trigger behaviour on real footage,
accuracy and devices are not proven.**
Author: Claude
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
   *Superseded in P1b:* the mismatch travels only through the lease-bound end hook
   (`frame_size_changed`, §16.2 item 7); the method was removed (§17.1).
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

## 16. P1b delta plan against the P1a baseline (design rev 3; implemented, §17)

Author: Claude, 2026-10-12. **P1b was authorized by the owner on design rev 3
(`417efbd`) and is implemented (§17), awaiting independent review.** This section
only fixes what P1b would change relative to the committed P1a core and to the §7.5/§14
text, so the owner and Codex can review it before any authorization. Design rev 1 was
BLOCKED by Codex (through the owner/PM) on three points; rev 2 answered them in 16.2
items 4, 6 and 7 and added the reviewer's acceptance cases (16.3). Rev 2 (`5aad9e5`) was
BLOCKED on three further points (Codex through the owner/PM; the three earlier findings
closed); rev 3 answers them: the cleanup handoff after the hook (item 5, acceptance 10),
an owner for preview captures (item 11, 16.4) and the reused-id acceptance that respects
the T3a2 lifecycle guard (acceptance 2).

### 16.1 Preflight

- Baseline: HEAD `1d13095` (`docs: correct P1a closure attribution and draft
  references`), on top of P1a `b3b7f3a`; pushed, `origin/main` = HEAD; nothing staged;
  `backend/` clean. The source facts below are unchanged since `b3b7f3a` (the last
  commit touched only this plan).
- Uncommitted files of other authors, left untouched: `ARCHITECTURE.md`, `PLAN.md`,
  `docs/FRONTEND.md`, `docs/design-docs/private-parking-mode.md`,
  `docs/exec-plans/active/t3-private-parking-integration.md`,
  `docs/exec-plans/completed/history.md`, `docs/exec-plans/tech-debt-tracker.md`,
  `docs/product-specs/index.md`, the F1 plan move, the untracked
  `camera-health-monitoring.md` and `private-parking-configuration-ui.md` (Codex), and the
  root reference files (`geminiService.ts`, `package.json`, `screenshot/`).
- References from this plan to the two untracked plans are plain text ("draft lokal, belum dipublikasikan"), not
  links, until their owners publish them.

Source facts read at `b3b7f3a` (no execution):

| Fact | Where |
|---|---|
| Reservation: one `_lock` section checks `IDLE`, creates token, `session_id` (factory, default `uuid4`) and the `_CaptureSession`, sets `STARTING`; sinks start afterwards under `_lifecycle_lock`; a required refusal rolls back through the cleanup runner and raises `VideoStartRefusedError` | `video_service.py:695-732`, `:376-397`, `:326` |
| Sink end fan-out under `_lifecycle_lock`, same lease on every attempt; a failing sink stays pending | `video_service.py:399-414` |
| One cleanup runner per attempt; `cleanup_ok` from facts (`None` while a capture release is deferred); a capture-only pass by the exiting capture thread or the opener finishes a deferred release; each explicit `stop()` resumes a failed cleanup once | `video_service.py:432-496`, `:582-592`, `:628-641` |
| `abort_start(token)` rolls back only a current `STARTING` reservation | `video_service.py:823-844` |
| File start: `start_file` attaches `upload_path` (deleted by cleanup), opens `cv2.VideoCapture(str(path))`, single-owner capture | `video_service.py:899-945`, `:524-538` |
| File loop: `cap.read()` then `imencode` then `_publish` (packet slot, then buffer/detector/scene offers) | `video_service.py:1088-1110`, `:1027-1042`, `:779-808` |
| `get_status()` returns `{state, source, error}` in one `_lock` section | `video_service.py:988-994` |
| Trigger: one config fixed at construction; `start_session` resets per session and never raises | `private_parking_trigger.py:139-185` |
| Coordinator: `start_session` calls the trigger through `_call`, which logs and swallows failures | `private_parking_coordinator.py:123-131`, `:186` |
| Detector: a component start failure raises `DetectorStartError`, a required sink refusal for VideoService | `detector_service.py:192-245` |
| Runtime: private mode builds `PrivateParkingTrigger(config)` from the startup file or the disabled path | `runtime.py:329-364` (P1c) |

### 16.2 Contract deltas for P1b (relative to §7.5 and the P1a code)

1. **Ticket consume.** Inside the existing reservation `_lock` section of a new
   `_atomic_reserve(ticket=...)` path: if not `IDLE`, raise `PlaybackRefused("video_busy")`
   **without** consuming (the store cancels the open ticket, outcome `REFUSED`, §0.2.1);
   otherwise create token, `session_id` and a new `session_ref` (`uuid4`) and call
   `ticket.try_consume(token, session_ref, session_id)` **before** any state change; on
   `False` (cancelled or past its deadline) return `False` with no reservation. Consume
   and the creation of the `_CaptureSession` happen in the same `_lock` section, so every
   later call for that lease finds the session. Missing registry or configuration →
   `PlaybackRefused("configuration_unavailable")` before the consume; never a fallback to
   the startup file.
2. **Return/raise contract of `start_prepared_file`.** Returns `True` only after `RUNNING`
   was published for this session. `False` only for a stale ticket (nothing reserved).
   After a consume, every way the start does not reach `RUNNING` raises, so the store never
   keeps a `PENDING` outcome: required refusal → `PlaybackRefused("required_component_refused")`;
   a Stop or `cancel_session` that won → `PlaybackRefused("stopped")` (the store keeps an
   outcome a cancel already wrote, write-once); open, probe or thread-start failure →
   `PreparedStartFailed` (store outcome `FAILED`, reason `start_failed`). In every case the
   session's cleanup runs and sends its hook (item 5); the file stays in use until a clean
   hook.
3. **Binding (R3).** Inside `_start_sinks`, under `_lifecycle_lock`, after the per-call alive
   check and before the first sink: `registry.bind(session_id, token, config)`; a
   `BindingConflict` is a required refusal (no sink starts, rollback, hook `refused`). In
   `_end_sinks`, after the sink loop: `registry.unbind(session_id, token)` (lease-bound,
   idempotent on Stop retries). The registry is a new optional constructor argument of
   VideoService; legacy paths never bind. A session invalidated before the alive check
   binds nothing and starts no sink.
4. **Capture ownership (blocker 1).** The decoder never owns a capture:
   `video_decoder.create_capture(path)` only constructs `cv2.VideoCapture(str(path))` and
   returns it; `video_decoder.probe(capture) -> fps` does `isOpened()`/`get()`. The
   prepared start does, in order: `_begin_open_locked(session)` (returns without creating
   anything if a Stop/cancel already released the session); `cap = create_capture(path)`;
   **immediately** `_adopt_capture(session, cap)` under `_lock` (before `isOpened()`/`get()`);
   then `probe(cap)`, `_attach`, `_start_capture`. Consequences:
   - constructor raises → no capture object exists, nothing to release; session ends
     `error`, cleanup runs, hook sent;
   - `isOpened()` false or `get()` raises → the adopted capture is released by this
     session's cleanup; a failing `release()` leaves `cap_released` false, so `cleanup_ok`
     is `False`, the hook reports it, the store keeps the file use (`ENDING`), and each
     explicit Stop retries the release (existing resumable cleanup) until a clean hook;
   - a Stop/cancel during the open: `opening` defers the release to the opener, which
     hands it to exactly one owner in `_end_open` (existing single-owner handoff); the
     opener never attaches a thread to a released session.
   No store-owned file is ever deleted by VideoService (no `upload_path`).
5. **Hook = a cleanup step, lease-bound (part of blocker 3).** For a session that consumed
   a ticket, every cleanup attempt (full pass, capture-only pass by the capture thread or
   the opener, Stop retry, refusal rollback) ends like this: compute the facts under
   `_lock` **while `cleanup_running` is still true** (state not `IDLE`, so no new
   reservation can start); release the lock; call
   `on_finished(ticket_id, token, end_reason, cleanup_ok)` outside every VideoService lock;
   then, in **one** `_lock` section, record the delivery and **re-check the handoff before
   giving up ownership**: if a release is still deferred and the capture is no longer in
   use (the capture thread exited or the opener finished while the hook ran — their own
   exit path saw `cleanup_running` true and correctly did not take over), the runner keeps
   ownership, clears `deferred`, runs the capture-only pass and sends one more hook with
   the new facts (the final one); only when nothing is left to take over does it clear
   `cleanup_running`, set `cleanup_ok` and finalize. If the capture is still in use at that
   point, ownership is released in the same section and the later exit path takes it over
   (existing handoff), which ends with its own hook. Either way exactly one owner finishes
   the release and a final hook follows it; the loop runs at most one extra pass, because
   a capture-only pass after the exit leaves nothing deferred. If the hook raises, the delivery counts as a failed cleanup
   step: `hook_pending = True`, `cleanup_ok = False`, state `ERROR` ("cleanup failed; Stop
   retries it"); the next explicit Stop resumes the cleanup (all steps idempotent) and sends
   the same lease-bound hook again. The store side keeps its ownership meanwhile (the ticket
   still holds the file; Play/DELETE/admission refused), so a lost hook fails closed.
   `end_reason`: `refused`, `cancelled`, `stop`, `eof`, `frame_size_changed`, else `error`;
   it is fixed on the session under `_lock` when the end is decided and repeated on every
   later hook of that session. The store's `on_finished` never calls VideoService
   (P1a §15.2) and does not raise for any input (unknown ticket, wrong lease or bad type →
   counted stale).
6. **`cancel_session(lease)` — one atomic point (blocker 2).** In **one** `_lock` section:
   the current session must have `token == lease` and not be released (else `False`,
   counted; a reused `session_id` never matches because the check is by token); then
   `cancel_requested = True`, `stop_requested = True`, `end_reason = "cancelled"` (unless an
   end was already decided), `_start_token = None`, `_invalidate_locked(session)`
   (`released = True`, `stop_event` set), state `STOPPING`. This is the linearization point:
   it does not look at `STARTING` vs `RUNNING` and does not call `abort_start` separately,
   so no transition can slip between a state check and the action. Outside the lock the
   caller is the cleanup runner (`_run_cleanup(full=True)`, then finalize). Every later step
   of that reservation already re-checks `released` under `_lock` and stops: `_start_sinks`
   (per-sink alive check), `_attach` (no thread), `_start_capture` (never publishes
   `RUNNING`), `_publish` (own `stop_event`). P1b tightens the offer boundary: the
   `_packet_is_active` check becomes `self._current is session and not session.released
   and state RUNNING` (session object, not `session_id`), so a frame of a cancelled session
   can never be offered to a newer session that reuses its id. If the session was already
   released by another invalidator (EOF, error, Stop), `cancel_session` records nothing new
   and returns `False`; that runner's hook reports the end.
7. **Geometry failure delivery (blocker 3).** On a size mismatch the capture thread, under
   `_lock` in `_finish_from_capture`, sets `end_reason = "frame_size_changed"` **before**
   anything else and ends the session (`ERROR "frame size changed"`); the mismatching frame
   and every later one reach no packet slot, buffer, tee, detector or classifier. The
   authoritative delivery to the store is the lease-bound hook of item 5, which carries
   `end_reason = "frame_size_changed"` on every attempt, including Stop retries and a
   redelivery after a failed hook. The store, in `on_finished` and before applying
   `cleanup_ok`, marks the generation `geometry_unstable` for that `end_reason`; the flag is
   sticky for the generation: a later clean hook releases the file use but `ready` stays
   false and Play answers `409 source_geometry_unstable` (only a new upload has a new
   generation). If no hook is delivered at all, the ticket still holds the file, so the
   source is not playable either. The separate `on_geometry_unstable` callback of rev 1 is
   **dropped**: no correctness depends on a best-effort callback. The approved scope
   extension on `prepared_source.py` and the P1a test fake is used instead for this store
   change (`end_reason` handling in `on_finished`), not for a new protocol keyword.
8. **Status identity (D6).** `_CaptureSession` gets `session_ref`; a new
   `get_session_identity() -> (state, session_ref, session_id)` reads all three in one
   `_lock` section. The identity stays visible while the current session's cleanup is not
   successful (`STOPPING`/`ERROR`) and is `None` only once the state is `IDLE`.
   `get_status()` and `models/video.py` stay unchanged in P1b; the wire fields are P1c.
9. **Trigger per session.** `PrivateParkingTrigger(config=None, *, session_config=None,
   ...)`: exactly one of the two. With `session_config`, `start_session(session_id)` reads
   the bound `OperatorConfiguration` and uses its `config` (line with `inside`, zones,
   thresholds) for that session only; none bound → raises a new
   `PrivateParkingAdmissionError` (sanitized, no ids); never falls back to a startup
   configuration. `end_session` and `close` unchanged. The startup-file constructor stays
   for the current composition until P1c.
10. **Coordinator re-raise.** `start_session` re-raises a trigger start failure as
    `SessionAdmissionError` (sanitized; held alerts of older sessions still cleared), so
    the required detector refuses and VideoService rolls back — never a session without
    configuration. `end_session` behaviour unchanged.
11. **Decoder.** New `video_decoder.py` with functions that never own a capture:
    `create_capture(path)` and `probe(capture)` (item 4) and
    `read_first_frame(capture)`, which only reads from a capture its caller already gave an
    owner (no release inside). No resize, crop or rotation of its own. The preview owner
    and its release retries are a store contract (16.4) used by the P1c preview route;
    rev 2's `read_first_frame(path)` with a logged `try/finally` release is withdrawn,
    because a failed release left the capture without an owner and a file deletion is no
    substitute for a capture release.

### 16.3 Files, tests and acceptance

| Files | Tests |
|---|---|
| `video_service.py`, new `video_decoder.py`, `private_parking_trigger.py`, `private_parking_coordinator.py`; approved extension: `prepared_source.py` (`frame_size_changed` handling in `on_finished`; hook input hardening) | new `test_video_prepared_playback.py` (real VideoService, fake capture via the `cv2` monkeypatch of `conftest.py`, a fake decoder with gates, real `HandoffTicket` and `PreparedSourceStore` with fake files and clock), new `test_private_session_config.py` (trigger per session, coordinator re-raise, registry under `_lifecycle_lock` with a forced reused `session_id`); adapted: `test_video_session_lifecycle.py`, `test_video_required_sinks.py`, `test_video_service.py`, `test_private_parking_trigger.py`, `test_private_parking_coordinator.py` (swallowed start → re-raised), `test_private_parking_contracts_integration.py`, `test_private_parking_evidence_sampler.py` (config type); approved extension: `test_prepared_source.py` (store geometry and hook-input cases) |

Acceptance (gates are `threading.Event`s with timeouts; no sleeps):

1. **Capture ownership (blocker 1).** (a) constructor raises → no capture, hook `error`,
   store `FAILED`; (b) `isOpened()` false and (c) `get()` raises → capture released once
   by the session's cleanup, hook `True`; (d) (b)/(c) with `release()` failing → hook
   `False`, store `ENDING` with the file use, a Stop retry releases and sends hook `True`
   → `PREPARED`; (e) Stop gated during `create_capture`, during `probe` and between
   `_attach` and `_start_capture` → exactly one release by exactly one owner, no thread
   started, never `RUNNING`, hook `stop`, `start_prepared_file` raises
   `PlaybackRefused("stopped")`.
2. **Cancel atomicity (blocker 2).** `cancel_session(lease)` gated at: after consume before
   bind/sink start (nothing bound, no sink started, registry empty); during a sink start;
   between `_attach` and `_start_capture` (thread never started); right after `RUNNING` was
   published (capture thread stops, cleanup once). In every case: one cleanup runner, no
   `RUNNING` after the cancel, hook `cancelled`. **Reused `session_id`, lifecycle guard
   first (T3a2):** A cancelled while its capture thread is gated holding a decoded frame
   (join times out, release deferred, state not `IDLE`). Two refusal levels, aligned with
   the owner (2026-10-12): (i) **in the one-store composition** the source is `ENDING`
   (A's ticket holds the file), so the store refuses Play **before the CAS** with
   `409 session_cleanup_pending`; no attempt is used and no ticket reaches VideoService —
   an `ENDING` source never reaches a new reservation; (ii) VideoService's own guard is
   tested directly (a ticket handed to `start_prepared_file` while A is still not `IDLE`):
   `PlaybackRefused("video_busy")` without consuming, so in a store this is an accepted
   CAS whose attempt is used with outcome `REFUSED` (§0.2.1). Then:
   releasing A's frame publishes no packet and offers nothing (A's own `stop_event`); A's
   thread exits, its capture-only pass releases once and sends the final hook `True`,
   state `IDLE`. Only then is B reserved with the same id and runs; afterwards a late hook
   of A, `cancel_session(lease A)` and a stale unbind of A are counted no-ops and B's
   binding, attempt and slot state are unchanged. The tightened offer guard (item 6) is
   additionally checked as a unit with a stale session object while B is current, as
   defence in depth; no test reserves B before A's cleanup succeeded.
3. **Geometry failure (blocker 3).** Size mismatch with (a) a store hook that raises on the
   first delivery → VideoService `ERROR`, store still holds the file (not ready), a Stop
   retry redelivers `frame_size_changed` → store `geometry_unstable`, file use released,
   `ready` false, Play `409 source_geometry_unstable`; (b) hook `cleanup_ok False` then a
   clean hook → flag kept, never ready; (c) no hook delivered → Play refused (ticket holds).
   The mismatching frame reaches no consumer (spies).
4. **Registry/configuration missing.** No registry or no configuration →
   `configuration_unavailable` before the consume (ticket open, store `REFUSED`, nothing
   reserved); a trigger start without a binding → `PrivateParkingAdmissionError` →
   coordinator re-raise → required refusal → rollback, hook `refused`; never the startup
   configuration as a fallback (spy).
5. **Rollback after consume.** Open failure and `thread.start()` failure after the consume
   (fake `thread_factory`) → session `ERROR`, cleanup releases what was adopted, hook with
   the facts, `PreparedStartFailed` → store `FAILED`; with a failing release → `ENDING`
   until a Stop retry.
6. **Snapshots of the cleanup result.** At each gate of 1-5: `get_session_identity()` shows
   the reservation's `session_ref`/`session_id` until `IDLE` and `None`s after it; the
   store's `/current` snapshot shows `ENDING` with `cleanup_ok` `False`/`None` until a
   clean hook, then `PREPARED`; never a mix of A's ref with B's id.
7. **Hook failed or late.** Store hook raising (fake) → item 5 path and Stop redelivery;
   hook after the `PLAY_PENDING` deadline with the real VideoService → `TIMED_OUT` kept
   (P1a §15.2); hook after store `close()` → recorded, no deletion, no exception in the
   cleanup runner; a hook with a stale lease → counted, no effect.
8. **Earlier parts.** §9.4 with the real VideoService; §9.5 real cleanup failures (join
   timeout, sink end failure, deferred release → hooks `None` then `True`); §9.6 forced
   reused `session_id` (late bind/unbind/hook of A never changes B); §9.7 frame part; §9.13
   status identity and the default factory (two reservations → distinct `uuid4` ids).
9. **Legacy and retail regressions.** `start_webcam`, `claim_start`/`start_file`,
   `abort_start` and `stop()` behave as before (existing suites green; the only intended
   assertion change is the coordinator start re-raise); a legacy session never binds, never
   calls a hook and never consults the registry; retail mode and the legacy parking
   trigger unchanged. The tightened offer boundary (item 6) is checked against the
   existing T3a2 reused-id tests.
10. **Cleanup handoff while the hook runs (rev 3).** Capture thread gated in a read so the
    join times out and the release is deferred; the runner's hook (`cleanup_ok None`) is
    gated in the store; the capture thread is released and exits while the hook is held
    (its exit path sees `cleanup_running` and does not run); the hook is released → the
    runner re-checks, runs the capture-only pass, releases the capture **once** and sends
    the final hook `True`; hooks seen by the store: `None` then `True`; the store ends in
    `PREPARED` with the file use released; no second release, no orphaned deferred
    release, `_on_capture_exit` ran no cleanup. Mirror case: the capture still in use when
    the gated hook returns → ownership released in the same section, the later exit takes
    over and sends the final hook. Repeat with the opener (`opening`) instead of the capture
    thread.

Verification: the new and adapted files, then `test_video_*`, `test_private_parking_*`,
`test_detector_*`, the full backend suite serial with a unique basetemp, `pip check`,
`git diff --check`, control bytes; no server, real video, webcam, provider, TTS or
speaker.

### 16.4 Preview capture ownership (contract for the P1c preview; rev 3)

The preview route (P1c) decodes the first frame outside VideoService. Its capture gets an
owner the moment it exists and keeps it until a release is proven:

- `begin_read` returns a `ReadHandle` (file user). The caller creates the capture with
  `video_decoder.create_capture(path)` and immediately calls
  `store.adopt_capture(handle, capture)` (under the store lock; no call on the capture
  before it), then `probe`/`read_first_frame` outside the lock.
- `store.end_read(handle)` releases the adopted capture outside the lock, single-flight
  per handle; a probe or read failure after adoption also ends through `end_read`.
  Success → the reader's file use ends. A failed release → the handle **stays a file user** with
  a capture obligation: same bounded policy as file cleanup (retry every 60 s, single
  flight, `CLEANUP_STALLED` after 10 attempts, explicit retry through the P1c recovery
  action of §0.2.2), counted in `/current.cleanup`. The capture object stays referenced
  only by the store.
- While a capture obligation exists the file use is not released, so the file is never
  deleted, a voided source stays in `DELETING`/`CLEANUP_PENDING`, and admission (`POST /`)
  stays closed; a live source stays readable but cannot be deleted until the capture is
  released. A file deletion that happens to succeed on the filesystem is never taken as
  proof that the capture was released.
- `close()` runs at most one final release per capture obligation (D11 rules), then the
  file attempt only if no user remains; a remaining obligation is logged and the process
  exit ends it (D12 handles the file at the next start).
- Acceptance (P1c, with the store part testable earlier): decode/probe failure together
  with a release failing once and repeatedly → the handle keeps the file use, retries are
  spaced and single-flight, admission stays closed and the file is not deleted until a
  release succeeded; a successful retry releases the file use and lets a pending
  deletion run; concurrent `end_read`, sweeper and `close()` → one release in flight;
  stalled capture obligation → explicit retry runs one attempt.
- **Scope:** this adds `adopt_capture`, capture obligations and their tests to
  `prepared_source.py` / `test_prepared_source*.py`, beyond the approved P1b extension
  (which covers `frame_size_changed` in `on_finished`). Proposed placement: P1c, together
  with the preview route; it needs the owner's approval of that extension. P1b itself
  does not create preview captures.

### 16.5 Risks and open points for the review

- **H2 (highest):** reviewed T1/T3 contracts change (reservation path, cleanup runner hook
  step, offer boundary, coordinator start). Mitigation: the hook step and binding exist
  only for a ticketed reservation; legacy suites unchanged; the offer-boundary change is a
  strict tightening.
- The hook runs while `cleanup_running` is true, so a slow store hook delays the end of
  that cleanup (never a VideoService lock is held). The store hook is in-memory only.
- A hook that keeps failing keeps VideoService in `ERROR` until a Stop succeeds in
  delivering it; the store stays fail-closed meanwhile.
- A cleanup that never completes keeps `cleanup_ok` `None`: the store keeps the file
  (recorded limitation, §8.4).
- Open: whether `get_status()` should already return the identity in P1b (proposal: no,
  P1c); approval of the 16.4 store extension for P1c.

## 17. P1b implementation record (Claude, 2026-10-12) — completed, §17.2

Authorization: owner, on design rev 3 (blob `417efbd`), with acceptance 2 aligned before
the implementation (two refusal levels, §16.3 item 2). Preflight: HEAD `1d13095` =
`origin/main`, nothing staged, `backend/` clean; the uncommitted documents of other
authors and the root reference files untouched. Scope: §16.2 items 1-11 except the
preview capture owner (§16.4, P1c), plus the approved P1a extension for the geometry
end reason. Nothing wired into the runtime or API (P1c).

**Files:**

| File | Change |
|---|---|
| `backend/app/services/video_service.py` | `start_prepared_file` (ticket consumed in the reservation `_lock` section, `session_ref`, refusals, `PreparedStartFailed`); bind/unbind in the sink fan-outs under `_lifecycle_lock`; decoder capture adopted before any call; end hook as a cleanup step with the handoff re-check after it and redelivery through Stop; `cancel_session` (one atomic section); per-frame size check before publication (`frame_size_changed`); offer guard by session object; `get_session_identity`; end reason fixed once per session |
| new `backend/app/services/video_decoder.py` | `create_capture`, `probe`, `read_first_frame`; never owns or releases a capture |
| `backend/app/services/private_parking_trigger.py` | `session_config` lookup per session; `PrivateParkingAdmissionError` without a binding; no fallback |
| `backend/app/services/private_parking_coordinator.py` | a trigger start failure is re-raised as `SessionAdmissionError` |
| `backend/app/services/prepared_source.py` (approved extension) | `on_finished`: `frame_size_changed` marks the generation `geometry_unstable` before the cleanup result (sticky); hook input hardening (never raises) |
| new tests | `test_video_prepared_playback.py` (32), `test_private_session_config.py` (9) |
| `backend/tests/test_prepared_source.py` (approved extension) | 11 store cases: sticky geometry with `True`/`False`/`None` first hooks, stale lease, invalid hook identity and values |

The adapted test files listed in §16.3 needed **no** change: the legacy, T3a2, T3c0.2,
trigger, coordinator, evidence-sampler and contract suites pass unchanged (the existing
coordinator tests do not assert the old swallowed start; the re-raise is covered in
`test_private_session_config.py`).

**Acceptance coverage (§16.3):**

| # | Covered by | Not covered |
|---|---|---|
| 1 capture ownership | constructor raises, not opened, `get()` raises; release failing then Stop retry; Stop gated during `create_capture` and during `probe` (one release, never `RUNNING`, `PlaybackRefused("stopped")`, hooks `None` then `True`) | a gate *between* `_attach` and `_start_capture`: no seam without a test-only hook (`_start_capture` re-checks `released` in the same `_lock` section as `thread.start()`) |
| 2 cancel atomicity, reused id | cancel with `_lifecycle_lock` held before bind/sink start; during a sink start; after `RUNNING`; another lease; reused id: (ii) VideoService `video_busy` without consuming, A's held frame not published, B only after A's clean hook, late `cancel_session(lease A)` no-op, offer guard with a stale session object; (i) composed: `ENDING` source → `session_cleanup_pending` before the CAS, `next_attempt` unchanged | cancel between `_attach` and `_start_capture` (same seam limit) |
| 3 geometry | mismatch ends before publication (2 offers, none of the mismatching frame); composed with a failed first hook delivery → VideoService `ERROR`, store still holds (not ready, Play refused), Stop redelivers → `geometry_unstable`, `PREPARED`, Play `409 source_geometry_unstable`; store sticky flag for `True`/`False`/`None` | — |
| 4 registry/config missing | no registry, no config, bad size → `configuration_unavailable`, ticket open; required refusal (detector start fails) → hook `refused`, bound then unbound; bind conflict → refusal, older binding kept; trigger without binding → `PrivateParkingAdmissionError`, coordinator re-raise, no fallback | the full chain through the real `DetectorService` (covered by its own required-sink contract, not re-composed here) |
| 5 rollback after consume | thread-start failure with and without a release failure (Stop retry) | — |
| 6 snapshots | `get_session_identity` while `RUNNING`, while the deferred cleanup keeps `ERROR` (ids kept) and after `IDLE` (`None`s); store snapshots in the composed cases | not asserted at every gate of 1-5 |
| 7 hook failed or late | failed delivery + Stop redelivery; hook held while the store closes (recorded after close, no deletion) | — |
| 8 earlier parts | composed EOF → `PREPARED` with the file kept; composed `PLAY_PENDING` deadline → `cancel_session` → `TIMED_OUT`, hook `cancelled`, `PREPARED`; composed busy → `REFUSED` with the attempt used, replay identical; default factory → distinct `uuid4` ids | composed `DELETE` with a consumed ticket, close in each handoff gate, sink end failure with the real fan-out |
| 9 legacy/retail | full backend suite unchanged (legacy start paths, T3a2/T3c0.2, retail, legacy parking trigger) | — |
| 10 handoff during the hook | capture exits while the hook is held → the runner releases once and sends the final hook; mirror (still in use → exit path takes over); opener variant | — |

**Implementer evidence (Claude, not reviewer results):**

- `test_video_prepared_playback.py` 32 passed; `test_private_session_config.py` 9 passed;
  `test_prepared_source.py` 53 passed.
- `test_video_prepared_playback.py`, `test_private_session_config.py`,
  `test_prepared_source.py` and `test_prepared_source_races.py` repeated 30 times: 0
  failures (run before the default-factory test was added).
- Mutation runs (scratch pytest plugin outside the repository, no source change): handoff
  re-check removed → 2 failures; per-frame size check disabled → 2; offer guard back to
  `session_id` → 1; hook failure ignored → 1; geometry end reason not sticky in the store
  → 4.
- The full backend suite serial with a unique basetemp: 1401 passed, 1 warning
  (Starlette deprecation, pre-existing). `pip check` clean; `git diff --check` clean;
  no control bytes or trailing whitespace; final LF.
- No server, provider, real video, webcam, TTS or speaker; nothing staged.

**Integration limits:**

- Nothing is wired: the runtime still builds the startup-file trigger and no store,
  registry or decoder (P1c); `get_status()`/`models/video.py` unchanged (P1c).
- After a required refusal, an open or thread-start failure or a size mismatch,
  VideoService stays `ERROR` until an explicit Stop (existing T3c0.2 contract). In the
  composed store the next Play depends on the store's checks **before** the CAS: after a
  required refusal or an open/thread-start failure they pass, so the Play is an accepted
  CAS answered `REFUSED video_busy` until the operator stops; after a size mismatch the
  generation is `geometry_unstable` (or still `ENDING` until the clean hook), so the Play
  is refused before the CAS (`409 source_geometry_unstable` / `session_cleanup_pending`,
  `next_attempt` unchanged) and stays refused after Stop: the remedy is delete, a new
  upload and a newly confirmed configuration. P1c must surface the Stop requirement.
  (Corrected 2026-10-12 after the Codex review of the P1c design rev 2.)
- A store hook that keeps failing keeps VideoService `ERROR` until a Stop delivers it;
  the store stays fail-closed meanwhile.
- The preview capture owner (§16.4) is P1c and not implemented; `read_first_frame` exists
  but is unused.
- The store's `report_geometry_unstable` (P1a) was removed in §17.1; the geometry failure
  travels only through the lease-bound end hook.
- Legacy `start_file` keeps its own `cv2` opener; only prepared files use the decoder.

### 17.1 Fixes for the Codex review of P1b (owner instruction, 2026-10-12)

| Finding | Fix | Tests |
|---|---|---|
| 1 Partial bind: the rollback obligation was recorded only after `registry.bind()` returned, so a bind that changed the registry and then raised was never undone | `binding_pending` is set **before** `bind()`; any bind exception is a required refusal and the cleanup unbinds with the session's token (lease-checked: another reservation's entry is never removed; `False` = nothing of ours). An unbind that raises stays pending, `cleanup_ok` is `False` until it succeeds (hook `False`, VideoService `ERROR`), and an explicit Stop retries it | a bind that stored then raised → entry removed, hook `refused`/`True`; unbind failing once (composed with the store) → hook `False`, registry entry kept, VideoService `ERROR`, store `ENDING` with the file use, Play `session_cleanup_pending` with `next_attempt` unchanged, `POST /` `slot_busy`; Stop → unbind retried, hook `True`, `PREPARED`, `IDLE`; a stale rollback (bind conflict with another lease's entry) → unbind with its own lease is ignored, the other binding kept |
| 2 Hook input: `cleanup_ok not in (True, False, None)` ran the object's `__eq__`, and invalid values were coerced and applied | `on_finished` checks by exact type and identity only (`type(...) is int/str`, `cleanup_ok is True/False/None`) before any comparison, truthiness or lookup; anything else is rejected and counted (`invalid_hooks`) without an exception and without changing state; never a successful cleanup | 13 parametrized inputs (ticket id `str`/`bool`; lease `None`/`bytes`/a `str` subclass whose `__eq__` raises; end reason `None`/an object raising on every comparison, truthiness and hash; `cleanup_ok` `1`, `"yes"`, `[True]`, a numpy array, `numpy.bool_`, a hostile object): no exception, snapshot, attempt record, file users and hook summary unchanged, then a valid hook still applies |
| 3 `report_geometry_unstable` unused in production | removed from `prepared_source.py`; its two test uses migrated to the end hook (`frame_size_changed` via the session's hook; the stale-lease case via `on_finished` with another lease) | sticky geometry and stale-lease coverage kept (`test_geometry_unstable_generation_cannot_be_reopened`, the P1b sticky and stale-lease cases) |

**Test race found and fixed (implementer):** a repeat of the four files failed once in
30 runs. Cause: the test `Hooks` recorder appends a call at the start of the hook, before
the hook raises or forwards to the store and before VideoService writes the final state,
so three tests that waited for "a hook was recorded" and then asserted `ERROR` (or the
store state) could observe `STOPPING`. The product behaviour was correct; the tests now
wait for the final state itself. The two affected tests of the previous round
(`test_a_size_mismatch_ends_before_publication`,
`test_composed_geometry_failure_with_a_failed_hook_delivery`) had the same latent race,
so the earlier "30 repetitions, 0 failures" (§17) did not exclude it.

**Implementer evidence after the fixes (Claude, not reviewer results):**

- `test_video_prepared_playback.py` 35, `test_prepared_source.py` 59,
  `test_private_session_config.py` 9 passed.
- The four files repeated 60 times after the race fix: 0 failures.
- Mutation runs against the pre-fix behaviour (scratch pytest plugin outside the
  repository): unbind obligation recorded after `bind()` → 2 failures (partial bind,
  stale rollback); the old hook validation → 9 failures (the hostile and non-exact
  inputs). The unbind-retry test passes in both versions (that path already existed).
- Full backend suite serial with a unique basetemp: 1410 passed, 1 warning (Starlette
  deprecation, pre-existing); `pip check` clean; `git diff --check` clean; no control
  bytes or trailing whitespace; final LF. Nothing staged; no server, provider, real video
  or device.

### 17.2 Closure of P1b — lifecycle core verified with fakes (owner instruction, 2026-10-12)

**Independent review (Codex, through the owner/PM) on the §17.1 state:** 131 target tests
passed, the full backend suite 1410 passed with one deprecation warning (Starlette),
and the remaining checks (`pip check`, diff, whitespace, newline, control bytes) clean.
The 60-fold repetition and the mutation runs of §17 and §17.1 are **implementer
evidence** (Claude), not reviewer results.

Hash check before the commit: the eight source and test files are unchanged since the
reviewed round, so the suite was not re-run for the closure.

| Path | Blob |
|---|---|
| `backend/app/services/video_service.py` | `7537ae8ddf111d9a52b5a0d1cec11861def7970c` |
| `backend/app/services/video_decoder.py` | `b72590541fe90ee983f8ab7dc42107ca976d3067` |
| `backend/app/services/private_parking_trigger.py` | `39c55e8903b8c4d80a7f6832affca3d1ed46065b` |
| `backend/app/services/private_parking_coordinator.py` | `b53002c11434cf436b433724f6382f71278529c5` |
| `backend/app/services/prepared_source.py` | `991b14e6089ea6a8c1bceca59397063cd4fb47eb` |
| `backend/tests/test_prepared_source.py` | `ab4d6b917b5cdd0ff8158d149cf3b821c319b108` |
| `backend/tests/test_video_prepared_playback.py` | `5a51bf2aebbca7131dcfd066679ee486efdaccee` |
| `backend/tests/test_private_session_config.py` | `97bfe3ae83689a3f9b397e5395c6642d7af128b6` |

This plan was reviewed at `45abc29`; only this closure record and the status lines were
added after it.

**Status:** completed — lifecycle core verified with fakes. Not proven: runtime and API
wiring (P1c), the preview capture owner (§16.4, P1c), the leftover recovery action
(§0.2.2, P1c), the status wire fields (P1c), UI (P1d), real video files, real devices.
The integration limits of §17 stay open, in particular `ERROR` until an explicit Stop
after a refusal, open failure or size mismatch (a size mismatch additionally leaves the
generation unplayable, §17).

## 18. P1c delta plan against the P1b baseline (design rev 3; implemented, §19)

Author: Claude, 2026-10-12. **P1c was authorized by the owner on design rev 3
(`93eb76e`) and is implemented (§19), awaiting independent review.** This section fixes
what P1c would change relative to the committed P1a/P1b code and to §5, §7 and §14, so
the owner and Codex can review it first. Rev 2 records the PM decisions of 2026-10-12
(18.1a) and aligns states, HTTP contract and acceptance with them. Rev 3 (Codex review of
rev 2 `4a5a99d`, BLOCKED on one point) aligns the size-mismatch acceptance with the sticky
geometry contract (item 9, acceptance 8, §17) and closes the window between capture
adoption and busy marking (item 3, acceptance 11).

### 18.1 Preflight

- Baseline: HEAD `3c5446a` (`feat: add prepared video lifecycle and session
  configuration`) = `origin/main`, on top of R1 `c7d4ddc`; nothing staged; `backend/`
  clean.
- Uncommitted files of other authors, untouched: `ARCHITECTURE.md`, `PLAN.md` (their
  rows; the R1 row is committed), `docs/FRONTEND.md`, `docs/design-docs/private-parking-mode.md`,
  `docs/exec-plans/active/t3-private-parking-integration.md`,
  `docs/exec-plans/completed/history.md`, `docs/exec-plans/tech-debt-tracker.md`,
  `docs/product-specs/index.md`, the F1 move, the untracked camera-health and UI plans,
  the original R1 copy, and the root reference files.
- `backend/.env.example` read under the owner's authorization (decision 7). Its private
  mode text still says that a missing or invalid configuration file disables parking
  candidates; P1c changes that wording (the file becomes an optional template). The real
  environment files (`.env`, `.env.smoke.local`) are neither read nor changed.

Source facts read at `3c5446a` (no execution):

| Fact | Where |
|---|---|
| Legacy routes: `POST /webcam/start`, `POST /upload` (own 200 MiB stream to `UPLOAD_DIR`, own `cv2` probe released in `finally`, `claim_start`/`start_file`), `POST /stop` (`stop()` then status), `GET /status`, `GET /stream` | `app/api/video.py:58-260` |
| Status wire model `{state, source, error}` | `app/models/video.py:55-58` |
| Runtime: `build_parking_wiring` builds the startup-file `PrivateParkingTrigger(config)` or the disabled path; `VideoService` built without registry or decoder; module-level singletons | `app/services/runtime.py:329-361`, `:415-421` |
| Shutdown: `video.stop()` first, then audio, worker, clients, classifier, detector, coordinator, trigger, tracker, tee, buffer, sampler, budget | `app/services/runtime.py:424-454` |
| Lifespan: creates `UPLOAD_DIR`, starts the worker and audio gate; routers under `/api` | `app/main.py:82-101`, `:123-127` |
| P1a store `close()` calls `video.stop()` itself (standalone shutdown, D11) | `app/services/prepared_source.py` (`close`) |

### 18.1a PM decisions (owner, 2026-10-12)

1. **Separate cleanup conditions.** A capture may be released once its constructor has
   returned and no probe or decode is running on it. The file is deleted only after every
   file user has finished. A capture's own handle never blocks the release of that capture.
2. **One capture per store.** At most one preview/probe capture exists per store, counting
   one being constructed or waiting for cleanup. While a capture cleanup is pending, no
   replacement capture is created and no new upload, preview, configuration or Play is
   accepted.
3. **Failed release.** A successful probe is not enough for `PREPARED`: if the probe's
   capture release fails, the answer is `503 cleanup_pending` with the source identity and
   generation, ownership is kept, and after the cleanup succeeds the failed operation's
   file is deleted and the operator uploads again.
4. **HTTP contract.** `frame_size_changed` is part of the contract; refusals before the CAS
   are distinguished from `200 REFUSED video_busy` after it; `analysis_stop_required` is
   transient information tied to a session identity, never a readiness input.
5. **Configuration size.** 4096 bytes apply to the whole JSON request envelope of the
   configuration request; the too-large answer and the frontend handling are aligned.
6. **Shutdown.** Exactly one owner calls Stop; `store.close()` and `shutdown_services()`
   must not each add a Stop attempt. A timeout keeps the obligations.
7. **Environment scope.** Claude may read and edit `backend/.env.example` for
   documentation and placeholders, never a real `.env`.

### 18.2 Contract deltas for P1c

1. **Runtime composition (private mode).** One `SessionConfigRegistry`; the trigger is
   always `PrivateParkingTrigger(session_config=registry.get)` in private mode; the startup
   file is only a template (§7.1): invalid or missing → `template_available: false` and a
   generic warning, never the disabled path. `VideoService(..., session_config_registry=registry)`.
   The store uses a **dedicated directory** (`app/storage/prepared`), never `UPLOAD_DIR`;
   it is created in the lifespan startup (its constructor sweeps the directory) and
   reached by the router through a runtime accessor that tests can replace with fakes.
   Legacy and disabled modes have no store. `DisabledParkingTrigger` only for an invalid
   mode value.
2. **Shutdown with one Stop owner (decision 6).** The store gets a two-phase shutdown used
   by the runtime: `store.begin_shutdown()` (admission closed for good, open ticket
   cancelled, slot voided, sweeper stopped; **no VideoService call**), then the existing
   `shutdown_services()` whose single `video.stop()` is the only Stop, then
   `store.finish_shutdown()` (at most one final release per capture obligation and one
   final deletion per file obligation without users, within the budget). `store.close()`
   stays for standalone use (= both phases plus its own Stop) and is not called by the
   runtime. A Stop that times out (hook not delivered) leaves the ticket holding the file:
   the final deletion is skipped, the obligation is logged generically and the startup
   sweep handles the file (D12).
3. **Capture slot and cleanup conditions (decisions 1-3) — store extension.** The store
   owns one **capture slot**: `EMPTY → CREATING → OWNED(busy | idle) → RELEASE_PENDING →
   EMPTY`, plus `RELEASE_STALLED`. `begin_capture(handle, kind)` reserves the slot under
   the store lock **before** `create_capture` (a second reservation: `409 capture_busy`;
   during `RELEASE_PENDING`/`RELEASE_STALLED`: `503 cleanup_pending`). The constructor runs
   outside the lock; a constructor that raises frees the slot (nothing to release). Its
   return is adopted by `adopt_capture(handle, capture) -> bool`, which in **one** store
   lock section records the owner and, only if the handle's operation is still valid (not
   cancelled, expired or shut down), marks the capture **busy** for the caller's next call
   and returns `True`; otherwise it adopts it idle and returns `False`, and the caller
   releases it without any call on it. There is no window between adoption and busy
   marking, so a cancel, expiry or shutdown can never release a capture that the handler
   then probes or decodes. Every later use goes through `begin_use(handle)` with the same
   validity check in the same section; `end_use(handle)` clears busy and, if the operation
   was invalidated meanwhile, makes the caller the releaser in that section. **Release
   condition:** adopted and not busy — independent of the file
   users and of the handle that owns it, so the handle never blocks its own release.
   Whoever makes the condition true releases it, single flight: the probing/decoding
   thread when it finishes, or the canceller (DELETE, expiry, close) when the capture is
   already idle. A failed release → `RELEASE_PENDING` (retry every 60 s, `RELEASE_STALLED`
   after 10, explicit retry through the recovery action). **File deletion condition:** no
   file user at all — writer, readers, tickets **and an unreleased capture** (the capture
   counts as a user until released).
4. **Failed probe release (decision 3).** Probe succeeds but its release fails → the
   source is **not** `PREPARED`: the generation is voided with reason
   `probe_release_failed`, the write answers `503 {category: "cleanup_pending",
   source_id, generation}`, the capture obligation keeps the file in use; after a
   successful release the file is deleted through the normal obligation and the slot
   becomes free (the operator uploads again). A failed **preview** release answers `503
   {category: "cleanup_pending", source_id, generation}` without the image; the source is
   not voided, but config, Play, upload and preview are refused until the release succeeds
   (decision 2); its idle TTL keeps running and an expiry waits for the release before
   the file is deleted.
5. **Refusals while a capture cleanup is pending (decision 2).** `PUT /{id}/file`,
   `GET /preview`, `PUT /configuration` and `POST /play` → `503 {category:
   "cleanup_pending", source_id, generation}`; `POST /` → `409 cleanup_pending`
   (admission, §5). Still available: `GET /template` (no capture is created), `/current`,
   `/play/{attempt}` and capabilities. `DELETE` stays allowed (it voids the generation; the
   deletion waits for the release).
6. **API `/api/video/sources` (§5) and wire models (`models/sources.py`).** Frozen for P1d:

   | Situation | Answer |
   |---|---|
   | Refused **before** the CAS (identity, generation, revision, attempt rules, `play_in_progress`, `session_cleanup_pending`, `config_not_ready`, `source_geometry_unstable`, `attempt_limit`, `operation_reused`) | `409 {category}` (`410` for `outcome_evicted`/`source_gone` lookups), nothing started, `next_attempt` unchanged |
   | Capture cleanup pending | `503 {category: "cleanup_pending", source_id, generation}` |
   | Passed the CAS | `200` attempt record; `outcome` `STARTED`, `REFUSED` (`reason`: `video_busy`, `required_component_refused`, `stopped`, `configuration_unavailable`), `FAILED` (`start_failed`), `TIMED_OUT`, `CANCELLED`; the attempt is used; replay identical; no server retry |
   | Session sub-record | `session.end_reason` ∈ `stop`, `eof`, `error`, `refused`, `cancelled`, **`frame_size_changed`**; `cleanup_ok` `true`/`false`/`null` |
   | Generation after `frame_size_changed` | `/current.geometry_unstable: true`, `config.ready: false`; Play `409 source_geometry_unstable`; re-save `409 source_geometry_unstable` |

   The upload handler streams to the store-provided path (200 MiB, MP4/WebM), checks the
   write handle's cancel flag between chunks, probes through the capture slot and ends
   through `finish_write`/`fail_write`.
7. **Configuration envelope (decision 5).** The `PUT /configuration` body is read with a
   cap: more than 4096 bytes of the whole JSON envelope (`generation`,
   `expected_revision`, `frame_width`, `frame_height`, `line`, `inside`, `zones`) →
   `413 {category: "configuration_too_large"}` before parsing; nothing changes and the
   TTL is not reset. The model's canonical-size check stays as a second guard
   (`422 invalid_geometry`, field `configuration`). Frontend: compute the serialized
   envelope size before sending, show the size error in the form, keep the draft, never
   retry automatically (recorded for the frontend plan; P1c only freezes the contract).
8. **Status identity (D6) — one lock section.** `VideoStatusResponse` gains optional
   `session_ref` and `session_id`; `get_status()` reads `state`, `source`, `error`,
   `session_ref`, `session_id` in **one** `_lock` section. Precondition (§5.3, D8): the
   composed runtime uses the default `session_id` factory; no environment value or API
   input can set it.
9. **`ERROR` until Stop, informational only (decision 4).** VideoService stays `ERROR`
   after a required refusal, an open or thread-start failure, a size mismatch, a failed
   hook delivery or a failed unbind until an explicit Stop; no endpoint stops
   automatically. `/current` adds `analysis: {stop_required, state, session_ref,
   session_id}` read from `video.get_status()` **outside** the store lock: a separate,
   transient observation tied to that session identity. It never enters `config.ready`,
   admission or the Play decision. The Play answer stays authoritative and follows the
   store's checks **before** the CAS first: `source_geometry_unstable` (after a size
   mismatch, sticky), `session_cleanup_pending` (session not yet cleaned), `cleanup_pending`
   (capture or file cleanup pending, 503) and the other 18.2 item 6 cases refuse without
   using an attempt. Only when all of them pass is the Play an accepted CAS, answered `200
   REFUSED video_busy` while VideoService is not `IDLE`. After `POST /api/video/stop`
   VideoService recovers; a refused-start or open-failure generation can then start the
   next Play, a `geometry_unstable` generation never can (delete, new upload, new
   configuration).
10. **Leftover and capture recovery (§0.2.2).** `POST /api/video/sources/cleanup/retry`
    (private mode, no body) → `store.retry_cleanup()`: one attempt per **stalled**
    obligation — file deletion or capture release — without an attempt in flight (a file
    deletion additionally needs no user), through the existing single-flight flags; only
    obligations the store recorded itself; no path, name or pattern from the request; no
    loop, no reset of the attempt count. `202 {cleanup: {pending, stalled, attempted}}`
    (counts only), `204` when none remains, `409 private_mode_inactive` / `closed`.
11. **Legacy routes by mode.** Private: `POST /webcam/start` and `POST /upload` answer `409
    private_flow_required` before any reservation, write or probe. Legacy: every `/sources`
    mutation `409 private_mode_inactive`; capabilities answers. `POST /stop`, `GET
    /status`, `GET /stream` unchanged.
12. **Sanitization.** No path, file name, configuration value, token, lease, JPEG/base64 or
    provider body in logs or error bodies; the preview answer is the image only.
13. **`.env.example` (decision 7).** Private-mode text: the configuration file is an
    optional template; missing/invalid → no template, never disabled; the size limit and
    the prepared-source directory documented; placeholders only, no values.

### 18.3 Files, tests and acceptance

| Files | Tests |
|---|---|
| new `app/api/sources.py`, `app/models/sources.py`; `app/api/video.py` (mode refusal); `app/models/video.py` (identity fields); `app/services/video_service.py` (`get_status` in one section); `app/services/runtime.py`, `app/main.py` (composition, lifespan, two-phase shutdown); `app/services/prepared_source.py` (capture slot, capture obligations, `begin_shutdown`/`finish_shutdown`, `retry_cleanup`); `backend/.env.example` | new `test_sources_api.py` (TestClient, fake decoder, fake filesystem and clock through the runtime accessor); capture-slot and shutdown cases in `test_prepared_source.py` / `test_prepared_source_races.py`; adapted `test_private_parking_runtime_wiring.py`, `test_private_parking_evaluation_enable.py`, `test_video_api.py`, `test_video_upload_rollback.py`, `test_evaluation_runtime_integration.py` |

Acceptance (fakes, `threading.Event` gates with timeouts, fake clock; no server, real
video, device or provider):

1. **§9 over HTTP:** §9.1-§9.3, §9.9, §9.10, §9.11 (polling `/current`, `/play/{n}`,
   `/capabilities`, `/api/video/status` never resets the TTL), §9.13 (wire models,
   one-section status read with a gate between reservation and sink start, the factory
   precondition on the composed runtime).
2. **Gate tests — capture constructor.** Gate inside `create_capture` for an upload probe
   and for a preview: (a) `DELETE`, (b) expiry (fake clock, no sweeper pass, then a tick),
   (c) `begin_shutdown`/`finish_shutdown` while the constructor is held. On release:
   the capture is adopted and released **once** by the creating thread (idle, cancelled),
   the probe/decode never runs, no `PREPARED`, no image; the file is deleted only after
   the release and every other user ended; a second capture request during the gate →
   `409 capture_busy`, zero extra constructor calls. Constructor raising → slot free,
   nothing released, file deletion proceeds.
3. **Gate tests — probe and decode.** Gate inside `probe` and inside `read_first_frame`:
   (a) `DELETE`, (b) expiry, (c) shutdown while held → no release during the call (busy);
   on return the calling thread releases once; the result is discarded (`409 cancelled` /
   `deadline_exceeded` for the write, `409 expired`/`source_gone` for the preview);
   file deletion only afterwards. With the release then failing → capture obligation,
   `503 cleanup_pending` with `source_id`/`generation`, file kept.
4. **Failed release (decision 3).** Probe succeeds, release fails once → `503
   cleanup_pending` with identity, not `PREPARED`; every new upload, preview, configuration
   and Play `503 cleanup_pending`, `POST /` `409 cleanup_pending`, zero new constructor
   calls; the retry 60 s later succeeds → file deleted, slot `EMPTY`, admission open, a new
   upload works. Preview variant: source kept, requests refused until the retry succeeds,
   then usable again.
5. **Stalled capture recovery.** Release failing 10 times → `RELEASE_STALLED`, no further
   automatic attempt, `/current.cleanup` lists it; `POST /cleanup/retry` → exactly one
   attempt (gated: a concurrent retry, `DELETE` and sweeper pass add none); success →
   file deletion proceeds; repeated failure keeps the obligation; counts only in the body.
6. **Shutdown without a double Stop (decision 6).** Spy on `video.stop`: the composed
   shutdown calls it **exactly once** (zero calls from the store phases). With a session
   running: the single Stop delivers the hook, `finish_shutdown` deletes the file once.
   With the hook gated past the Stop wait: no second Stop, the ticket keeps the file,
   `finish_shutdown` skips the deletion, the obligation is logged, no exception. With a
   capture obligation: one final release attempt only. With an attempt already in
   flight: none started beside it.
7. **Configuration envelope (decision 5).** 4096 bytes → accepted (if valid); 4097 → `413
   configuration_too_large`, nothing changed, TTL not reset; a body without a length
   header read with the cap; the canonical-size guard still `422`.
8. **`ERROR` until Stop (decision 4).** (a) Required refusal and open failure each →
   `/current.analysis.stop_required: true` with that session's identity, `config.ready`
   unchanged by it, next Play `200 REFUSED video_busy` with the attempt used; `POST /stop`
   → next Play `STARTED`. (b) Size mismatch (aligned 2026-10-12 with the implemented
   check order): while the hook is not yet applied the slot is still `PLAYING`, so Play
   answers `409 play_in_progress`; once the `frame_size_changed` hook is applied — with
   `cleanup_ok` `true`, `false` or `null` — Play answers `409 source_geometry_unstable`
   (checked before `session_cleanup_pending`). All of these refusals happen **before the
   CAS** (`next_attempt` unchanged, no VideoService call); after `POST /stop` VideoService
   is `IDLE` but the generation stays
   unplayable (same `409`); `DELETE`, a new upload and a newly confirmed configuration →
   Play `STARTED`. (c) Capture cleanup pending → Play `503 cleanup_pending` before the
   CAS. (d) A stale `analysis` observation (other `session_id`) never changes readiness.
9. **Runtime and modes.** Private with missing/invalid startup file → store, registry,
   per-session trigger built, `template_available: false`; legacy → no store, legacy routes
   unchanged; private-mode legacy routes `409 private_flow_required` before reservation,
   write or probe; the startup sweep touches only the dedicated directory.
10. Full backend suite, `pip check`, `git diff --check`; then the shared documents (18.4).
11. **Gate between adoption and use (rev 3).** Gate at the constructor's return (before
    `adopt_capture`) and fire (a) `DELETE`, (b) expiry, (c) `begin_shutdown`; release →
    `adopt_capture` returns `False`, the handler makes **zero** probe/decode calls (spy),
    the capture is released once by the handler, the file deletion follows. Fire the same
    events right after `adopt_capture` returned `True` (gate at the start of `probe` /
    `read_first_frame`) → no release while busy; `end_use` makes the handler the releaser;
    one release. Concurrent `begin_use` after an invalidation → refused, no call on the
    capture.

### 18.4 Risks and open points for the review

- **Mode-specific behaviour change (M3):** private mode refuses the legacy start routes;
  webcam analysis is unavailable in private mode until a webcam preview phase exists.
- **Shared documents:** `ARCHITECTURE.md` and `PLAN.md` carry uncommitted changes of other
  authors; P1c's documentation step must be coordinated (owner decides order).
- **One capture per store** serializes previews: a second operator tab gets `409
  capture_busy` while a decode runs (intended by decision 2).
- The store's `close()` keeps its own Stop for standalone use; the runtime must never call
  it together with `shutdown_services()` (acceptance 6 checks the composed path).
- Open technical points carried over (§11): `DELETE` `202` vs `204`, zone renaming,
  first-frame preview, zone overlap. The 4096-byte question is decided (decision 5).
- The frozen HTTP table (18.2 item 6) must be reviewed before P1d starts on fakes.

## 19. P1c implementation record (Claude, 2026-10-12) — completed, §19.3

Authorization: owner, on design rev 3 (`93eb76e`), with acceptance 8(b) aligned first
(18.3: `409 play_in_progress` before the hook is applied, `409 source_geometry_unstable`
once the `frame_size_changed` hook is applied with any `cleanup_ok`, all before the CAS).
Preflight: HEAD `3c5446a` = `origin/main`, nothing staged, `backend/` clean; documents of
other authors untouched; `backend/.env.example` read and edited under decision 7, no real
environment file read.

**Files:**

| File | Change |
|---|---|
| new `backend/app/api/sources.py` | `/api/video/sources` routes; strict body/query parsing (never echoed); category → status table; upload streaming + probe through the capture slot; preview decode through the capture slot; 4096-byte envelope cap; recovery action |
| new `backend/app/models/sources.py` | Frozen wire models (attempt record, `/current`, capabilities, template, saved configuration) |
| `backend/app/services/prepared_source.py` | Capture slot (`begin_capture`, `capture_failed`, `adopt_capture`, `finish_capture`, release obligation with retry/stall), an unreleased capture counts as a file user, `cleanup_pending` refusals before the CAS, `retry_cleanup`, two-phase shutdown (`begin_shutdown`/`finish_shutdown`; `close()` = both + its own Stop), snapshot `capture_state`/`capture_attempts`, `write_refusal`/`read_refusal` |
| `backend/app/services/video_service.py` | `get_status()` adds `session_ref`/`session_id` in the same `_lock` section |
| `backend/app/models/video.py` | `VideoStatusResponse` additive `session_ref`, `session_id` |
| `backend/app/services/runtime.py` | Private mode always private: registry, `PrivateParkingTrigger(session_config=registry.get)`, optional template; `VideoService(session_config_registry=...)`; `PREPARED_DIR`, `prepared_store`, `start_prepared_store()` |
| `backend/app/main.py` | Sources router; lifespan creates the store (private only); shutdown `begin_shutdown` → `shutdown_services` (the single Stop) → `finish_shutdown` |
| `backend/app/api/video.py` | Private mode: `POST /webcam/start` and `POST /upload` → `409 {category: private_flow_required}` before any reservation, write or probe |
| `backend/.env.example` | Private-mode text: the configuration file is an optional template, the prepared directory, no disabled path |
| new `backend/tests/test_sources_api.py` (35 tests) | HTTP flow, Play contract, envelope, capture gates, failed release, recovery, shutdown, status identity, factory, legacy refusal, runtime store |
| `backend/tests/test_private_parking_runtime_wiring.py` (adapted) | Invalid/missing template → private without template and no fallback (was: disabled); private sessions start through `start_prepared_file` with the bound configuration; the `Stack` binds per lease like VideoService; subprocess mode selection updated |

**Actual wire contract (`/api/video/sources`):**

| Method / path | Success | Refusals (`{category, field?}`) |
|---|---|---|
| `GET /capabilities` | `200 {mode, preparation_available, source_kinds:["file"], template_available, evaluation_enabled, limits:{max_upload_bytes, accepted_types, max_zones, min_line_length, min_zone_area, max_configuration_bytes}}` | — (every mode) |
| `POST /` `{operation_id}` (UUID) | `201 {source_id, generation, state}`; same `operation_id` → same source | `409 slot_busy / cleanup_pending / private_mode_inactive`, `422 invalid_request / invalid_operation_id`, `503 storage_unavailable / closed` |
| `PUT /{id}/file?generation=g` (multipart) | `200 {state:"PREPARED", frame_width, frame_height, preview_revision}` (decoded first-frame size) | `409 generation_mismatch / upload_not_expected / cancelled / deadline_exceeded / capture_busy`, `410 source_gone`, `422 missing_file / unsupported_type / too_large / not_decodable / invalid_request`, `500 write_failed`, **`503 {category:"cleanup_pending", source_id, generation}`** (probe release failed: not `PREPARED`, file deleted after the release, upload again), `503 closed` |
| `GET /current` | `200 {source_id, generation, state, upload_operation_id, frame_width, frame_height, preview_revision, config:{revision, ready}\|null, geometry_unstable, template_available, next_attempt, last_attempt, expires_in_seconds, deadline_in_seconds, cleanup:{pending, reason, attempts, capture, capture_attempts}, analysis:{stop_required, state, session_ref, session_id}}` or `204` | `409 private_mode_inactive` |
| `GET /{id}/preview?generation=g` | `200 image/jpeg` | `409 generation_mismatch / expired / not_prepared / capture_busy`, `410 source_gone`, `500 preview_failed`, `503 cleanup_pending` + ids |
| `GET /{id}/template?generation=g` | `200 {line:{p1,p2}, inside, zones, frame_width, frame_height, confirmed:false}` or `204` | as preview (no capture) |
| `PUT /{id}/configuration` (envelope ≤ 4096 bytes) | `200 {revision, ready, canonical}` | `413 configuration_too_large` (before parsing), `422 invalid_request / invalid_geometry + field`, `409 generation_mismatch / revision_conflict / frame_size_mismatch / play_pending / playing / session_cleanup_pending / source_geometry_unstable / not_prepared`, `503 cleanup_pending` + ids |
| `DELETE /{id}?generation=g` | `202 {state:"DELETING"}` or `204` | `409 generation_mismatch / playing / session_cleanup_pending`, `404` |
| `POST /{id}/play` `{generation, config_revision, attempt, operation_id}` | **`200` attempt record for every Play past the CAS** (`STARTED`, `REFUSED` + `video_busy` / `required_component_refused` / `stopped` / `configuration_unavailable`, `FAILED start_failed`, `TIMED_OUT`, `CANCELLED`); replay identical | before the CAS: `409 operation_reused / attempt_superseded / attempt_conflict / attempt_limit / source_geometry_unstable / session_cleanup_pending / play_in_progress / config_not_ready / revision_stale`, `410 outcome_evicted / source_gone`, `503 cleanup_pending` + ids |
| `GET /{id}/play/{attempt}` | `200` attempt record | `404 unknown_attempt / unknown_source`, `410 outcome_evicted / source_gone` |
| `POST /cleanup/retry` | `202 {cleanup:{pending, stalled, attempted}}` or `204` | `409 private_mode_inactive`, `503 closed` |

Attempt record: `{attempt, operation_id, outcome, reason, session_ref, session_id,
session:{phase NONE|RUNNING|ENDING|ENDED, end_reason stop|eof|error|refused|cancelled|frame_size_changed|null, cleanup_ok}}`.
`GET /api/video/status` adds `session_ref` and `session_id` (null once `IDLE`).

**Deviations from the design text, for the reviewer:**

1. `begin_use`/`end_use` are not separate calls: every capture has exactly one use (the
   upload probe or the preview decode). `adopt_capture` marks it busy for that use only
   if the operation is valid; `finish_capture` ends the use and releases in one call. A
   second use of one capture does not exist, so the separate re-check is not needed.
2. `source_gone` answers `410` on every route (not only lookups); `closed` answers `503`.
3. The prepared directory is `backend/app/storage/uploads/prepared`, not
   `app/storage/prepared`: only `backend/app/storage/uploads/*` is git-ignored, and
   `.gitignore` is outside the P1c scope, so a directory outside it could let uploaded
   videos be committed. It is still a dedicated directory: the legacy route writes only
   directly into `uploads/` and never sweeps; the store sweeps only `prepared/`.
   Alternative for the owner: a `.gitignore` entry for `app/storage/prepared/`.
4. The upload probe reads the first decoded frame to obtain the frame size (the same size
   the per-frame check of P1b compares), instead of container properties.
5. ~~`GET /template` resets the idle TTL even when no template exists (`204`).~~
   Superseded by §19.1 item 6: a `204` validates the source without resetting the TTL.

**Acceptance coverage (18.3):**

| # | Covered | Not covered |
|---|---|---|
| 1 | full flow, replay, superseded/conflict/stale, refused `200`, lost-ACK replay; error bodies and app logs without values; status one-section read (gate between reservation and sink start); default factory on the composed runtime | §9.1 preview-isolation spies on detector/CLIP/worker over HTTP; §9.11 polling over HTTP (covered at store level, P1a) |
| 2 | upload-probe constructor gate with `DELETE`, expiry and `begin_shutdown`: zero probe/decode calls, one release, file after it; constructor raising | preview-constructor gate with expiry/shutdown (only `DELETE`) |
| 3 | probe gate with `DELETE`/expiry: no release while busy, one release after; preview decode gate with expiry; second preview → `409 capture_busy`, no second constructor call | shutdown inside probe/decode; release failing right after a gate |
| 4 | probe release failure → `503` + ids, not `PREPARED`, `POST /` `409`, retry → file deleted, upload again; preview release failure → config/Play `503` before the CAS, `next_attempt` unchanged, retry → Play `STARTED` | — |
| 5 | stalled capture → no automatic attempt; recovery → one attempt, counts only; success → `204` and the expired file deleted after the release; stalled startup leftover recovered without a source id | concurrent recovery/`DELETE`/sweeper under a gate |
| 6 | store phases make zero VideoService calls; lifespan order `begin_shutdown` → `shutdown_services` → `finish_shutdown` | Stop timeout with a gated hook in the composed runtime; a capture obligation at shutdown over HTTP |
| 7 | 4096 accepted, 4097 → `413`, nothing changed, TTL not reset; streamed body without a length header → `413` | the canonical-size `422` guard over HTTP (model test exists) |
| 8 | (b) `play_in_progress` before the hook, `source_geometry_unstable` after it for `True`/`False`/`None`, re-save refused, `next_attempt` unchanged; (c) capture cleanup → `503` before the CAS; (d) `analysis` informational | (a) required refusal/open failure through the real VideoService over HTTP (P1b covers it at service level) |
| 9 | private without/with invalid template stays private with no fallback (refused start); legacy routes `409 private_flow_required` before reserving; store only in private mode; startup sweep only its directory | — |
| 11 | adoption after `DELETE` (preview) and after `DELETE`/expiry/shutdown (upload probe): no decode/probe call | separate `begin_use` (deviation 1) |

**Implementer evidence (Claude, not reviewer results):**

- `test_sources_api.py` 35 passed; the full backend suite serial with a unique basetemp:
  1445 passed, 1 warning (Starlette deprecation, pre-existing).
- `test_sources_api.py`, `test_prepared_source.py`, `test_prepared_source_races.py` and
  `test_video_prepared_playback.py` repeated 30 times: 0 failures.
- Mutation runs (scratch pytest plugin outside the repository): adoption always busy → 4
  failures; capture not counted as a file user → 2; no `cleanup_pending` refusal → 1;
  `begin_shutdown` calling `video.stop()` → 2; envelope cap removed → 4; probe release
  failure ignored → 2.
- `pip check` clean; `git diff --check` clean; no control bytes or trailing whitespace;
  final LF. Nothing staged; no server, provider, real video, webcam, TTS or speaker.

**Integration limits:**

- Not run against a real server, real video files or devices; the decoder is faked.
- One capture per store serializes previews (`409 capture_busy` for a second tab).
- A handler thread that never returns from a probe/decode keeps the capture busy and the
  file in use until restart (same limit as a writer that never returns).
- `ERROR` until Stop remains operator-driven; the UI must show `analysis.stop_required`
  and offer Stop (P1d).
- Shared documents (`ARCHITECTURE.md`, `docs/RELIABILITY.md`, `PLAN.md`) are not updated:
  they carry uncommitted changes of other authors (18.4).

### 19.1 Fixes for the Codex review of P1c (owner instruction, 2026-10-12)

Preflight: HEAD `3c5446a` = `origin/main`, nothing staged; the committed P1a/P1b test
files unchanged against HEAD; the twelve P1c files identical to the hashes of §19, so
the baseline had not moved.

| # | Finding | Fix | Regression test |
|---|---|---|---|
| 1 | Async handlers called blocking store operations on the event loop (`store.play()` reaches VideoService; a drain can delete files) | `create`, `save_configuration` (+ snapshot) and `play` run through `run_in_threadpool`; the upload is parsed asynchronously, then `begin_write` and the whole write/probe step run in the threadpool; preview, template, delete, lookup, current and recovery stay plain `def` handlers (FastAPI's threadpool). Cancellation: `anyio.to_thread.run_sync` keeps `abandon_on_cancel=False`, so a cancelled request waits for the thread and the store keeps a consistent owner. **Corrected in §19.2:** this claim did not hold for the upload (two awaits) nor for a native asyncio cancellation | One ASGI event loop (`httpx.ASGITransport`, `asyncio.run`): Play held inside the VideoService start; `DELETE` on the same loop is answered while Play is still held; Play then answers `200 CANCELLED`, no reservation |
| 2 | A decode that outlived its operation could still answer `200` with a JPEG | After `finish_capture` (the release always happens first) the preview route calls `store.read_still_valid(handle)`, which applies due deadlines and the TTL under the lock and checks the read's source, generation, voiding and shutdown; a stale frame is discarded and the refusal category answered | Decode gated, then expiry (with and without a sweeper pass) or `begin_shutdown`: `409 expired` / `503 closed`, never `image/jpeg`; the capture released once; the file deleted after it (the former test that expected `200` was replaced) |
| 3 | `503 cleanup_pending` took the identity from the latest snapshot (could be another source) | `SourceError` carries `source_id`/`generation`, captured under the store lock when raised (`_cleanup_pending_error_locked`: the source whose capture is pending); the route answers with the error's identity only | Play of A refused and held after the refusal (delegating store wrapper); A's capture released, A deleted, B created; the answer still names A |
| 4 | An invalid `file` field reached FastAPI's parameter validation, whose `422` detail echoes the input | The upload route no longer declares the file parameter: it parses the multipart body itself (`await request.form()`), accepts only an `UploadFile` under `file`, answers `422 {category: missing_file / invalid_request / unsupported_type}`, closes the form | A text field `file` carrying a marker, a malformed multipart body with the marker in the boundary and body, and a wrong field name: `422` with a category only; the marker absent from the answer and from every non-client log; nothing created. Evidence for the old shape: a scratch app with the former `file: UploadFile \| None = File(None)` parameter answers `422` **with the marker echoed** |
| 5 | `Content-Length` was converted with `int()` before validation (a 5000-digit value raises) | `_declared_length` checks the header as text first: empty, non-ASCII, non-digit or signed → `422 invalid_request`; more than 15 digits → too large without conversion; the configuration route answers `413 configuration_too_large`, the small bodies `422`, the upload `422 too_large` above 200 MiB plus the multipart overhead | Raw ASGI calls (a client library would not send these headers): 5000 digits → `413`; `12abc`, `-5`, a non-ASCII digit, empty → `422`; nothing changed; the same on `POST /` and `POST /play` |
| 6 | A template `204` reset the idle TTL | Without a template the route calls `store.validate_source()` (due deadlines applied, no TTL reset) and answers `204`; an available template still uses `touch_template` (reset, D5) | Fake clock: a `204` at 29 min leaves 60 s to expiry and the source expires at 30 min; an available template at 29 min resets to 1800 s |

**Implementer evidence (Claude, not reviewer results):**

- `test_sources_api.py` 49 passed; full backend suite serial with a unique basetemp: 1459
  passed, 1 warning (Starlette deprecation, pre-existing); the legacy/retail suites
  (`test_video_api`, `test_video_upload_rollback`, `test_video_service`,
  `test_retail_candidate_trigger`, `test_parking_candidate_trigger`) 197 passed.
- `test_sources_api.py`, `test_prepared_source.py`, `test_prepared_source_races.py` and
  `test_video_prepared_playback.py` repeated 30 times: 0 failures.
- Each fix was undone by a scratch pytest plugin outside the repository and its test
  failed: store calls inline on the loop → 1; no preview revalidation → 3; identity from
  the snapshot → 1; `int()` before validation → 5; template `204` resetting the TTL → 1.
  The multipart fix cannot be undone that way (the route shape changed); the scratch-app
  evidence above shows the old echo.
- `pip check` clean; `git diff --check` clean; no control bytes or trailing whitespace;
  final LF. Every test thread is joined (gates have timeouts; the event-loop test also
  has a safety timer that is cancelled). Nothing staged; no server, provider, real
  video, webcam or speaker.

**Limits unchanged:** the decoder is faked; a malformed `Content-Length` would normally be
rejected by the HTTP server before the application (the test calls the ASGI app
directly); the other §19 integration limits stay open.

### 19.2 Fix: a cancelled upload left a writer without an executor (Codex re-review, BLOCKED)

Codex re-review of §19.1 (`sources.py` `a9e3aa8`, `prepared_source.py` `cca3f05`,
`test_sources_api.py` `b137bad`, plan `3f6e13c`): the five §19.1 findings and the template
TTL closed; **one new high finding.** The upload reserved the writer
(`await run_in_threadpool(store.begin_write, ...)`) and ran the write/probe in a **second**
await. A request task cancelled between them (reproduced with `asyncio.Task.cancel()` and
an AnyIO `CancelScope`) left the writer registered (`done=False`, one file user, zero
decoder calls); `DELETE` answered `202`, and expiry and recovery kept the obligation and
admission closed.

While fixing it a second cause appeared: a **native** asyncio cancellation is not shielded
by `run_in_threadpool` — the await is abandoned while the worker keeps running — and the
handler's `finally` then closed the multipart form, i.e. the file the worker was reading;
the next read raised `ValueError` (not `OSError`) and the worker died with the writer
still registered. The docstring and the §19.1 claim relied on shielding; both are
corrected.

Fix (`backend/app/api/sources.py`, design independent of shielding):

- **One ownership unit** (`_UploadUnit`): `begin_write` and the whole write/probe step run
  in ONE threadpool call. States under a lock: `pending → running → done`, or `pending →
  abandoned`. The worker claims the unit before doing anything; a request that ended
  first (`abandon()` in the handler's `finally`) leaves it `abandoned` and the worker
  then reserves nothing.
- **The running unit owns the uploaded file**: only the worker closes it; the handler
  closes it only while the unit is still `pending`, and never closes the form under a
  running worker.
- **The writer is always ended**: the unit's `finally` calls `fail_write(handle,
  "write_failed")` if the writer it registered is not done, whatever the exception.

Regression tests (`test_sources_api.py`, deterministic, gate with timeout and a cancelled
safety timer, all threads joined):

| Case | Expected |
|---|---|
| Writer reservation held inside its worker; the ASGI request task cancelled with `asyncio.Task.cancel()` | the request is cancelled; the unit runs to its end: the probe runs once, the capture is released, `PREPARED`, zero file users; `DELETE` then deletes the file and admission opens |
| Same with an AnyIO `CancelScope` around the request task | the worker await is shielded, the request may complete; same end state, no writer left |
| A unit abandoned before it started | `run` returns without `begin_write`; the uploaded file closed once; the source still `AWAITING_FILE` |
| An unexpected exception inside the unit (e.g. a closed upload file) | `500` without the message; the writer ended (`fail_write`), the partial file deleted, admission open |

Implementer evidence (Claude, not reviewer results): both cancellation cases failed on
the reviewed code (writer leaked) and pass now; scratch-plugin mutations — the former two
awaits → 3 failures, no writer guard in the unit → 3, the handler closing the file of a
running unit → 2; `test_sources_api.py` 53 passed; full backend suite serial with a
unique basetemp 1463 passed, 1 warning (Starlette deprecation, pre-existing); the four
P1 store/API test files repeated 30 times: 0 failures; `pip check`, `git diff --check`,
control bytes, trailing whitespace and final LF clean; nothing staged; no server,
provider, real video or device.

Audit of the other handlers: `create`, `save_configuration` and `play` are one store call
each inside one threadpool unit, after the body was read (nothing is reserved before);
preview, template, delete, lookup, current and recovery are plain `def` handlers whose
reservation and release (`begin_read` … `end_read`, capture slot) happen inside one
threadpool call. A native cancellation can abandon such an await, but the worker then
still finishes its own `finally` (the reader and the capture slot are ended in the same
thread), so no reservation is split across awaits.

### 19.3 Closure of P1c — runtime/API integration verified with fakes (owner instruction, 2026-10-12)

**Final review verdict: PASS WITH LIMITATIONS** — Codex, through the owner/PM, on the
§19.2 delta (`sources.py` `59a4841`, `test_sources_api.py` `c1020c8`, plan `81d2c6c`); the
reviewer re-checked all twelve candidates (only those three changed since the previous
round) and found no new blocker. Reviewer results of that round: 175 target tests and
the full backend suite 1463 passed with one deprecation warning; `pip check`, diff,
whitespace, newlines of the new candidates and the plan links clean. Verification uses
fakes; it does not prove a real server, OpenCV decoding or devices.

**Closure per finding** (each with the review that closed it; implementer evidence —
the 30-fold repetitions and the scratch-plugin mutations — is Claude's, not the
reviewer's):

| Finding | Fixed in | Closed by (Codex, through the owner/PM) | Tests |
|---|---|---|---|
| Blocking store calls (Play) on the event loop | §19.1 item 1 | re-review of §19.1: closed | one-loop Play held + `DELETE` answered |
| Stale preview after expiry/shutdown | §19.1 item 2 | re-review of §19.1: closed | decode gate + expiry (swept/unswept) / shutdown → no JPEG |
| Error identity from a later snapshot | §19.1 item 3 | re-review of §19.1: closed | refusal of A held while A deleted and B created → names A |
| Multipart input echoed | §19.1 item 4 | re-review of §19.1: closed | text field, malformed body, wrong field with a marker → category only |
| Extreme/malformed `Content-Length` | §19.1 item 5 | re-review of §19.1: closed | raw ASGI: 5000 digits, letters, signed, non-ASCII, empty |
| Template `204` resetting the TTL | §19.1 item 6 | re-review of §19.1: "koreksi TTL template juga lulus" | fake clock: `204` keeps 60 s; an available template resets to 1800 s |
| Cancelled upload leaving a writer without an executor | §19.2 | re-review of §19.2: closed (reproduced with `asyncio.Task.cancel()` and an AnyIO `CancelScope`: writer finished, capture released once, `DELETE` completes cleanup, admission open; regressions cover a unit abandoned before it started and an unexpected exception) | both cancellation variants, abandoned unit, unexpected error |

Hash check before the commit (full blobs, identical to the reviewed versions, so the
suite was not re-run for the closure):

| Path | Blob |
|---|---|
| `backend/app/api/sources.py` | `59a4841ba2425c77a3a9ccaf279c54dfb6016fca` |
| `backend/tests/test_sources_api.py` | `c1020c860be46adfe0d83ab1838dfb1a8480e7c5` |
| `backend/app/services/prepared_source.py` | `cca3f0553f85afb88c47b1296f9c3e637d345653` |
| `backend/app/models/sources.py` | `4c7e9ebbec163ac634e15d113fe54f30e454d310` |
| `backend/app/services/video_service.py` | `0266030c062af4e2ed731fabfcb691a211e11b59` |
| `backend/app/models/video.py` | `671264b69278d100111d92d129aaf85e25618e4a` |
| `backend/app/services/runtime.py` | `3fe6deb0bbd3c15649202187771119d7f8ae91e5` |
| `backend/app/main.py` | `ade37a0256ae90b74eee1efa5147ec0926281a4b` |
| `backend/app/api/video.py` | `d20364a18cd2504809e89b2fa13b4b4b697c4b13` |
| `backend/.env.example` | `0831d89d114ebc02c6bec7f255c0022c3f841466` |
| `backend/tests/test_private_parking_runtime_wiring.py` | `67f35fb52f1294dd066fa1fef5bc73eb94c773eb` |

This plan was reviewed at `81d2c6c`; only this closure record and the status lines were
added after it.

**Frozen wire contract for the frontend (P1d).** The endpoint table of §19 ("Actual wire
contract") is frozen as amended by §19.1 (multipart parsed by the route; `Content-Length`
rules; template `204` without a TTL reset). In addition the frontend relies on:

- **Error bodies:** `{category}` or `{category, field}` only; `503 cleanup_pending` adds
  `source_id` and `generation` of the source whose cleanup is pending — taken from the
  refusal itself, never from a later snapshot. No input value, path, file name or
  exception text is ever returned.
- **Categories by status:** `404 unknown_source / unknown_attempt`; `409` conflicts of
  §19 (incl. `capture_busy`, `play_in_progress`, `session_cleanup_pending`,
  `source_geometry_unstable`, `private_mode_inactive`, `private_flow_required` on the legacy
  routes); `410 source_gone / outcome_evicted`; `413 configuration_too_large`; `422
  invalid_request (+field) / invalid_operation_id / invalid_geometry (+field) / missing_file
  / unsupported_type / too_large / not_decodable`; `500 write_failed / preview_failed`;
  `503 cleanup_pending (+identity) / storage_unavailable / closed`.
- **Identity:** every source request carries `source_id` + `generation`; configuration
  answers carry `revision`; a Play intent is `(attempt = /current.next_attempt,
  operation_id)` and its record carries `session_ref` and `session_id`;
  `GET /api/video/status` returns `state`, `session_ref`, `session_id` from one lock
  section (null once `IDLE`). Results join by `source_session_id == status.session_id`
  under a matching `session_ref` (§5.3, D8: only as strong as the default `uuid4`
  factory; no cross-endpoint atomicity).
- **Readiness:** Play is possible only when `/current.config.ready` is true (an accepted
  configuration for this generation, state `PREPARED`, no geometry instability, no capture
  cleanup pending). `/current.analysis` (`stop_required`, `state`, `session_ref`,
  `session_id`) is a transient observation of VideoService and never a readiness input; when
  `stop_required` is true the UI offers Stop (`POST /api/video/stop`), never an automatic
  one. Polling `/current`, `/play/{attempt}`, capabilities or video status never extends
  the idle TTL; preview, an available template and accepted mutations do.
- **Reconciliation:** a Play past the CAS always answers `200` with the attempt record
  (`REFUSED` and `FAILED` included, the attempt used); refusals before the CAS (`409` /
  `410` / `503`) never use an attempt. After a lost answer the UI replays the same
  `(attempt, operation_id)` (identical record) or reads `GET /play/{attempt}`; it never
  creates a new `operation_id` to resolve an unknown outcome and never retries
  automatically. `410 outcome_evicted` / `source_gone` end the reconciliation. A
  `frame_size_changed` generation stays unplayable (delete, upload again, confirm a new
  configuration). Upload identity after a lost `POST /` answer: `/current.upload_operation_id`.

**Remaining limits:** decoder, filesystem and VideoService are fakes in the P1c tests; no
real server, OpenCV decoding or device is proven; the §19 integration limits stay open;
shared documents (`ARCHITECTURE.md`, `docs/RELIABILITY.md`, `PLAN.md`) are not updated
here (uncommitted changes of other authors).
