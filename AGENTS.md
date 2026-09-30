# AGENTS.md — Sentinel Vision AI

Handoff guide for coding agents (Codex, Claude Code). Read this before changing code.
State described here matches commit `389256e` (280 backend tests passing).

## What this is

Local CCTV analytics prototype. A video source (webcam or uploaded MP4/WebM) is
analysed in-process: YOLO object detection → anonymous tracking → scene-domain
classification (YOLO evidence + CLIP) → domain-specific *review candidates*
(currently parking/motorcycle) → optional external LLM evaluation via Langflow.

- Backend: Python 3.11, FastAPI, OpenCV, ultralytics YOLO (`backend/yolo26s.pt`, git-ignored),
  transformers CLIP, httpx. Conda env: `sentinel-vision`.
- Frontend: React 18 + TypeScript + Vite 5, polling REST (no WebSocket yet).
- No database yet. Everything is in-memory.

## Commands

```powershell
conda activate sentinel-vision
cd backend
pytest tests/ -v                                   # must stay green; no GPU/webcam needed
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

cd frontend
npm install
npm run dev                                        # http://localhost:5173, proxies /api → :8000
npm run build                                      # tsc + vite build (type check)
```

## Architecture

Composition root: `backend/app/services/runtime.py` builds every singleton and
injects dependencies. `backend/app/main.py` wires routers and calls
`shutdown_services(...)` on shutdown.

```
VideoService (capture thread, MJPEG)          services/video_service.py
 ├─ EventFrameBuffer.offer(JPEG)              services/event_frame_buffer.py   (~6 s ring, bounded)
 ├─ DetectorService.offer(packet)             services/detector_service.py     (YOLO worker, latest-frame-wins)
 │    ├─ DomainService.observe(result)        services/domain_service.py       (YOLO evidence)
 │    ├─ TrackerService.observe(frame)        services/tracker_service.py      (CPU, anonymous IDs)
 │    └─ ParkingCandidateTrigger.observe(...) services/parking_candidate_trigger.py (person↔motorcycle episodes)
 └─ SceneDomainClassifier.offer(packet)       services/scene_domain_classifier.py + clip_scene_runner.py (CLIP)
      └─ DomainService (locks PARKING_MOTORCYCLE / RETAIL_STORE per session)

Built and unit-tested, NOT yet wired into runtime/API/UI:
 EvaluationWorker                             services/evaluation_worker.py
   trigger.claim_next_candidate → EvaluationBudget.try_reserve (services/evaluation_budget.py)
   → FrameSelectionPlan (5 frames at -2, -1, -0.4, 0, +0.4 s; models/event_frame.py)
   → EventFrameBuffer.wait_for_bundle → LangflowEvaluationClient.evaluate (services/langflow_evaluation_client.py)
   → EvaluationRecord (models/evaluation.py)
```

Layout:
- `backend/app/api/`: thin FastAPI routers (`health`, `video`, `detections`, `domain`). Read-only GETs return 204 when there is nothing to report (see `api/detections.py`).
- `backend/app/models/`: frozen dataclasses for internal contracts. Pydantic models only for HTTP responses.
- `backend/app/services/`: thread-owning services.
- `backend/tests/`: one test file per service, plus integration tests (`test_video_*`, `test_detector_tracker_integration.py`). `conftest.py` provides `FakeCapture`.
- `frontend/src/api/videoApi.ts`: all typed fetch wrappers. `hooks/use*.ts`: polling hooks. `components/`: UI.

## Invariants — do not break

1. **Single process, `--workers 1`.** Services are in-memory singletons.
2. **Capture and MJPEG paths never block.** They only call non-blocking `offer(...)`. No model loading, inference, HTTP, or `shared_cuda_gate` on those threads.
3. **One GPU gate.** `runtime.shared_cuda_gate` is injected into YOLO and CLIP. Never import it from worker modules.
4. **Models load lazily inside worker threads.** Importing a module must not allocate GPU memory.
5. **Session scoping.** Every pipeline service implements `start_session(id)` / `end_session(id)` / `close()`. Results from an old session must be dropped, never published. `VideoService` drives the lifecycle.
6. **Monotonic time** (`time.monotonic`) for all pipeline timestamps. Inject `clock` for tests.
7. **Image privacy.** JPEG bytes stay in memory. Never log or persist them, and never put them in exception messages (see `LangflowEvaluationError`). Dataclass fields that hold bytes use `repr=False`.
8. **Neutral semantics.** Parking candidates are prompts for review, not theft verdicts. Never infer identity, ownership, or guilt. `ThreatAssessment.validate()` enforces the score→status bands (0–34 NORMAL, 35–64 WARNING, 65–100 CRITICAL).
9. **Bounded everything.** Queues, caches, and histories have explicit limits. Validate numeric config (finite, non-negative) in constructors.
10. **Shutdown order** is in `runtime.shutdown_services`. Add new services there.

