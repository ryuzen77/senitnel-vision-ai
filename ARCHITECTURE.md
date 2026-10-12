# Architecture — Sentinel Vision AI

Verified against source at commit `36ffa20` (2026-10-04); the private-parking rows,
§3, §5 and §6 were re-checked at `67a04fb` and the UI paths again at `8a6b8e5`
(2026-10-12). "Runtime" means wired in
`backend/app/services/runtime.py` and used by the running app; "core only" means
built and unit-tested in isolation, not wired; "planned" means a draft exec plan.

## 1. Shape

Single FastAPI process, exactly one Uvicorn worker. All state is in memory and
session-scoped; there is no database. A *session* starts when video starts and ends
on Stop, EOF, error or a new start; every pipeline service drops results from an
old session.

```
VideoService (capture thread, JPEG once, MJPEG slot)            video_service.py
 ├─ EventFrameBuffer.offer(BufferedFrame)                        event_frame_buffer.py
 ├─ DetectorService.offer(packet)  [YOLO worker, latest-frame-wins, CUDA gate]
 │    ├─ DomainService.observe        3-of-5 motorcycle → PARKING_MOTORCYCLE lock
 │    ├─ TrackerService.observe       anonymous person/motorcycle tracks
 │    └─ ParkingCandidateTrigger.observe → candidate episodes
 ├─ SceneDomainClassifier.offer(packet) [5 early samples → CLIP, CUDA gate]
 │    └─ DomainService.apply_scene_decision (first lock wins; may lock UNKNOWN)
 └─ EvaluationWorker (start/end_session from VideoService; daemon thread)
      claim → EvaluationBudget.try_reserve → FrameSelectionPlan (strategy by domain)
      → EventFrameBuffer.wait_for_bundle → atomic handoff (quota) → dispatcher
      → result accepted only through accept_callback → EvaluationRecord
      → AudioAlertGate.offer (parking domain, CRITICAL only)
```

## 2. Components

| Component | Files | Status |
|---|---|---|
| Video input, upload guards, MJPEG | `services/video_service.py`, `api/video.py` | runtime |
| Detection (YOLO `yolo26s.pt`, COCO 80 classes) | `services/detector_service.py` | runtime |
| Tracking (IoU + centre, anonymous IDs, 1.5 s loss tolerance) | `services/tracker_service.py` | runtime |
| Domain lock (YOLO fast path + CLIP) | `services/domain_service.py`, `scene_domain_classifier.py`, `clip_scene_runner.py` | runtime |
| Parking candidate trigger (person ↔ motorcycle episodes) | `services/parking_candidate_trigger.py` | runtime |
| Event frame buffer (6 s / 240 frames / 32 MiB JPEG) | `services/event_frame_buffer.py` | runtime |
| Evaluation budget (1 pending, 10 s interval, 2 per 60 s) | `services/evaluation_budget.py` | runtime |
| Evaluation worker (states, deadlines, per-process quota) | `services/evaluation_worker.py` | runtime |
| Gemini direct transport (REST `generateContent`) | `services/gemini_evaluation_client.py` | runtime when `SENTINEL_EVALUATION_TRANSPORT=gemini` |
| Langflow Webhook transport + callback | `services/langflow_evaluation_client.py`, `api/evaluation.py` | runtime when transport `langflow` (default) |
| Current evaluation view | `GET /api/evaluation/current` | runtime |
| Local audio alert gate | `services/audio_alert_service.py` | runtime, off by default |
| Retail trigger (ROI zones) + retail plan/strategy | `services/retail_candidate_trigger.py`, `models/event_frame.py` | core only (no router/runtime) |
| Private parking trigger (T1) | `services/private_parking_trigger.py`, `models/private_parking.py`, `services/private_parking_authorization.py` | runtime in `SENTINEL_PARKING_MODE=private` (off by default); no authorization source |
| Private parking evidence sampler (T2) | `services/private_parking_evidence_sampler.py`, `models/private_parking_evidence.py` | runtime in private mode (fed by the tee; the only private bundle source) |
| Private parking coordinator + frame tee (T3b) | `services/private_parking_coordinator.py` | runtime in private mode |
| Parking mode selection (T3c) | `services/runtime.py` (`build_parking_wiring`), `services/disabled_parking_trigger.py` | runtime; private dispatch only with `SENTINEL_PRIVATE_PARKING_EVALUATION=true` and the Gemini transport (T3d); see [`docs/exec-plans/active/t3-private-parking-integration.md`](docs/exec-plans/active/t3-private-parking-integration.md) |
| Prepared sources (P1): upload, probe, preview, operator configuration, Play, cleanup | `services/prepared_source.py`, `services/video_decoder.py`, `api/sources.py`, `models/sources.py` | runtime in private mode; single source slot, files under `storage/uploads/prepared/`; see the [backend plan](docs/exec-plans/active/private-parking-preview-configuration.md) §19.3 |
| Per-session operator configuration binding (P1) | `services/session_config_registry.py` | runtime in private mode; the trigger reads only the configuration bound to its session |
| Frontend dashboard | `frontend/src/` | runtime; see [`docs/FRONTEND.md`](docs/FRONTEND.md) |
| Private-parking preparation UI (P1d): preview, line/ROI editor, save, Play | `frontend/src/hooks/useSourcePreparation.ts`, `components/PrivateParkingConfiguration.tsx`, `components/ConfigurationOverlay.tsx`, `geometry/`, `api/preparedSourceApi.ts` | runtime when capabilities report private mode; see the [frontend plan](docs/exec-plans/active/private-parking-configuration-ui.md) |

