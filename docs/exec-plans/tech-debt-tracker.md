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
| 12 | `torch` not pinned; must match local CUDA (tested `2.7.1+cu128`) | Environment drift | requirements | Install order and tested build documented in `README.md` (2026-10-12); pinning per platform still open |
| 13 | Ctrl+C twice forces uvicorn exit and skips lifespan shutdown | Audio/threads not closed cleanly | owner traceback, 2026-10-01 | Investigate graceful shutdown with open connections |
| 14 | Prompt text lives in the clients; Langflow flow prompt not verified | Drift between transports | code | Provider/prompt refactor (backlog) |
| 15 | No local warning voice clip exists (private mode local audio is off without it). Owner-approved candidate text: “Permisi, ada keperluan apa, ya? Kalau tidak ada keperluan, tolong jangan pegang motornya.” — Indonesian TTS, natural, questioning then firm; separate from the CRITICAL clip; duration within the current validator | Local warning silent; deterrence unproven | owner decision 2026-10-11 | Create the asset outside the repository (TTS generation is a separate, owner-authorized task); validate with the existing audio factory |
| 16 | No camera health signals (no frames, frozen, obstructed, shifted view) | Silent loss of coverage; line/zones wrong after a shift | PM 2026-10-11 | [Draft plan](active/camera-health-monitoring.md); high priority before real CCTV use |
| 17 | F1 low findings: stream counter mutated in `useMemo`; malformed 2xx shown as a connection error; `lang="id"` set after render | Minor UI correctness | [F1 review](completed/f1-frontend-ui-ux.md) §12 | Next frontend change |
| 18 | YOLO weights path is fixed to `backend/yolo26s.pt` (`services/detector_service.py`); no setting, and the file is git-ignored | Setup fails silently on a new machine until the file is placed | docs audit 2026-10-12 | Source task: configurable path or a documented download step |
| 19 | `services/runtime.py` module docstring (first paragraph) still says private mode needs a valid `SENTINEL_PRIVATE_PARKING_CONFIG_FILE`; since P1c the file is only an optional template (a later paragraph and the code agree) | Misleading for maintainers | docs audit 2026-10-12 | Correct the docstring in the next backend change |
| 20 | `requirements.txt` pins `httpx==0.27.0`, the maintainer environment has `0.28.1`; `pip check` does not report it | Fresh installs differ from the tested environment | docs audit 2026-10-12 | Align the pin or the environment, then re-run the suite |
| 21 | `AGENTS.md` verify block uses the maintainer's absolute Python path | Commands do not run on another machine | docs audit 2026-10-12 | Owner decision (shared file): keep as a local note or use the active environment's `python` |
| 22 | P1 zone-editor low UX findings (early validation messages, invisible success message, pointer message worded for form fields, line handles capture clicks near endpoints in line mode, rounded container corner not clickable) | Minor operator friction | independent review of `8a6b8e5` (frontend plan §24), 2026-10-12 | Next frontend change |
