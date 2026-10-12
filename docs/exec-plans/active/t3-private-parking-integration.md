# Exec plan T3 — private parking integration

Code baseline `36ffa20` (T2 sampler); plan HEAD `7c41672` (H1 docs). §4.1 records the
facts before T3a1. §4.2, §4.4, §4.5, §4.6 and §4.7 describe trigger, sampler, worker and
audio-gate contracts as implemented by T3a1 (completed, core only, not wired into runtime).
§4.3 (`VideoService` lifecycle) is implemented by T3a2 (completed — lifecycle only). §4.2 alert sequence and §4.4 resync application are implemented by T3b (completed — core only: coordinator and tee, not wired). The T3c lifecycle prerequisites are implemented and committed: T3c0.1 (`e50d9e2`, detector cleanup/lease/delivery) and T3c0.2 (`d479357`, VideoService required-sink/refusal capability, token-bound upload rollback, capture cleanup). The rest of §4.8 — private-mode configuration and runtime wiring — is T3c: completed — fake integration verified (committed as "feat: wire private parking runtime with fail-closed configuration"); not proven on a real device. T3d (private prompt and private dispatch gate) completed — reviewed PASS WITH LIMITATIONS, closure check PASS (Codex via PM); committed as "feat: enable direct Gemini evaluation for private parking"; model accuracy, gesture, latency and real devices not proven; T3e code does not exist.
Status: see §8 — **rev 13: design PASS WITH LIMITATIONS (owner-reported); T3a1 completed — core only (reviewed PASS WITH LIMITATIONS, Codex via owner; committed as "feat: prepare private parking integration contracts"); T3a2 completed — lifecycle only (reviewed PASS; committed as "fix: isolate video session lifecycle and cleanup"); T3b completed — core only (coordinator and bounded tee; independent review PASS on the overflow delta; committed as "feat: add private parking coordinator and bounded frame tee"); T3c0.1 completed (`e50d9e2`) and T3c0.2 completed (`d479357`) — lifecycle prerequisites, reviewed PASS WITH LIMITATIONS; T3c (wiring and mode) completed — fake integration verified (committed as "feat: wire private parking runtime with fail-closed configuration"); not proven on a real device; T3d (private prompt and private dispatch gate) completed — reviewed PASS WITH LIMITATIONS, closure check PASS (Codex via PM); committed as "feat: enable direct Gemini evaluation for private parking"; model accuracy, gesture, latency and real devices not proven; T3e not started, not authorized. T3 as a whole is not complete.**

## 1. Goal

