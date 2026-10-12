# PLAN.md — where the work is

This file only points. Plans themselves live in [`docs/exec-plans/`](docs/exec-plans/);
the rules for writing and reviewing them are in [`docs/PLANS.md`](docs/PLANS.md).
Last reviewed at commit `8a6b8e5` (2026-10-12).

## Active

| Plan | Status |
|---|---|
| [T3 — private parking integration](docs/exec-plans/active/t3-private-parking-integration.md) | **T3 not complete.** Completed and committed: T3a1 contracts `424cf9d`, T3a2 video lifecycle `a23fe62`, T3b coordinator + bounded tee `ec9b8cb`, T3c0.1 detector cleanup `e50d9e2`, T3c0.2 video start rollback `d479357`, T3c runtime wiring `d075207` (fake integration verified), **T3d private prompt and dispatch gate `1771d6b`** (review PASS WITH LIMITATIONS, closure PASS; fakes only, no live request). Open: gesture/authorization source (every unauthorized approach warns), local warning clip asset (sound), T3e real gesture/device checks. The configuration UI is done (P1, below). Not proven: the private trigger on real video, VLM (Gemini) evaluation of private candidates with a real provider, model accuracy, latency, real devices and field validation. |
| P1 — preview and line/zone configuration: [backend plan](docs/exec-plans/active/private-parking-preview-configuration.md), [frontend plan](docs/exec-plans/active/private-parking-configuration-ui.md) | **All four phases committed, fakes verified:** P1a store `b3b7f3a`, P1b lifecycle `3c5446a`, P1c API/runtime `eb6a3ac` (wire contract frozen), P1d frontend `67a04fb` (completed per frontend plan §23). Real video, cloud and audio off: agent local replay through the API on `67a04fb`, and an owner UI test of Play, Stop, delete, line and ROI only (report baseline `67a04fb`; the commit of the running app was not recorded) ([history](docs/exec-plans/completed/history.md)). The owner test found zone-editor pointer conflicts, fixed in `8a6b8e5` (frontend plan §24, independent review PASS WITH LIMITATIONS; fakes only, the real-video test was not repeated). Both plans stay active until the owner's closing decision for P1 as a whole; then they move to `completed/` together. Not proven: private-trigger behaviour on real footage, accuracy, devices. |
| [C1H — camera health monitoring](docs/exec-plans/active/camera-health-monitoring.md) | **Draft backlog, not authorized; high priority before real CCTV use.** Frame watchdog, freeze vs static scene, obstruction, camera shift; technical status only. |
| [R1 — risk and validation](docs/exec-plans/active/r1-risk-validation.md) | **Planning record approved by the PM (2026-10-12).** Risk matrix, mandatory scenarios, measurement definitions and validation phases; probabilities, KPI targets and controls not yet measured or proven. Does not authorize new controls, algorithm or threshold changes, or live tests. |

## Next candidates (not yet planned; order is a proposal for the owner)

1. Gesture authorization source for private parking (pose model or non-visual signal;
   needs a dependency and hardware decision).
2. Local warning voice clip (backlog, asset not created): candidate text approved by the
   owner — “Permisi, ada keperluan apa, ya? Kalau tidak ada keperluan, tolong jangan
   pegang motornya.” Indonesian TTS, natural conversational tone, questioning then
   firm; a separate clip from the CRITICAL clip, duration within the current validator.
   Deterrent effect unproven. See the [tech-debt tracker](docs/exec-plans/tech-debt-tracker.md).
3. F1 follow-ups (three low findings in the [F1 plan](docs/exec-plans/completed/f1-frontend-ui-ux.md) §12)
   and the five low UX findings from the independent review of the P1 editor delta
   (frontend plan §24).
4. Retail integration: router, runtime wiring, per-domain prompt (cores exist).
5. Parking trigger timing for open parking: candidate priority and evidence window
   (see the replay finding in the tech-debt tracker).
6. Gemini `thinkingConfig` / `maxOutputTokens` decision for the chosen model.
7. Separate backlog, not started: provider-layer refactor, API cost and latency
   optimisation, SQLite history, WebSocket, cashier domain, Interactions API.

## Done

[`docs/exec-plans/completed/history.md`](docs/exec-plans/completed/history.md) lists
shipped change sets with commits and the evidence each one has; closed plans live
next to it ([H1 — documentation harness](docs/exec-plans/completed/h1-documentation-harness.md),
[F1 — UI for UMKM operators](docs/exec-plans/completed/f1-frontend-ui-ux.md): completed
for its scope `a45c986`; UI/UX work as a whole is not finished).
Known debt: [`docs/exec-plans/tech-debt-tracker.md`](docs/exec-plans/tech-debt-tracker.md).
