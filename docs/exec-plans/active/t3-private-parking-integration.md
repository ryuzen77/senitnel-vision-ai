# Exec plan T3 — private parking integration

Code baseline `36ffa20` (T2 sampler); plan HEAD `7c41672` (H1 docs). §4.1 records the
facts before T3a1. §4.2, §4.4, §4.5, §4.6 and §4.7 describe trigger, sampler, worker and
audio-gate contracts as implemented by T3a1 (completed, core only, not wired into runtime).
§4.3 (`VideoService` lifecycle) is planned for T3a2 and §4.8 for T3b/T3c; neither is
implemented. T3a2–T3e code does not exist.
Status: see §8 — **rev 11: design PASS WITH LIMITATIONS (owner-reported); T3a1 completed — core only (reviewed PASS WITH LIMITATIONS, Codex via owner; committed as "feat: prepare private parking integration contracts"); not wired into runtime; T3a2–T3e not started, not authorized. T3 as a whole is not complete.**

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

### 4.3 Start/Stop lifecycle (PM decision of the "rev 5 review" message; planned for T3a2, not implemented)

Rules: invalidation is immediate; cleanup may wait for an in-memory start that is
already running; after cleanup no component is active in the old session; a late
start result never disturbs a newer session.

```
_atomic_reserve():
    with _lock:                                   # unchanged reservation
        require IDLE; sid = new id; _session_id = sid; _released = False; STARTING
    with _lifecycle_lock:                         # order: _lifecycle_lock → _lock
        for sink in (buffer_or_tee, detector, scene, worker):
            with _lock: alive = (_session_id == sid and not _released)
            if not alive: break                   # skip remaining starts
            sink.start_session(sid)               # in-memory only, never blocks on I/O
    return token

stop():
    with _lock: if IDLE: return; STOPPING; _start_token = None
    _release_resources()                          # called without _lock (existing)
    with _lock: IDLE

_release_resources():
    with _lock:                                   # immediate invalidation (existing)
        if _released: return
        _released = True; capture sid, cap, thread, upload
    join thread / release cap / delete upload     # no lock held (existing)
    clear packet slot under _packet_cond          # existing, no _lock
    with _lifecycle_lock:                         # waits only for a running start fan-out
        for sink in (worker, detector, scene, buffer_or_tee):
            sink.end_session(sid)                 # services ignore a non-matching id
```

Lock order: `_lifecycle_lock` → `_lock` (brief read) → each service lock. `_lock` is
never held while acquiring `_lifecycle_lock`; `_lifecycle_lock` is never held during
join, capture release, file deletion, HTTP or device calls (HTTP happens on the worker
thread, device calls on the gate thread, neither inside `start_session`/`end_session`).
Why it holds: a start fan-out for `sid` checks `_released` before every call; the end
fan-out for `sid` runs after it and covers every sink that was started; session B can
only be reserved after `stop()` returned (state `IDLE`), so a late start for A sees
`_session_id != A` and stops. Coordinator and tee also keep generations bumped by their
own `start_session`/`end_session` and drop results of calls captured under an older one.

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

### 4.8 Other rules (planned for T3b/T3c, not implemented)

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
Files: `backend/app/services/video_service.py` and its tests.
Scope: §4.3 `_lifecycle_lock`, per-call revive check, serialized end fan-out.
Acceptance: 17, 18; all existing video and integration tests green.

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

**T3c — runtime wiring and mode**
Files: `backend/app/services/runtime.py`, `backend/app/main.py`, `backend/.env.example`,
integration tests.
Scope: mode/config/local-clip variables, worker `enabled=False` in private mode until
T3d, tee wiring, shutdown order.
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
- **T3a2, T3b, T3c, T3d, T3e: not authorized** — each waits for its own owner
  authorization (`docs/PLANS.md`).
- Open for review: the deviations and limitations listed under "T3a1 results", the
  worker identity extension in "T3a1 delta 1", the episode-instance contract in
  "T3a1 delta 2", and the audio point in §4.7.
