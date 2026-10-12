# Sentinel Vision AI

Local CCTV analytics prototype for small businesses: video input → YOLO detection →
anonymous tracking → scene-domain classification (YOLO + CLIP) → review candidates →
optional AI evaluation. The dashboard is in Indonesian.

A candidate or an AI score is a prompt for a human to look, never proof of theft,
identity or ownership.

> Coding agents start at [`AGENTS.md`](AGENTS.md). Current work: [`PLAN.md`](PLAN.md);
> architecture and wiring: [`ARCHITECTURE.md`](ARCHITECTURE.md); shipped change sets and
> their evidence: [`docs/exec-plans/completed/history.md`](docs/exec-plans/completed/history.md).

| Layer | Technology |
|---|---|
| Frontend | React 18 · TypeScript · Vite 6 · Vitest |
| Backend | Python 3.11 · FastAPI · Uvicorn (exactly one worker) |
| Vision | OpenCV · Ultralytics YOLO (`yolo26s.pt`) · transformers CLIP · PyTorch |
| AI evaluation (optional) | Direct Gemini API (current direction) or Langflow Webhook (early integration) |
| Real-time | REST polling and MJPEG; no database (state is in memory) |

---

## Modes

The backend has one process-wide mode, chosen by `SENTINEL_PARKING_MODE` at startup.

| Mode | Value | What it does |
|---|---|---|
| Legacy | unset or `legacy` (default) | Webcam or file upload starts analysis immediately. The scene-domain lock can label a scene `PARKING_MOTORCYCLE` or `RETAIL_STORE`; only the open-parking candidate trigger is wired. The retail candidate trigger and retail evaluation contracts exist as cores but are **not wired into the runtime**, so a retail scene is classified, not analysed further. |
| Private parking | `private` | Video **file** only. Upload → preview (analysis not started) → operator draws the access line and motorcycle zones (ROI) → save → **Mulai analisis** (Play). The legacy upload and webcam routes answer `409 private_flow_required`. |
| Disabled | any other value | Parking candidates are disabled; no fallback to legacy. |

### Private parking: line and ROI configuration

1. Choose an MP4 or WebM file (max 200 MB). The backend stores it under
   `backend/app/storage/uploads/prepared/` (git-ignored, cleared on startup, on delete
   and on expiry) and probes the first frame.
2. **Muat preview** shows one JPEG frame; analysis has not started.
3. Draw the **access line** with two points P1 → P2 and choose which side is inside
   (left or right of the P1 → P2 direction). The shaded side shows the choice.
4. Add 1–8 **motorcycle zones** (ROI) by clicking two opposite corners, or type the
   values as percentages of the original image. Invalid geometry cannot be saved.
5. **Simpan pengaturan** binds the configuration to this source. Play is offered only
   after the backend reports the configuration ready.
6. **Mulai analisis** starts analysis with the saved configuration; **Hentikan analisis**
   stops it and keeps the source and configuration; **Hapus sumber** deletes the file.

An optional startup template (`SENTINEL_PRIVATE_PARKING_CONFIG_FILE`) is only an
unconfirmed starting point; analysis always uses the configuration the operator saved
for that source. Design and contracts: [private parking mode](docs/design-docs/private-parking-mode.md),
[backend plan](docs/exec-plans/completed/private-parking-preview-configuration.md),
[frontend plan](docs/exec-plans/completed/private-parking-configuration-ui.md).

### AI evaluation and audio are off unless configured

- **Direct Gemini API** (`SENTINEL_EVALUATION_TRANSPORT=gemini`) is the direction of the
  current version and the only transport with the private-parking prompt.
- **Langflow** (Webhook + callback) was the early integration. It is still the code
  default when `SENTINEL_EVALUATION_TRANSPORT` is unset, but it stays inactive without
  `SENTINEL_LANGFLOW_URL` and `SENTINEL_CALLBACK_TOKEN`.
- Private parking evaluates only with `SENTINEL_PRIVATE_PARKING_EVALUATION=true` **and**
  the Gemini transport. Provider requests are billed: do not enable them for a normal
  local run, and cap any approved test with `SENTINEL_EVALUATION_MAX_DISPATCHES=1`.
- Local audio needs `SENTINEL_AUDIO_ALERT_ENABLED=true` and a WAV outside the
  repository; it is off when unset.

IBM Bob was used as a development assistant for this repository. It is not a runtime
component and is not part of the video pipeline.

---

## Feature status

Evidence words follow [`docs/PLANS.md`](docs/PLANS.md). Per-commit counts and reviewers
are in [`history.md`](docs/exec-plans/completed/history.md).

**Verified in software with fakes** (backend pytest, frontend Vitest; no GPU, network,
webcam or speaker): video lifecycle and MJPEG, detector and domain lock, open-parking
candidates, evaluation worker, budget and both transports, audio gate, private-parking
trigger, sampler, coordinator and runtime wiring, prepared-source store and
`/api/video/sources` API, and the private-parking UI (upload, preview, line/ROI editor,
save, Play, Stop, delete, recovery states).