## 3. Domains and modes

`DomainName`: `UNKNOWN`, `PARKING_MOTORCYCLE`, `RETAIL_STORE`. The domain is locked
once per session. **Private parking is a mode of `PARKING_MOTORCYCLE`**, not a fourth
domain, selected by `SENTINEL_PARKING_MODE` (unset/`legacy` = the legacy parking trigger;
`private` = coordinator + private trigger + evidence sampler + frame tee, detector and tee
required; private evaluation only with `SENTINEL_PRIVATE_PARKING_EVALUATION=true`, the
direct Gemini transport and its private prompt (T3d), Langflow has none); any other value
= an explicitly disabled candidate path, never a fallback to legacy). Since P1c the
private startup file is only an optional, unconfirmed template: analysis uses the
configuration the operator saved for each source, and a missing or invalid template
leaves the editor available (it no longer disables the mode). Private mode accepts
video files only; the legacy upload and webcam routes answer `409`. No gesture/authorization source exists, so every unauthorized approach warns;
real-device use waits for it (O1). A cashier domain does not exist.

## 4. Contracts that matter

- **Sessions**: every pipeline service implements `start_session/end_session/close`
  and ignores input from another session. `VideoService` drives the lifecycle.
- **Two timelines**: observation timestamps (`FramePacket.ts`, monotonic) order frames,
  tracks and markers; injected control clocks drive TTLs and deadlines so they expire
  without new frames.
- **Evaluation handoff**: the dispatch is committed at one atomic handoff under the
  worker lock (session re-check, quota increment, pending record). Stop before handoff
  means no dispatch; after handoff the request may still reach the transport, but a
  late ACK, error or result can never revive state, publish or reach audio. No retry,
  no fallback between transports, spent quota is never returned.
- **Single acceptance path**: the Langflow HTTP callback and the Gemini direct result
  both go through `EvaluationWorker.accept_callback`; event/session IDs come from the
  backend, never from the model; `ThreatAssessment.validate()` enforces score bands
  (0–34 NORMAL, 35–64 WARNING, 65–100 CRITICAL).
- **Bundles**: exactly five JPEG frames, strictly increasing timestamps and sequence
  numbers, each within the plan's deviation (`EvaluationWorker._valid_bundle`).
  Parking offsets are −2, −1, −0.4, 0, +0.4 s. Private-parking evidence plans carry
  the sealed frames' actual offsets with a 1e-6 s tolerance (exact 0.0 fails on
  float rounding).
