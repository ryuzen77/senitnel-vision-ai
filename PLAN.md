# PLAN.md — where the work is

This file only points. Plans themselves live in [`docs/exec-plans/`](docs/exec-plans/);
the rules for writing and reviewing them are in [`docs/PLANS.md`](docs/PLANS.md).
Last reviewed at commit `36ffa20` (2026-10-04).

## Active

| Plan | Status |
|---|---|
| [T3 — private parking integration](docs/exec-plans/active/t3-private-parking-integration.md) | **T3a1 completed — core only (reviewed PASS WITH LIMITATIONS; not wired into runtime); T3a2 completed — lifecycle only (reviewed PASS); T3b completed — core only (coordinator and bounded tee, reviewed PASS; not wired); T3c0 design rev 4 final (PASS WITH LIMITATIONS); T3c0.1 completed — detector cleanup/lease/generation-bound delivery (reviewed PASS WITH LIMITATIONS); T3c0.2 not authorized, runtime not wired; T3c–T3e not started, not authorized; T3 not complete** |

## Next candidates (not yet planned; order is a proposal for the owner)

1. Retail integration: router, runtime wiring, per-domain prompt (cores exist).
2. Gesture authorization source for private parking (pose model or non-visual signal;
   needs a dependency and hardware decision).
3. Parking trigger timing for open parking: candidate priority and evidence window
   (see the replay finding in the tech-debt tracker).
4. Gemini `thinkingConfig` / `maxOutputTokens` decision for the chosen model.
5. Separate backlog, not started: frontend redesign, provider-layer refactor, API cost
   and latency optimisation, SQLite history, WebSocket, cashier domain, Interactions API.

## Done

[`docs/exec-plans/completed/history.md`](docs/exec-plans/completed/history.md) lists
shipped change sets with commits and the evidence each one has; closed plans live
next to it (e.g. [H1 — documentation harness](docs/exec-plans/completed/h1-documentation-harness.md)).
Known debt: [`docs/exec-plans/tech-debt-tracker.md`](docs/exec-plans/tech-debt-tracker.md).