**Tested with a real video file** (local, cloud evaluation and audio off):

| Who | Date / commit | What was exercised |
|---|---|---|
| Owner, through the UI | 2026-10-12; report baseline `67a04fb` (the commit the running app was built from was not recorded) | Play, Stop, delete source, access line and ROI drawing only. This test exposed zone-editor pointer conflicts, fixed in `8a6b8e5` (fakes and independent review only; the owner has not repeated the real-video test since). |
| Agent (local replay through the API, not the UI) | 2026-10-12 / `67a04fb` | Upload and probe, preview, configuration save, Play, YOLO detections and domain lock `PARKING_MOTORCYCLE`, end of file, manual Stop, delete and file cleanup. The line and zone were generic placeholders, so private-trigger behaviour was not assessed. |

Earlier single live checks are listed in `history.md` (one Langflow evaluation, one
direct Gemini evaluation with a NORMAL result, one local speaker check).

**Not proven yet:** private-parking candidates and warnings on real footage; detection
accuracy and latency; gesture authorization (no source exists, so every unauthorized
approach would warn); the local warning voice clip (asset not created); private-parking
AI evaluation with a real provider; the CRITICAL → speaker path with a real provider;
retail analysis (not wired); webcam or CCTV in private mode (files only); camera-health
monitoring; HTTPS or LAN deployment; multi-camera use and persistence across restarts.

---

## Demo

All three links were provided by the owner. Tautan dari owner; isi tidak diverifikasi
dalam review ini. The descriptions are the owner's.

