# Sentinel Vision AI

Full-stack foundation for the Sentinel Vision AI platform — Phase 1: Video Input Foundation.

| Layer     | Technology                          |
|-----------|-------------------------------------|
| Frontend  | React 18 · TypeScript · Vite 5      |
| Backend   | Python FastAPI · Uvicorn            |
| Database  | SQLite (planned)                    |
| Real-time | WebSocket (planned)                 |
| Video     | OpenCV · MJPEG stream               |

---

## Project structure

```
sentinel-vision-ai/
├── README.md
├── phase1-video-input-plan.md
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                        # FastAPI app + CORS + lifespan
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── health.py                  # GET /api/health
│   │   │   └── video.py                   # GET+POST /api/video/*
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   └── video.py                   # VideoState, FramePacket, schemas
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   └── video_service.py           # State machine + capture threads
│   │   └── storage/
│   │       ├── __init__.py
│   │       └── uploads/                   # Temp upload files (auto-created)
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py                    # FakeCapture + fixtures
│       ├── test_video_service.py          # Unit tests (26 tests)
│       └── test_video_api.py              # Integration tests (22 tests)
└── frontend/
    ├── index.html
    ├── package.json
    ├── tsconfig.json
    ├── tsconfig.node.json
    ├── vite.config.ts
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── index.css
        ├── api/
        │   └── videoApi.ts                # Typed fetch wrappers
        ├── components/
        │   ├── VideoPreview.tsx           # MJPEG preview (stable URL)
        │   ├── VideoSourceSelector.tsx    # Webcam/Upload tab control
        │   └── VideoStatusBadge.tsx       # State pill badge
        └── hooks/
            └── useVideoStatus.ts          # Status polling hook
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

No physical webcam or video file required — all tests use `FakeCapture`.

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
