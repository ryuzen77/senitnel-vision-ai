# Sentinel Vision AI

Local CCTV analytics prototype: video input → YOLO detection → anonymous tracking →
scene-domain classification (YOLO + CLIP) → parking review candidates → (planned)
Langflow LLM evaluation.

> Coding agents: see [`AGENTS.md`](AGENTS.md) for architecture, invariants, and roadmap.

| Layer     | Technology                                   |
|-----------|----------------------------------------------|
| Frontend  | React 18 · TypeScript · Vite 5               |
| Backend   | Python FastAPI · Uvicorn (single worker)     |
| Vision    | OpenCV · ultralytics YOLO · transformers CLIP |
| LLM eval  | Langflow run API via httpx (not wired yet)   |
| Database  | SQLite (planned)                             |
| Real-time | REST polling; WebSocket (planned)            |

---

## Project structure

```
sentinel-vision-ai/
├── AGENTS.md                      # Handoff guide for coding agents
├── README.md
├── phase1-video-input-plan.md     # Historical Phase 1 plan
├── backend/
│   ├── requirements.txt
│   ├── .env.example               # SENTINEL_* configuration
│   ├── yolo26s.pt                 # YOLO weights (git-ignored, place manually)
│   ├── app/
│   │   ├── main.py                # FastAPI app, CORS, lifespan, routers
│   │   ├── api/                   # health, video, detections, domain
│   │   ├── models/                # Frozen dataclass contracts + Pydantic responses
│   │   ├── services/
│   │   │   ├── runtime.py         # Composition root (singletons, CUDA gate, shutdown)
│   │   │   ├── video_service.py   # Capture state machine + MJPEG
│   │   │   ├── detector_service.py            # YOLO worker
│   │   │   ├── tracker_service.py             # Anonymous CPU tracker
│   │   │   ├── domain_service.py              # Session domain lock
│   │   │   ├── scene_domain_classifier.py     # CLIP sampling
│   │   │   ├── clip_scene_runner.py           # CLIP model runner
│   │   │   ├── parking_candidate_trigger.py   # Parking review candidates
│   │   │   ├── event_frame_buffer.py          # Bounded JPEG ring buffer
│   │   │   ├── evaluation_budget.py           # Global LLM request budget
│   │   │   ├── evaluation_worker.py           # Candidate → LLM orchestration
│   │   │   └── langflow_evaluation_client.py  # Langflow HTTP client
│   │   └── storage/uploads/       # Temp upload files
│   └── tests/                     # pytest suite (no GPU/webcam/network needed)
└── frontend/
    └── src/
        ├── App.tsx
        ├── api/videoApi.ts        # Typed fetch wrappers
        ├── components/            # VideoPreview, VideoSourceSelector, DetectionPanel, badges
        └── hooks/                 # useVideoStatus, useLatestDetection, useCurrentDomain
```

---

## Running the backend

```powershell
# Activate the Miniconda environment
conda activate sentinel-vision

# Install dependencies (first time only)
cd backend
pip install -r requirements.txt
```

**Development** (auto-reload on file change):
```powershell
conda activate sentinel-vision
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Stable / production** (exactly one worker — required):
```powershell
conda activate sentinel-vision
cd backend
uvicorn app.main:app --workers 1 --host 0.0.0.0 --port 8000
```

> **Important:** `VideoService` is an in-memory singleton. Running more than
> one worker would give each worker its own independent state copy, causing
> split-brain behaviour (one worker starts the webcam; another reports IDLE).
> Always use exactly `--workers 1` for stable operation. Do not combine
> `--reload` with `--workers`.

API available at `http://localhost:8000`
Interactive docs at `http://localhost:8000/docs`

---

## Running the frontend

```bash
# Install dependencies (first time only)
cd frontend
npm install

# Start the dev server
npm run dev
```

App available at `http://localhost:5173`  
All `/api/*` requests are proxied automatically to `http://localhost:8000`.

---

## Running backend tests

```bash
conda activate sentinel-vision
cd backend
pytest tests/ -v
```

No webcam, GPU, network, or model weights required — tests use fakes (`FakeCapture`, fake model/runner factories, fake HTTP clients).

---

## API endpoints

| Method | Path                        | Description                    |
|--------|-----------------------------|--------------------------------|
| GET    | /api/health                 | Service health check           |
| POST   | /api/video/webcam/start     | Start webcam capture           |
| POST   | /api/video/upload           | Upload and play MP4/WebM file  |
| POST   | /api/video/stop             | Stop capture / reset error     |
| GET    | /api/video/status           | Current video state            |
| GET    | /api/video/stream           | Live MJPEG stream              |
| GET    | /api/detections/latest      | Latest YOLO result (204 if none) |
| GET    | /api/domain/current         | Current scene-domain snapshot  |

### Health response

```json
{
  "status": "online",
  "service": "sentinel-vision-backend"
}
```

### Video status response

```json
{
  "state": "RUNNING",
  "source": "webcam",
  "error": null
}
```

### Video states

| State    | Meaning                                |
|----------|----------------------------------------|
| IDLE     | No active video source                 |
| STARTING | Source being initialised               |
| RUNNING  | Capture active; stream available       |
| STOPPING | Capture being released                 |
| ERROR    | Capture failed; call /stop to reset    |