- [Demo 1 — mosque parking video, assessed safe (owner's description)](https://lnkd.in/p/gTssPPpj)
- [Demo 2 — motorcycle theft video, assessed CRITICAL (owner's description)](https://lnkd.in/p/g2cPZ3Py)
- [Demo 3](https://lnkd.in/p/ggiS_Dui) — Demo proyek lama, dibuat sebelum penggunaan IBM
  Bob; referensi konsep, bukan bukti seluruh fitur versi terbaru.

The demos are illustrations only and are not part of the evidence record. A result is an
automated assessment for a person to review, not proof of theft, and one example does
not measure accuracy; the evidence for each feature is in [Feature status](#feature-status).

---

## Installation (Windows, PowerShell)

> These steps match the repository and the maintainer's working environment
> (Miniconda, Python 3.11.16, PyTorch 2.7.1 + CUDA 12.8, Node.js 26.8.2, npm 11.19.1).
> **Installation on a fresh machine has not been verified**; treat versions other than
> these as untested.

**CPU versus CUDA.** The test suites use fakes and need no GPU. Running the app with real
inference currently needs an NVIDIA GPU with CUDA: YOLO runs on `device=0` and the CLIP
runner stops with an error when CUDA is unavailable.

**Environment versus `requirements.txt`.** The maintainer environment differs from the
pins in one place: `httpx` is pinned to `0.27.0` but `0.28.1` is installed. Unpinned
packages are installed at `numpy 2.4.6`, `pillow 12.3.0` and `ultralytics 8.4.156`.
PyTorch (`2.7.1+cu128`, `torchvision 0.22.1+cu128`) is not in `requirements.txt`. A
fresh install from these files may therefore differ from the tested environment.

### 1. Miniconda

Install Miniconda from the official page
([docs.anaconda.com/miniconda](https://docs.anaconda.com/miniconda/)), then open an
"Anaconda PowerShell Prompt" (or run `conda init powershell` once).

### 2. Python environment

`requirements.txt` does not pin a Python version; Python 3.11 is the version the test
suite runs on.

```powershell
conda create -n sentinel-vision python=3.11
conda activate sentinel-vision
```

### 3. Backend dependencies

PyTorch is not in `requirements.txt`: install the CUDA build first, so that Ultralytics
does not pull a different one.

```powershell
# from the repository root
pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
pip install -r backend/requirements.txt
```

A CPU-only PyTorch build may be enough to run the fake test suite; that has not been
verified, and it cannot run the app's real inference (see CPU versus CUDA above).

### 4. Model weights

- **YOLO:** place `yolo26s.pt` in `backend/` (the path is fixed in
  `backend/app/services/detector_service.py`; `*.pt` files are git-ignored).
- **CLIP:** the runtime loads `CLIPProcessor` and `CLIPModel` for
  `SENTINEL_CLIP_MODEL_ID` (default `openai/clip-vit-base-patch32`) at
  `SENTINEL_CLIP_MODEL_REVISION` (default `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`) with
  `local_files_only=True`. It **never downloads**; the Hugging Face cache must hold that
  model and revision before the backend runs. Without it the CLIP runner fails (logged
  as an exception) and only the YOLO domain lock remains. Prepare the cache once, with
  network access, using the same classes, ID
  and revision (not verified on a fresh machine):

  ```powershell
  python -c "from transformers import CLIPModel, CLIPProcessor; m='openai/clip-vit-base-patch32'; r='3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268'; CLIPProcessor.from_pretrained(m, revision=r); CLIPModel.from_pretrained(m, revision=r)"
  ```

  If you change either variable, prepare the cache for the new values the same way.

### 5. Frontend dependencies

```powershell
cd frontend
npm ci
```

---

## Configuration

Configuration comes from environment variables only. The backend does **not** load any
`.env` file: [`backend/.env.example`](backend/.env.example) documents every
`SENTINEL_*` variable; set the ones you need in the shell that starts the backend. Never
commit keys, tokens or real `.env` files.

### Local run with cloud evaluation and audio off (private mode)

```powershell
conda activate sentinel-vision
cd backend
$env:SENTINEL_PARKING_MODE = 'private'
$env:SENTINEL_EVALUATION_TRANSPORT = 'disabled'   # any value other than langflow/gemini disables external evaluation
$env:SENTINEL_PRIVATE_PARKING_EVALUATION = 'false'
$env:SENTINEL_AUDIO_ALERT_ENABLED = 'false'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

With these values the startup log contains `External evaluation disabled: invalid
transport selection` (expected) and `/api/video/sources/capabilities` reports
`"mode":"private"` and `"evaluation_enabled":false`. For legacy mode, leave
`SENTINEL_PARKING_MODE` unset; keep the other three lines.

Always run exactly one worker and no `--reload`: video, sources and evaluation state
live in memory in one process.

### Frontend

```powershell
cd frontend
npm run dev
```

Open `http://localhost:5173`; `/api/*` is proxied to `http://localhost:8000`. Uploading,
saving and Play in private mode need a secure context (`localhost` or HTTPS), because
operation IDs come from `crypto.randomUUID()`.

---

## Tests and checks

```powershell
# backend, from the repository root
$py = (Get-Command python).Source        # inside the sentinel-vision environment
$testTemp = Join-Path $env:TEMP ('sentinel-tests-' + [guid]::NewGuid().ToString())
& $py -m pytest backend/tests -q --basetemp $testTemp
& $py -m pip check

# frontend
cd frontend
npm test
npm run build
```

Tests need no GPU, webcam, network, model weights or provider keys.

---

## API overview

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Service health |
| POST | `/api/video/webcam/start` | Start webcam (legacy mode) |
| POST | `/api/video/upload` | Upload and play a file immediately (legacy mode) |
| POST | `/api/video/stop` | Stop analysis / reset an error |
| GET | `/api/video/status` | State plus `session_ref` / `session_id` |
| GET | `/api/video/stream` | MJPEG stream while RUNNING |
| GET | `/api/detections/latest` | Latest YOLO result (`204` if none) |
| GET | `/api/domain/current` | Scene-domain snapshot |
| GET | `/api/evaluation/current` | Current AI evaluation state |
| POST | `/api/evaluation/callback` | Langflow callback (token required; unavailable with Gemini) |
| GET | `/api/video/sources/capabilities` | Mode, limits, template availability |
| GET | `/api/video/sources/current` | Prepared source snapshot (`204` if none) |
| POST | `/api/video/sources` | Create a source (private mode) |
| PUT | `/api/video/sources/{id}/file` | Upload and probe the file |
| GET | `/api/video/sources/{id}/preview` | One JPEG preview frame |
| GET | `/api/video/sources/{id}/template` | Unconfirmed startup template (`204` if none) |
| PUT | `/api/video/sources/{id}/configuration` | Save line and zones |
| POST | `/api/video/sources/{id}/play` | Start analysis for a saved configuration |
| GET | `/api/video/sources/{id}/play/{attempt}` | Outcome of a Play attempt |
| DELETE | `/api/video/sources/{id}` | Delete the source and its file |
| POST | `/api/video/sources/cleanup/retry` | Operator retry of a stalled cleanup |

The frozen request and error contract of the sources API is in the backend plan §19.3.
Interactive docs: `http://localhost:8000/docs` while the backend runs.

---

## Project structure (main paths)

```
backend/
  requirements.txt, .env.example, yolo26s.pt (git-ignored, place manually)
  app/main.py                    FastAPI app, CORS, lifespan, routers
  app/api/                       health, video, sources, detections, domain, evaluation
  app/models/                    frozen contracts and response models
  app/services/runtime.py        composition root (mode selection, wiring, shutdown)
  app/services/video_service.py  capture state machine, MJPEG, session lifecycle
  app/services/prepared_source.py  private-mode upload/preview/configuration/Play store
  app/services/private_parking_*.py, parking_candidate_trigger.py, retail_candidate_trigger.py
  app/services/evaluation_worker.py, gemini_evaluation_client.py, langflow_evaluation_client.py
  app/services/audio_alert_service.py
  app/storage/uploads/           runtime files (git-ignored)
  tests/                         pytest suite with fakes
frontend/src/
  App.tsx, api/, components/, hooks/, geometry/
docs/                            harness: plans, design, reliability, security, product specs
```

`phase1-video-input-plan.md` is a historical record and partly stale.