## Working conventions

- Match existing style: `from __future__ import annotations`, dense validation, `threading.Lock/Condition`, and immutable snapshots via `get_snapshot()`.
- New capability = two commits: `feat: add <X> core` (service + unit tests with fakes and fake clock), then `feat: integrate <X> pipeline` (runtime/API/UI wiring + integration tests). Fixes use `fix:`.
- Every change keeps `pytest tests/` green and adds tests. Tests must not require GPU, webcam, network, or model weights. Inject `model_factory` / `runner_factory` / `http_client` fakes.
- The frontend UI copy is Indonesian.

## Configuration (env vars)

See `backend/.env.example`. Nothing loads `.env` automatically yet, so set the variables in the shell.

| Var | Default | Used by |
|---|---|---|
| `SENTINEL_CLIP_MODEL_ID` | `openai/clip-vit-base-patch32` | clip_scene_runner |
| `SENTINEL_CLIP_MODEL_REVISION` | pinned commit hash | clip_scene_runner |
| `SENTINEL_LANGFLOW_URL` | *(empty → client raises INVALID_CONFIG)* | must be `http(s)://host/api/v1/run/<flow_id>` |
| `SENTINEL_LANGFLOW_API_KEY` | empty | sent as `x-api-key` |
| `SENTINEL_LANGFLOW_TIMEOUT_SECONDS` | `30` | |
| `SENTINEL_LANGFLOW_COMPONENT_ID` | `Webhook-exTZU` | tweak target in the flow |
| `SENTINEL_LANGFLOW_MODEL` | `gemini-3-flash-preview` | passed to the flow |

## Next steps (roadmap, in order)

1. **Integrate the evaluation pipeline**: `feat: integrate evaluation worker pipeline`
   - In `runtime.py`, build `EvaluationBudget()` and, only when `SENTINEL_LANGFLOW_URL` is set, `LangflowEvaluationClient()` + `EvaluationWorker(parking_candidate_trigger, budget, event_frame_buffer, client)`. Start the worker from the `main.py` lifespan startup (not at import, which keeps tests clean).
   - Extend `shutdown_services`: close the worker before the trigger and buffer, then the budget and client.
   - Without the URL, the app must run exactly as today.
2. **Evaluation API**: `api/evaluation.py` with `GET /api/evaluation/latest` (204 if none; Pydantic response from `EvaluationRecord`, no image data) and `GET /api/evaluation/status` (`WorkerSnapshot` + budget snapshot). Register it in `main.py`. Follow the `api/detections.py` pattern and tests.
3. **Frontend**: typed wrappers in `videoApi.ts`, a `useLatestEvaluation` hook (same pattern as `useLatestDetection`), and an `EvaluationPanel` (score, status, reasoning, subject profile). Turn on the "Response System" `PipelineStep` in `App.tsx`.
4. Later: a RETAIL_STORE candidate trigger + frame-selection strategy, SQLite persistence of evaluation records (metadata only), and WebSocket push instead of polling.

## Known issues / tech debt

- The 5-frame count is hardcoded in `EvaluationWorker._valid_bundle` and `LangflowEvaluationClient._build_payload`. Generalize it to `plan.required_frame_count` before adding the retail strategy.
- Rate limiting is duplicated: `ParkingCandidateTrigger(global_request_interval_seconds=10)` and `EvaluationBudget(minimum_request_interval_seconds=10)`. Keep the two consistent or remove one deliberately.
- The LLM prompt is hardcoded in `LangflowEvaluationClient._build_payload`.
- `EvaluationWorker._run` catches all exceptions as `CLIENT_ERROR`, so there is no logging. Add logging, but never log payloads.
- `phase1-video-input-plan.md` is historical (Phase 1 only). Don't treat it as current spec.
- `torch` must be installed matching the local CUDA (tested with `2.7.1+cu128`). `requirements.txt` does not pin it.
