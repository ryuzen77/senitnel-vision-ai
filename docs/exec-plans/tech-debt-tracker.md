# Tech-debt tracker

Each row: what, why it matters, where it was found, proposed next step. Remove a row
only in the commit that fixes it.

| # | Debt | Impact | Source | Next step |
|---|---|---|---|---|
| 1 | Parking trigger timing: with quota 1 the only dispatch can go to a passer-by; even the right candidate's frames (−2…+0.4 s) cover only the first touch | Live NORMAL on a theft clip is consistent with this | Local replay with production components + fake dispatcher, 2026-10-04 | Plan candidate priority / evidence window for open parking |
| 2 | Private parking without a gesture source treats every entrant as unauthorized | Warnings for legitimate owners | T1 design | Gesture source task; T3 decision 1 |
| 3 | `UNKNOWN_INSIDE` never warns (first seen inside, ID switch, detector cold start) | Missed coverage | T1 | Relink or re-entry design after calibration data |
| 4 | Track ID switches split episodes / confuse the escorted motorcycle | Missed or false warnings | T1 | Calibrate tracker; consider appearance-free relink |
| 5 | T1/T2 thresholds and parking/retail thresholds uncalibrated | Unknown precision/recall | all cores | Annotated clips per camera |
| 6 | Gemini `thinkingConfig` / `maxOutputTokens` are model defaults | Latency and cost unknown | Gemini transport | Decide per model, measure |
| 7 | API latency and cost not budgeted (one live success took ~16 s dispatch → COMPLETED by polling) | Free-tier quota; user wait | live test 5 | Measure `provider_ms` across runs; cost plan (backlog) |
| 8 | `generateContent` is labelled legacy by Google | Future migration | official docs | Interactions API only with `store=false`, separate task |
| 9 | Rate limiting duplicated (trigger global interval and budget interval) | Confusing tuning | code | Keep consistent or remove one deliberately |
| 10 | Budget grant is spent when a plan/bundle is missing before handoff | Fewer evaluations under load | worker design | Accept or reserve after bundle (design task) |
| 11 | `README.md` and `phase1-video-input-plan.md` are stale (Vite 5, "not wired yet", `--reload` example) | Misleading onboarding | docs audit 2026-10-04 | Rewrite README to point at AGENTS/ARCHITECTURE |
| 12 | `torch` not pinned; must match local CUDA (tested `2.7.1+cu128`) | Environment drift | requirements | Document or pin per platform |
| 13 | Ctrl+C twice forces uvicorn exit and skips lifespan shutdown | Audio/threads not closed cleanly | owner traceback, 2026-10-01 | Investigate graceful shutdown with open connections |
| 14 | Prompt text lives in the clients; Langflow flow prompt not verified | Drift between transports | code | Provider/prompt refactor (backlog) |