- **Audio**: only an ACCEPTED parking-domain CRITICAL result is offered to
  `AudioAlertGate`. The gate plays at most one clip per event, CRITICAL only,
  first-wins with cooldown; Stop/new session cancels; a failed device stop disables
  audio until restart. An offer is not proof a sound was heard.

### Local access warning vs CRITICAL assessment (private mode)

These are two different signals and must stay separate:
- **Local access warning**: a rule outcome of the private-parking trigger — an
  episode that crossed the entry line without authorization was confirmed close to a
  parked motorcycle. It is issued by the local rule without waiting for a model or
  evidence, and is not a threat score. Its end-to-end latency (target under one
  second) is unverified; in private mode the coordinator offers it to the audio gate
  with the separate local clip (`SENTINEL_AUDIO_LOCAL_ALERT_FILE`), tested with fakes only
  — no real speaker run and no local clip asset yet.
- **CRITICAL assessment**: a validated model result for the same event, possibly
  seconds later, possibly NORMAL. It never upgrades or replaces the warning, and the
  warning never fabricates a CRITICAL.
They share one `event_id` so audio deduplication plays at most once. Wired in T3c/T3d
(runtime, fakes only; no local clip asset and no real speaker run).

## 5. API

`GET /api/health` · `POST /api/video/webcam/start` · `POST /api/video/upload`
(MP4/WebM, ≤ 200 MB) · `POST /api/video/stop` · `GET /api/video/status` ·
`GET /api/video/stream` (MJPEG) · `GET /api/detections/latest` (204 = none) ·
`GET /api/domain/current` · `GET /api/evaluation/current` ·
`POST /api/evaluation/callback` (Langflow transport only; static shared token header,
constant-time comparison, 16 KiB body limit). Private mode adds `/api/video/sources`:
`capabilities`, `current`, create, `{id}/file`, `{id}/preview`, `{id}/template`,
`{id}/configuration`, `{id}/play`, `{id}/play/{attempt}`, `DELETE {id}` and
`cleanup/retry`; errors are `{category, field?}` bodies (contract frozen in the backend
plan §19.3). CORS allows only the local Vite origin.
No other endpoint is authenticated: the API is meant for `127.0.0.1`.

## 6. Configuration

Environment variables only (nothing loads `.env`); see `backend/.env.example`.
Invalid values disable the affected feature and log a generic warning without the
value. Key switches: `SENTINEL_EVALUATION_TRANSPORT` (`langflow` default | `gemini`),
`SENTINEL_GEMINI_*`, `SENTINEL_LANGFLOW_*`, `SENTINEL_CALLBACK_TOKEN`,
`SENTINEL_EVALUATION_CALLBACK_TIMEOUT_SECONDS` (45), `SENTINEL_EVALUATION_MAX_DISPATCHES`,
`SENTINEL_AUDIO_ALERT_*`, `SENTINEL_AUDIO_LOCAL_ALERT_FILE`, `SENTINEL_PARKING_MODE`,
`SENTINEL_PRIVATE_PARKING_CONFIG_FILE` (optional editor template, local JSON read once at
startup; path and content never logged), `SENTINEL_PRIVATE_PARKING_EVALUATION`,
`SENTINEL_CLIP_MODEL_*`, `SENTINEL_VIDEO_STARTUP_TIMING_ENABLED`. Fixed paths, not
configurable: YOLO weights `backend/yolo26s.pt`, uploads `backend/app/storage/uploads/`
(prepared files in its `prepared/` subfolder).

## 7. Threads and locks

Capture thread (never blocks; only `offer`), detector worker, CLIP worker, evaluation
worker, audio device thread, FastAPI threadpool. One shared CUDA lock for YOLO and CLIP.
Lock order flows outward from the evaluation worker: while holding its state lock it
calls the trigger, budget, frame buffer (snapshots/completion) and the audio gate;
none of those ever call back into the worker. The audio device (PlaySound) and all
HTTP run outside every lock. Shutdown order: `runtime.shutdown_services`.
