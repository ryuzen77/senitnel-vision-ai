# Exec plan T3 — private parking integration

**Review status: DRAFT — not reviewed, not approved, not implemented.**
Baseline `36ffa20` (2026-10-04). No code for T3 exists.

## 1. Goal

When the owner configures an entry line and motor zones, `PARKING_MOTORCYCLE` runs in
*private mode*: a person who crosses in and confirmedly approaches a parked motorcycle
without authorization produces (a) one **local access warning** that never waits for the cloud and (b) one
cloud evaluation built from journey evidence (crossing → three journey frames → approach).

## 2. Scope / non-scope

In scope (phased, see §7): trigger markers, sampler feed alias, audio local offer,
a coordinator, runtime/env wiring, fake tests.

Not in scope: a real gesture source (no pose model, no dependency), frontend changes,
retail wiring, prompt changes, threshold calibration, live provider or speaker runs,
cost/latency optimisation.

## 3. Dependencies

- T1 trigger core `d999885` and T2 evidence sampler `36ffa20` (both core only).
- Existing worker handoff/acceptance contracts and `AudioAlertGate` (runtime).
- **Open owner decisions** (must be answered before approval):
  1. *No gesture source exists.* With no `AuthorizationSignalSource`, every entrant is
     unauthorized, so every confirmed approach warns and spends a dispatch. Proposal:
     allow wiring, keep private mode off unless explicitly configured, document it.
     Alternative: defer runtime activation until a gesture source exists.
  2. *Mode selection*: private mode replaces the legacy parking trigger for the
     process when a valid config is set (no router, no mixed candidates); invalid
     config falls back to the legacy parking path with a generic warning.
  3. *Local warning channel*: reuse `AudioAlertGate` through a new local-offer method
     so that a later CRITICAL for the same `event_id` never plays twice.

## 4. Design

```
VideoService capture → sampler.offer(BufferedFrame)          capture thread, O(episodes), no wait
DetectorService → PrivateParkingCoordinator.observe(domain, tracks)    detector thread
    trigger.observe(...)
    trigger.pop_markers() → sampler.begin / cancel / end_episode
    trigger.pop_alerts()  → sampler.seal(event_id, triggered_at) → audio_gate.offer_local(...)
EvaluationWorker  trigger = PrivateParkingTrigger, frame buffer = sampler,
                  strategy PARKING_MOTORCYCLE = sampler.plan_for(event_id)
```

- The local warning never depends on evidence; it is offered even if `seal` fails.
- Missing/expired evidence → `plan_for` is `None` → worker records `BUNDLE_INVALID`
  and does not dispatch. **No fallback** to `EventFrameBuffer` or the −2…+0.4 s plan
  (in private mode the worker's buffer *is* the sampler).
- The warning is not a CRITICAL assessment and never creates one (see ARCHITECTURE §4).
- No service lock is held while calling another service except the existing
  worker → gate order. Stale markers are dropped by each service's session check.
- Budget is reserved before the plan is built, so missing evidence spends one budget
  grant but no dispatch quota. Accepted and documented.

Existing files to change (each change reviewed):

| File | Change |
|---|---|
| `services/private_parking_trigger.py`, `models/private_parking.py` | bounded `pop_markers()`: started (with `crossed_in_at`), authorized, ended |
| `services/private_parking_evidence_sampler.py` | `offer(frame)` alias so `VideoService` can feed it unchanged |
| `services/audio_alert_service.py` | `offer_local(event_id, session_id)`: same session/fault/dedup/cooldown rules, no model status |
| `services/evaluation_worker.py` | frame-buffer type becomes a small Protocol; no behaviour change |
| `services/runtime.py`, `main.py`, `backend/.env.example` | `SENTINEL_PRIVATE_PARKING_CONFIG` (JSON, fail-closed), mode selection, shutdown order |

New file: `services/private_parking_coordinator.py`.

## 5. Acceptance

- Variable unset or invalid → runtime objects and behaviour identical to today (legacy parking).
- Valid config → one local offer and at most one dispatch per unauthorized episode;
  authorized episodes produce neither; evidence failure still warns locally and never dispatches.
- A later CRITICAL for the same event plays no second clip; NORMAL/WARNING never play.
- Local latency is measured, not assumed: fake integration asserts the local offer
  happens in the same observe cycle as the alert, without waiting for evidence or
  dispatch; an owner-authorized device check records trigger → playback start latency
  against the under-one-second target. Until then the target stays unverified.
- Stop/new session cleans trigger, sampler, coordinator, worker and gate; late input
  from an old session changes nothing.
- No images, secrets or config values in logs.

## 6. Risks / limitations

- Without a gesture source every entrant is unauthorized (decision 1).
- `UNKNOWN_INSIDE` (first seen inside, track ID switch, detector cold start) never warns.
- Track ID switches can split a person (no event) or make the motorcycle that entered
  with someone look like "another" one (possible false warning).
- T1/T2 thresholds are uncalibrated; evidence spacing widens on long journeys.
- Each warned episode can cost one billed request and seconds of provider latency;
  quota and budget limits apply but cost per day is not estimated.

## 7. Verification and phases

Fake unit and fake integration tests only; full backend suite with unique
`--basetemp`, `pip check`, `git diff --check`, whitespace check.

1. **T3a core deltas** — markers, sampler alias, gate `offer_local`, worker Protocol (+ tests).
2. **T3b coordinator** — new file (+ tests: markers→sampler, alert→seal+local offer,
   authorization cancels evidence, isolated errors).
3. **T3c runtime wiring** — runtime, main, env example (+ tests: mode selection,
   worker with sampler bundle passes `_valid_bundle`, missing evidence → no dispatch,
   end-to-end with `FakeCapture` and fake client, Stop mid-flow).

Each phase is reviewed and committed separately. A live check with a real camera
view, owner permission and `MAX_DISPATCHES=1` is a separate task after T3c.