When the owner configures an entry line and motor zones, `PARKING_MOTORCYCLE` runs in
*private mode*: a person who crosses in and is confirmed approaching a parked
motorcycle without authorization produces (a) one **local access warning** with its own
clip that never waits for the cloud and (b) — from T3d on — at most one cloud
evaluation built from journey evidence (crossing → three journey frames → approach).
Private mode is off by default and selected explicitly. The warning is a rule outcome,
never a CRITICAL assessment (`ARCHITECTURE.md`, "Local access warning vs CRITICAL
assessment").

## 2. Scope / non-scope

Phased (§7); each phase lists its own files. Overall non-scope: frontend and HTTP API
changes, retail wiring, threshold calibration, cost/latency optimisation, persistence,
new retries. Live provider, real video, webcam, speaker, package installs and gesture
models happen only in T3e with explicit owner permission per action.

## 3. Dependencies and decisions

Dependencies: T1 trigger core `d999885`, T2 sampler `36ffa20` (core only); runtime
`EvaluationWorker`, `AudioAlertGate`, `DetectorService` consumer slot, `VideoService`
lifecycle.

### 3.1 Authorized by the owner/PM

| # | Decision | Reference |
|---|---|---|
| A1 | Private parking is a mode of `PARKING_MOTORCYCLE`; the legacy parking path stays available | T1 brief §2 (2026-10-04); H1 brief rule 4 |
| A2 | Unauthorized approach → one local warning + one evaluation candidate, shared correlation id, never a fake CRITICAL | design audit request and T1 brief §E (2026-10-04); `pop_alerts` clarification |
| A3 | The local warning does not wait for Gemini | T1 brief §2 |
| A4 | Evidence failure fails only the cloud evaluation, never the local warning | T2 brief §4 |
| A5 | No fallback from private evidence to the legacy parking bundle | T2 implementation message, point 5 |
| A6 | `UNKNOWN_INSIDE` produces no event and is a coverage limit, not "safe" | T1 clarification message |
| A7 | Under one second is an unverified target; measuring it is integration acceptance | H1 delta message, point 3 |
| A8 | Audio plays only for parking CRITICAL in the existing worker path | committed behaviour `9c4515e`, `c8d475b` |
| A9 | Phases: core contracts → coordinator → runtime/mode → private prompt → real gesture and device test | PM message "revisi rencana T3" (2026-10-04) |
| A10 | Acceptance items 1–7 in §5 (marked *PM*) | PM message "revisi rencana T3" (2026-10-04) |
| A11 | Decisions O1–O6 in §3.2 | PM message "catat keputusan PM O1–O6" (2026-10-04) |
| A12 | Prepare-then-publish candidates, session-generation-safe lifecycle incl. `VideoService`, same-event audio policy, explicit strategy result, deterministic acceptance 15–21 | PM message "revisi desain T3" (2026-10-04) |
| A13 | Candidate token, Start/Stop rules (immediate invalidation, cleanup may wait for a running in-memory start, no old-session component after cleanup, late start never disturbs a new session), authoritative resync, explicit `PlanUnavailable` mapping, cleanup without observation, acceptance 23–29 and two-stage 17 | PM message "revisi desain T3 berdasarkan review rev 5" (2026-10-04) |
| A14 | Fixes for the two rev 6 blockers: publish reports `EXPIRED` even after a sweep; resync keeps working samples of episodes whose candidate is still `PREPARING` | PM/reviewer verdict "BLOCKED oleh dua ketidaksesuaian dalam rev 6" (2026-10-04) |
| A15 | Design verdict PASS WITH LIMITATIONS; acceptance 24 and 30 wording finalised; **only T3a1 authorized for implementation** | PM message "finalisasi rencana dan implementasi T3a1" (2026-10-04), verdict reported by the owner |

### 3.2 PM decisions O1–O6

Received in the PM message "catat keputusan PM O1–O6" (2026-10-04). Directions, not
test results. Original text:

> **O1 — mode privat tanpa gesture nyata.** Integrasi boleh dibangun dan diuji dengan
> fake. Mode privat default nonaktif. Aktivasi perangkat nyata menunggu implementasi
> gesture atau izin owner khusus untuk pengujian tanpa gesture. Konfigurasi valid saja
> belum merupakan izin uji nyata.
>
> **O2 — pemilihan mode.** Gunakan pilihan mode eksplisit. Jika mode privat dipilih
> tetapi konfigurasinya invalid, jalur kandidat privat nonaktif; jangan beralih otomatis
> ke trigger parkiran lama. Video tetap berjalan. Jalur lama tersedia ketika mode legacy
> dipilih secara eksplisit. EventFrameBuffer tetap diisi untuk kompatibilitas jalur lain.
>
> **O3 — saluran audio.** Gunakan AudioAlertGate dengan satu pemilik perangkat. Klip
> lokal terpisah; slot first-wins dan cooldown bersama dipertahankan untuk versi awal.
> Event lain dalam cooldown dapat dibuang: catat alasannya dan uji perilakunya. Satu
> event menghasilkan paling banyak satu playback, bukan jaminan pasti berbunyi.
>
> **O4 — prompt privat.** Dispatch kandidat privat nonaktif sampai prompt privat
> tersedia pada fase T3d. Prompt harus menjelaskan bukti perjalanan dari crossing sampai
> pendekatan, tanpa menganggap akses tanpa gesture sebagai bukti pencurian. Peringatan
> lokal tidak menunggu fase prompt.
>
> **O5 — klip lokal.** Gunakan WAV lokal terpisah melalui
> SENTINEL_AUDIO_LOCAL_ALERT_FILE, di luar repo. Jangan memakai klip CRITICAL sebagai
> fallback. Jika klip lokal tidak dikonfigurasi atau invalid, audio lokal nonaktif
> dengan diagnostik yang jelas. Pembuatan aset dan pemutaran nyata menjadi task terpisah.
>
> **O6 — expiry kandidat.** Pertahankan TTL kandidat 2 detik dan aturan satu evaluasi
> in-flight untuk versi awal. Kandidat expired tidak dikirim, tanpa retry atau
> perpanjangan antrean otomatis. Peringatan lokal tetap independen. Catat expiry dan
> keterbatasan cakupan evaluasi.

Implementation consequences (implementer proposal, needs review):
- `SENTINEL_PARKING_MODE` = `legacy` (default when unset) | `private`; any other value
  → parking candidate path off with a generic warning.
- Before T3d, private mode builds the worker with `enabled=False` (it never claims;
  verified `evaluation_worker.py` `_run`/`process_one`), so private candidates expire
  after 2 s and nothing is dispatched; the worker still drives the gate lifecycle.
- From T3d, private dispatch is enabled only when the selected transport has the
  private prompt.

## 4. Design

### 4.1 Baseline facts before T3a1 (source at `7c41672`)

- Trigger: `_issue_locked` queues the alert **and** a claimable candidate in one step;
  `_expire_locked(self._clock())` runs only inside `observe`, `pop_alerts`,
  `wait_for_candidate`, `claim_next_candidate` and `get_snapshot`; `start_session`
  resets unconditionally and bumps `_session_generation`; `EpisodeState` is
  `ENTERED | AUTHORIZED | TRIGGERED`; `EpisodeView` has no crossing time or revision.
- Sampler: sealed evidence and failure records are keyed by `event_id` only;
  `seal` rejects an `event_id` already sealed or failed; `release(event_id)` has no
  session or token check and leaves no failure record; TTL expiry records
  `EvidenceFailure.EXPIRED`; failure records are bounded (`max_failure_records` 32,
  oldest evicted, counted); `_expire_locked` runs inside every public method
  (`offer_frame`, episode methods, `seal`, `plan_for`, `wait_for_bundle`,
  `failure_for`, `get_snapshot`) — there is no timer thread.
- `EvidenceFailure` = `CROSSING_ANCHOR_MISSING | APPROACH_ANCHOR_MISSING |
  INSUFFICIENT_JOURNEY | CAPACITY | EXPIRED | UNKNOWN_EPISODE`.
- Worker: strategy lookup → `_current` → budget reserve → strategy → bundle; an early
  return after a claim reaches `trigger.fail` in a `finally` block. `_run` sleeps when
  `enabled=False`; while a synchronous dispatch is in HTTP the worker thread makes no
  trigger calls.
- Gate: one clip; an event is remembered **at grant** ("attempt spent even if it fails").
- `VideoService`: `_atomic_reserve` sets `_session_id`, `_released=False` under `_lock`,
  then starts sinks outside `_lock` (only the worker call re-checks); `stop()` sets
  `STOPPING` under `_lock`, then calls `_release_resources()` outside `_lock`, which sets
  `_released=True` under `_lock` first, joins, releases, then ends sinks.

### 4.2 Candidate token and preparation (as implemented in T3a1)

Identity: `CandidateToken(session_generation, serial, episode_instance)`, minted by the
trigger in `_issue_locked`. `serial` and `episode_instance` come from one process-wide
counter that is never reset or reused; `episode_instance` is assigned when an episode
starts (crossing), so an `episode_id` may repeat but an instance never does. The token is
carried by `LocalProximityAlert.token` and `PrivateParkingCandidate.token` — never inside
`ProximityEvidence`, so it never reaches a provider payload. `event_id` stays the
correlation id for logs, audio and provider; the token is the authority for state
changes. A live episode never shares its id (a colliding minted id is replaced, bounded).

States: `PREPARING → PUBLISHED → CLAIMED → FINISHED`; `PREPARING → ABANDONED | EXPIRED
| REJECTED_FULL`; `PUBLISHED → EXPIRED`; a token from another session generation is
`STALE`. No retry from any state. Expiry is checked with the injected control clock
inside the trigger lock by `publish_candidate` and `abandon_candidate` before any sweep;
a closed preparing candidate leaves a bounded tombstone (4 × `max_candidates`, replaced
on session reset), so a late call reports `EXPIRED` while the tombstone is kept and
`UNKNOWN` after eviction. An expired candidate is never published.

Trigger API (implemented):

```
publish_candidate(token) -> CandidateOutcome      # PUBLISHED | EXPIRED | ABANDONED | REJECTED_FULL | STALE | UNKNOWN
abandon_candidate(token) -> CandidateOutcome      # ABANDONED, or EXPIRED past the deadline
claim_next_candidate(now) -> PrivateParkingCandidate | None   # published only; sets (pending_event_id, pending_token)
complete(event_id, now, *, token) / fail(event_id, now, *, token) -> bool
    # both event_id AND token must match the claim; an id-only call is refused (no fallback);
    # only the entry with that token is removed; published expiry spares only the claimed token
pop_alerts() -> tuple[LocalProximityAlert, ...]  # each alert carries its token
pop_markers() -> MarkerBatch                     # see §4.4
get_snapshot().pending_token                     # identity of the claimed candidate
```

Sampler API (implemented; `token` is keyword-only):

```
seal(session_id, episode_id, event_id, triggered_at, *, token) -> SealResult
    # validates BEFORE any mutation: token type, current marker generation,
    # token.episode_instance and the (episode_id, instance) pair; a mismatch returns
    # STALE_TOKEN (never recorded); an unverifiable tokened call returns
    # UNKNOWN_EPISODE without recording
release(event_id, *, token) -> bool              # only the sealing token; leaves a RELEASED tombstone
plan_for(event_id, *, token) / failure_for(event_id, *, token)
plan_for_candidate(candidate) -> FrameSelectionPlan | PlanUnavailable   # worker strategy (§4.5)
apply_markers(batch) -> bool                      # §4.4
offer(frame)                                      # alias of offer_frame (frame-sink name)
```

Coordinator sequence per popped alert (T3b; detector thread, no trigger lock held). It
must pass each alert's OWN `(event_id, episode_id, token)` triple, never mixed with
another alert's (lookup by `event_id` remains in `wait_for_bundle` and failure records):

```
gate.offer_local(alert.event_id, alert.source_session_id)                     # independent
result = sampler.seal(alert.source_session_id, alert.episode_id, alert.event_id,
                      alert.triggered_at, token=alert.token)
if result.evidence is not None:
    if trigger.publish_candidate(alert.token) is not CandidateOutcome.PUBLISHED:
        sampler.release(alert.event_id, token=alert.token)
else:
    trigger.abandon_candidate(alert.token)
```

Worker identity (implemented): `_PendingEvaluation.candidate_token`; for a candidate with
a `token` the worker calls `complete/fail(event_id, now, token=token)` and requires
`snapshot.pending_token == token` in `_current` and callback acceptance; candidates
without a token (legacy, retail) keep the id-only calls.

### 4.3 Start/Stop lifecycle (T3a2; PM decision of the "rev 5 review" message + preflight delta)

Rules: invalidation is immediate; cleanup may wait for an in-memory start that is
already running; after cleanup no component is active in the old session; a late
start result never disturbs a newer session.

Preflight findings (T3a2, 2026-10-08) that the rev 5 pseudocode did not cover:
- L1: the capture thread's "clean stop" path set `IDLE` itself while `stop()` was still
  releasing resources and ending sinks; a new session could be reserved in that window
  and `stop()`'s final write then overwrote the new session's state.
- L2: when EOF/an error had already started cleanup, a concurrent `stop()` returned at
  once (idempotent early return) before that cleanup had ended the sinks.
- L5: after a join timeout (3 s) a new reservation cleared the shared `_stop_event`; the
  old capture thread could resume, label its frames with the new session id, and run
  EOF/error cleanup that released the new session's resources.
- Webcam open had no stop check, so a `stop()` during a slow open could be revived into
  `RUNNING`; a file open could be revived the same way after its token check.

Design (all in `video_service.py`):
- One `_CaptureSession` per reservation: `session_id`, token, its own `stop_event`,
  `released` flag (guarded by `_lock`), its own `cap`/`thread`/`upload_path`/timing, and
  a `cleanup_done` event. The capture thread receives its session and uses only it:
  frames carry that session's id, the stop check is that session's event, and the
  shared `_stop_event` is no longer read by capture loops.
- Invalidation (`_lock`, O(1)): `released = True`, set the session's `stop_event`, and —
  if it is still current — clear `_session_id` and set the shared `_stop_event` (used by
  `wait_for_packet`). The first caller to invalidate becomes the cleanup runner; a later
  caller waits for that attempt outside every lock, bounded by
  `cleanup_wait_timeout_seconds` (logged if exceeded). The capture thread never waits.
- Cleanup attempt vs cleanup success (review delta 1): `cleanup_done` means an attempt
  ended; success (`cleanup_ok is True`) is computed from facts — capture released,
  upload deleted, no sink left to end, no unexpected failure. A failing
  `cap.release()`, upload deletion or `sink.end_session()` keeps that step pending, is
  logged, and never stops the other steps.
- Full pass (no `_lock`): close timing; join the thread if it was started and is not the
  caller (bounded by `capture_join_timeout_seconds`); if the capture thread may still be
  inside `read()`, the capture and upload release is **deferred** (never released
  concurrently with a read); clear the packet slot only for this session; end every
  pending sink under `_lifecycle_lock`.
- Handoff of a deferred release (review delta 2): the runner's check "has the capture
  thread left?" and its release of `cleanup_running` happen in ONE `_lock` section, and
  `_on_capture_exit()` sets `capture_exited` and checks for an unowned deferred release
  under the same lock. So exactly one owner finishes the release: the runner (thread
  left before that section) or the exiting capture thread (thread left after it). The
  release itself runs outside `_lock`, at most once (`cap_released`), and never while
  the capture may still be reading. No automatic retry.
- State is derived, never assumed (`_finalize_locked`, current session only): attempt
  running or release deferred → `ERROR` ("cleanup has not finished" / "waits for it to
  exit"); attempt failed → `ERROR` ("cleanup failed; Stop retries it"); succeeded and
  stopped or EOF → `IDLE`; succeeded after a capture/open error → `ERROR` with that
  error. A new session needs `IDLE`, so B is refused until A's cleanup succeeded.
- Recovery after `ERROR`: each explicit `stop()` with no runner active resumes the
  cleanup once per call (manual, never automatic) when it failed (`cleanup_ok False`) or
  when a release is still deferred although the capture thread already left
  (`cleanup_ok None`); only still-pending steps run. A capture thread that never leaves
  `read()` keeps the service in `ERROR` until the process restarts.
- Start: reservation under `_lock`, then the sink fan-out under `_lifecycle_lock` with a
  per-call alive check. Attaching a capture and starting its thread happen under `_lock`
  only while the session is current and unreleased; otherwise the opener releases the
  capture itself and nothing is revived. Capture/open errors set a transitional
  `STOPPING`; the final `ERROR` is written when the cleanup attempt ended.

```
_atomic_reserve():
    with _lock: require IDLE; session = _CaptureSession(new ids); _current = session; STARTING
    with _lifecycle_lock:                         # order: _lifecycle_lock → _lock
        for sink in (buffer, detector, scene, worker):
            with _lock: alive = (_current is session and not session.released)
            if not alive: break
            sink.start_session(session_id)        # in-memory only

stop():
    with _lock: if IDLE: return; STOPPING; _start_token = None; session.stop_requested = True
    _release_session(session)   # first invalidator → full pass; failed before → retry once;
                                # otherwise wait (bounded) for the running attempt
    with _lock: _finalize_locked(session)         # IDLE only after a successful cleanup

capture EOF / error / open error:
    with _lock: if released or not current: return; record end_reason; STOPPING
    _release_session(session)                     # the attempt finalizes IDLE or ERROR

runner handoff (end of each pass, one _lock section):
    if deferred and capture_exited: keep ownership, deferred = False → capture-only pass
    else: cleanup_running = False; cleanup_ok = None if deferred else success; finalize

capture thread exit (same _lock):
    capture_exited = True; take over if a release is deferred and no runner owns it
    capture-only pass (release capture + upload) outside the lock, then the handoff
```

Lock order: `_lifecycle_lock` → `_lock` (brief) → each service lock; `_packet_cond` is
never taken with `_lock` held. `_lock` is never held while acquiring `_lifecycle_lock`,
and neither lock is held during a join, capture release, file deletion, HTTP or device
call; a thread `start()` happens under `_lock` (it does not wait for the new thread's
work). Session B can only be reserved after A is `IDLE`, i.e. after A's cleanup
succeeded; a waiter whose bounded wait expires leaves `ERROR`, never `IDLE`, and A's
later finalization never writes another session's state.

### 4.4 Authoritative resync of working samples

- Trigger keeps a per-session `marker_seq` and a per-episode `revision` (bumped on every
  state change). `EpisodeMarker(seq, kind, episode_id, revision, crossed_in_at, token,
  keep_samples, episode_instance)`; kinds: `EPISODE_ENTERED`, `EPISODE_AUTHORIZED`,
  `EPISODE_TRIGGERED`, `EPISODE_ENDED`, `CANDIDATE_CLOSED` (preparing candidate closed
  without publication), `CANDIDATE_EXPIRED`, `CANDIDATE_FINISHED` (with the token). The
  batch carries `session_id` and `session_generation`. Every marker names its episode
  instance; the sampler acts only when `(episode_id, episode_instance)` matches, so a late
  marker for an old instance never touches a new one with the same id.
- Marker effects on working samples: `ENTERED` → begin; `AUTHORIZED`, `ENDED` → cancel;
  `TRIGGERED` → **no change** (the episode is still waiting for `seal`);
  `CANDIDATE_CLOSED` → cancel that episode's working samples if still present.
- Marker queue bounded. On overflow the trigger drops the queue and sets
  `resync_required`. The next `pop_markers()` returns, atomically under the trigger lock,
  `MarkerBatch(session_id, session_generation, upto_seq, markers=(), view=EvidenceView)`;
  `EvidenceView.entered` rows are `(episode_id, episode_instance, revision,
  crossed_in_at)`, `preparing` rows are `(episode_id, token)` (the token names the
  instance) and `live_tokens` lists preparing, published and claimed tokens; later
  markers have `seq > upto_seq`.
- Sampler `apply_markers(batch)` keeps `applied_seq` per session: a batch from an older
  generation, a marker with `seq <= applied_seq` and a view with
  `upto_seq <= applied_seq` are ignored and counted. Marker-managed episodes are keyed
  by instance; untokened T2 episodes by id, so the two never meet.
- Working samples are kept for episodes that still need evidence: state `ENTERED`, and
  state `TRIGGERED` whose candidate is still `PREPARING` (`preparing_token` set; the
  coordinator has not sealed yet). `AUTHORIZED`, `ENDED`, `TRIGGERED` without a
  preparing token, and unknown episodes are cancelled and their bytes released.
  Missing `ENTERED` episodes are begun from `crossed_in_at` (pre-roll permitting; else a
  later seal fails `CROSSING_ANCHOR_MISSING`); a missing `TRIGGERED` episode is not
  re-begun (its seal will fail `UNKNOWN_EPISODE`, abandon, no dispatch).
- The kept samples of a preparing episode end through the seal result (`seal` pops the
  episode on success and on failure), or — if the candidate is closed without a seal
  (expiry, abandon, alert dropped) — through `CANDIDATE_CLOSED`, the next resync view
  (no `preparing_token`), or the 300 s age backstop. Sealed evidence is untouched by
  resync; it follows the token lifecycle (§4.2) and its own TTL.
- Backstop: sampler `episode_max_age_seconds` (300 s).

### 4.5 `PlanUnavailable` mapping

Private strategy receives the claimed candidate through the worker's new
`candidate_strategies: Mapping[DomainName, (event) -> FrameSelectionPlan | PlanUnavailable]`
(`sampler.plan_for_candidate`), which takes precedence for its domain; the existing
`strategies` (3-argument legacy/retail factories) are unchanged. Mapping, using only
what the sampler records for the candidate's token:

| Sampler state for the token | Result |
|---|---|
| sealed, session and `triggered_at` match | plan |
| failure record `CROSSING_ANCHOR_MISSING`, `APPROACH_ANCHOR_MISSING`, `INSUFFICIENT_JOURNEY`, `CAPACITY`, `UNKNOWN_EPISODE` | `FAILED(<that reason>)` |
| failure record `EXPIRED` (sealed TTL) | `EXPIRED` |
| new tombstone `RELEASED` (explicit `release`) | `RELEASED` |
| sealed or record under another token, or nothing (never sealed, record evicted, session reset) | `MISSING` |

`NOT_READY` is dropped: publish happens only after a successful seal, so "not ready"
is unobservable and falls under `MISSING`. Diagnostics stay bounded: tombstones share
`max_failure_records`; worker counters are keyed by the fixed reason enum.

Worker order becomes: strategy lookup (`NO_STRATEGY`) → `_current` (`STALE_SESSION`) →
**plan** (`BUNDLE_INVALID` for an invalid plan, `EVIDENCE_UNAVAILABLE` for
`PlanUnavailable`) → budget (`BUDGET_DENIED`) → bundle. Error priority: the first
failing step is recorded and later steps are not run. Change for legacy/retail: an
invalid plan with a budget that would also deny is now recorded as `BUNDLE_INVALID`
(today `BUDGET_DENIED`), and no budget is reserved or rate-limited by it. Legacy and
retail factories are pure and always valid in current code, so normal outcomes do not
change. Every early return after a claim ends via the existing `finally` → `trigger.fail`.

### 4.6 Cleanup without observation (no timers)

There is no timer thread. Two different guarantees:
- **Not claimable after deadline** — enforced in-lock with the injected clock by
  `publish_candidate`, `claim_next_candidate` and `wait_for_candidate`, whenever they
  are called; a late publish or claim after the deadline never succeeds.
- **Bytes actually released** — only when a sampler method runs its sweep:
  `offer_frame` (every captured frame while a session runs), episode/seal/plan/bundle
  calls, `get_snapshot`, `end_session`, `close`. While video runs, frames arrive
  continuously, so release happens at the next frame. If no frames arrive and the
  session stays open, bytes stay until the next call, Stop or close; the hard cap
  `max_total_bytes` (24 MiB default) always applies. Trigger state holds no images; its
  counters update at its next call.
- Worker disabled (before T3d) or blocked in HTTP makes no trigger calls; candidates
  then expire logically and are counted at the next `observe`/`pop_*` call.

### 4.7 Audio policy for one event (unchanged from rev 5)

`offer_local` uses the gate's current session/epoch, separate local clip, shared slot
and cooldown; an event is remembered only at grant, so a pre-grant rejection of the
local offer (e.g. no clip) lets one later CRITICAL grant through, while a local grant —
even with a failed device start — blocks a later CRITICAL for that event. The CRITICAL
clip is never used for a local attempt. Open for review: whether busy/cooldown
pre-grant rejections should also let a CRITICAL through (current rule: yes).

### 4.8 Other rules (core parts implemented in T3b — `FrameSinkTee`, lock-free coordinator calls; runtime use is T3c, not implemented)

One `AudioAlertGate` owns the device; `FrameSinkTee` feeds `EventFrameBuffer` and the
sampler; no fallback to `EventFrameBuffer` evidence; the coordinator holds no lock
across calls.

## 5. Acceptance

1. *PM* — Local audio works with evaluation disabled and honours fault, cooldown,
   Stop/new session.
2. *PM* — Invalid private config: private candidate path off, video keeps running, no
   automatic switch to legacy trigger or evidence, generic warning (O2).
3. *PM* — Missing evidence, exhausted budget/quota, expired candidates or a blocked
   HTTP dispatch never delay or suppress the local warning path.
4. *PM* — Lifecycle markers lost to capacity never leave stale episodes or samples.
5. *PM* — A pending local offer from an old session is rejected, including after the
   gate epoch changed.
6. *PM* — Legacy parking tests stay green; `EventFrameBuffer` stays built and fed.
7. *PM* — Local and CRITICAL clips are never swapped.
8. Mode unset or `legacy` → behaviour identical to today; private never on by default.
9. Private mode: at most one local offer per unauthorized episode; none when
   authorized. Before T3d zero dispatches; from T3d at most one dispatch per episode,
   no retry, bundle passes `_valid_bundle`.
10. Local offer in the same `observe` call as the alert; device latency measured in T3e.
11. Stop/new session cleans trigger, sampler, coordinator, tee, worker and gate.
12. No images, secrets, config values or provider bodies in logs.
13. Another event inside cooldown or a busy slot is dropped with its reason (O3).
14. Local clip missing/invalid → local audio off with a diagnostic; never the CRITICAL clip.
15. *PM* — Worker claim while `seal` is held: nothing claimable; after seal + publish the
    worker is woken and claims exactly that token.
16. *PM* — Stop/new session while `seal` is held; the old seal result arrives: publish
    returns `STALE`, `release(token)` frees its bytes, nothing becomes claimable.
17. *PM, two stages* — (a) a sink `start_session(A)` is held on a gate; `stop()` is
    called: `_released` is set immediately, remaining starts for A are skipped, and
    `stop()` waits on `_lifecycle_lock` without holding `_lock`; (b) the gate is
    released: the held start finishes, the end fan-out ends A on every started sink,
    and after `stop()` returns no component is in session A. Lock-order check: no
    path takes `_lifecycle_lock` while holding `_lock`.
18. *PM* — Late cleanup or start for A after session B started leaves every component
    in session B.
19. *PM* — A published candidate expires while another evaluation is in flight: never
    claimed or dispatched, counted, evidence released by marker or sweep, no retry.
20. *PM* — Local offer dropped for a missing local clip, then an accepted CRITICAL for
    the same event is granted once with the CRITICAL clip.
21. *PM* — Local grant (incl. failed device start) then accepted CRITICAL for the same
    event: no second grant.
22. Seal failure: abandoned and counted, no dispatch, no budget, local offer unaffected.
23. *PM* — ID reuse: a stale publish/abandon/release with an old token after the same
    `session_id` (restarted) or the same `event_id` was reused changes nothing for the
    new candidate or evidence.
24. *PM* — Seal completes after the candidate deadline (fake clock advanced inside the
    held seal): an expired candidate is never published, its evidence is released and
    nothing becomes claimable — in two variants: (a) no other trigger call before
    publish; (b) an `observe`/`pop_alerts` sweep closes the candidate first. The reason
    `EXPIRED` is guaranteed while the tombstone is kept; after tombstone eviction
    `UNKNOWN` is allowed.
25. *PM* — Overflow that drops an `AUTHORIZED` marker: after resync the episode's working
    samples are cancelled and bytes released; no seal possible for it.
26. *PM* — Interleaving observe/resync: a batch or view with `seq <= applied_seq` or an
    old generation is ignored; a newer `ENTERED → AUTHORIZED` transition is never
    overwritten by an older view.
27. *PM* — `PlanUnavailable` for each mapped reason ends through `trigger.fail`, with no
    budget reservation and zero dispatch attempts; reasons stay distinct from
    `BUNDLE_INVALID`; diagnostics bounded.
28. *PM* — Legacy/retail error priority: invalid plan + budget that would deny →
    `BUNDLE_INVALID`, no reservation; valid plan + denied budget → `BUDGET_DENIED`.
29. *PM* — Without frames or worker calls: after the deadline a candidate cannot be
    published or claimed; bytes are released at the next sampler call (shown with a
    fake clock and an explicit call), and no background timer exists.
30. *PM* — Overflow while a candidate is `PREPARING` (episode already `TRIGGERED`, seal
    not yet called): the resync keeps that episode's working samples; the following
    seal succeeds with the same frames and publish returns `PUBLISHED`. Variant: the
    candidate expires before its seal → no publication and no dispatch, and the
    temporary evidence is released (`CANDIDATE_CLOSED` or the next view); a seal may
    still be attempted.

All race tests use events/barriers with bounded timeouts and fake clocks, never sleeps.

## 6. Risks / limitations

- No gesture source until T3e: every entrant is unauthorized; private mode off by
  default; real-device use needs a gesture or specific owner permission (O1).
- `UNKNOWN_INSIDE` never warns; track ID switches can split a person or misattribute
  the motorcycle that entered with someone.
- T1/T2 thresholds uncalibrated; evidence spacing widens on long journeys.
- Shared slot/cooldown can drop another event's clip; at most one playback per event,
  no guarantee it sounds (O3).
- Evaluation coverage: 2 s TTL with one evaluation in flight → later candidates expire
  unsent, no retry (O6); before T3d no private evaluation at all (O4).
- Bytes are freed only on sampler calls; with no frames and an open session they stay
  until the next call, Stop or close (bounded by `max_total_bytes`).
- Resync re-begins `ENTERED` episodes from `crossed_in_at`; if the pre-roll no longer
  covers it, that episode's evidence fails later (`CROSSING_ANCHOR_MISSING`).
- `VideoService`, the worker order and the factory signature are shared by legacy and
  retail paths; their full suites must stay green.
- Stop can wait for a running in-memory start fan-out (bounded by those calls).
- Two frame sinks reference the same JPEG bytes; retention adds up (6 s ring + sampler).
- Each dispatched episode can cost one billed request; cost per day not estimated.

## 7. Phases

Every phase: fake tests only unless stated; target tests, full backend suite with
unique `--basetemp`, `pip check`, `git diff --check`, untracked whitespace check;
programmer report → read-only review → owner commit decision.

**T3a1 — private core contracts**
Files: `backend/app/models/private_parking.py`, `backend/app/services/private_parking_trigger.py`,
`backend/app/models/private_parking_evidence.py`, `backend/app/services/private_parking_evidence_sampler.py`,
`backend/app/services/audio_alert_service.py`, `backend/app/models/evaluation.py`,
`backend/app/services/evaluation_worker.py`, plus tests.
Scope: §4.2 token/preparation; §4.4 markers, revisions, resync view and sampler
reconcile; §4.5 token-checked sampler calls, `RELEASED` tombstone, `PlanUnavailable`,
worker order and factory signature; §4.7 `offer_local`, local clip, clip kind.
Acceptance: 4, 5, 7, 13–16 (trigger/sampler part), 19–30.
Status: **completed — core only** (implemented 2026-10-04 with deltas 1–2; reviewed PASS
WITH LIMITATIONS for the core, review Codex disampaikan melalui owner, see §8; committed on
the owner's instruction "commit T3a1", 2026-10-05). Not wired into runtime; no
coordinator, `VideoService` lifecycle or runtime/env wiring yet. See "T3a1 results" below.

**T3a1 results (agent run, 2026-10-04, HEAD `7c41672`, working tree)**

Changed: `backend/app/models/private_parking.py`, `backend/app/services/private_parking_trigger.py`,
`backend/app/models/private_parking_evidence.py`, `backend/app/services/private_parking_evidence_sampler.py`,
`backend/app/services/audio_alert_service.py`, `backend/app/models/evaluation.py`,
`backend/app/services/evaluation_worker.py`; tests `backend/tests/test_private_parking_trigger.py`,
`backend/tests/test_private_parking_evidence_sampler.py`, `backend/tests/test_audio_alert_service.py`,
`backend/tests/test_evaluation_worker.py`; new `backend/tests/test_private_parking_contracts_integration.py`
(real trigger + sampler + worker, fake client/clock/frames).

| Check | Command | Result |
|---|---|---|
| Target tests | `python -m pytest <5 test files above> -q --basetemp <unique>` | 358 passed |
| Full backend | `python -m pytest backend/tests -q --basetemp <unique>` | 1012 passed (baseline 935) |
| Dependencies | `python -m pip check` | no broken requirements |
| Diff | `git diff --check`; new file: no CR, no trailing whitespace, one final LF | clean |
| Mutations | 13 targeted mutants (tombstone, deadline, generation, publish queue, resync preparing, ENDED keep, marker seq, stale view, release token, local clip path, no-clip dedup, local session, failure category) | 13 killed |

Acceptance covered by tests: 4 (core), 5, 7, 13, 14, 15, 16, 19–30; 1 (gate part:
fault, cooldown, Stop cut). Fake tests only: no runtime wiring, no live provider, no
device. The pytest run printed two `PytestCacheWarning`s (cache directory not
writable in this environment), unrelated to the change.

Deviations from the rev 7 text (for review):
- The worker keeps `strategies` (3-argument legacy factories) unchanged and gains
  `candidate_strategies: Mapping[DomainName, (event) -> FrameSelectionPlan | PlanUnavailable]`,
  which takes precedence for its domain; legacy factories are not wrapped.
- The token lives on `LocalProximityAlert` and `PrivateParkingCandidate`, not inside
  `ProximityEvidence`, so it never reaches a provider payload.
- Sampler token parameters are keyword-only and default to `None`; the stored token
  must equal the given one, so `None` matches only untokened (T2-style) entries.
- Extra outcome `REJECTED_FULL` (candidate store full at issue). A repeated publish of
  a published token answers `PUBLISHED` from its tombstone without re-queueing.
- New threshold `marker_queue_limit` (default 64, 4–1024). New `EvidenceFailure.RELEASED`,
  `EvaluationFailure.EVIDENCE_UNAVAILABLE`, `PlanUnavailableReason`, and additive
  diagnostic fields (trigger, sampler, worker, audio snapshots).
- The gate refuses a local clip path that is lexically the CRITICAL clip path
  (`normcase(normpath)`); resolving links is left to the T3c validator.

Limitations found during T3a1:
- `wait_for_bundle` still looks up sealed evidence by `plan.event_id`; mixing is
  prevented because `plan_for_candidate` checks the token and a second seal for a held
  `event_id` is refused, and the bundle must match the plan's offsets and `triggered_at`.
- The budget's `STALE_CANDIDATE` rule compares a candidate's `triggered_at`
  (observation timeline) with the control clock. Production frames use
  `time.monotonic()` (`video_service.py`), so both share a timeline; T3c must keep it so.
- Busy/cooldown rejections of a local offer leave the event eligible for one later
  CRITICAL grant (open audio point, §4.7) — implemented as described, still for review.
- Nothing is wired: no coordinator (T3b; tests use a stand-in), `VideoService`
  lifecycle unchanged (T3a2), no runtime/env (T3c).

**T3a1 delta 1 — identity invariants (reviewer findings, PM message "delta perbaikan T3a1", 2026-10-04)**

Defects found (agent audit of every operation that changes evidence/candidate state):

| # | Defect | Where |
|---|---|---|
| D1 | A stale `seal` popped the working episode by `episode_id` before checking token/generation; after a restart with reused ids it could consume a newer episode and its JPEG references | sampler `seal` |
| D2 | The public `cancel_episode`/`end_episode` (T2, untokened) could remove a marker-managed episode by id | sampler `_remove_episode` |
| D3 | `_finish` (complete/fail) matched the claim by `event_id` only and removed every queued entry with that id; published expiry spared the claim by `event_id` | trigger `_finish`, `_expire_locked` |
| D4 | The worker contract carried only `event_id` to `complete/fail` and to its "still current" checks | worker `_PendingEvaluation`, `_current`, callback acceptance |
| D5 | `abandon_candidate` did not check the deadline | trigger |

Fixes:
- Sampler: each working episode records its trigger generation (markers/views set it;
  the public `begin_episode` leaves it `None`). `seal` validates first — a token must be
  a `CandidateToken` of the current marker generation and of the episode's generation;
  an untokened call matches only an untokened episode — and a stale call returns the new
  `EvidenceFailure.STALE_TOKEN` (never recorded) without touching episodes, JPEG
  references, counters of ownership or records. The public cancel/end refuse
  marker-managed episodes (only `apply_markers` of their generation may change them).
- Trigger: the claim is identified by `(pending_event_id, pending_token)`;
  `complete/fail(event_id, now, *, token)` succeed only when both match — an id-only call
  is refused (`finish_rejected`), no fallback; only the entry with that token is removed;
  published expiry spares only the claimed token; `pending_token` is in the snapshot.
  `abandon_candidate` reads the control clock inside the lock and records EXPIRED past
  the deadline, with or without a prior sweep.
- Worker contract delta (in T3a1's files, explained here before relying on it):
  `_PendingEvaluation.candidate_token`; when a claimed candidate carries a `token`, the
  worker calls `complete/fail(event_id, now, token=token)` and requires
  `snapshot.pending_token == token` in `_current` and in callback acceptance. Candidates
  without a token (legacy parking, retail) keep the exact id-only calls.

Evidence (agent run, 2026-10-04, HEAD `7c41672`, working tree):

| Check | Result |
|---|---|
| New regression tests | sampler: old-token seal after trigger restart and after sampler restart with reused session/episode ids leaves full state (episodes, refcounts, bytes, counters, records) identical, then the new seal succeeds; token/untokened never cross; trigger: old cleanup with reused event id after restart is refused (with token and id-only), two same-id candidates in one generation finish/expire only by token, abandon after deadline = EXPIRED with and without sweep; worker: token forwarded on complete, every early fail and deadline fail; a different `pending_token` makes the event stale; legacy calls unchanged |
| Target tests (5 files) | 373 passed |
| Full backend (`--basetemp` unique, cache provider off) | 1027 passed |
| `pip check` / `git diff --check` / new-file whitespace | clean |
| Mutations | 9 delta mutants + the 13 earlier ones: 22 killed (two delta survivors closed by the same-id tests) |

Separate from the token fixes — `AGENTS.md` diff: one new section "Errors: one attempt,
then references" (14 lines), added on the owner's direct chat request ("tambahkan rule
untuk error code, error test dan error lainnya …"), 2026-10-04. It is a working rule for
agents, not a core change and not an API decision; it should be reviewed/committed
separately from T3a1.

**T3a1 delta 2 — episode instance identity (PM message "perbaikan identitas episode T3a1", 2026-10-04)**

Defect: generation alone did not identify an episode. Within one generation an
`episode_id` can repeat (A ends, B enters with the same id), and the sampler keyed working
episodes, marker effects and resync by `episode_id`; a token issued for A could seal B, a
late A marker (`CANDIDATE_CLOSED`, `AUTHORIZED`, `ENDED`) could cancel B, and the
trigger's `keep_samples` matched preparing candidates by id.

Contract (models `private_parking.py`, trigger, sampler; no other file):
- Every episode instance gets an `episode_instance` from the trigger's never-reused
  serial counter (shared with token serials). `CandidateToken(session_generation, serial,
  episode_instance)` binds the token to that instance at issue, before any seal.
- `EpisodeMarker.episode_instance` on every marker; `EvidenceView.entered` rows are
  `(episode_id, episode_instance, revision, crossed_in_at)`; preparing rows name the
  instance through their token. ENTERED markers without an int instance are ignored.
- Sampler keys marker-managed episodes by instance (untokened T2 episodes by id, so the
  two never meet). Every marker effect and resync decision requires the pair
  (episode_id, instance) to match. `seal` validates token type, generation, instance and
  the (episode_id, instance) pair BEFORE any mutation; if the token's instance is gone and
  the id belongs to another instance (or call kind) it returns `STALE_TOKEN`; if nothing
  can be verified it returns `UNKNOWN_EPISODE` without recording (a record could block the
  rightful event). The trigger's `keep_samples` matches preparing candidates by instance.
- Trigger: a live episode never shares its id — a minted id already live is replaced
  (bounded, counted `episode_id_collisions`), otherwise the crossing is dropped (counted).
- Protection lives in the trigger and sampler themselves, not only in the T3b coordinator.

Evidence (agent run, 2026-10-04, HEAD `7c41672`, working tree):

| Check | Result |
|---|---|
| Regressions (requested 1–5) | ENTERED A → ENDED A → ENTERED B with the same id in one generation; seal B with token A → `STALE_TOKEN`, B active, bytes/refcounts/counters/records unchanged; seal B with token B succeeds with B's crossing frame; another episode's token refused both ways; late A markers (AUTHORIZED/ENDED/CANDIDATE_CLOSED/EXPIRED/FINISHED), public cancel/end and `release` with token A never cancel or release B |
| Further cases | view naming another instance drops the unlisted one; marker naming B's instance with another id changes nothing; unverifiable tokened seal records nothing; markers without instance ignored; trigger binds distinct instances to reused ids and keeps samples only for its own preparing candidate; live-id collision replaced or dropped |
| Interleaving (gate) | real trigger + sampler: an old coordinator seal with token A held on an Event while A expires and B enters with the same id → `STALE_TOKEN`, B's state unchanged, B then publishes |
| Target tests (5 files) | 385 passed |
| Full backend (`--basetemp` unique, cache provider off) | 1039 passed |
| `pip check` / `git diff --check` / new-file whitespace | clean |
| Mutations | 11 instance mutants killed (4 initial survivors closed by targeted tests); earlier sets re-run and retargeted: all killed except 2 equivalent mutants (public cancel generation guard and seal episode-generation check — unreachable now that instance keys separate tokened and untokened episodes and generation replacement drops old instances) |

Limitations (delta 2):
- `wait_for_bundle` and failure records are still found by `event_id` (token-checked
  where the caller has it); a verified token used with another candidate's `event_id`
  could still occupy that id — the coordinator must pass the alert's own event id/token pair.
- Authorization signals still name an episode by id; they are additionally bound to the
  track and to `crossed_in_at <= observed_at`, which excludes a previous instance's signal.
- The two equivalent mutants mark redundant guards kept as defence in depth.

**T3a2 — session lifecycle**
Files: `backend/app/services/video_service.py` and its tests (plus this plan).
Scope: §4.3 — per-session capture context, `_lifecycle_lock`, per-call revive check,
serialized end fan-out, invalidator-owned finalization, bounded waits, no revival of a
stopped start. Out of scope: coordinator, runtime/env, API.
Acceptance: 17, 18; deterministic gate tests for: start A held then Stop; Stop
concurrent with EOF (both orders); A's finalization never overwrites B; capture A
resuming after a join timeout; cleanup failure/timeout; old packets/resources never
owned by B; all existing video and integration tests green.
Status: **completed — lifecycle only, reviewed PASS** (implemented 2026-10-08 with review
deltas 1–2; verdict PASS stated in the owner's commit instruction, 2026-10-08; committed as
"fix: isolate video session lifecycle and cleanup"). Coordinator, runtime/env and API
are not part of T3a2.

**T3a2 results (agent run, 2026-10-08, HEAD `745cd44`, working tree; incl. review deltas 1–2)**

Changed: `backend/app/services/video_service.py`; new
`backend/tests/test_video_session_lifecycle.py`; this plan (§4.3, T3a2); `PLAN.md` status
line. No other file.

Review delta 1 (Codex findings relayed by the owner, 2026-10-08): (1) a cleanup waiter
timeout no longer opens `IDLE` — A stays invalidated in `ERROR` and B is refused until A's
cleanup succeeded, and the later finalization is consistent; (2) "attempt ended" is
separated from "cleanup succeeded" — a failing `cap.release()`, upload deletion or
`sink.end_session()` yields `ERROR`, keeps that step pending and still cleans the rest;
(3) a capture still inside `read()` after the join timeout is never released
concurrently — the release is deferred; (4) fake tests strengthened (below).

Review delta 2 (Codex finding relayed by the owner, 2026-10-08): the deferred-release
handoff was not atomic — the runner checked "thread left?" and released ownership in two
`_lock` sections, so a capture thread exiting between them saw the runner still owning
and did nothing, leaving the release unowned (`cleanup_ok None`, `ERROR` forever). Fixed
by one `_lock` section for check + ownership release (shared with `_on_capture_exit`),
and an explicit `stop()` now also resumes a deferred release whose capture thread has
left (not only `cleanup_ok False`).

Behaviour: per-session `_CaptureSession`; immediate invalidation; one cleanup runner per
attempt; derived final state (§4.3); capture threads receive their session through
`kwargs` (thread `args` unchanged) and never read the shared `_stop_event`; packets are
never published after invalidation and only the own packet is cleared;
`_lifecycle_lock` serializes sink start/end; a stop that wins during a slow open is
never revived (a failing open after it is not turned into `ERROR`); constructor keywords
`capture_join_timeout_seconds` (3 s) and `cleanup_wait_timeout_seconds` (join + 2 s),
validated finite and > 0; the file pacing wait wakes on the session's stop.

| Check | Command / method | Result |
|---|---|---|
| Lifecycle tests | `pytest backend/tests/test_video_session_lifecycle.py` (process timeout 120 s) | 25 passed, 3 consecutive runs |
| Handoff gap reproduced | the handoff tests run against the previous (non-atomic) `video_service.py`, restored byte-for-byte | `[after]` (thread exits after "left?"=False, before ownership release) and the lost-handoff Stop test fail; `[before]`/`[inside]` pass there too |
| Video + related integration | `pytest` on the 8 video/integration files (`test_video_session_lifecycle`, `test_video_service`, `test_video_api`, `test_video_event_frame_buffer`, `test_video_scene_pipeline`, `test_video_startup_timing`, `test_evaluation_runtime_integration`, `test_detector_tracker_integration`) | 193 passed |
| Full backend, run after the targeted run (`--basetemp` unique) | `pytest backend/tests -q` | 1064 passed (1039 + 25) |
| `pip check` / `git diff --check` / new-file whitespace | as listed | clean |
| Mutations (20) | alive check, end fan-out lock, session signal, loser wait, publish stop check, revival on attach, finalize current-only, no IDLE while unfinished, no IDLE after failure, failed-start ownership, failed sink stays pending, failed release stays pending, no release during a read, capture thread finishes a deferred release, runner resumes it, runner gives up ownership before checking the thread (non-atomic handoff), Stop never resumes, Stop resumes only failures, bounded join, capture ownership | 19 killed; 1 equivalent (capture ownership guard: a late capture finish can only occur while A is deferred and B is refused) |

Tests cover: start A held then Stop (two stages + lock-order check); EOF cleanup first
then Stop, and Stop first then EOF; waiter timeout while a sink is held → `ERROR`, B
refused, consistent `IDLE` after the cleanup succeeds; a late `stop()` wake never writing
B; capture A blocked in `read()` past the join timeout (late frame / EOF / error) →
`ERROR`, B refused, no release during the read, release by the capture thread on exit;
the thread leaving while the runner is still ending sinks; a failing capture release
(`ERROR`, B refused, each Stop retries once, success after the fault is gone); a sink that
raises before clearing its session (`ERROR`, other sinks ended, only that sink retried);
the runner/exit handoff paused before, inside and after the runner's handoff section
(release exactly once, never during a read, cleanup succeeds, B accepted only after
`IDLE`); a lost handoff resumed by an explicit Stop;
slow webcam/file open raced by Stop (incl. a failing open); capture error with a
concurrent Stop; invalid timeouts. The pre-delta "old implementation" comparison is no
longer meaningful and is not repeated.

Limitations:
- A capture thread that never returns from `read()` keeps the service in `ERROR` (its
  capture is intentionally not released); only a process restart clears it.
- The handoff guarantee is shown with an instrumented `_lock` in fakes at three pause
  points; it is not a proof for every interleaving of a real OpenCV backend.
- A sink whose `end_session` keeps failing keeps the service in `ERROR`; each Stop
  retries once. Nothing retries automatically.
- After a bounded wait expires the caller returns while the other attempt continues;
  the state stays `ERROR` until that attempt finalizes.
- The `ERROR` text is the only external signal of a pending/failed cleanup (no new API
  field; API changes are out of scope).
- Only fakes are tested; the OpenCV backend behaviour of a blocked `read()` is not.

**T3b — coordinator**
Files: new `backend/app/services/private_parking_coordinator.py` (coordinator +
`FrameSinkTee`) and its tests.
Scope: §4.2 alert sequence, marker/resync application, token release, generations,
error isolation.
Acceptance: 3 (fake blocking dispatcher), 4, 10 (fake), 15, 16, 23–26, 30 (end to end).
Obligation carried from T3a1 (lookup limitation): the sampler still finds bundles and
failure records by `event_id`, so the coordinator must pass each alert's OWN
`(event_id, episode_id, token)` triple — unchanged and never mixed with another alert's —
to `seal`, `publish_candidate`, `abandon_candidate` and `release`; a T3b test must show
that mixing two alerts' fields is refused or has no effect on the other candidate.

PM decisions for T3b (PM message "keputusan K1–K7", 2026-10-08): K1 (a) bind `event_id`
into the candidate token and validate it in seal/release/plan; K2 seal and publish even
when the worker is disabled; K3 gate optional, audio failures logged sanitized, evidence
processing continues; K4 wrappers never close their dependencies (runtime owns shutdown in
T3c), `close()` only stops new work and is idempotent; K5 no coordinator generation —
token and dependency session checks, proven by Stop/new-session interleaving tests; K6
order: trigger observe → drain/apply markers → alerts (audio before seal/publish) →
drain markers once more, no unbounded draining; K7 replace the `prepare()` stand-in with
the real coordinator. Added: a marker drain/apply failure must hold publication of the
affected candidates until synchronization is proven again (local audio may still run).

Scope expansion recorded before editing (K1, K7):
- `backend/app/models/private_parking.py` — `CandidateToken.event_id`.
- `backend/app/services/private_parking_trigger.py` — token producer (mints the token with
  its `event_id`) and a bounded `request_resync()` so the coordinator can ask for an
  authoritative view after a failed marker drain/apply.
- `backend/app/services/private_parking_evidence_sampler.py` — validate token ↔ `event_id`
  in seal, release, plan and failure lookups.
- `backend/tests/test_private_parking_trigger.py`,
  `backend/tests/test_private_parking_evidence_sampler.py` — tests for the bound token.
- `backend/tests/test_private_parking_contracts_integration.py` — `prepare()` replaced by
  the coordinator (K7).
- New: `backend/app/services/private_parking_coordinator.py`,
  `backend/tests/test_private_parking_coordinator.py`.

Status: **completed — core only** (implemented 2026-10-08, deltas 2026-10-09/11;
independent review PASS on the overflow delta; committed on the PM instruction "penutupan dan
commit core T3b", 2026-10-11). Not wired into runtime — runtime/env, API, mode selection
and prompt stay in T3c/T3d; the two T3c prerequisites below remain open.

**T3b results (agent run, 2026-10-08, HEAD `a23fe62`, working tree)**
- K1: `CandidateToken.event_id` (default `None` for older callers); the trigger mints it
  with the alert's `event_id`; the sampler refuses a token naming another event in
  `seal` (`STALE_TOKEN`, checked before any mutation, nothing recorded), `release`,
  `plan_for`, `failure_for` and `plan_for_candidate` (`PlanUnavailable(MISSING)`).
  Test: `test_mixed_event_and_token_are_refused_without_touching_the_other_event`;
  "forged" tokens (right event, wrong instance) keep the instance checks covered.
- Trigger: `request_resync()` — session-scoped, bounded (clears the marker queue; the
  next `pop_markers` returns one authoritative view), counted `resyncs_requested`.
- `PrivateParkingCoordinator` (detector parking-trigger slot): K6 order per observation —
  `trigger.observe` → one marker drain/apply → `pop_alerts` → per alert: local audio offer
  first (gate optional, K3), then `seal` with the alert's own triple → `publish`, or
  `abandon` on an evidence failure, or `release` with the own token when publication did
  not succeed → one more drain/apply. Every dependency call is isolated; logs carry only
  step name and exception type. No generation of its own (K5); `close()` idempotent,
  stops new work, never closes a dependency (K4); `end_session` forwarded even after close.
- Marker sync failure (drain or apply raises, or apply returns False): the coordinator
  requests a resync and **holds** new alerts (audio still offered; no seal/publish) until
  a batch carrying a view was applied; held alerts are bounded (32, oldest dropped →
  its candidate expires unpublished) and voided by a new session. A held candidate that
  expires is never published; its seal fails (`UNKNOWN_EPISODE`) and the publish/abandon
  returns `EXPIRED`.
- `FrameSinkTee` (VideoService event-frame-buffer slot): forwards `offer`/`start_session`
  in order and `end_session` in reverse (even after close); one sink's exception never
  stops the others. It does no I/O, HTTP or inference, but it is **not** claimed
  non-blocking: each call takes the sinks' own (bounded, in-memory) locks and can wait for
  them.
- K7: `test_private_parking_contracts_integration.py` now drives the real coordinator
  (the `prepare()` stand-in is gone); held-seal cases run the final observation in a
  thread; late/stale calls use the coordinator's `_prepare` with a recorded alert.
- Real-core acceptance added: audio before seal (10/15); worker claim while the seal is
  held finds nothing until publication; Stop while the seal is held → STALE, nothing
  claimable, `held_bytes` 0 (16); restart with the same id between seal and publish →
  sealed evidence released (23); old tokens never touch a new candidate (24); seal past
  the deadline → EXPIRED and released; blocked dispatch never delays the local warning
  or the next candidate's evidence (3, fake blocking client); K2 without any worker;
  overflow/resync cases 25, 26, 30; marker-apply failure → hold → view → publish, and the
  expiry variant → never published, samples gone; reused episode id with a late old seal.
- Tests (serial, fakes only, unique `--basetemp`): coordinator 19, integration 17, all
  `test_private_parking_*` 231, evaluation worker 119, audio 62, video 100 — all passed;
  full backend suite **1091 passed**, 1 Starlette `DeprecationWarning`; `pip check`
  clean; `git diff --check` clean; new files end with a newline, no trailing whitespace.
- Limitations: not wired into runtime at that time (historical; T3c since completed, fake integration verified); no real video, device, speaker or provider
  request was run; mutation testing not run for T3b; the tee's latency depends on the
  sinks' locks; held alerts are lost on overflow beyond 32 (their candidates expire).

**T3b review delta 1 (Codex findings via PM, 2026-10-09; agent run, HEAD `a23fe62`, working tree)**
- Tee cleanup failure: `FrameSinkTee.end_session` still tries every sink, then raises
  `SessionCleanupError` (sanitized: count only, `from None`) when any sink did not end,
  so VideoService keeps the tee pending → ERROR, `cleanup_ok=False`, a new session is
  refused. The tee remembers, per session id, which sinks failed (the delta-1 bound evicted
  the oldest entry — superseded by delta 2: obligations are never evicted), so the next explicit Stop ends only
  the failed sinks — sinks that already ended are not ended again. `pending_cleanups()`
  for diagnostics. Test through VideoService: a sink fails before ending A → ERROR,
  `cleanup_ok=False`, B refused; the next Stop ends only that sink → IDLE, B accepted.
- Coordinator `end_session`: no longer swallows a trigger failure — it raises
  `SessionCleanupError` (sanitized); a second call retries the trigger.
  **Gap (outside the assigned files, for T3c):** `DetectorService.end_session` clears its
  active session id before calling the parking-trigger slot, so a VideoService retry
  returns early and never re-calls the coordinator; the failure is reported once (ERROR)
  but the retry reports success. The trigger is reset by its next `start_session`, so no
  candidate survives into B, but the detector should keep the slot pending. Fix belongs
  in `detector_service.py` (T3c or a separate authorization).
- Resync after "applied, then exception": a view now takes its own newer `seq`
  (`pop_markers` advances the marker seq when it issues a view; `upto_seq` = that seq), so
  a requested view always supersedes every batch popped before it, even one the sampler
  already applied, without waiting for a new episode transition. The sampler's ordering
  rule is unchanged: a replayed older view (`upto_seq <= applied_seq`) is refused
  (`views_stale`), replayed markers are skipped (`markers_stale`), older generations and
  old-session tokens stay refused. Trigger test updated (overflow view `upto_seq` 6 → 7).
  Real-core test: the sampler applies a batch and then the resync view, each followed by
  an exception → the alert stays held; the next view (only the TRIGGERED marker emitted
  in between) proves sync → the candidate is published and claimable; replaying the old
  view/batch leaves episodes, frames, bytes and sealed evidence unchanged.
  Mutation (agent run, scratch script, source restored): without the seq advance this
  test fails.
- Tests (serial, fakes only, unique `--basetemp`): coordinator 22, integration 18,
  trigger 105, sampler 90, evaluation worker 119, audio 62, video 100 — all passed; full
  backend suite **1095 passed**, 1 Starlette `DeprecationWarning`; `pip check` clean;
  `git diff --check` clean; new files end with a newline, no trailing whitespace.

**T3b review delta 2 — tee retry bound (PM message "perbaikan batas retry T3b", 2026-10-11; agent run, HEAD `a23fe62`, working tree)**
Finding (self-review, confirmed by the PM): the delta-1 retry record evicted the oldest
pending session at 8 entries, and `start_session` with the same id cleared it, so an
unfinished cleanup obligation could disappear from the tee. Also D1/D2 doc corrections.
- `FrameSinkTee` contract (public use: VideoService event-frame-buffer slot, one session
  at a time — `start_session` → `offer`… → `end_session`, `end_session` repeated until it
  stops raising):
  - Obligations = the ACTIVE session (started, not ended) + sessions with sinks still to
    end. Bounded by `max_pending_cleanups` (default 8, valid 1–64); **never evicted**.
  - `start_session` reserves the slot the session's cleanup may need, so a failed end
    always fits. Overflow: when another session is still active, when the same id still
    has cleanup pending, or when all slots are taken, `start_session` raises
    `FrameSinkAdmissionError` (sanitized reason, no ids) **before any sink is called**,
    and counts `starts_refused_active` / `_pending` / `_capacity`. Ignored after close().
  - `end_session` for an active or pending session always runs (also at full capacity
    and after close()); it tries every sink still to end, keeps exactly the failed ones
    pending and raises `SessionCleanupError`; a later call retries only those. A
    successful cleanup frees the slot. `end_session` for a session the tee never started
    is a no-op (no obligation).
  - Start/end (with their sink calls) are serialized by a tee lifecycle lock, so two
    cleanups of one session never race; `offer` does not take it.
  - `get_diagnostics()` (counts only): `active_sessions`, `pending_sessions`,
    `pending_sink_ends`, `free_slots`, `capacity`, refusal counters, `end_failures`,
    `cleanups_completed`, `closed`.
- VideoService behaviour (unchanged file): it treats the tee as an optional sink, so a
  refused tee start is logged ("Unable to start event frame buffer session") and the
  video session continues without buffer/evidence for that session; no sink was
  touched. VideoService itself refuses a new session while a cleanup is pending (ERROR),
  so in normal use the tee holds at most one obligation. Making a tee refusal fail the
  video start would need a VideoService change (T3c decision).
- Tests (`test_private_parking_coordinator.py`, 27): full capacity keeps every old
  obligation (all still end, nothing evicted); an extra session — capacity full, same id
  pending, another active — is refused before any sink starts; a retry at full capacity
  never re-ends a sink that already ended; a successful cleanup frees its slot;
  diagnostics count every open obligation without ids; existing tee tests now start a
  session before ending it (an unknown session is a no-op); VideoService slot tests
  unchanged and passing. Mutations (agent run, scratch script, source restored): no
  capacity check → 3 failed; retry ends every sink → 3 failed; refusal after touching
  sinks → 1 failed.
- Tests (serial, fakes only, unique `--basetemp`): coordinator 27, integration 18 (incl.
  the resync tests of delta 1), trigger 105, sampler 90, evaluation worker 119, audio 62,
  video 100 — all passed; full backend suite **1100 passed**, 1 Starlette
  `DeprecationWarning`; `pip check` clean; `git diff --check` clean; new files end with
  a newline, no trailing whitespace.
- Limitations: the tee's latency still depends on the sinks' locks (start/end also wait
  on the tee lifecycle lock); a refused tee start does not stop the video (see above).

**T3c0 — lifecycle prerequisites for T3c wiring** (design rev 4/5; PM messages
"rencana prasyarat T3c", "revisi rencana prasyarat T3c", "revisi desain T3c0 dari rev 2" and
"revisi terarah T3c0 rev 3", 2026-10-11; baseline `ec9b8cb`). **Status: completed —
T3c0.1 committed in `e50d9e2`, T3c0.2 committed in `d479357`.** The paragraphs below keep
the design history as written at each step.

Design review: **PASS WITH LIMITATIONS** (independent reviewer, Codex via PM message
"finalisasi rencana dan implementasi T3c0.1", 2026-10-11), with three notes applied in
rev 4 final: (1) the partial-start test expects cleanup only for attempted
dependencies; (2) the Stop guarantee is mutual exclusion of delivery and invalidation,
not a proven wait bound; (3) the delivery-versus-lifecycle test proves that end A is
already waiting for the delivery before start B runs. Authorization (historical, as of
that message): **only T3c0.1** was authorized; T3c0.2, T3c wiring, prompt, gesture and
frontend were not. T3c0.2 was authorized later (rev 5 final) and completed in `d479357`;
(Historical, as of that step: T3c wiring was not yet authorized; it was authorized later and is now completed, fake integration verified.) Prompt, gesture and frontend remain unauthorized.

Rev 4 delta: detector frame/job binding to a generation with an atomic
delivery-versus-invalidation point (closes the detector blocker); standalone T3c0.1
acceptance; T3c0.2 acceptance for immediate EOF/error, Stop at the
`thread.start`/RUNNING boundary and no RUNNING after finalization; lease opt-in boundary
stated; TQ2–TQ4 kept as reviewed answers.

Rev 5 (plan only, 2026-10-11, baseline `e50d9e2`; PM message "fase berikutnya"): T3c0.2
aligned with the lease contract implemented and committed in T3c0.1 — see "T3c0.2
alignment with the implemented T3c0.1 contract". Runtime wiring is not part of it.

Rev 5 final (PM message "finalisasi rencana dan implementasi T3c0.2", 2026-10-11):
**T3c0.2 authorized** (since completed in `d479357`); T3c wiring NOT authorized (historical, at that step). The
DetectorService cleanup retry is done (T3c0.1, commit `e50d9e2`). Added rule A8 (sanitized lifecycle/rollback logging)
and acceptance 17 (synthetic id/token/path never logged or returned).

PM decisions (recorded 2026-10-11, PM message "revisi desain T3c0 dari rev 2"):
- **O-P1 — decided:** in private mode both the detector and the tee are REQUIRED. A
  private start is refused when either core component does not start. This guarantees
  only that the components started — not that anything is detected or played back.
- **O-P2 — decided:** a component refusal leaves the video in ERROR until an explicit
  Stop; the HTTP answer for a component refusal is 503. A session conflict keeps the
  existing conflict contract (409 "not IDLE"). New admission opens only after a cleanup
  that succeeded.
- **O-P3 — decided:** `backend/app/api/video.py` is in scope for the refusal mapping and
  for token-bound rollback on EVERY upload rollback path, legacy included. Rolling back
  upload A never stops B.

Facts at `ec9b8cb` (source read, not run):
- F1 `DetectorService.end_session` clears `_active_session_id` under `_cond`, then calls
  result consumer → tracker → parking trigger without isolation (the first exception
  skips the rest). The first failure is reported (VideoService keeps "detector" pending →
  ERROR). The defect is the retry: the detector already forgot the session, returns
  early, VideoService counts the step as done and can write IDLE although the
  coordinator/trigger never ended. `start_session` implicitly ends a different previous
  session the same way; `close()` calls dependencies with no isolation before `join`.
- F2 `VideoService._atomic_reserve` (used by `claim_start` and `start_webcam`) reserves
  under `_lock`, then starts sinks under `_lifecycle_lock` (non-reentrant) before any
  capture is opened, ignoring every start failure. `_end_sinks` (cleanup runner) takes
  `_lifecycle_lock` too.
- F3 Upload route order: `claim_start()` (reservation + sink starts) → MIME/extension
  checks → chunked write → handler PROBE (`api.video.cv2.VideoCapture`) → `start_file`
  (PLAYBACK capture, `video_service.cv2.VideoCapture`). Every failure path rolls back with
  a global `video_service.stop()` without a token check (A can stop B).
- F4 `_attach` writes RUNNING before `_start_capture` calls `thread.start()`; if
  `thread.start()` raises, the exception escapes with the state RUNNING, the capture
  attached and no cleanup.
- F5 Sinks key their obligations by session id only; ids are `uuid4` strings in
  VideoService, but nothing in the wrappers stops a caller from reusing an id, and a
  late `end_session(id)` cannot be told apart from an end for a newer session with the
  same id.
- F6 `FrameSinkTee.start_session` and `PrivateParkingCoordinator.start_session` return
  silently after `close()` (T3b K4); a caller cannot see that nothing started.
- F7 Shutdown order (`runtime.shutdown_services`): `video.stop()` → … → `detector.close()`
  → `parking_trigger.close()` (the coordinator closes after the detector).
- F8 Detector worker: `offer` accepts a packet when `packet.session_id` equals the active
  id; inference runs outside every lock; afterwards ONE `_cond` check
  (`packet.session_id == _active_session_id`) publishes `latest_result`, then the result
  consumer, tracker and parking trigger are called OUTSIDE `_cond`. With a reused id, a
  late A result passes the check after B started; and between that check and the
  consumer calls, Stop/Start can run, so an old result can reach B's consumer state.
  `AudioAlertGate.offer_local` (reached through the coordinator) takes only its leaf
  lock and never waits.

**Lifecycle identity (closes Codex finding "identity")** — chosen mechanism: an
operation LEASE passed and checked, not an id-reuse ban.
- The lease is VideoService's per-reservation token (`_CaptureSession.token`, `uuid4`,
  never reused). Lease-aware wrappers declare `accepts_lifecycle_lease = True` and take
  `start_session(session_id, *, lease=None)` / `end_session(session_id, *, lease=None)`.
  VideoService passes `lease=session.token` only to sinks that declare it (detector,
  tee); every other sink keeps the id-only protocol.
- A lease-aware wrapper records `(session_id, lease)` for the active and each pending
  session. `end_session` acts only when BOTH match a record; a late end with the old
  lease after B started (even with the same id) is a no-op and counted
  (`stale_ends`). `start_session` is refused while ANY record (active or pending) has the
  same session id, whatever the lease. A call with `lease=None` matches only records
  created with `lease=None` (legacy callers keep today's id-only behaviour).
- Inside the wrappers, dependency calls (coordinator/trigger, buffer/sampler) are made
  only for the wrapper's own records under its lifecycle lock, so a dependency is never
  asked to end a session the wrapper no longer owns; dependencies keep their own
  generation checks (trigger/sampler session generation, candidate tokens).
- VideoService: `abort_start(token)` and the cleanup runner act on one `_CaptureSession`
  object; the session id factory is injectable for tests only (to force id reuse).
- Boundary of the opt-in lease: it protects the lifecycle driven by the owner
  (VideoService → detector/tee, and the wrappers' own dependency calls). It does NOT
  protect direct external calls with `lease=None`, id-only sinks called by someone other
  than VideoService (EventFrameBuffer, scene classifier, evaluation worker), or the
  scene-classifier → `DomainService.apply_scene_decision` path, which stays id-only; in
  production those ids are fresh `uuid4` values minted by VideoService.

*T3c0.1 — DetectorService obligations, lease and close* (no API dependency)
Files: `backend/app/services/detector_service.py`;
`backend/app/services/private_parking_coordinator.py` (coordinator and tee: start after
close raises, F6; tee lease support); new `backend/tests/test_detector_session_cleanup.py`;
`backend/tests/test_private_parking_coordinator.py` (adapt the close tests, add lease and
closed-start tests).
Contract:
- Fixed dependency labels `result consumer`, `tracker`, `parking trigger` (never ids).
- Start: under `_lifecycle_lock`; refused with `DetectorAdmissionError` (sanitized,
  before any dependency call) when closed, when any cleanup is pending, or when the same
  id is active/pending. For each dependency, in order: under `_cond`, re-check `_closed`
  and RECORD the label as an obligation of `(sid, lease)`, then call its
  `start_session` outside `_cond`. The record precedes the call, so a dependency that
  changes state and then raises is covered. A failure (or `_closed` seen at a record
  point) stops further starts and raises `DetectorStartError`; the session stays active
  with the recorded obligations.
- End: active `(sid, lease)` → ending with all recorded obligations; pending → only those
  still pending; anything else → no-op (`stale_ends`). Every target tried (isolated),
  successes removed, failures kept together, then `DetectorCleanupError` (count only,
  `from None`). Retry calls only what is still pending. At most one active + one pending
  session.
- Implicit switch (legacy `start_session(B)` while A active, no lease): ends A via
  `_end_locked` while already holding `_lifecycle_lock` (no re-acquire); success → start
  B; failure → A pending, B refused.
- Close: (1) under `_cond`: `_closed = True`, notify the worker; (2) take
  `_lifecycle_lock` — waits for an in-flight start, which stops at its next record point
  — then snapshot the active and pending obligations under `_cond` and try each once
  (isolated, never raises, failures counted/logged); (3) `join` the worker with no lock
  held. Because recording happens under `_cond` with a `_closed` check, the snapshot in
  (2) contains every dependency that was or will ever be started; no new start after (1).
  The worker stop is requested in (1), so a raising dependency cannot prevent it.
- Coordinator and tee after `close()`: `start_session` raises (sanitized
  `FrameSinkAdmissionError` / coordinator `SessionAdmissionError`) instead of returning
  silently; `end_session` stays allowed (cleanup never refused). This changes the T3b
  K4 detail "start ignored after close" (recorded as a contract change).
- Frame/job binding (closes the detector blocker, F8): every admitted session gets a
  detector GENERATION (`int`, strictly increasing per instance, never reused, also for
  the same id). `offer(packet, *, lease=None)` accepts a packet only when its session id
  AND lease match the active record, and stores `(packet, generation)` in the
  latest-frame slot. Inference runs with no detector lock held (CUDA gate only).
- Atomic delivery versus invalidation: a new `D._delivery_lock` is held by the worker for
  the whole delivery — the `_cond` check `generation == active generation and not
  closed`, publishing `latest_result`, and the calls to result consumer, tracker and
  parking trigger. Every lifecycle change (end, implicit switch, start, close) takes
  `D._delivery_lock` and then `_cond` to change the active generation. So the check and
  the consumer calls form one unit with respect to invalidation: a delivery either
  completes entirely before the lifecycle change or is dropped afterwards (generation
  mismatch, counted `stale_results`). A result of generation A is never delivered after A
  was invalidated, even when B has the same session id; `latest_result` is cleared on
  invalidation and set only by a current-generation delivery.
- Delivery work is bounded and in-memory: domain observe, tracker observe, coordinator
  observe (trigger, sampler, `offer_local` — leaf lock, never waits). No inference, HTTP
  or audio-device operation runs under `D._delivery_lock` or `D._lifecycle_lock`
  (evaluation HTTP stays on the evaluation worker; PlaySound on the audio device thread).
  Consumers must never call back into the detector's lifecycle or take its locks.
- Consequence for Stop: delivery and invalidation are mutually exclusive — a lifecycle
  change waits while a delivery holds `D._delivery_lock` and a delivery never starts
  for an invalidated generation. No wait bound is proven: the wait lasts as long as the
  in-flight delivery's consumers run (in-memory by contract). A Stop never waits for
  inference; an inference that finishes after the Stop is dropped at its delivery check. No consumer contract change is needed: consumers keep their session-id checks
  and are reached only through this serialized delivery; therefore
  `domain_service.py`, `tracker_service.py` and `private_parking_trigger.py` are not in
  scope (an explicit consumer generation would be required only if a consumer were fed
  by another path for the same session id — see the lease boundary).
Diagnostics `get_lifecycle_diagnostics()` (counts/labels only): `active_sessions`,
`pending_sessions`, `pending_dependency_ends`, pending per label, `start_failures`,
`end_failures`, `stale_ends`, `stale_results`, `cleanups_completed`, `starts_refused`,
`close_failures`.

*T3c0.2 — VideoService required sinks, thread start, token-bound rollback, API*
(depends on T3c0.1)
Files: `backend/app/services/video_service.py`; `backend/app/api/video.py` (O-P3);
new `backend/tests/test_video_required_sinks.py` and
`backend/tests/test_video_upload_rollback.py`; `backend/tests/test_video_api.py` only
where an assertion encodes the global-stop rollback.
Contract:
- `VideoService(..., required_sinks=frozenset())` (labels `event frame buffer`,
  `detector`, `scene classifier`, `evaluation worker`). Empty = legacy. Private mode
  (T3c runtime config) passes `{"event frame buffer", "detector"}` (O-P1).
- Start, outcome then rollback: `_start_sinks` holds `_lifecycle_lock` only for the start
  loop and returns `ok` | `stopped` | `refused(label)`; it never starts a cleanup.
  Obligations are implicit: invalidation marks EVERY sink pending, so the cleanup ends
  started sinks, the refusing sink and sinks that changed state before raising (no-op
  for sinks that never started). After the loop (lock released), `_atomic_reserve` takes
  `_lock`: session already released (a Stop/abort won) → return `stopped`, the winner's
  runner owns cleanup; refused → `end_reason="error"`, `start_refused=True`, error
  "Required pipeline component refused the session" (label only), STOPPING, release
  `_lock`, `_release_session(session)` → runner → final ERROR (O-P2), or ERROR "Video
  cleanup failed; Stop retries it" with the failed sinks pending if the rollback failed.
- Refusal reporting (O-P2): `claim_start()` and `start_webcam()` raise
  `VideoStartRefusedError` (not a `RuntimeError` subclass) after the rollback ran; the
  routes answer 503 "Required pipeline component is unavailable". Not IDLE → `None` /
  `False` → 409 as today. Legacy configuration never raises it.
- Upload admission before write/probe (F3): reservation and sink starts happen in
  `claim_start()` before MIME checks, the write and the handler PROBE; a refusal returns
  503 before the file is created. After a successful `claim_start`, the handler owns the
  reservation until `start_file` succeeds; EVERY failure path (validation 422, size 422,
  write 500, probe 422/500, `start_file` mismatch 409, unexpected 500) calls
  `video_service.abort_start(token)` and deletes only the file the handler still owns.
  No global `stop()` remains in the upload handler.
- `abort_start(token) -> bool`: under `_lock`, acts only when the CURRENT session's token
  equals `token` and the session was not handed to a capture thread; sets
  `stop_requested` (a rollback by its owner) and invalidates it; its runner releases that
  session's capture/upload/sinks; final IDLE when the cleanup succeeded, ERROR (Stop
  retries) when it failed. Any other token → `False`, nothing touched (`stale_aborts`).
- Thread start (F4): `_attach` attaches the capture and creates the thread but keeps
  STARTING; `_start_capture` holds `V._lock` for one section that re-checks "current and
  not released", calls `thread.start()`, and publishes RUNNING only after it returned.
  The capture thread can report EOF/error only through `_finish_from_capture`, which
  needs `V._lock`, so it observes RUNNING (never STARTING) and its STOPPING transition
  follows the publication. After finalization (IDLE/ERROR) nothing writes RUNNING for
  that session again: `_start_capture`/`_attach` refuse a released session. If it raises: under `_lock` `end_reason="error"`, error "Capture
  thread could not start" (sanitized), `session.thread = None`, STOPPING; after `_lock`,
  `_release_session(session)` → runner releases this session's capture and upload and
  ends its sinks → ERROR until Stop; cleanup failures stay pending. `start_webcam`
  returns `True` (attempted; status ERROR); `start_file` returns normally (status ERROR;
  the attached upload is deleted by the runner); the route reports the status as for an
  open failure today.
- Detector frames: `_offer_consumers(packet, *, lease=None)`; `_publish(session, ...)`
  passes `lease=session.token`, forwarded only to a lease-aware detector `offer` (see
  A5 below).
- Admission rule (O-P2): `_atomic_reserve` requires IDLE, and IDLE is written only by a
  cleanup with every obligation done (capture, upload, every pending sink — including the
  detector's and tee's own pending dependencies, which raise until done).
Diagnostics `get_cleanup_diagnostics()` (labels/counts only): state, pending sink labels
of the current session, `required_sinks`, `required_start_refusals`,
`thread_start_failures`, `aborted_starts`, `stale_aborts`.

*T3c0.2 alignment with the implemented T3c0.1 contract* (rev 5; source read at `e50d9e2`)
Implemented facts T3c0.2 must use (not re-designed):
- `DetectorService` and `FrameSinkTee` declare `accepts_lifecycle_lease = True` and take
  `start_session(sid, *, lease=None)`, `end_session(sid, *, lease=None)`; the detector
  also `offer(packet, *, lease=None)`. An end acts only on an id AND lease match;
  otherwise it is a no-op counted `stale_ends` (no exception).
- Start refusals raise: detector `DetectorAdmissionError` (closed, cleanup pending, same
  id active, previous session's cleanup failed during an implicit switch) and
  `DetectorStartError` (a dependency did not start or close raced the start — the
  attempted dependencies STAY active obligations); tee `FrameSinkAdmissionError`
  (closed, another session active, same id pending, capacity full). Ends raise
  `DetectorCleanupError` / `SessionCleanupError` while a dependency or sink did not end;
  a repeated end with the same lease retries only those.
- All errors are sanitized (no ids, leases or exception text); VideoService may log them
  with its existing per-label messages.
Deltas to the T3c0.2 contract above:
- A1 Lease passing is gated by `getattr(sink, "accepts_lifecycle_lease", False) is True`
  (the attribute name actually implemented). VideoService passes `lease=session.token`
  to such sinks in `_start_sinks`, `_end_sinks` (every attempt and every Stop retry,
  same `_CaptureSession` → same lease) and detector `offer`. Every other sink keeps the
  id-only call (EventFrameBuffer in today's runtime, scene classifier, evaluation worker,
  test fakes) — this is the documented lease boundary.
- A2 Required refusal = ANY exception from a required sink's `start_session`
  (`DetectorAdmissionError`, `DetectorStartError`, `FrameSinkAdmissionError` or another
  error). Optional sinks keep today's "log and continue". With `DetectorStartError` the
  detector session stays active with its attempted obligations; the rollback (required)
  or the later Stop (optional, legacy) ends it with the same lease.
- A3 Ends on sinks that never recorded the session (a tee/detector that refused, or a sink
  after an early refusal) return without error and increment that sink's `stale_ends`;
  VideoService removes them from `pending_sinks` as succeeded. Tests must expect these
  counts; they are not failures.
- A4 Lock chain confirmed against the code: `V._lifecycle_lock` → detector
  `_lifecycle_lock` → `_delivery_lock` → `_cond`, and `V._lifecycle_lock` → tee
  `_lifecycle_lock` → tee `_lock`. Consequence: a VideoService Start/Stop/abort that
  reaches the detector waits for an in-flight detector delivery (exclusion, no proven
  bound — T3c0.1 limitation now visible at VideoService level). The capture thread's
  `offer` takes only the detector's `_cond` and never waits for a delivery.
- A5 `_offer_consumers(packet, *, lease=None)` keeps its one-argument form for existing
  direct test callers (`test_video_event_frame_buffer.py`, id-only fakes); `_publish`
  passes `session.token`. A lease-started detector ignores an offer without its lease
  (no frame delivered), which is the intended stale-frame guard.
- A6 Shutdown: `runtime.shutdown_services` calls `video.stop()` before `detector.close()`;
  a start after the detector/tee closed is refused (A2) → 503 in private mode, logged in
  legacy mode.
- A7 Acceptance 10 and 15 use the real `DetectorService` and `FrameSinkTee` (fake model,
  fake sinks/dependencies) and assert their `stale_ends` / `get_lifecycle_diagnostics()`
  / `get_diagnostics()` counters rather than private fields.
- A8 Sanitized logging (PM, rev 5 final): every lifecycle/rollback log in VideoService and
  the video routes uses a fixed label and a safe category (exception TYPE name only);
  no `logger.exception`, no `exc_info`, no interpolation of an exception message. Public
  status errors and HTTP details carry fixed texts only (capture errors, open failures,
  thread start, refusal); no session id, token, path or exception text.
Scope (unchanged files plus conditional adjacent tests, each to be reported before it
is edited): `backend/app/services/video_service.py`, `backend/app/api/video.py`, new
`backend/tests/test_video_required_sinks.py`, new
`backend/tests/test_video_upload_rollback.py`; conditional: `backend/tests/test_video_api.py`
(assertions that encode the global-stop rollback), `backend/tests/test_video_session_lifecycle.py`
or `backend/tests/test_video_service.py` (only if an assertion encodes RUNNING written
before `thread.start()`; the current ones only wait for RUNNING),
`backend/tests/test_private_parking_coordinator.py` (VideoService-slot tee tests; expected
unchanged with leases). Not in scope: `detector_service.py`,
`private_parking_coordinator.py` (contract consumed as implemented), runtime, frontend.
Docs after implementation: this plan, `PLAN.md`, `ARCHITECTURE.md` §7, `RELIABILITY.md`.
Risk added: M1c — through A4 a Stop/abort can wait for a detector delivery; acceptance 7
and 13 keep every gate bounded and assert both threads finish.

Lock order (all locks non-reentrant; no path re-acquires a lock it holds):
- Start success / required refusal: `V._lock` (reserve) → release → `V._lifecycle_lock`
  → per sink: tee `_lifecycle_lock` → tee `_lock` | sink locks; detector
  `_lifecycle_lock` → `D._cond` (record) → release → dependency locks → release
  `V._lifecycle_lock` → `V._lock` (decide) → release → `_release_session`: `V._lock`
  (invalidate) → release → runner (no `_lock` across steps): join, release capture/upload
  → `V._packet_cond` → `V._lifecycle_lock` → sink ends (same chains) → release → `V._lock`
  (finalize).
- Stop during start: `V._lock` (stop_requested, invalidate) → release → runner blocks on
  `V._lifecycle_lock` held by the start loop; the loop sees `released` at its next sink,
  returns `stopped`, releases the lock; the runner ends every sink; the start path sees
  `released` under `V._lock` and does not start a second cleanup.
- Thread start failure: `V._lock` (thread.start, mark failure) → release → same runner
  chain as above.
- Abort: `V._lock` (token check, invalidate) → release → same runner chain.
- Detector implicit switch: `D._lifecycle_lock` → `D._cond` → release → `_end_locked(A)`
  dependency calls → `D._cond` (record B) → release → dependency starts; never calls
  VideoService, never re-takes `D._lifecycle_lock`.
- Detector close: `D._cond` (closed, notify) → release → `D._lifecycle_lock` →
  `D._delivery_lock` → `D._cond` (invalidate generation, snapshot) → release `_cond`,
  `_delivery_lock` → dependency ends → release `D._lifecycle_lock` → `join` with no lock.
- Detector lifecycle change (end / start / switch): `D._lifecycle_lock` →
  `D._delivery_lock` → `D._cond` (change generation, record) → release `_cond` and
  `_delivery_lock` → dependency calls (holding only `D._lifecycle_lock`).
- Detector worker: `D._cond` (take job) → release → inference (CUDA gate only) →
  `D._delivery_lock` → `D._cond` (generation check, publish) → release `_cond` → consumer
  calls (holding `D._delivery_lock`) → release. The worker never takes
  `D._lifecycle_lock`; consumers never take detector locks. Global order:
  `V._lifecycle_lock` → `D._lifecycle_lock` → `D._delivery_lock` → `D._cond`; consumer
  locks are taken under `D._delivery_lock` only by the worker, and under
  `D._lifecycle_lock` only by lifecycle calls (never both directions).

Acceptance (fakes; `threading.Event`/`Barrier` gates; every wait bounded; every thread
joined with a timeout and asserted finished; process `timeout`).

T3c0.1 — standalone (DetectorService with a fake model, fake consumers, real
coordinator where stated; no VideoService):
1. Inference A held (gated fake model) → `end_session(A)` → `start_session(B)` with the
   SAME id and another lease → release A's inference: `latest_result` stays B's (None),
   result consumer, tracker and coordinator/trigger receive nothing from A
   (`stale_results` = 1); B's next frame is delivered normally.
2. Delivery A held exactly before the consumer calls (gated fake result consumer, after
   the generation check) → `end_session(A)` in a thread; a deterministic gate proves it
   is already waiting for `D._delivery_lock` (it has taken `D._lifecycle_lock` and its
   end has not run) BEFORE `start_session(B, same id)` is issued; release → the A
   delivery completes into A's state, then A ends, then B starts; B's consumer state holds
   nothing from A, and an A result produced meanwhile is dropped (`stale_results`). No
   timing bound is asserted beyond the test timeout.
3. Partial start (result consumer ok, tracker changes state then raises; parking trigger
   never attempted): the obligations are exactly result consumer and tracker. Both end
   calls fail → `DetectorCleanupError`, both recorded; retry #1: only those two called,
   one succeeds; retry #2: only the remaining one; the parking trigger is never ended
   (never attempted). implicit switch with a gated dependency end; close while the worker
   is inside a gated delivery and a dependency end raises → close returns, worker
   joined, no thread left, no deadlock.
4. Closed start is a refusal: after `close()`, detector, tee and coordinator
   `start_session` raise the sanitized admission error (never a silent return); a
   `end_session` / retry for a recorded session still runs after close and finishes its
   obligations.
5. Start versus close at the record boundary: a dependency start blocks after being
   recorded; `close()` in a thread → close ends it, the next dependency is never started,
   start raises, worker joined.
6. Late lease-bound end: `end_session(id, lease=A)` after B (same id) started → no-op
   (`stale_ends`), B intact (detector and tee).

T3c0.2 — VideoService and API (uses T3c0.1):
7. Required-start failure concurrently with Stop: exactly one cleanup runner, each sink
   ended once, both threads finish.
8. Refusal before write/probe/playback: required tee or detector refuses in
   `claim_start` → 503; handler PROBE (`api.video.cv2.VideoCapture`) and PLAYBACK capture
   (`video_service.cv2.VideoCapture`) counted separately — both 0; no file written;
   webcam: `cv2.VideoCapture` 0 calls, 503; ERROR until Stop.
9. Upload A held in `file.read` → Stop → Start B → A fails validation →
   `abort_start(A)` False; B RUNNING, B's sinks never ended; A's file deleted by the
   handler.
10. Late operations with id reuse: forced same id for A and B with different tokens; late
   `abort_start(A)`, late `start_file(A)` and late lease-bound ends → no effect on B; while
   A is pending, B is refused by VideoService, tee and detector.
11. `thread.start()` fails: RUNNING never published for that session; capture released,
   upload deleted, sinks ended; ERROR until Stop; a failing sink end stays pending and
   Stop retries it; another session untouched.
12. Immediate EOF and immediate capture error: the fake capture returns EOF / raises on
   the first read; the thread's report waits for the RUNNING publication, then STOPPING
   → IDLE (EOF) or ERROR (error); no RUNNING after finalization; every sink ended once.
13. Stop at the `thread.start`/RUNNING boundary: (a) Stop wins before `_start_capture`
   takes `V._lock` (gate just before it) → the thread is never started, capture released,
   IDLE; (b) Stop arrives while `thread.start()` runs under `V._lock` (gated start) → Stop
   waits, then ends the RUNNING session normally; in both cases RUNNING is never written
   after the session was finalized (state-transition log asserted).
14. Publication under the state lock: the state-transition log shows RUNNING written in
   the same `V._lock` section as the successful `thread.start()` and the current/released
   check (instrumented lock, as in the T3a2 lifecycle tests).
15. IDLE/admission only after every obligation: VideoService + DetectorService (fake
   model) + real coordinator whose trigger `end_session` fails once → ERROR, new session
   409; Stop reaches the coordinator again (only it) → IDLE; then admitted.
16. Legacy regression and sanitization: `required_sinks` empty → an optional sink start
   failure still leaves the video RUNNING; existing `test_video_*`, `test_detector_*`,
   private parking and worker suites pass (only rollback assertions that encoded the
   global stop change); new errors, response details and logs carry no session id,
   token, path or exception message.
17. Generic exceptions whose messages contain a synthetic session id, token and path —
   raised by sink start/end, capture release, upload deletion, `thread.start()`, the
   capture read and the upload handler — never appear in `caplog` or in any public
   status error / HTTP detail.
Verification: targeted files serially, then the full backend suite, `pip check`, `git
diff --check`, whitespace on new files; docs: this plan, `PLAN.md`, and after
implementation `ARCHITECTURE.md` §7 and `RELIABILITY.md`.

Risks (high → low):
- H1 Deadlock between start, Stop, abort and close (F2) — outcome-then-rollback, record
  points, join outside locks; acceptance 1, 5, 8.
- H2 A late operation ends or stops a newer session (F3, F5) — leases + token-bound
  `abort_start`; acceptance 3, 4.
- H3 False IDLE after a forgotten obligation (F1, F4) — recorded obligations, thread-start
  rollback; acceptance 6, 7, 9.
- M1 A dependency blocking in `start_session`/`end_session` holds the detector/tee
  lifecycle lock; Start/Stop/close wait (frames do not); join is bounded, the dependency
  call is not.
- M1b A consumer that blocks inside a delivery holds `D._delivery_lock`, so a Stop waits
  for it (inference never). No bound is proven; it rests on the consumer contract
  (in-memory, no HTTP/audio device/inference); acceptance 2 asserts the exclusion.
- M2 Legacy behaviour changes on failure paths: detector start/end errors become
  sanitized aggregate errors; implicit switch refuses B when A's end fails; upload
  rollback is token-bound; a thread-start failure now gives ERROR instead of a stuck
  RUNNING; tee/coordinator start after close raises.
- M3 New 503 response for both start routes; the frontend may need a message (outside
  this scope; to be reported).
- L1 Diagnostics/errors must stay free of ids, tokens, paths and exception text.
- L2 Thread tests can hang on a missed gate — every wait bounded, process timeout.
Dependencies: T3b core (`ec9b8cb`) → T3c0.1 → T3c0.2 (uses detector/tee leases and
closed-start refusal; acceptance 9 needs T3c0.1) → T3c (runtime passes
`required_sinks={"event frame buffer", "detector"}` in private mode). No package, model or
hardware dependency.
Technical answers (TQ2–TQ4 kept as given in rev 3 per PM message "revisi terarah T3c0
rev 3"; TQ1 restated as the lease boundary above):
- TQ1 Lease opt-in by class attribute; its protection boundary is stated under
  "Lifecycle identity". Changing every sink protocol is not part of T3c0.
- TQ2 `abort_start` final state is IDLE after a successful rollback (owner rollback, not a
  component refusal), ERROR if the rollback failed.
- TQ3 A thread-start failure is reported like an open failure (200 with `state=ERROR`),
  not 503 (not a component refusal) and not 500.
- TQ4 The coordinator/tee closed-start change (F6) alters the reviewed T3b detail "start
  ignored after close" into a sanitized refusal; end/retry after close unchanged.

**T3c0.1 — completed, commit `e50d9e2`** (independent review PASS WITH LIMITATIONS;
committed on the PM instruction "penutupan dan commit T3c0.1", 2026-10-11, as "fix:
preserve detector cleanup and isolate stale delivery"). At that time T3c0.2 was not done
(historical; completed later in `d479357`); runtime wiring was NOT done at that time (historical; T3c is now completed, fake integration verified).
- Independent review (Codex via PM, 2026-10-11): **PASS WITH LIMITATIONS**; 74 target
  tests and 1,110 full backend tests passed; `pip check` and diff/whitespace checks
  clean; the mutation testing and the stability repetitions below were NOT reproduced by
  the reviewer (agent runs only).
- Closure check (agent run, 2026-10-11): HEAD `ec9b8cb`, nothing staged; no source/test
  file was modified after the agent's last verification run (the only later source
  write was the mutation script's byte-identical restore, before the full run); blob
  hashes at closure: `detector_service.py` `95e90f8`, `private_parking_coordinator.py`
  `0b70737`, `test_detector_service.py` `d0ecef4`, `test_private_parking_coordinator.py`
  `31d367f`, `test_detector_session_cleanup.py` `45629a3`.
- Limitations kept open: the scene classifier and its `DomainService.apply_scene_decision`
  path are not covered (id-only); id-only sinks and direct external calls with
  `lease=None` are not lease-protected, and VideoService passes no lease until T3c0.2; a
  lifecycle change can be held by an in-flight delivery's consumers (no proven wait
  bound).

**T3c0.1 results (agent run, 2026-10-11, HEAD `ec9b8cb`, working tree)**
Files: `backend/app/services/detector_service.py`,
`backend/app/services/private_parking_coordinator.py`, new
`backend/tests/test_detector_session_cleanup.py`,
`backend/tests/test_private_parking_coordinator.py`, and one assertion in the adjacent
`backend/tests/test_detector_service.py` (see legacy changes); this plan and `PLAN.md`.
- DetectorService: `_SessionRecord(session_id, lease, generation, obligations,
  remaining)`; generation per admitted session; `offer(packet, *, lease=None)` stores
  `(packet, generation)`; the worker holds `_delivery_lock` from the generation check
  through every consumer call; `_end_record_locked` invalidates under `_delivery_lock` →
  `_cond`; obligations recorded under `_cond` (with a `_closed` check) before each
  dependency start; end/retry try only `remaining`; one pending session, start refused
  while pending; implicit switch through the same helper; `close()` = closed+notify →
  obligations once (failures stay pending, never raises) → join with no lock.
  Errors `DetectorAdmissionError` / `DetectorStartError` / `DetectorCleanupError`
  (sanitized, `from None`); logs: label, step, exception type.
  `get_lifecycle_diagnostics()` counts/labels only.
- Tee: `accepts_lifecycle_lease`; records `(session_id, lease)`; end acts only on an id
  AND lease match (else `stale_ends`); start after close raises
  `FrameSinkAdmissionError`. Coordinator: start after close raises
  `SessionAdmissionError` (counted); end still forwarded after close. The coordinator is
  not lease-aware: the detector calls it only for its own records under its lifecycle
  lock.
- Tests (`test_detector_session_cleanup.py`, 9; all with bounded gates, threads joined):
  stale inference A never delivered to B with the same id; end A proven waiting for the
  delivery lock (lifecycle lock held, no end call yet) before start B is issued, then A's
  delivery → A's end → B's start; partial start with exactly the attempted obligations,
  two failing ends, staged retry, never-attempted trigger never ended; implicit switch
  with a gated end and a failing switch; close during a delivery with a raising end
  (returns, worker joined, retry after close); closed start refused and forwarded from a
  closed coordinator as `DetectorStartError`; close during a start ends only the
  recorded dependency; late lease-bound end never touches B; sanitized errors/logs.
  Coordinator file: lease test for the tee; closed-start tests updated (28 tests).
- Mutations (agent run, scratch script, source restored): no generation check, invalidation
  without the delivery lock, record after the start call, end ignoring the lease, retry
  ending every obligation, closed start returning silently — each fails the new tests.
  Stability: the new file passed 5 consecutive runs.
- Tests (serial, fakes only, unique `--basetemp`): detector cleanup 9, detector service 9,
  detector/tracker integration 10, coordinator 28, contracts integration 18, all
  `test_private_parking_*` 241, video 100, detection API 5, domain API 6, evaluation
  worker 119, audio 62 — all passed; full backend suite **1110 passed**, 1 Starlette
  `DeprecationWarning`; `pip check` clean; `git diff --check` clean; the new test file has
  no trailing whitespace and ends with a newline.
- Legacy behaviour changes (reported): the implicit switch now ends EVERY obligation of
  the previous session, including the result consumer (before: only tracker and parking
  trigger) — one assertion in `test_detector_service.py` updated; a start for an id that
  is already active is refused (before: restarted); dependency start/end errors are
  re-raised as sanitized detector errors after isolation (before: the raw first error,
  skipping the rest); start after close raises on detector, tee and coordinator (before:
  silent return).
- Deviation from acceptance 2 (rev 4 final): an A result "produced meanwhile" is not
  part of test 2 — the single worker cannot hold a delivery and an inference at once, so
  it is covered by test 1 (`stale_results`).
- Limitations: VideoService does not pass leases yet (T3c0.2), so in the running app the
  detector and tee are still driven id-only (`lease=None`); the generation binding and
  the delivery exclusion apply already. Not covered: the scene classifier and its
  `DomainService.apply_scene_decision` path (id-only, outside the lease), id-only sinks
  called directly, and direct external calls with `lease=None`. No wait bound is proven
  for a lifecycle change behind an in-flight delivery. No runtime, video, device or
  provider run.

**T3c0.2 — completed, commit `d479357`** (independent review PASS WITH LIMITATIONS;
committed on the PM instruction "finalisasi dan commit T3c0.2", 2026-10-11, as "fix:
isolate video start rollback and retain capture cleanup"). Completed here: the
required-sink/refusal CAPABILITY of VideoService (`required_sinks`, refusal → ERROR until
Stop, HTTP 503), leases, safe RUNNING publication, token-bound upload rollback and
single-owner capture cleanup. NOT done at that time (historical; since completed in T3c, fake integration verified): private-mode configuration (passing
`required_sinks={"event frame buffer", "detector"}`), tee/coordinator wiring and mode
selection in runtime.
- Independent review (Codex via PM, 2026-10-11): **PASS WITH LIMITATIONS**; 216 target
  tests passed; full backend 1,152 passed. Limitations kept open: a `cv2.VideoCapture`
  constructor that never returns keeps the session in ERROR ("still opening") with no
  timeout, and a capture release that always fails keeps the service in ERROR until a
  restart (each Stop retries it); no real device, server or provider was used.
- Closure (agent run, 2026-10-11): HEAD `e50d9e2`, nothing staged; no source/test change
  after the last verification except a docstring of `_CaptureSession` aligned with the
  reviewed design (an opener that is still active after invalidation adopts its capture
  into the OLD context only for that context's cleanup; it never continues the session
  or moves to another one) — verified as docstring-only by comparing the module AST
  without docstrings (identical), so the suite was not re-run.

**T3c0.2 results (agent run, 2026-10-11, HEAD `e50d9e2`, working tree)**
Final scope: `backend/app/services/video_service.py`, `backend/app/api/video.py`; new
`backend/tests/test_video_required_sinks.py` (16) and
`backend/tests/test_video_upload_rollback.py` (5); adjacent test edits:
`backend/tests/test_video_service.py` (one assertion: the public webcam-open error is now
the fixed text, not the raw exception message — A8) and
`backend/tests/test_private_parking_coordinator.py` (a `FrameSinkTee` subclass in the
VideoService-slot test now accepts `lease`, because VideoService passes it — A1).
`test_video_api.py` and `test_video_session_lifecycle.py` needed no change. Plus this
plan and `PLAN.md`.
- VideoService: `required_sinks` (validated against the configured sinks);
  `_start_sinks` returns the refusing required label and never cleans up; the caller
  rolls back after `_lifecycle_lock` was released (`_release_session` → runner → ERROR
  "Required pipeline component refused the session" until Stop) and raises
  `VideoStartRefusedError` (an `Exception`, not a `RuntimeError`); a stop() that won owns
  the cleanup. `_lease_kwargs` passes `lease=session.token` to sinks with
  `accepts_lifecycle_lease` on start, every end attempt and the detector `offer`
  (`_publish` → `_offer_consumers(packet, lease=...)`). `_attach` keeps STARTING;
  `_start_capture` re-checks, calls `thread.start()` and publishes RUNNING in one `_lock`
  section; a failing `thread.start()` → `session.thread=None`, ERROR "Capture thread could
  not start", this session's runner releases capture/upload/sinks (outside `_lock`).
  `abort_start(token)` (current + same token + not released + STARTING) and
  `get_cleanup_diagnostics()` (labels/counts). Injectable `session_id_factory` and
  `thread_factory` (tests only).
- Sanitized logging (A8): every lifecycle/rollback log goes through `_log_failure` (fixed
  text + exception type, ERROR level as the replaced `logger.exception`); public status
  texts are fixed: "Webcam open failed", "Unable to open video file" (no file name),
  "Video capture failed", refusal and thread-start texts.
- Routes: webcam and upload map `VideoStartRefusedError` → 503 "Required pipeline
  component is unavailable" (upload: before any write/probe); every upload rollback
  calls `abort_start(token)` and deletes only a still-owned file; the unexpected-error
  path logs the exception type only and raises `from None`. The user's `/stop` route
  keeps its global `stop()` (that is the explicit Stop).
- Tests: required refusal racing a Stop (one runner, both threads joined); refusal with
  no capture, ERROR until Stop, 409 while ERROR; late abort/start_file/lease-bound ends
  with a reused id (real `DetectorService` + `FrameSinkTee` + coordinator), same-id
  refusals while pending, retry with the same lease; failing `thread.start()` (webcam and
  file: upload deleted, failed sink pending, Stop retries only it); immediate EOF and
  immediate capture error after the RUNNING publication; Stop before the RUNNING section
  (thread never started) and during `thread.start()` (waits, then ends it); RUNNING written
  in the same counted `_lock` section as `thread.start()`; IDLE/admission only after the
  coordinator really ended; legacy optional sinks (incl. a closed tee) keep the video
  RUNNING; same lease on start, both end attempts and detector offers (id-only scene
  sink gets none); an old abort never rolls back B while B is still STARTING; sanitized
  logs/status/diagnostics with synthetic id/token/path; routes: 503 before
  write/probe/playback (probe and playback counted separately), webcam 503 without
  opening the camera, held upload A → Stop → B → A's 422 rollback leaves B RUNNING and
  deletes A's file, validation rollback to IDLE, sanitized route errors.
- Mutations (agent run, scratch script, sources restored): required refusal ignored,
  RUNNING before `thread.start()`, abort ignoring the token, end without the lease,
  thread-start failure re-raised, raw exception message logged, route rollback as a global
  stop, refusal mapped to 409 — each fails the new tests (the token mutant survived the
  first version; the STARTING-B abort test was added and kills it). Stability: the two
  new files passed 5 consecutive runs.
- Tests (serial, fakes only, unique `--basetemp`): required sinks 16, upload rollback 5,
  video API 22, lifecycle 25, video service 26, all `test_video*` 121, detector cleanup 9,
  detector service 9, detector/tracker 10, coordinator 28, all `test_private_parking_*`
  241, detection API 5, domain API 6, evaluation worker 119, audio 62 — all passed; full
  backend suite **1131 passed**, 1 Starlette `DeprecationWarning`; `pip check` clean; `git
  diff --check` clean; new files without trailing whitespace, ending with a newline.
- Limitations: not wired into runtime at that time (historical; T3c since completed, fake integration verified, decides `required_sinks` and the tee); no
  server, real webcam, real file decoding, device or provider run (route coroutines were
  called directly, not through an HTTP server); leases reach only lease-aware sinks
  (scene classifier, evaluation worker and EventFrameBuffer stay id-only); a lifecycle
  change can still wait for an in-flight detector delivery (no proven bound); the MJPEG
  stream generator still uses `logger.exception` (not a lifecycle/rollback path, left
  unchanged); the frontend has no specific message for 503; after a refusal the next
  start answers 409 until Stop (decided O-P2).

**T3c0.2 review delta 1 (reviewer findings via PM message "perbaikan delta T3c0.2", 2026-10-11; agent run, HEAD `e50d9e2`, working tree)**
- Capture ownership before attach. Baseline defect (present at `ec9b8cb`/`e50d9e2`, not
  introduced by T3c0.2, fixed inside this cleanup scope): in `start_file` an exception
  from `cv2.VideoCapture(...)`, `isOpened()` or `get()` escaped without releasing the
  capture and left the reservation STARTING; the route then classified any
  `RuntimeError` as a token mismatch. Fix: `start_webcam` and `start_file` run the whole
  preparation (constructor, `isOpened()`, `get()`, `_attach`) in one guarded block; until
  `_attach` succeeded the opener releases the capture exactly once and calls
  `_fail_start` (open-failure contract: ERROR "Unable to open video file" / "Webcam open
  failed" / "Unable to open webcam (index 0)" until Stop; this reservation's cleanup
  deletes its upload and ends its sinks). `_attach` now builds the thread BEFORE taking
  ownership of the capture, so a failing thread factory leaves the capture with the
  opener and it is never released twice; after `_attach` only the session cleanup
  releases it. A failed open of A after a Stop never touches B (`_fail_start` writes
  only the current session's state).
- Exception classification: new `StaleStartError` (a `RuntimeError` subclass for existing
  direct callers; message keeps "Session token mismatch" and no longer claims a Stop)
  for a reservation that is no longer current; the upload route catches only this class
  → 409 "Upload aborted: the reservation is no longer active". Any other hand-off error →
  type-only log, 500 "Service start error", `abort_start(token)` of this reservation.
  Capture/open errors inside `start_file` follow the open-failure contract (200 with
  `state=ERROR`, sanitized message).
- Deterministic Stop interleaving: the test `CountingLock` counts threads blocked
  entering a section (`waiting`). The refusal-versus-Stop test waits until the Stop's
  runner is blocked on `_lifecycle_lock` before releasing the refusal; the
  thread-start test waits until the Stop is blocked entering `_lock` (the RUNNING
  publication section) — and RUNNING is not yet written — before releasing
  `thread.start()`.
- Regressions added: `isOpened()`/`get()`/thread factory raising before attach (file and
  webcam; release exactly once, upload deleted, no reservation left — `abort_start`
  finds nothing, Stop → IDLE, next start works); a failed open of A during Stop/Start B
  (A's capture released once, B RUNNING and its sinks untouched); stale reservation vs
  general error at service level; route: stale → 409 without a Stop claim, general
  hand-off error → 500 + own rollback, playback open failure → 200 `state=ERROR` with the
  capture released once and the upload deleted.
- Mutations (agent run, scratch script, sources restored): no local release after a
  preparation failure, capture attached before the thread is built, `get()` outside the
  guarded block, route treating every `RuntimeError` as stale, RUNNING published in a
  separate lock section — each fails the tests. Stability: the two new files passed 5
  consecutive runs.
- Tests (serial, fakes only, unique `--basetemp`): required sinks 23, upload rollback 8,
  video API 22, video service 26, lifecycle 25, all `test_video*` 131, detector cleanup 9,
  detector service 9, detector/tracker 10, coordinator 28, all `test_private_parking_*`
  241, detection API 5, domain API 6, evaluation worker 119, audio 62 — all passed; full
  backend suite **1141 passed**, 1 Starlette `DeprecationWarning`; `pip check` clean; `git
  diff --check` clean; new test files without trailing whitespace, ending with a newline.

**T3c0.2 review delta 2 — single-owner capture cleanup (PM message "tutup kegagalan release pre-attach T3c0.2", 2026-10-11; agent run, HEAD `e50d9e2`, working tree)**
- Defect (in delta 1, and in the baseline's opener-owned release since `ec9b8cb`): a
  pre-attach capture was released by the opener with the error swallowed — a failed
  release lost the obligation (no retry, state could become IDLE, admission reopened).
  Delta 1's "opener releases locally" rule is superseded.
- Mechanism (consistent with the T3a2 deferred-release design): the session ADOPTS the
  capture the moment it is created (`_adopt_capture`, under `_lock`); only that
  session's cleanup releases it — `cap_released` is set only on success, a failure keeps
  `cleanup_ok=False` → ERROR "Video cleanup failed; Stop retries it", admission refused,
  and each explicit Stop retries only what is still pending (capture, upload, sinks). While
  the opener may still use the capture (`opening`: from before `cv2.VideoCapture` until
  `_attach` or the failure path) the release is deferred (`_capture_in_use_locked`, shared
  by the full pass, the runner's resume check and the Stop retry); `_end_open` hands a
  deferred release to exactly one owner atomically, like `_on_capture_exit`. Releases
  run outside `_lock` and `_lifecycle_lock`. Same path for every pre-attach failure
  (constructor, `isOpened()` False/raising, `get()`, thread factory) and for the
  post-attach `thread.start()` failure (`session.thread=None`, same runner). The unused
  opener-release helper `_release_capture` was removed.
- Behaviour changes: a Stop during a slow open now leaves ERROR "Video source is still
  opening; cleanup waits for it" until the opener returns (then IDLE once the capture is
  released) and refuses a new session meanwhile (before: IDLE at once, B could start
  while A's opener still held a capture); a start whose reservation was already stopped
  no longer opens a capture at all (one assertion in `test_video_session_lifecycle.py`
  updated from "opened and released once" to "nothing opened"). Diagnostics add
  `capture_pending` and `upload_pending`.
- Tests (`test_video_required_sinks.py`, now 34): for five capture paths (file `get()`,
  file `isOpened()` raising, webcam `isOpened()` False, webcam thread factory, file
  `thread.start()`): release fails once → ERROR with `capture_pending`, admission refused,
  Stop retries the capture only (sinks not re-ended) → IDLE, admission opens, the cleaned
  capture is never retried; release failing repeatedly → ERROR after every Stop, the
  obligation stays until it succeeds; a Stop while the opener is inside `isOpened()` never
  releases concurrently (deferred, handed over, released once, upload deleted after the
  hand-over); an old session's late cleanup calls never touch B or B's capture; an
  immediately successful release happens exactly once; a Stop retry for another failed
  obligation never re-releases a released capture; a failed open of A blocks B until A's
  capture is released.
- Mutations (agent run, scratch script, source restored): capture never adopted, failed
  release swallowed, opener not treated as a capture user, opener never handing a
  deferred release over, released capture retried — each fails the tests (the last one
  survived until the "retry for another obligation" test was added). Stability: the new
  and lifecycle files passed 5 consecutive runs.
- Tests (serial, fakes only, unique `--basetemp`): required sinks 34, upload rollback 8,
  video API 22, video service 26, lifecycle 25, all `test_video*` 142, detector cleanup 9,
  detector service 9, detector/tracker 10, coordinator 28, all `test_private_parking_*`
  241, detection API 5, domain API 6, evaluation worker 119, audio 62 — all passed; full
  backend suite **1152 passed**, 1 Starlette `DeprecationWarning` (re-run after removing
  the dead helper: 1152 passed); `pip check` clean; `git diff --check` clean; new test
  files without trailing whitespace, ending with a newline.
- Limitations: a capture constructor that never returns keeps the session in ERROR
  ("still opening") indefinitely — there is no timeout on `cv2.VideoCapture`; a release
  that always fails keeps the service in ERROR until a restart; no real device was used.

**T3c — runtime wiring and mode**
Files: `backend/app/services/runtime.py`, `backend/app/main.py`, `backend/.env.example`,
integration tests.
Scope: mode/config/local-clip variables, worker `enabled=False` in private mode until
T3d, tee wiring, shutdown order.

*T3c plan aligned with the source at `05a95b8`* — **rev 3** (plan only, 2026-10-11, PM
messages "commit koreksi status dan siapkan rencana T3c", "revisi desain wiring T3c" and
"delta terakhir desain T3c"). Rev 3 adds the audio factory extension for the local clip;
the mode-selection builder stays limited. Design review: **PASS WITH LIMITATIONS**
(independent reviewer, Codex via PM message "implementasi T3c", 2026-10-11).
**T3c authorized and in progress** (same message); gesture, T3d prompt, configuration UI,
real voice assets and provider/device tests stay out of scope.

Source facts used (read, not run):
- `runtime.py` builds module-level singletons from `os.environ` at import:
  `ParkingCandidateTrigger` in the detector's parking-trigger slot, `EventFrameBuffer`
  directly in VideoService's buffer slot, `EvaluationWorker(parking_candidate_trigger,
  budget, event_frame_buffer, client, enabled=..., audio_gate=...)`, `VideoService(...)`
  without `required_sinks`, `DetectorService` with the production (YOLO) model factory.
  `shutdown_services(video, scene, detector, parking_trigger, tracker, frame_buffer, ...)`;
  `main.py` lifespan starts worker and gate. `test_evaluation_runtime_integration.py`
  imports `runtime` in a subprocess with a prepared env.
- The worker's third argument is its bundle source: it calls only
  `wait_for_bundle(plan, timeout)` and `get_snapshot().source_session_id` on it.
  `EpisodeEvidenceSampler` implements both (`wait_for_bundle` never waits; missing evidence
  → `INSUFFICIENT_HISTORY`, closed → `CLOSED`, other session → `SESSION_MISMATCH`), and
  `plan_for_candidate` maps FAILED/EXPIRED/RELEASED/MISSING to `PlanUnavailable`.
- Worker `CandidateTrigger` Protocol: `wait_for_candidate(timeout)`, `wake_waiters()`,
  `claim_next_candidate(now)`, `complete(event_id, now)`, `fail(event_id, now)`,
  `get_snapshot()` (`source_session_id`, `pending_event_id`); the worker calls
  `wake_waiters()` on close. Detector `ParkingCandidateConsumer` Protocol:
  `start_session`, `observe(domain, tracks)`, `end_session`, `close`.
- `parse_private_parking_config(str)`: JSON ≤ 4096 bytes, fail-closed → None;
  `PrivateParkingTrigger(config, authorization=None)` acts only while the domain is LOCKED
  `PARKING_MOTORCYCLE`; an alert needs a crossing (ENTERED) AND a confirmed approach to a
  configured motor zone while unauthorized.
- Detector and tee are lease-aware; VideoService passes `lease=session.token`;
  `required_sinks` refusal → ERROR until Stop, 503. `AudioAlertGate.offer_local` never
  waits; the gate exists only with `SENTINEL_AUDIO_ALERT_ENABLED=true` and a valid CRITICAL
  clip outside the repository.

Configuration (O1, O2, O5; **C1 recorded**):
- `SENTINEL_PARKING_MODE`: unset or `legacy` → today's runtime, unchanged (private mode OFF
  by default); `private` → private mode; any other value → parking candidate path DISABLED
  (see "Disabled composition"), video runs, generic warning without the value.
- **C1 (PM decision, 2026-10-11): `SENTINEL_PRIVATE_PARKING_CONFIG_FILE`** — a local JSON
  file read ONCE at startup (size-capped read of at most 4096 + 1 bytes, UTF-8) and
  validated by `parse_private_parking_config`. Neither the path nor the content is ever
  logged (warning: "Private parking disabled: invalid configuration"). Unset, unreadable,
  too large or invalid → Disabled composition, never the legacy trigger. Setting the
  line/zones in a UI stays a separate task.
- `SENTINEL_AUDIO_LOCAL_ALERT_FILE` (O5): read by the audio factory (see "Audio factory
  extension"); missing/invalid → local audio off with a diagnostic, the CRITICAL clip
  stays as configured and is never a fallback for the local warning.

Audio factory extension (rev 3; in `runtime._create_audio_alert_gate`, no change to
`audio_alert_service.py`):
- Source fact: `AudioAlertGate(device, wav_path, duration, local_wav_path=None,
  local_duration_seconds=None)` already plays the local clip for `offer_local` and drops a
  local offer without a local clip (`dropped_no_local_clip`, never the CRITICAL clip). Its
  constructor RAISES `ValueError` when the local path lexically equals the CRITICAL path
  (`_same_path`) — passing such a path would fail the whole gate and silence the valid
  CRITICAL clip. The factory therefore validates the local clip BEFORE constructing the
  gate and only ever drops the local part.
- Order: (1) the existing enable flag, platform, cooldown and CRITICAL-clip checks run
  unchanged — any failure there disables audio exactly as today (legacy behaviour); (2)
  only with a valid CRITICAL clip, `_local_alert_clip(critical_path)` evaluates
  `SENTINEL_AUDIO_LOCAL_ALERT_FILE`: unset → no local clip, no warning; otherwise the same
  rules as the CRITICAL clip (`Path.resolve(strict=True)`, regular file, outside the
  repository, `probe_wav_duration` not None and ≤ `MAX_ALERT_SECONDS`) plus "not the
  CRITICAL asset"; (3) the gate is built with `local_wav_path`/`local_duration_seconds` only
  when the local clip passed, otherwise without them. If the local variable is set while
  audio is disabled, one warning says the local clip is unused (no path).
- Same-asset detection without printing paths: after both paths resolved strictly
  (symlinks, `..`, case resolved by the OS), the local clip is "the CRITICAL asset" when
  (a) the resolved paths are equal, or (b) `os.path.samefile` is true (same file reached
  through a hard link, a different spelling or a short name), or (c) the two files have the
  same size and the same SHA-256 digest (a byte-identical copy of the CRITICAL clip). The
  comparison happens in memory; only booleans are used. Diagnostic text is fixed:
  "Local audio alert disabled: invalid local clip configuration" or "...: the local clip
  must differ from the CRITICAL clip" — never the path, file name, digest or size.
- Unchanged: one device owner, shared first-wins slot and cooldown, per-event dedup across
  local and CRITICAL, Stop/new-session cancel, `fail_closed` (O3).

Private composition (mode `private`, valid config):
- `private_trigger = PrivateParkingTrigger(config, authorization=None)`;
  `sampler = EpisodeEvidenceSampler()`;
  `coordinator = PrivateParkingCoordinator(private_trigger, sampler, audio_gate=gate)`;
  detector parking-trigger slot = `coordinator`;
  VideoService buffer slot = `FrameSinkTee(event_frame_buffer, sampler)` — frames reach both;
  `required_sinks={"event frame buffer", "detector"}` (O-P1).
- **Bundle source (rev 2):** `EvaluationWorker(private_trigger, budget, sampler, client,
  enabled=False, candidate_strategies={PARKING_MOTORCYCLE: sampler.plan_for_candidate},
  audio_gate=gate)` — the SAMPLER is the worker's bundle source. The `EventFrameBuffer`
  keeps being fed through the tee (compatibility, O2) but is never a source of private
  evidence: no router, no fallback. Missing/expired/released evidence fails explicitly
  (`PlanUnavailable` → `EVIDENCE_UNAVAILABLE`, or a non-ready `BundleDecision`), with zero
  `EventFrameBuffer.wait_for_bundle` calls. `enabled=False` until T3d (O4): no claim, no
  budget, no dispatch; candidates expire after their TTL (O6); the local warning is
  independent of evaluation.
Disabled composition (invalid mode value; `private` with missing/invalid config):
- New `DisabledParkingTrigger` (one small module) that satisfies BOTH Protocols:
  `start_session`/`end_session` record nothing claimable; `observe` ignores input
  (counted); `claim_next_candidate` → None; `complete`/`fail` → False; `get_snapshot()` →
  `source_session_id=None`, `pending_event_id=None`; `wait_for_candidate(timeout)` blocks on
  its own condition until `wake_waiters()`, `close()` or the timeout, then returns False
  (no busy loop); `wake_waiters()` notifies; `close()` is idempotent, wakes waiters, and
  later `start_session` raises a sanitized refusal. It is placed in the detector slot and
  given to the worker (`enabled=False`, bundle source `EventFrameBuffer`, never reached).
  Neither `None` nor the legacy trigger is used as a silent substitute.
- Buffer slot = `EventFrameBuffer` directly (still fed); `required_sinks` empty; video runs.
Leases: no configuration — VideoService passes them to detector and tee; sampler,
`EventFrameBuffer`, scene classifier and worker stay id-only (documented boundary).
Lifecycle and shutdown:
- Stop/EOF/new session: VideoService ends tee (buffer, sampler) and detector (coordinator
  → private trigger) with the lease; the worker drives the gate lifecycle as today.
- `shutdown_services` gains optional keyword-only arguments for the private/disabled
  objects; order: `video.stop()` → gate close → worker close → client close → scene close
  → `detector.close()` → `coordinator.close()` → `private_trigger.close()` (or the disabled
  trigger) → tracker close → `tee.close()` → `event_frame_buffer.close()` →
  `sampler.close()` → budget close. Legacy arguments and order unchanged.
Runtime extraction (minimal, justified): the module-level singletons read `os.environ`
and build the YOLO model factory at import, so mode behaviour cannot be exercised with a
fake model, fake capture and fake audio in-process. Extract ONLY the mode-dependent
selection into one pure function, e.g. `build_parking_wiring(env, *, config_text, gate,
event_frame_buffer, budget, client, clock) -> ParkingWiring` (detector slot object,
buffer-slot sink, worker trigger, worker bundle source, `candidate_strategies`,
`required_sinks`, worker `enabled`, objects to close), plus the config reader
`_read_private_parking_config(env)`. The rest of `runtime.py` (CLIP, transports, budget,
gate, detector construction) stays as it is and calls the function once. Tests build a
`DetectorService` with a fake model and use the SAME function, so they test the
production selection, not a copy.

Files (definite): `backend/app/services/runtime.py` (mode + C1 reader + minimal
extraction + shutdown kwargs + audio factory extension), new
`backend/app/services/disabled_parking_trigger.py`,
`backend/app/main.py` (pass the new closables to `shutdown_services`),
`backend/.env.example` (`SENTINEL_PARKING_MODE`, `SENTINEL_PRIVATE_PARKING_CONFIG_FILE`,
`SENTINEL_AUDIO_LOCAL_ALERT_FILE`, values commented, no real paths), new
`backend/tests/test_private_parking_runtime_wiring.py` (in-process integration with
fakes + a few subprocess imports of `runtime` for the env → composition check), new
`backend/tests/test_runtime_audio_local_clip.py` (production factory with a fake WAV probe
and a fake device), this plan and `PLAN.md`; after implementation `ARCHITECTURE.md` (component/mode rows) and
`RELIABILITY.md` (failure table). Not changed: `audio_alert_service.py`,
`detector_service.py`, `video_service.py`, `private_parking_*` services, API, frontend.
Conditional (report before editing): `backend/tests/test_evaluation_runtime_integration.py`
only if the shutdown call, an import or its audio-factory assertions change there.

Acceptance (fakes: fake model/tracker tracks, `FakeCapture`, fake dispatcher, fake audio
device, fake clock; no server, device or provider):
1. Mode unset/`legacy` → composition identical to today (same classes/slots, no tee, no
   `required_sinks`); existing runtime and legacy suites green (§5: 6, 8).
2. Invalid mode; `private` with config unset, unreadable, > 4096 bytes or invalid → the
   `DisabledParkingTrigger` in the detector slot and the worker; no candidate ever, no
   legacy fallback, video RUNNING, `EventFrameBuffer` fed; Start/Stop, worker close and
   full shutdown complete (bounded); logs carry neither path nor content (§5: 2, 12).
3. Private valid → detector slot = coordinator, buffer slot = tee(buffer, sampler),
   `required_sinks` = {"event frame buffer", "detector"}; frames reach buffer and sampler;
   a refused tee/detector start → 503/ERROR (O-P1).
4. Bundle source: with evaluation enabled in the test only (to prove the source), evidence
   that exists is taken from the sampler; missing/expired evidence fails explicitly
   (`EVIDENCE_UNAVAILABLE`/non-ready decision); `EventFrameBuffer.wait_for_bundle` is called
   zero times (spy). Production stays `enabled=False` until T3d.
5. Cloud unavailable/disabled: the gate follows the session lifecycle
   (`apply_lifecycle` on start/stop), the local offer happens with the LOCAL clip, zero
   claims, zero budget reservations, zero dispatches; local clip missing → local audio off
   with a diagnostic, never the CRITICAL clip (§5: 1, 9, 14, 20, 21).
6. Expiry without observation: a published candidate is not claimable after its deadline
   (fake clock, no new frames); its sealed bytes are released at the next sampler call
   (call-based sweep, no timer) (§5: 29).
7. Stop/EOF/restart and failed cleanup: a sink/dependency end fails → ERROR, admission
   closed (409) until a Stop retry succeeds; afterwards a new session starts; late calls
   with the old lease/token change nothing and no old state is revived (§5: 11, 18).
8. Crossing only (ENTERED, no approach) → no alert and no local offer; a confirmed approach
   to a motor zone without authorization → exactly one alert and one local offer (§5: 9).
9. Shutdown closes every private/disabled object once in the order above; no hang.
10. No images, secrets, config values or paths in logs (§5: 12).
Audio factory acceptance (through `runtime._create_audio_alert_gate` with env set by
`monkeypatch`, `runtime.probe_wav_duration` replaced by a fake probe, `runtime.WinsoundDevice`
replaced by a fake device, placeholder files in `tmp_path` outside the repository; no WAV
is generated or played):
11. Valid local clip with evaluation/cloud disabled → the gate is built with the local
   clip (`local_clip_configured`); an `offer_local` starts the fake device with the LOCAL
   path (compared in memory).
12. Local clip unset → no warning; missing, unreadable, probe-invalid, too long, inside
   the repository, or the CRITICAL asset (same path, other spelling via `..`, hard link,
   byte-identical copy) → gate built WITHOUT a local clip, `offer_local` dropped as
   `dropped_no_local_clip` with no device start, one fixed warning, and `caplog` contains
   no path, file name or digest.
13. The CRITICAL clip works as configured before: a valid CRITICAL clip with any local
   outcome → the CRITICAL offer starts the device with the CRITICAL path; invalid CRITICAL
   or disabled flag → no gate (unchanged), the local setting does not enable audio.
14. Lifecycle, dedup and Stop cancel still hold with a local clip: a local grant then an
   accepted CRITICAL for the same event → no second grant; a new lifecycle epoch/Stop
   cancels a playing local clip (fake device `stop`); a stale-session local offer is
   rejected.
Dependencies not finished (block real use, not the fake-based wiring): T3d private
prompt (dispatch stays off, O4); gesture/authorization source — with `authorization=None`
every unauthorized approach, including the owner's, warns, so real activation waits for
the gesture or an explicit owner permission (O1); a separate local warning clip (the
approved voice WAV is the CRITICAL clip); the domain must lock to `PARKING_MOTORCYCLE`;
T3e real-device checks (owner permission each). Carried limitations: scene classifier
id-only, MJPEG generator `logger.exception`, hanging capture constructor /
always-failing release keep ERROR.
Separate UI/API work (not T3c): the operator flow **preview → configure line/zones →
Play** (draw the access line, inside point and motor zones on a preview frame, validate
with `parse_private_parking_config`, then start). Needs its own endpoints, frontend and
plan; T3c only reads the static C1 file at startup.

**T3c results (agent run, 2026-10-11, HEAD `05a95b8`, working tree; at that time awaiting independent re-review — completed at closure, fake integration verified)**
Files: `backend/app/services/runtime.py`, new `backend/app/services/disabled_parking_trigger.py`,
`backend/app/main.py`, `backend/.env.example`, new
`backend/tests/test_private_parking_runtime_wiring.py` (17), new
`backend/tests/test_runtime_audio_local_clip.py` (6); docs: this plan, `PLAN.md`,
`ARCHITECTURE.md` (component rows, modes, local warning, configuration keys),
`docs/RELIABILITY.md` (three failure rows), `docs/SECURITY.md` (config/clip paths never
logged — added because the PM listed SECURITY.md among the affected documents). No other
source or test file changed.
- Mode selection: `build_parking_wiring(env, *, event_frame_buffer, audio_gate, clock)` →
  `ParkingWiring` (mode, detector slot, worker trigger, buffer slot, bundle source,
  candidate strategies, required sinks, worker `enabled` override, private objects).
  Legacy (unset/`legacy`): a new `ParkingCandidateTrigger` in both slots, the
  `EventFrameBuffer` as buffer slot and bundle source, no required sinks, the evaluation
  configuration decides `enabled` — the module composition is unchanged. Private (valid
  C1 file): `PrivateParkingTrigger(config, clock)` (no authorization source),
  `EpisodeEvidenceSampler(clock)`, `PrivateParkingCoordinator(trigger, sampler, gate)` in
  the detector slot, `FrameSinkTee(buffer, sampler)` in the buffer slot, the SAMPLER as
  bundle source with `{PARKING_MOTORCYCLE: sampler.plan_for_candidate}`, required
  `{"event frame buffer", "detector"}`, worker `enabled=False`. Invalid mode or missing,
  unreadable, oversized (> 4096 bytes), non-UTF-8 or invalid config: one
  `DisabledParkingTrigger` in both slots, buffer slot = `EventFrameBuffer`, worker
  disabled. Warnings are fixed texts (no value, path or content).
- Module wiring: `event_frame_buffer` and `audio_alert_gate` are now built before the
  detector (the coordinator needs the gate); `parking_candidate_trigger` is the wiring's
  worker trigger (the legacy trigger in legacy mode, so existing imports keep working);
  `shutdown_services` gained `coordinator`, `frame_tee`, `sampler` keyword arguments
  (order: detector → coordinator → trigger → tracker → tee → buffer → sampler); `main.py`
  passes them (None outside private mode).
- Audio factory: `_validated_clip` (unchanged CRITICAL rules, shared), `_local_alert_clip`
  (only after a valid CRITICAL clip; same-asset check: equal resolved paths,
  `os.path.samefile`, or equal size + SHA-256; unprovable → treated as the same), the
  gate built with `local_wav_path`/`local_duration_seconds` only when the local clip passed;
  a set local clip while audio is disabled logs one "unused" warning. Flag, platform,
  cooldown and CRITICAL validation are unchanged.
- Tests (wiring, 17): legacy default and explicit (composition unchanged); 7 invalid
  cases (mode value, config unset/missing/too large/not JSON/invalid/not UTF-8) — disabled
  trigger, no candidate, no fallback, video RUNNING and buffer fed, full
  `shutdown_services` completes, no path/content in logs; private composition (tee feeds
  buffer AND sampler, required sinks, a closed tee → `VideoStartRefusedError`/ERROR until
  Stop); bundles only from the sampler (test-only enabled worker: request from sealed
  evidence, 5 frames, zero `EventFrameBuffer.wait_for_bundle` calls; released evidence →
  `EVIDENCE_UNAVAILABLE`, no request, zero buffer calls); cloud disabled: crossing + journey
  → no alert, confirmed approach → exactly one alert and one LOCAL-clip grant, zero claims,
  budget and dispatches, no second alert for the same episode, a late local offer after
  the session ended is not granted; expiry without observation (not claimable after the
  TTL; sealed bytes released by the next sampler call, `sealed_expired` = 1); Stop with a
  failing trigger end → ERROR and admission closed, Stop retry with the same lease → IDLE,
  file EOF, restart, late old-lease ends change nothing; shutdown order; the real
  `runtime` module imported in subprocesses selects legacy / private / disabled from the
  environment and never prints the config path.
  Tests (audio factory, 6): valid local clip passed and played as LOCAL; missing, probe-
  invalid, too long, inside the repository, same path, `..` spelling, byte-identical
  copy and hard link → local off with the fixed warning, CRITICAL kept, no `.wav`/path in
  logs; unset local clip silent; CRITICAL plays with any local outcome; flag off or invalid
  CRITICAL → no gate (local never enables audio), legacy default logs nothing; local grant
  then CRITICAL for the same event → one grant (`dropped_duplicate`), Stop cancels the
  playing local clip, stale-session local offer dropped.
- Mutations (agent run, scratch script, source restored): invalid mode → legacy, invalid
  config → legacy, private bundles from the `EventFrameBuffer`, private sinks not
  required, private worker following the cloud setting, no content comparison of clips,
  invalid local clip disabling the gate, config path logged, coordinator closed after its
  trigger — each fails the tests. Stability: both new files passed 3 consecutive runs.
- Tests (serial, fakes only, unique `--basetemp`): wiring 17, audio factory 6, runtime
  integration 83, audio 62, video 142, detector 28, all `test_private_parking_*` 258
  (incl. wiring), evaluation worker 119, current-evaluation API 10, legacy parking trigger
  19 — all passed; full backend suite **1175 passed**, 1 Starlette `DeprecationWarning`;
  `pip check` clean; `git diff --check` clean; new files without trailing whitespace,
  ending with a newline.
- Limitations: no gesture/authorization source — in private mode every unauthorized
  approach (the owner's included) raises the local warning, so real use waits for T3e/O1;
  private dispatch disabled until the T3d prompt; no local warning clip asset exists (the
  approved voice WAV is the CRITICAL clip); the trigger acts only after the domain locks to
  `PARKING_MOTORCYCLE`; with a configured Langflow transport the callback route stays
  mounted in private mode, but the disabled worker has no pending evaluation and rejects
  any callback; the frontend has no message for the 503 refusal and no mode indicator;
  configuration only through the C1 file (the preview → line/zone → Play UI is separate);
  carried limitations (scene classifier id-only, MJPEG `logger.exception`, hanging capture
  constructor / failing release keep ERROR). No server, real video, speaker, provider or
  device was run.

**T3c review delta 1 (reviewer findings via PM message "perbaikan delta wiring T3c", 2026-10-11; agent run, HEAD `05a95b8`, working tree)**
- Fail-closed config boundary: a small JSON with an integer coordinate or threshold of
  10**350 made `parse_private_parking_config` raise `OverflowError`, which escaped
  `_read_private_parking_config` (startup would fail instead of disabling the private
  path). `_read_private_parking_config` now catches `UnicodeDecodeError`, `ValueError`,
  `TypeError`, `OverflowError` and `RecursionError` around the decode + parse step ONLY →
  `DisabledParkingTrigger`, no legacy fallback, the fixed generic warning (no value, path
  or exception). Construction of the private services is outside that `try`: a service
  construction error still propagates (tested). The parser itself
  (`models/private_parking.py`) is outside the reviewed scope and unchanged. Float
  exponents such as `1e350` and deep nesting were already rejected by the parser (None).
- `.env.example`: the two new example paths contained a CR (`\r` from an escape inside a
  shell heredoc) and a TAB; both lines now use unambiguous placeholders
  (`<absolute path to …>`). A byte test asserts the file has no control byte except LF
  and documents the three new keys. All 11 changed files were scanned: no control byte
  except LF.
- Tests: wiring file now 22 (two OverflowError cases through the full invalid-config
  lifecycle and shutdown, the builder-level 10**350 regression, a construction error not
  hidden, the `.env.example` byte check); audio factory 6 unchanged. Mutations (agent run,
  scratch script, sources restored): removing `OverflowError` from the boundary fails 7
  tests; putting a CR back into `.env.example` fails the byte test. Stability: the wiring
  file passed 3 consecutive runs.
- Tests (serial, fakes only, unique `--basetemp`): wiring 22, audio factory 6, runtime
  integration 83, audio 62, all `test_private_parking_*` 263, video 142 — all passed; full
  backend suite **1180 passed**, 1 Starlette `DeprecationWarning`; `pip check` clean; `git
  diff --check` clean; byte scan of the 11 changed files: no control byte except LF.

**T3c review delta 2 — invalid-config matrix and status (PM message "delta penutupan T3c", 2026-10-11; agent run, HEAD `05a95b8`, working tree)**
- Test defect (fixture only, production source unchanged): `_invalid_cases` built every
  case at once and all file cases wrote the SAME path, so the last payload ("huge integer
  threshold") overwrote the others — the oversized, broken-JSON, invalid, non-UTF-8 and
  huge-coordinate cases did not read their own payloads. Now `INVALID_PAYLOADS` holds the
  payloads, `_invalid_env` writes only the running case to its own file
  (`<case>.json`), `_config_file` refuses to overwrite an existing file, each case reads
  its file back and a spy on `runtime.parse_private_parking_config` proves what the parser
  received (exactly that payload for broken JSON / invalid / huge numbers; nothing for
  oversized, non-UTF-8, missing, unset or an invalid mode). New test: all payload files
  coexist with distinct paths, each holding its own bytes, all different.
- Mutations of the fixture (agent run, test file restored): a shared file name with
  overwrite allowed → 1 failure; the old behaviour (the last payload overwriting the
  case file) → 6 failures (5 cases + the fixture test).
- Status: "Still to do in T3c" replaced by the implemented wiring; a "Status of T3c" list
  separated (at that time) implemented/awaiting re-review, completed (nothing of T3c yet) and features
  not available (T3d prompt, gesture, configuration UI, local clip asset, real devices).
- Tests: wiring file 23 passed in 3 consecutive runs; audio factory 6 passed; full suite
  not re-run (only a test fixture and documentation changed; see the targeted results).

**T3c — completed, fake integration verified** (closure on the PM instruction "penutupan dan
commit T3c", 2026-10-11; committed as "feat: wire private parking runtime with fail-closed
configuration"). Not proven on a real device, speaker or provider.
- Independent review (Codex via PM, 2026-10-11): **PASS** on the closure delta (review
  delta 2); 23 targeted tests passed in that last review. The full backend suite of
  **1,180 passed** comes from the earlier independent review of delta 1 — it was not
  re-run for this closure (no source change since; only a test fixture and documentation
  changed in delta 2).
- Closure check (agent run): HEAD `05a95b8`, nothing staged; the blob hashes reported by
  Codex were not included in the PM message, so they could not be matched directly; no
  source/test file changed after the delta-2 report (the last test-file write was the
  byte-identical restore of the fixture-mutation script). Blobs at closure:
  `runtime.py` `be76cd7`, `main.py` `0a9f686`, `.env.example` `13cfd05`,
  `disabled_parking_trigger.py` `ee45c69`, `test_private_parking_runtime_wiring.py`
  `a0bb1b2`, `test_runtime_audio_local_clip.py` `31383be`.
- Still NOT done (outside T3c): the private prompt and private dispatch (T3d); a
  gesture/authorization source; the configuration UI (preview → line/zones → Play); a
  local warning clip asset; real-device, speaker and provider tests (T3e).
**Former blocker — DetectorService cleanup retry: resolved by T3c0.1 (commit `e50d9e2`).**
History (from T3b review delta 1): `DetectorService.end_session` cleared its active
session before calling the parking-trigger slot; the first failure gave ERROR, but the
Stop retry found the session forgotten and VideoService wrote IDLE although the
coordinator/trigger never ended. T3c0.1 keeps every dependency obligation pending and
retries only the failed ones.
**Required sinks in private mode — capability done in T3c0.2 (commit `d479357`);
configuration and wiring completed in T3c (fake integration verified).** History (from T3b review delta 2): VideoService treated a refused
`FrameSinkTee.start_session` as an optional sink failure and kept the video running
without buffer/evidence. T3c0.2 added `VideoService(required_sinks=...)`: a refusal by a
required sink fails the start (ERROR until Stop, HTTP 503). T3c (completed, fake integration verified) passes `required_sinks={"event frame buffer", "detector"}` in private mode (PM
decision O-P1), wires the tee and the coordinator, and selects the mode through
`build_parking_wiring`; legacy and disabled modes keep `required_sinks` empty.

**Status of T3c, kept apart (2026-10-11; updated at closure):**
- *Completed — fake integration verified (closure, PM message "penutupan dan commit T3c"):*
  mode
  selection (`SENTINEL_PARKING_MODE`), the C1 config file reader (fail-closed),
  `DisabledParkingTrigger`, private wiring (coordinator, private trigger, sampler, tee,
  required sinks, sampler as the only private bundle source, worker disabled), the local
  clip in the audio factory, shutdown order — proven with fakes only, never on a real
  device. Prerequisites T3c0.1 (`e50d9e2`) and T3c0.2 (`d479357`) completed earlier.
- *Not available (not part of T3c):* the private prompt and private dispatch (T3d); a
  gesture/authorization source (none exists — every unauthorized approach warns); the
  configuration UI (preview → line/zones → Play, separate UI/API work); a local warning
  clip asset; any real-device, speaker or provider run (T3e, owner permission each).
Acceptance: 1, 2, 6, 8, 9 (pre-T3d), 11, 12, 14 with `FakeCapture`, fake dispatcher and
fake audio device.

**T3d — private prompt**
Files: `backend/app/services/gemini_evaluation_client.py`,
`backend/app/services/langflow_evaluation_client.py`, `backend/app/services/runtime.py`
(enable private dispatch), tests.
Scope: journey context selected by evidence type, frames labelled by role with actual
offsets, explicit statement that access without a gesture is not evidence of theft (O4);
legacy and retail prompts byte-identical.
Acceptance: mock-transport tests; 9 (post-T3d); no live request.

*T3d plan — backend, direct Gemini API* — **rev 2** (plan only, 2026-10-11, baseline
`d075207`; PM messages "backend T3d: rencana prompt privat" and the T3d design correction
of 2026-10-11). Implementation is authorized within this plan once the design findings
are closed by an independent review (Codex); until then no backend source is changed.
Rev 2 replaces the scoring rule of D3 (the "raise only for clear tampering" rule is
withdrawn) and adds prompt acceptance 9-12. Supersedes the file list above: Langflow is FROZEN (its client is not
changed; private requests never reach it).

Source facts (read at `d075207`, not run):
- `GeminiEvaluationClient._encode` builds one user turn: `_COMMON_RULES` with
  `_DOMAIN_CONTEXT[request.domain]`, then per frame a text part `Frame i of 5,
  t=±x.xx s` and the JPEG as `inlineData`; `generationConfig` holds only
  `responseFormat.text{mimeType: APPLICATION_JSON, schema: ASSESSMENT_SCHEMA}` — no
  thinking level, no `maxOutputTokens`, no temperature (model defaults; recorded as open
  in `docs/references/gemini-generatecontent.md`). `_parse` accepts only `finishReason
  STOP` and validates the assessment (score band ↔ status) unchanged.
- The prompt is selected by DOMAIN only. Private candidates are also
  `PARKING_MOTORCYCLE`, so today they would get the open-parking context ("a person
  stayed near a parked motorcycle"). Private requests are recognisable by
  `request.evidence` being `ProximityEvidence` (legacy: `ParkingCandidateEvidence`).
- A private seal is always 5 frames in time order: 1 `CROSSING`, 3 `JOURNEY`, 1
  `APPROACH` (`EvidenceFrame.role`), but the bundle's `SelectedFrame` carries no role, so
  the client cannot label frames by role today.
- Runtime: private mode builds the worker with `enabled=False` (T3c); the Langflow
  transport has no private prompt.

Design:
- D1 Frame roles: add an optional `role: str | None = None` (last field, keyword
  default) to `SelectedFrame`; the sampler's `wait_for_bundle` fills it from
  `EvidenceFrame.role`; `EventFrameBuffer` keeps `None`. Legacy/retail bundles,
  `_valid_bundle` and the worker are unchanged.
- D2 Prompt selection: `_encode` uses the PRIVATE context only when `request.evidence` is
  a `ProximityEvidence`, the domain is `PARKING_MOTORCYCLE` and the five frames carry
  exactly the roles CROSSING, JOURNEY, JOURNEY, JOURNEY, APPROACH in this order;
  a private request that does not satisfy this is `INVALID_REQUEST` (no HTTP, no fallback
  to the open-parking context). Every other request keeps today's text byte for byte.
- D3 Private context (rev 2; English instructions, Indonesian output, as the existing
  prompt). Content, in this order:
  - Scene and sequence: a private parking area with an access line; frame 1 = the person
    crossing into the area (CROSSING), frames 2-4 = the walk inside (JOURNEY), frame 5 =
    the approach to a parked motorcycle (APPROACH). Judge the visible ACTIONS across the
    whole CROSSING → JOURNEY → APPROACH sequence, not a single frame.
  - What is NOT evidence of theft: crossing the line, the missing authorization gesture,
    looking around, walking through the area and approaching or standing next to a
    motorcycle — alone or together. Owners, family, staff and visitors often do all of
    this without a gesture.
  - What to describe when visible, exactly as far as the frames show it: hands on or
    interaction with the lock or the handlebar, handling of components (ignition, cables,
    seat, body panels), and movement of the motorcycle (pushed, lifted, rolled). Weigh
    these in the score as the visible evidence supports them; repeated or forceful
    handling of the lock/ignition or moving a parked motorcycle away weighs more than a
    brief touch.
  - Never infer identity, ownership or permission ("izin") from the images, nor intent or
    a crime from appearance, clothing or presence.
  - Ambiguous, occluded or low-quality evidence is stated as ambiguous ("tidak terlihat
    jelas" / "ambigu") and must not be turned into certainty in either direction.
  - The local access warning was issued by a separate rule (no gesture seen); this
    assessment is independent of it and neither confirms nor cancels it.
  Frame labels: `Frame i of 5 (crossing|journey|approach), t=±x.xx s` with the actual
  offsets. The score bands, status rule and output fields are the existing ones (D4).
- D4 Output and validation unchanged: the same `ASSESSMENT_SCHEMA` (shared by all
  domains, so it is not edited), `_parse`, `ThreatAssessment.validate`, single acceptance
  path through `EvaluationWorker.accept_callback`. The private CRITICAL clip path and
  dedup with the local warning stay as implemented.
- D5 Cost (UMKM): no change to model, thinking level, `maxOutputTokens` or temperature
  (open decisions below). Within the prompt only: "reasoning at most two short
  sentences; clothing/observedAction a few words; lists only visible items, at most a
  few" (private context only; legacy text unchanged). Existing budget rules stay: one
  evaluation in flight, 10 s spacing, 2 per 60 s, candidate TTL 2 s (O6), no retry,
  optional `SENTINEL_EVALUATION_MAX_DISPATCHES` per process.
- D6 Enabling private dispatch (runtime): the private worker is enabled only when ALL
  hold — mode `private` with a valid config; new flag
  `SENTINEL_PRIVATE_PARKING_EVALUATION=true` (default unset/false; any other value →
  off with a generic warning); `SENTINEL_EVALUATION_TRANSPORT=gemini`; the existing
  Gemini readiness (`evaluation_enabled`: key/model/timeout below the result deadline,
  callback timeout, dispatch limit valid). Langflow selected → private dispatch stays off
  with the warning "Private parking evaluation disabled: the selected transport has no
  private prompt". Legacy and disabled modes ignore the flag. Enabling the flag is a
  configuration change only; a LIVE request still needs an explicit owner permission
  per run (RELIABILITY live-test protocol, `SENTINEL_EVALUATION_MAX_DISPATCHES=1`).

Files: `backend/app/services/gemini_evaluation_client.py` (D2, D3, D5),
`backend/app/models/event_frame.py` (D1, one optional field),
`backend/app/services/private_parking_evidence_sampler.py` (D1, fill the role),
`backend/app/services/runtime.py` (D6 rule), `backend/.env.example` (the new flag,
commented); new `backend/tests/test_gemini_private_prompt.py`; new
`backend/tests/test_private_parking_evaluation_enable.py`; this plan. Not changed:
`langflow_evaluation_client.py` (frozen), `evaluation_worker.py`, `ASSESSMENT_SCHEMA`,
API, frontend, `AGENTS.md`, `PLAN.md` (shared; status update only after PM
coordination), `ARCHITECTURE.md`/`RELIABILITY.md` (proposed after implementation, with PM
coordination).
Configuration changes: one new variable `SENTINEL_PRIVATE_PARKING_EVALUATION`
(`true` | `false`, default off). Effective private dispatch additionally needs
`SENTINEL_PARKING_MODE=private`, a valid `SENTINEL_PRIVATE_PARKING_CONFIG_FILE`,
`SENTINEL_EVALUATION_TRANSPORT=gemini` and the existing Gemini variables. No model,
thinking or token variable is added.

Acceptance (fakes and `httpx.MockTransport` only; no live request, device or speaker):
1. Private request (ProximityEvidence + roles) → the payload has the private context, the
   "missing gesture is not evidence of theft" statement, the brevity rule and role labels
   in order with the actual offsets; `generationConfig` equals today's (same schema, no
   thinking/token/temperature keys).
2. Legacy parking and retail payloads are byte-identical to `d075207` (golden payloads
   built in the test from fixed requests; any diff fails).
3. A private request with missing roles, wrong order, wrong count or another domain →
   `INVALID_REQUEST`, zero HTTP calls, no fallback to the open-parking context.
4. Response handling unchanged: valid JSON → accepted callback; band mismatch, extra
   field, non-STOP finish, blocked prompt → the existing rejections; private and legacy
   share them.
5. Sampler bundles carry the roles CROSSING, JOURNEY×3, APPROACH; `EventFrameBuffer`
   bundles carry `None`; `_valid_bundle` still accepts both.
6. Enable matrix (D6): flag unset/false/invalid, Langflow selected, Gemini not ready →
   private worker disabled (warning without values); flag true + Gemini ready → enabled;
   legacy/disabled modes unaffected by the flag.
7. End to end with fakes: private stack, worker enabled by the D6 rule, Gemini client over
   a MockTransport → at most one dispatch per episode, the bundle passes `_valid_bundle`,
   a CRITICAL result after the local warning for the same event is NOT granted a second
   time (dedup), a failed dispatch is not retried, expired candidates are never sent.
8. Logs carry no prompt text, images, base64, key or response body (existing rule).
9. Prompt case "owner without a gesture": a fixture describing an ordinary entry and
   approach — the request carries the D3 statement that crossing, a missing gesture and
   approaching are not evidence of theft, and a mocked NORMAL response is accepted.
10. Prompt case "ordinary approach": a mocked response with NORMAL/WARNING and a
   reasoning that mentions looking around passes validation unchanged; no client-side
   rule raises or lowers the model's score.
11. Prompt case "ambiguous interaction": the request carries the ambiguity instruction;
   a mocked WARNING response stating "tidak terlihat jelas" is accepted as is.
12. Prompt case "visible manipulation": the request carries the lock/handlebar/
   component/movement instruction; a mocked CRITICAL response is accepted and goes
   through the existing CRITICAL path (audio dedup with the local warning unchanged).
   Acceptance 9-12 test the REQUEST TEXT and the unchanged handling of fixed MOCKED
   responses only. They are not evidence of model accuracy; real model behaviour on
   these cases is unproven until an owner-authorized live evaluation.
Kept unchanged (rev 2): model, thinking level, `maxOutputTokens`, temperature, image
quality/size, `ASSESSMENT_SCHEMA`, the legacy and retail prompts (byte-identical), and the
frozen Langflow client.
Open decisions for the PM (no change without an explicit decision): thinking level and
`maxOutputTokens` for cost (currently model defaults); image size/quality sent per frame
(currently the captured JPEG, ≤ 12 MiB per bundle); whether the private prompt may add a
schema-level length limit (would change the shared schema, so not proposed).
Dependencies not finished: gesture source (O1); the first live private evaluation needs
owner permission; the local warning clip asset; T3e device checks.

**T3d results (agent run, 2026-10-11, HEAD `d075207`, working tree; implemented — at that time awaiting independent review; see "T3d independent review")**
Design review (Codex via PM, 2026-10-11): **PASS WITH LIMITATIONS** for rev 2; the plan
blob matched the reviewed version (`098dcf22f21d1502451e8fcf711fe264f19d083d`) before any
source change. Implementation authorized within this plan by the same PM message.
Files: `backend/app/models/event_frame.py` (`SelectedFrame.role: str | None = None`),
`backend/app/services/private_parking_evidence_sampler.py` (bundle frames carry
`role=frame.role.value`), `backend/app/services/gemini_evaluation_client.py`
(`PRIVATE_FRAME_ROLES`, `_PRIVATE_PARKING_CONTEXT`, private selection and role labels in
`_encode`), `backend/app/services/runtime.py` (`_private_parking_evaluation_requested`,
`evaluation_worker_enabled`, used for the production worker), `backend/.env.example`
(`SENTINEL_PRIVATE_PARKING_EVALUATION`, commented, default false); new
`backend/tests/test_gemini_private_prompt.py` (15) and
`backend/tests/test_private_parking_evaluation_enable.py` (13); this plan. Not changed:
`langflow_evaluation_client.py`, `evaluation_worker.py`, `ASSESSMENT_SCHEMA`,
`generationConfig`, model/thinking/token/temperature/image handling, API, frontend,
`AGENTS.md`, `PLAN.md`, `ARCHITECTURE.md`, `RELIABILITY.md`.
- Behaviour: a request whose evidence is `ProximityEvidence` gets the private context
  (sequence CROSSING → JOURNEY → APPROACH; crossing / no gesture / looking around /
  approaching are not evidence of theft; lock, handlebar, component handling and
  motorcycle movement described and weighed as visible; no identity, ownership or
  permission inferred; ambiguity stated; independent of the local access warning;
  brevity rule) and labels `Frame i of 5 (crossing|journey|approach), t=±x.xx s`. It must
  be `PARKING_MOTORCYCLE` with the roles in exactly that order, otherwise
  `INVALID_REQUEST` before HTTP, never the open-parking context. Legacy and retail
  payloads are byte-identical (SHA-256 captured from the client at `d075207` before the
  change and asserted in the tests). Private dispatch: legacy keeps the evaluation
  configuration; disabled mode never; private only with
  `SENTINEL_PRIVATE_PARKING_EVALUATION=true` + transport `gemini` + the existing Gemini
  readiness; Langflow → off with a warning; invalid flag → off with a warning (no value).
- Tests: private payload text, labels and unchanged `generationConfig`; golden legacy and
  retail payloads (also with roles on non-private evidence); 5 invalid private requests
  rejected with zero HTTP calls; shared response validation; buffer bundles carry no role;
  prompt cases 9-12 (owner without gesture, ordinary approach, ambiguous interaction,
  visible manipulation) — request text + unchanged handling of fixed MOCKED responses,
  NOT evidence of model accuracy; logs free of prompt, image and key; activation matrix
  (7 private cases, legacy/disabled ignore the flag); the real `runtime` module (subprocess)
  enables the private worker only with the flag; end to end with fakes (private wiring,
  real Gemini client over `MockTransport`): local LOCAL-clip warning first, one dispatch
  with role labels CROSSING, JOURNEY×3, APPROACH from the sampler bundle, the CRITICAL
  result for the same event dropped as a duplicate (one grant in total), no second
  dispatch for the same episode, a failed (HTTP 500) dispatch not retried, an expired
  candidate never sent.
- Mutations (agent run, scratch script, sources restored): private evidence with the
  open-parking context, role order not validated, invalid private request falling back,
  sampler not filling the role, flag ignored, Langflow allowed — each fails the tests.
  Stability: both new files passed 3 consecutive runs.
- Tests (serial, fakes only, unique `--basetemp`): private prompt 15, activation 13,
  Gemini client 117, Gemini worker transport 40, event frame buffer 21, runtime wiring 23,
  audio factory 6, runtime integration 83, evaluation worker 119, all
  `test_private_parking_*` 277 — all passed; full backend suite **1209 passed**, 1
  Starlette `DeprecationWarning`; `pip check` clean; `git diff --check` clean; changed and
  new files: no control byte except LF, no trailing whitespace, final newline.
- Limitations: no live request was made — the model's behaviour on the four prompt cases
  is unproven; the first live private evaluation needs the owner's permission
  (`SENTINEL_EVALUATION_MAX_DISPATCHES=1`); no gesture source (every unauthorized approach
  still raises the local warning); no local clip asset; thinking level, `maxOutputTokens`
  and image size remain open PM decisions; Langflow stays frozen without a private prompt.

**T3d independent review (Codex via PM message "delta penutupan T3d", 2026-10-11)**
- Verdict: **PASS WITH LIMITATIONS**. Reviewer's own run (relayed, not re-run by this
  agent): 291 target tests and the full backend suite of 1,209 tests passed, one
  Starlette `DeprecationWarning`; `pip check` clean; diff/whitespace checks clean.
- Not reproduced by the reviewer: the agent's mutation and stability runs above (agent
  runs only).
- Not proven (by the review or the agent): model accuracy on the prompt cases, gesture
  authorization, latency, real devices, speaker or provider behaviour.
- Closure delta (agent run, same day): only this plan's status lines and the module
  docstring of `runtime.py` changed — the docstring now describes the private evaluation
  gate (`evaluation_worker_enabled`); the module AST without docstrings is identical, so
  no behaviour changed and the full suite was not re-run. Status now: implemented,
  independently reviewed, awaiting closure verification; not committed.

**T3d closure (PM authorization "penutupan dan commit T3d", 2026-10-11)**
- Final delta after the independent review: two docstrings in `runtime.py` only — the
  module docstring and the `build_parking_wiring` docstring now describe the private
  evaluation gate (`evaluation_worker_enabled`) instead of "disabled until the private
  prompt". Each delta was verified by the agent as docstring-only (module AST without
  docstrings identical before/after: `623dd96` → `4cd7512` → `99ec59a`); Codex's read-only
  closure check (via PM): **PASS, no blocker**, runtime behaviour identical to the reviewed
  version, plan status consistent. The full suite was not re-run (no behaviour change);
  the last full run is the reviewer's 1,209 passed.
- Committed paths and blobs (hashes matched before staging): `backend/.env.example`
  `bbddcc9`, `backend/app/models/event_frame.py` `eb643cf`,
  `backend/app/services/gemini_evaluation_client.py` `60c86a6`,
  `backend/app/services/private_parking_evidence_sampler.py` `afc0b08`,
  `backend/app/services/runtime.py` `99ec59a`, `backend/tests/test_gemini_private_prompt.py`
  `601c585`, `backend/tests/test_private_parking_evaluation_enable.py` `91470b4`, and this
  plan. Not included: frontend/F1, `AGENTS.md`, `PLAN.md`, root reference files.
- T3d is completed for the backend prompt and dispatch gate with fakes only; no live
  provider request was made. T3 remains open.

**T3e — real gesture and device checks** (each step needs explicit owner permission)
Scope: gesture `AuthorizationSignalSource` (dependency decision); local clip asset
(separate task, O5); device latency and clip identity by ear; one live provider check
with `MAX_DISPATCHES=1`; real-device private mode before a gesture only with a specific
owner permission (O1).

## 8. Review status

**rev 8 — design PASS WITH LIMITATIONS** (verdict reported by the owner in the PM
message "finalisasi rencana dan implementasi T3a1", 2026-10-04; the reviewer's own
evidence was not included in the relay). Acceptance 24 and 30 finalised.

- **T3a1: completed — core only.** Implemented (deltas 1–2), reviewed PASS WITH
  LIMITATIONS for the core (review Codex disampaikan melalui owner, 2026-10-04; recorded
  on the PM instruction "lengkapi bukti penutupan T3a1", 2026-10-05), committed on the
  owner's instruction "commit T3a1" (2026-10-05) as "feat: prepare private parking
  integration contracts". Not wired into runtime. The plan stays in `active/` because
  T3a2–T3e remain.
  - Reviewer findings: the token carries `episode_instance`; the sampler checks
    instance, episode id and generation before a seal changes evidence; the interleaving
    test shows an old seal cannot take the evidence of a new episode with the same id,
    and the new episode's token still works.
  - Reviewer's independent verification (as relayed, not run by this agent): 385
    target tests passed; 1,039 backend tests passed with fakes and a unique
    `--basetemp`; `pip check` clean; `git diff --check` passed; one Starlette
    `DeprecationWarning`; mutation testing **not** reproduced by the reviewer (the
    mutation results in "T3a1 results/delta 1/delta 2" are agent runs only).
  - Reviewer limitations: a core pass, not runtime integration; part of the evidence
    lookup still uses `event_id`; T3b must pass the correct identity pair/triple and
    test that fields from different alerts are never mixed.
  - Agent-run evidence stays separate in "T3a1 results" and "T3a1 delta 1/2".
- Blob check before closure (agent run, 2026-10-05, HEAD `7c41672`): all 12 core
  source/test paths and `AGENTS.md` match the preflight `git hash-object` values
  exactly, so the full suite was not re-run.
- Closure preflight (agent run, 2026-10-04, HEAD `7c41672`): no source/test edit since
  the last verification (385 target / 1039 full passed), so the suite was not re-run;
  `git diff --check` clean; secret scan of added lines and the new test file clean;
  CRLF working copies normalise to pure-LF blobs (`git hash-object` = LF-normalised hash).
- Limitation kept open: `event_id`-based lookup in `wait_for_bundle` and failure
  records; T3b must pass each alert's own triple (see T3b).
- **T3a2: completed — lifecycle only, reviewed PASS** (review deltas 1–2 applied; see
  "T3a2 results"; committed on the owner's instruction, 2026-10-08).
- **T3b: completed — core only** (PM decisions K1–K7, review deltas 1–2; see "T3b
  results", "T3b review delta 1/2"). Committed on the PM instruction "penutupan dan commit
  core T3b" (2026-10-11) as "feat: add private parking coordinator and bounded frame tee".
  Not wired into runtime; private parking integration (T3) is not complete.
  - Independent review (Codex via PM, 2026-10-11): **PASS** on the overflow delta
    (delta 2); 420 fake-based tests passed, `pip check` clean, `git diff --check` and
    whitespace checks passed (reviewer's run, relayed; not re-run by this agent).
  - Full backend suite **1100 passed** (1 Starlette `DeprecationWarning`): implementer
    run (agent, 2026-10-11, HEAD `a23fe62`, working tree), not an independent result.
  - Closure check (agent run, 2026-10-11): all eight source/test blobs equal the
    versions submitted for the delta-2 review (`git hash-object`), so no test was re-run.
  - Not proven: runtime wiring, real video, webcam, speaker, provider requests.
  - The 2026-10-09 re-review of delta 1 was a self-review by the implementing agent
    (PASS WITH LIMITATIONS, findings D1/D2, both corrected), not an independent review.
  - Prerequisites for T3c as recorded at T3b closure (historical): (1) `DetectorService`
    cleanup retry — resolved by T3c0.1 (`e50d9e2`); (2) a refused tee start must fail the
    private-mode start — capability done in T3c0.2 (`d479357`); private-mode
    configuration and wiring since completed in T3c (fake integration verified).
- **T3c0 (lifecycle prerequisites): design rev 4 final, reviewed PASS WITH LIMITATIONS**
  (Codex via PM, 2026-10-11). **T3c0.1 completed** — independent review PASS WITH
  LIMITATIONS (Codex via PM: 74 target + 1,110 full tests passed, `pip check` and
  diff/whitespace clean; mutation and stability runs not reproduced by the reviewer);
  committed as "fix: preserve detector cleanup and isolate stale delivery". Limitations:
  scene classifier not covered, id-only calls unprotected, lifecycle can be held by a
  consumer. Commit `e50d9e2`. (Historical, at T3c0.1 closure: T3c0.2 was not yet
  authorized.) T3c0.2 plan aligned with the implemented lease contract (rev 5,
  2026-10-11). **T3c0.2 authorized** (rev 5 final, PM 2026-10-11) and **completed, commit
  `d479357`** — independent review PASS WITH LIMITATIONS (Codex via
  PM: 216 target tests, 1,152 full backend); committed as "fix: isolate video start
  rollback and retain capture cleanup". Limitations: a hanging capture constructor or a
  permanently failing release keeps ERROR (no timeout / restart needed). T3c0.2 delivers
  the required-sink/refusal capability only; at that time private-mode configuration
  and runtime wiring (T3c) were not authorized and not done (historical — see the T3c
  entry below for the current state).
- **T3c (runtime wiring and mode): completed — fake integration verified** (design PASS
  WITH LIMITATIONS; review deltas 1–2; closure delta PASS, Codex via PM, 23 targeted tests;
  full suite 1,180 passed in the earlier independent review, not re-run). Committed as
  "feat: wire private parking runtime with fail-closed configuration". Not proven on a
  real device. At that closure T3d prompt, gesture, configuration UI, local clip asset
  and real tests remained open; the T3d prompt and the configuration UI are done since
  (T3d entry below; P1). Rev 3: C1 recorded (`SENTINEL_PRIVATE_PARKING_CONFIG_FILE`);
  sampler is the private bundle source; `DisabledParkingTrigger` for invalid
  modes/configs; audio factory validates the local clip without affecting the CRITICAL
  clip. The preview → line/zone → Play operator flow is a separate UI/API work item.
- **T3d (private prompt, private dispatch gate): completed** — independent review PASS WITH
  LIMITATIONS and closure check PASS without blocker (Codex via PM, 2026-10-11); committed
  as "feat: enable direct Gemini evaluation for private parking" (see "T3d results", "T3d
  independent review" and "T3d closure"). Not proven: model accuracy, gesture, latency,
  real devices. T3 as a whole is NOT complete: the gesture source, the local clip asset
  and T3e remain, and the private trigger, video path and VLM evaluation are not
  validated on real footage or in the field. The configuration UI is no longer open: it
  was delivered by P1 (P1d `67a04fb`, editor fix `8a6b8e5`).
- **T3e: not started, not authorized** — waits for its own owner authorization
  (`docs/PLANS.md`).
- Backlog recorded 2026-10-11 (not started): local warning voice clip — owner-approved
  candidate text “Permisi, ada keperluan apa, ya? Kalau tidak ada keperluan, tolong
  jangan pegang motornya.”, Indonesian TTS, natural, questioning then firm, separate from
  the CRITICAL clip, duration within the current validator; asset not created, deterrent
  effect unproven (tech-debt #15). Configuration UI flow:
  [P1](private-parking-preview-configuration.md), committed `b3b7f3a`…`8a6b8e5` (fakes;
  owner real-video UI test of Play, Stop, delete, line and ROI only, report baseline
  `67a04fb`, commit of the running app not recorded). Camera health: draft
  [C1H](camera-health-monitoring.md).
- Open for review: the deviations and limitations listed under "T3a1 results", the
  worker identity extension in "T3a1 delta 1", the episode-instance contract in
  "T3a1 delta 2", and the audio point in §4.7.
