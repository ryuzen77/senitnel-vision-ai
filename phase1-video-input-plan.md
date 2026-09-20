# Phase 1 — Video Input Foundation Plan (Revision 3 — Final)

## Overview

Add video input capability to Sentinel Vision AI so the backend can capture frames
from a local webcam (OpenCV `VideoCapture(0)`) or from an uploaded MP4/WebM file,
stream MJPEG frames to the frontend via a dedicated HTTP endpoint, and expose full
lifecycle control (start / stop / status) through a REST API.

The frontend gains a source-selector UI (Webcam vs Upload), a live preview panel,
and a status badge that mirrors the backend state machine.

No YOLO, no tracking, no domain detection, no alarms, no database — raw video
pipeline only.

Uvicorn runs with exactly **one worker** because `VideoService` is a singleton
held in process memory. Multiple workers would each have an independent service
instance, making state invisible across processes.

---

## State Machine

```
IDLE ──start──► STARTING ──ready──► RUNNING ──stop──► STOPPING ──done──► IDLE
                    │                   │                   │
                  error               error               error
                    └───────────────────┴───────────────────► ERROR
                                                                │
                                                     POST /api/video/stop
                                                                │
                                                              IDLE
```

States are owned by the backend service and reported on `GET /api/video/status`.
`POST /api/video/stop` acts as both a normal stop and an error reset — no
separate `/reset` endpoint is needed.

**State ownership rule**: `_release_resources()` never changes `_state`. Every
caller (capture thread, `stop()`, or upload handler rollback) is responsible for
setting the state that is appropriate to its own outcome. This prevents a cleanup
helper from accidentally overwriting a state transition that the caller has not
yet made.

---

## Architecture

```
frontend/src/
  components/
    VideoSourceSelector.tsx   # Webcam / Upload tab control
    VideoPreview.tsx          # <img> tag driven by MJPEG stream (stable URL)
    VideoStatusBadge.tsx      # Shows IDLE / RUNNING / ERROR etc.
  hooks/
    useVideoStatus.ts         # Polls GET /api/video/status every second
  api/
    videoApi.ts               # Typed wrappers for all /api/video/* calls

backend/app/
  api/
    video.py                  # FastAPI router — 5 endpoints (public API only)
  services/
    video_service.py          # State machine + OpenCV capture loop
  models/
    video.py                  # Pydantic schemas (request + response)
  storage/
    uploads/                  # Temp dir for uploaded video files (absolute path)

backend/tests/
  conftest.py                 # Shared pytest fixtures, FakeCapture stub
  test_video_service.py       # Unit tests for VideoService state machine
  test_video_api.py           # Integration tests for all 5 endpoints
```

---

## Sub-Tasks

---

### ST-1 — Backend: Pydantic models

**Intent**
Define the shared data contracts used by every video endpoint and the service
layer. Creating these first prevents circular imports and makes subsequent tasks
unambiguous.

**Expected Outcomes**
- `backend/app/models/video.py` exists with `VideoState` enum, `FramePacket`
  dataclass, and response schemas.

**Todo List**
1. Create `backend/app/models/video.py`.
2. Define `VideoState` enum: `IDLE`, `STARTING`, `RUNNING`, `STOPPING`, `ERROR`.
3. Define `FramePacket` dataclass with fields:
   - `seq: int` — monotonically increasing sequence number (starts at 0,
     increments per captured frame; never resets within a single session).
   - `ts: float` — `time.monotonic()` timestamp recorded at the moment the frame
     is captured.
   - `frame` — raw OpenCV numpy array (`numpy.ndarray`); retained for future
     processing hooks.
   - `jpeg: bytes` — JPEG-encoded bytes of the frame.
4. Define `VideoStatusResponse` Pydantic model: `state: VideoState`,
   `source: str | None`, `error: str | None`.
5. Define `VideoStartResponse` Pydantic model: `state: VideoState`,
   `message: str`.

**Relevant Context**
- `backend/app/models/__init__.py` is currently empty — no existing patterns.
- Pydantic v2 ships with FastAPI 0.111 — use `model_config` style, not
  `class Config`.
- `FramePacket` is a plain `dataclasses.dataclass`, not a Pydantic model — it is
  internal to the service and never serialised to JSON directly.

**Status** — `[ ] pending`

---

### ST-2 — Backend: VideoService

**Intent**
Encapsulate all OpenCV and file-based capture logic in a single service class that
owns the state machine, the capture thread, and all resource cleanup. The API
layer never touches `cv2` directly.

Key design constraints enforced in this revision:
- `_release_resources()` is a pure resource-cleanup helper. It never touches
  `_state`. Callers set state before and/or after calling it.
- `_packet` is exclusively protected by `_packet_cond` (a `threading.Condition`).
  `_lock` is never held while interacting with `_packet_cond`.
- `_release_resources()` skips `thread.join()` when called from the capture thread
  itself (detected via `threading.current_thread()`), preventing self-join.
- Start operations are atomic reservations: under `_lock`, the state moves to
  `STARTING` before any I/O begins, so two concurrent requests cannot both
  observe `IDLE` and proceed.
- File-based playback uses a monotonic deadline loop — the loop records the
  deadline before `cap.read()` and sleeps only for the remaining time after
  encode completes, so decode + encode time is absorbed.
- The webcam source enters `ERROR` only after a configurable number of consecutive
  `ret == False` reads (`WEBCAM_MAX_CONSECUTIVE_FAILURES`, default `5`), not on
  the first failure, to tolerate transient frame drops.
- Public method `wait_for_packet(after_seq, timeout)` exposes the Condition
  to the API layer without leaking `_packet_cond`.

**Expected Outcomes**
- `backend/app/services/video_service.py` exists.
- Public interface: `start_webcam()`, `start_file(path)`, `stop()`,
  `get_status()`, `get_latest_packet()`, `wait_for_packet(after_seq, timeout)`.
- `_release_resources()` is private, state-neutral, and idempotent.
- Capture thread never self-joins.
- `_packet` slot only updated and read under `_packet_cond`.
- File playback uses monotonic deadline, not `sleep(1/fps)`.
- Webcam errors require `WEBCAM_MAX_CONSECUTIVE_FAILURES` before ERROR.
- Start operations are atomic (reservation under `_lock`).

**Todo List**
1. Create `backend/app/services/video_service.py`.
2. Import `cv2`, `threading`, `time`, `dataclasses`, `pathlib`, and models from
   ST-1.
3. Define module-level constants:
   - `DEFAULT_FILE_FPS: float = 25.0`
   - `WEBCAM_MAX_CONSECUTIVE_FAILURES: int = 5`
   - `UPLOAD_DIR = Path(__file__).resolve().parent.parent / "storage" / "uploads"`
4. Implement `VideoService`:
   a. Slots: `_state: VideoState`, `_source: str | None`, `_error: str | None`,
      `_cap: cv2.VideoCapture | None`, `_thread: threading.Thread | None`,
      `_stop_event: threading.Event`, `_packet_cond: threading.Condition`,
      `_packet: FramePacket | None` (accessed only under `_packet_cond`),
      `_upload_path: Path | None`, `_released: bool`, `_lock: threading.Lock`.
   b. `_state`, `_source`, `_error`, `_cap`, `_thread`, `_stop_event`,
      `_upload_path`, `_released` are guarded by `_lock`.
   c. `_packet` is guarded exclusively by `_packet_cond` — never hold `_lock`
      while acquiring `_packet_cond` or calling its methods.
5. Implement `_release_resources()`:
   - Acquire `_lock`; if `_released` is True, return immediately (idempotent).
   - Set `_released = True`.
   - Capture local refs to `_cap`, `_thread`; set both to None on the instance.
   - Delete `_upload_path` from disk if it exists; set `_upload_path = None`.
   - Release `_lock` **before** the join block to avoid deadlock.
   - If `thread` is not None and `thread` is **not** `threading.current_thread()`,
     call `thread.join(timeout=3.0)` (self-join guard).
   - If `cap` is not None, call `cap.release()`.
   - After cleanup, acquire `_packet_cond`; set `_packet = None`;
     call `_packet_cond.notify_all()` so any waiting stream consumer unblocks.
6. Implement `_atomic_reserve()` — private helper:
   - Acquire `_lock`.
   - If `_state != IDLE`, release lock and return False.
   - Set `_state = STARTING`; reset `_stop_event`; set `_released = False`.
   - Release lock; return True.
   - This is the single gate used by both `start_webcam()` and `start_file()`.
7. Implement `start_webcam()`:
   - Call `_atomic_reserve()`; if False, return (state already not IDLE).
   - Open `cv2.VideoCapture(0)`.
   - If not opened: under `_lock`, set `_state = ERROR`; call
     `_release_resources()`; return.
   - Under `_lock`: store `_cap`; set `_source = "webcam"`; set `_state = RUNNING`.
   - Launch capture thread (webcam mode); store `_thread` under `_lock`.
8. Implement `start_file(path: Path)`:
   - Call `_atomic_reserve()`; if False, return.
   - Under `_lock`, set `_upload_path = path`.
   - Open `cv2.VideoCapture(str(path))`.
   - If not opened: under `_lock`, set `_state = ERROR`; call
     `_release_resources()`; return.
   - Read `cap.get(cv2.CAP_PROP_FPS)`; use `DEFAULT_FILE_FPS` if value ≤ 0.
   - Under `_lock`: store `_cap`; set `_source = path.name`; set
     `_state = RUNNING`.
   - Launch capture thread (file mode, passing target FPS); store `_thread` under
     `_lock`.
9. Implement `_run_capture_webcam()` (capture thread body for webcam):
   - `consecutive_failures = 0`
   - Loop:
     - If `_stop_event.is_set()`: break (clean stop path).
     - `ret, frame = cap.read()`.
     - If `not ret`:
       - `consecutive_failures += 1`
       - If `consecutive_failures >= WEBCAM_MAX_CONSECUTIVE_FAILURES`:
         under `_lock`, set `_state = ERROR`, `_error = "Read failure"`.
         Call `_release_resources()`. Return.
       - Continue (absorb transient failure).
     - `consecutive_failures = 0`
     - Encode frame to JPEG; create `FramePacket(seq, time.monotonic(), frame,
       jpeg)` where `seq` is an int counter local to this thread session.
     - Acquire `_packet_cond`; set `_packet`; call `notify_all()`.
   - (Loop exits via break — clean stop.)
   - Under `_lock`, set `_state = STOPPING`.
   - Call `_release_resources()`.
   - Under `_lock`, set `_state = IDLE`.
10. Implement `_run_capture_file(fps: float)` (capture thread body for file):
    - `frame_interval = 1.0 / fps`
    - Loop:
      - `deadline = time.monotonic() + frame_interval`
      - If `_stop_event.is_set()`: break (clean stop path).
      - `ret, frame = cap.read()`.
      - If `not ret` and `_stop_event.is_set()`: break.
      - If `not ret` (EOF — stop_event not set):
        under `_lock`, set `_state = STOPPING`.
        Call `_release_resources()`.
        Under `_lock`, set `_state = IDLE`.
        Return.
      - Encode frame to JPEG; create `FramePacket`; acquire `_packet_cond`;
        set `_packet`; call `notify_all()`.
      - `remaining = deadline - time.monotonic()`.
      - If `remaining > 0`, `time.sleep(remaining)` — monotonic deadline absorbs
        encode time.
    - (Loop exits via break — clean stop.)
    - Under `_lock`, set `_state = STOPPING`.
    - Call `_release_resources()`.
    - Under `_lock`, set `_state = IDLE`.
11. Both capture methods must wrap the main loop in a `try/except Exception`:
    - On exception: under `_lock`, set `_state = ERROR`, `_error = str(e)`.
      Call `_release_resources()`. Return.
12. Implement `stop()`:
    - Under `_lock`: if `_state == IDLE`, return (idempotent).
    - Under `_lock`: set `_state = STOPPING`.
    - Call `_release_resources()`.
    - Under `_lock`: set `_state = IDLE`, clear `_error`.
13. Implement `get_status() -> VideoStatusResponse` — reads `_state`, `_source`,
    `_error` under `_lock`.
14. Implement `get_latest_packet() -> FramePacket | None` — acquires
    `_packet_cond`, reads `_packet`, returns it without blocking.
15. Implement `wait_for_packet(after_seq: int, timeout: float) -> FramePacket | None`:
    - Acquire `_packet_cond`.
    - Use `_packet_cond.wait_for(lambda: _packet is None or _packet.seq > after_seq,
      timeout=timeout)`.
    - Return current `_packet` (may be None if service stopped or timeout).
    - This is the only method the API layer calls to block-wait for new frames.
    - Never exposes `_packet_cond` itself to callers.
16. Create module-level singleton: `video_service = VideoService()`.

**Relevant Context**
- `backend/app/services/__init__.py` is empty.
- `cv2` comes from `opencv-python` (ST-5).
- `threading.Condition` has its own internal lock. Never hold `_lock` while
  calling `_packet_cond.acquire()` / `.wait()` / `.notify_all()` — these are
  always called on a separately-acquired Condition context.
- `FramePacket` defined in ST-1.
- Thread must be `daemon=True` as a last-resort failsafe if join times out.

**Status** — `[ ] pending`

---

### ST-3 — Backend: Video API router

**Intent**
Expose the five REST endpoints that the frontend will call. The router is thin —
it delegates all logic to `VideoService` via public methods only. The MJPEG
stream generator uses the public `wait_for_packet()` method so no private
internals are accessed from the router. The stream response includes a
`Cache-Control: no-store` header. Client disconnection is handled via generator
cancellation.

**Expected Outcomes**
- `backend/app/api/video.py` exists with all five endpoints using only the public
  service interface.
- `GET /api/video/stream` response carries `Cache-Control: no-store`.
- Stream generator detects client disconnect and exits cleanly.
- `backend/app/main.py` registers the new router using a lifespan context
  manager.
- Upload handler uses an atomic reservation to prevent race conditions before
  writing any file.

**Todo List**
1. Create `backend/app/api/video.py`.
2. Import `video_service` singleton, `UPLOAD_DIR`, and models from ST-1 and ST-2.
3. `POST /api/video/webcam/start`:
   - Read `service.get_status().state`; return 409 if not IDLE.
   - Call `service.start_webcam()`; return `VideoStartResponse`.
4. `POST /api/video/upload`:
   - **Atomic reservation first**: call `service.start_webcam()`'s atomic-reserve
     equivalent for upload — specifically, this means the route handler itself
     should call a public `reserve_start()` method on the service (or simply
     check-and-claim by attempting the atomic reserve). If state is not IDLE,
     return 409 **before** any file I/O.
     Implementation note: the upload handler must call a public method that
     atomically moves the service from IDLE → STARTING (the same `_atomic_reserve`
     gate used by `start_webcam`). If it returns False → 409. From this point on,
     the service is in STARTING and the upload handler is the sole owner.
   - Validate `UploadFile.content_type` is `video/mp4` or `video/webm`; if
     invalid: call `service.stop()` to roll back to IDLE; return 422.
   - Validate file extension (`.mp4`, `.webm`) from original filename; same
     rollback if invalid.
   - Determine `ext` from original filename.
   - `saved_path = UPLOAD_DIR / f"{uuid4()}.{ext}"`
   - Stream-write in 1 MB chunks, accumulating `total_bytes`. If `total_bytes`
     exceeds 200 MB: delete `saved_path` if it exists; call `service.stop()`;
     return 422.
   - Wrap the entire write loop in `try/finally` so a partial file is always
     deleted on any exception path; call `service.stop()` in the except branch.
   - After successful write, open a **probe** `cv2.VideoCapture(str(saved_path))`
     in a `try/finally` block:
     - `try`: check `probe.isOpened()`.
     - `finally`: `probe.release()` — always released before any further action.
     - If not opened: delete `saved_path`; call `service.stop()`; return 422.
   - Call `service.start_file(saved_path)` — this picks up from STARTING and
     transitions to RUNNING.
   - Return `VideoStartResponse`.
5. `POST /api/video/stop`:
   - Call `service.stop()` (idempotent — also recovers from ERROR → IDLE).
   - Return `service.get_status()`.
6. `GET /api/video/status`:
   - Return `service.get_status()`.
7. `GET /api/video/stream`:
   - Read current status; return 503 if state is not RUNNING.
   - Define an **async generator** `generate()`:
     - `last_seq = -1`
     - Loop:
       - Offload `service.wait_for_packet(last_seq, timeout=2.0)` to a thread
         pool via `await asyncio.get_event_loop().run_in_executor(None, ...)` so
         the event loop is not blocked.
       - If the returned packet is None: check if state is still RUNNING; if not,
         break (stream ended normally).
       - If `packet.seq == last_seq`: continue (timeout wake, no new frame).
       - `last_seq = packet.seq`
       - `yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + packet.jpeg + b"\r\n"`
     - Wrap the loop body in `try/except (GeneratorExit, Exception)`: on
       `GeneratorExit` (client disconnected), break and return cleanly.
   - Return `StreamingResponse(generate(), media_type=
     "multipart/x-mixed-replace; boundary=frame",
     headers={"Cache-Control": "no-store"})`.
8. Expose a thin public wrapper `reserve_start()` on `VideoService` that calls
   `_atomic_reserve()` and returns bool — the upload handler calls this instead
   of checking state and calling start separately.
   (Alternatively, document that `start_webcam()` and `start_file()` call
   `_atomic_reserve()` internally, and provide a standalone public
   `claim_start() -> bool` for the upload route to use as a two-phase start.)
9. Register `video.router` in `backend/app/main.py` using lifespan (see ST-4).

**Relevant Context**
- Existing router pattern: `backend/app/api/health.py`.
- `python-multipart==0.0.9` already in `requirements.txt`.
- `asynccontextmanager` lifespan replaces deprecated `@app.on_event`.
- `run_in_executor(None, fn)` uses the default thread pool — appropriate for
  the 2-second `wait_for_packet` call.
- `GeneratorExit` is raised inside async generators when the client disconnects
  or the response is closed; catching it allows clean teardown without logging
  noise.

**Status** — `[ ] pending`

---

### ST-4 — Backend: Storage directory + lifespan

**Intent**
Ensure the uploads directory exists at startup using an absolute path anchored to
the service file location (not the current working directory), and that the service
is shut down cleanly on application exit.

**Expected Outcomes**
- `UPLOAD_DIR` defined in `video_service.py` via `Path(__file__).resolve()` —
  stable regardless of where `uvicorn` is invoked.
- On startup: `UPLOAD_DIR.mkdir(parents=True, exist_ok=True)`.
- On shutdown: `video_service.stop()` called to release any active capture.
- A `.gitkeep` placeholder keeps `uploads/` in version control.

**Todo List**
1. Add `backend/app/storage/uploads/.gitkeep` (empty file).
2. `UPLOAD_DIR` is already defined in `video_service.py` (ST-2, step 3); no
   duplication needed.
3. In `backend/app/api/video.py`, import `UPLOAD_DIR` from the service module.
4. In `backend/app/main.py`, add a lifespan context manager:
   ```python
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
       yield
       video_service.stop()
   ```
5. Pass `lifespan=lifespan` to `FastAPI(...)`.

**Relevant Context**
- `Path(__file__)` in `video_service.py` is
  `backend/app/services/video_service.py`; `.parent.parent` is `backend/app/`.
- FastAPI lifespan signature:
  `@asynccontextmanager async def lifespan(app: FastAPI): ...`

**Status** — `[ ] pending`

---

### ST-5 — Backend: Dependencies update

**Intent**
Add `opencv-python` and the test stack to `requirements.txt` so the environment
is reproducible.

**Expected Outcomes**
- `backend/requirements.txt` includes `opencv-python`, `pytest`,
  `pytest-asyncio`, and `httpx` at pinned versions.

**Todo List**
1. Add `opencv-python==4.10.0.84`.
2. Add `pytest==8.2.2`.
3. Add `pytest-asyncio==0.23.7`.
4. Add `httpx==0.27.0` (FastAPI `TestClient` async transport).

**Relevant Context**
- Miniconda environment `sentinel-vision`; install via `pip install -r
  requirements.txt`.
- `opencv-python` ships its own `numpy`; no separate numpy pin needed.
- `httpx` is required for FastAPI's async `TestClient`.

**Status** — `[ ] pending`

---

### ST-6 — Frontend: Type definitions and API client

**Intent**
Create a single typed module for all backend communication. The stream URL is a
stable constant — it does not embed a timestamp. Cache-busting is handled once,
at component mount time, not on every render or poll.

**Expected Outcomes**
- `frontend/src/api/videoApi.ts` exists.
- Exports `VideoState` enum, `VideoStatusResponse` type, `VideoValidationError`,
  and async functions: `startWebcam()`, `uploadVideo(file)`, `stopVideo()`,
  `getVideoStatus()`.
- `VIDEO_STREAM_URL` exported as a plain string constant `/api/video/stream`.
- `uploadVideo()` enforces MIME and size guards before sending.

**Todo List**
1. Create `frontend/src/api/videoApi.ts`.
2. Define `VideoState` enum: `IDLE`, `STARTING`, `RUNNING`, `STOPPING`, `ERROR`.
3. Define `VideoStatusResponse` interface: `state: VideoState`,
   `source: string | null`, `error: string | null`.
4. Define `VideoValidationError` class extending `Error`.
5. Implement `startWebcam()` → `POST /api/video/webcam/start`.
6. Implement `uploadVideo(file: File)`:
   - Guard: `file.type` must be `video/mp4` or `video/webm`; throw
     `VideoValidationError` otherwise.
   - Guard: `file.size` ≤ `200 * 1024 * 1024`; throw `VideoValidationError`
     otherwise.
   - `POST /api/video/upload` with `FormData`.
7. Implement `stopVideo()` → `POST /api/video/stop`.
8. Implement `getVideoStatus()` → `GET /api/video/status`.
9. Export `VIDEO_STREAM_URL = '/api/video/stream'` (no timestamp embedded here).

**Relevant Context**
- `vite.config.ts` proxies `/api` to `http://localhost:8000`.
- No Axios; use native `fetch`.

**Status** — `[ ] pending`

---

### ST-7 — Frontend: `useVideoStatus` hook

**Intent**
Provide a reusable hook that polls the backend status endpoint and exposes the
current state to consuming components, with automatic cleanup on unmount.

**Expected Outcomes**
- `frontend/src/hooks/useVideoStatus.ts` exists.
- Returns `{ status: VideoStatusResponse | null, fetchError: string | null }`.
- Polls every 1 second; clears interval on unmount.

**Todo List**
1. Create `frontend/src/hooks/useVideoStatus.ts`.
2. Use `useState` for `status` and `fetchError`.
3. Use `useEffect` with `setInterval` calling `getVideoStatus()` every 1000 ms.
4. Clear interval in the `useEffect` cleanup function.
5. On fetch error: set `fetchError`; on success: clear `fetchError`, update
   `status`.

**Relevant Context**
- Pattern mirrors existing `useEffect` + `fetch` in `App.tsx`.

**Status** — `[ ] pending`

---

### ST-8 — Frontend: UI components

**Intent**
Build three focused components that together form the video input panel.

`VideoPreview` keeps a stable MJPEG URL for the lifetime of the RUNNING session.
The URL is computed once when the component transitions into RUNNING state (or
when mounted while already RUNNING) and stored in a `useRef`. It is not
recomputed on every status poll. The `Cache-Control: no-store` header on the
backend stream response handles browser caching instead.

**Expected Outcomes**
- `VideoStatusBadge.tsx` — coloured pill badge per `VideoState`.
- `VideoPreview.tsx` — stable MJPEG URL computed once per RUNNING session via
  `useRef`; `<img>` src does not change on polling.
- `VideoSourceSelector.tsx` — two tabs; actions disabled during transitions.

**Todo List**
1. Create `frontend/src/components/VideoStatusBadge.tsx`.
   - Map each `VideoState` to a label and background colour.
   - Pure presentational; accepts `state: VideoState` prop.
2. Create `frontend/src/components/VideoPreview.tsx`.
   - Accepts `state: VideoState` prop.
   - Use `useRef<string | null>(null)` for `streamUrl`.
   - In a `useEffect` keyed on `state`:
     - When `state` transitions to `RUNNING` and `streamUrl.current` is null:
       set `streamUrl.current = VIDEO_STREAM_URL + '?sid=' + Date.now()`.
       (The `?sid` cache-buster is computed once per RUNNING session, not on
       every render or poll.)
     - When `state` leaves `RUNNING`: set `streamUrl.current = null`.
   - When RUNNING and `streamUrl.current` is set: render
     `<img src={streamUrl.current} alt="Video stream" />`.
   - Otherwise: render a grey placeholder box with the current state label.
3. Create `frontend/src/components/VideoSourceSelector.tsx`.
   - Local state for active tab (`'webcam' | 'upload'`).
   - Webcam tab: one button — label "Start Webcam" when IDLE/ERROR, "Stop" when
     RUNNING. Calls `startWebcam()` or `stopVideo()`.
   - Upload tab: `<input type="file" accept="video/mp4,video/webm">`. On change:
     run `uploadVideo(file)`. Catch `VideoValidationError` → inline message.
     Catch API errors → inline message.
   - Buttons and file input disabled when state is `STARTING` or `STOPPING`.

**Relevant Context**
- No UI library installed — plain HTML + inline styles.
- `VIDEO_STREAM_URL` imported from ST-6 (plain constant, no timestamp).

**Status** — `[ ] pending`

---

### ST-9 — Frontend: Wire into App.tsx

**Intent**
Integrate the new components and hook into the existing `App.tsx` entry point.
The original health check is preserved.

**Expected Outcomes**
- `App.tsx` renders `VideoStatusBadge`, `VideoSourceSelector`, and `VideoPreview`
  below the health check line.
- `useVideoStatus` drives the `state` prop passed to all three components.

**Todo List**
1. Import `useVideoStatus` from ST-7.
2. Import the three components from ST-8.
3. Import `VideoState` from ST-6.
4. `const { status } = useVideoStatus()`.
5. Render `VideoStatusBadge` with `state={status?.state ?? VideoState.IDLE}`.
6. Render `VideoSourceSelector` with `state={status?.state ?? VideoState.IDLE}`.
7. Render `VideoPreview` with `state={status?.state ?? VideoState.IDLE}`.

**Relevant Context**
- Preserve the existing health check `useEffect`; add the video panel below
  `<h1>`.

**Status** — `[ ] pending`

---

### ST-10 — Backend: Automated tests

**Intent**
Provide a pytest suite that verifies `VideoService` and all five endpoints without
a physical webcam. `FakeCapture` stubs all `cv2.VideoCapture` calls. The test
suite includes cases for every new design constraint added in this revision.

**Expected Outcomes**
- `backend/tests/conftest.py` with `FakeCapture` and fixtures.
- `backend/tests/test_video_service.py` — unit tests for the service.
- `backend/tests/test_video_api.py` — integration tests for endpoints.
- All 24 tests pass with `pytest tests/ -v` from `backend/`.

**Todo List**
1. Create `backend/tests/__init__.py` (empty).
2. Create `backend/tests/conftest.py`:
   - Define `FakeCapture(source, *, opened=True, reads=None)`:
     - `source`: int or str.
     - `opened`: controls `isOpened()` return value.
     - `reads`: optional list of `(ret, frame)` tuples; if None, yields infinite
       `(True, numpy.zeros((480,640,3), uint8))`.
     - `get(prop)`: returns `30.0` for `CAP_PROP_FPS`; `0` otherwise.
     - `release()`: records call count; no-op.
     - `release_call_count: int` attribute for assertions.
   - `fake_cap_factory(monkeypatch, **kwargs)` fixture: monkeypatches
     `app.services.video_service.cv2.VideoCapture` to return a `FakeCapture`
     with the given kwargs.
   - `fresh_service(monkeypatch)` fixture: returns a fresh `VideoService()`
     with default `FakeCapture` injected (opened=True, infinite reads).
3. Create `backend/tests/test_video_service.py`:
   - `test_initial_state_is_idle`
   - `test_start_webcam_transitions_to_running`
   - `test_stop_from_running_transitions_to_idle`
   - `test_stop_from_idle_is_idempotent`
   - `test_stop_from_error_transitions_to_idle`
   - `test_concurrent_start_only_one_wins` — two threads call `start_webcam()`
     simultaneously; assert exactly one sees RUNNING and one is rejected; no
     state corruption.
   - `test_failed_open_transitions_to_error`
   - `test_file_eof_transitions_to_idle` — `FakeCapture(reads=[(False, None)])`
     on first read; wait for thread to exit; assert state is IDLE.
   - `test_frame_packet_seq_increments` — let two frames through; assert
     `second.seq == first.seq + 1`.
   - `test_cleanup_from_capture_thread_does_not_self_join` — `FakeCapture` that
     triggers an error on first read; after ERROR state is reached, assert no
     deadlock occurred (i.e. test completes within timeout).
   - `test_release_resources_idempotent` — call `stop()` twice from RUNNING;
     assert no exception and `FakeCapture.release_call_count == 1`.
   - `test_wait_for_packet_returns_none_on_timeout` — call
     `wait_for_packet(after_seq=999, timeout=0.1)` on idle service; assert
     returns None within ~0.1 s.
   - `test_stream_does_not_deliver_same_seq_twice` — start service; call
     `wait_for_packet(-1, timeout=1.0)` twice; assert second `seq > first seq`.
4. Create `backend/tests/test_video_api.py`:
   - `test_health_endpoint`
   - `test_start_webcam_returns_200_when_idle`
   - `test_start_webcam_returns_409_when_running`
   - `test_stop_returns_status`
   - `test_stop_from_error_returns_idle`
   - `test_status_endpoint_returns_valid_schema`
   - `test_upload_rejects_wrong_mime` — content_type=`text/plain`, ext=`.txt`;
     expect 422.
   - `test_upload_rejects_when_running` — start webcam; attempt upload; expect
     409 and assert no file written to `UPLOAD_DIR`.
   - `test_stream_returns_503_when_not_running`
   - `test_partial_upload_is_always_deleted` — send a file that exceeds 200 MB
     limit (via `FakeCapture` and a generator that produces enough bytes); assert
     `UPLOAD_DIR` contains no new files after the 422.
   - `test_probe_capture_is_always_released` — patch `cv2.VideoCapture` to track
     all instances; upload a file that fails the probe; assert every created
     VideoCapture instance has `release()` called.

**Relevant Context**
- `pytest==8.2.2`, `httpx==0.27.0` from ST-5.
- Monkeypatch target: `app.services.video_service.cv2.VideoCapture`.
- For concurrency test, use `threading.Barrier` to synchronise two threads before
  both call `start_webcam()`.
- For the self-join test, a short timeout (e.g. `pytest-timeout` or
  `threading.Timer`) verifies the thread exits without hanging.

**Status** — `[ ] pending`

---

### ST-11 — README update

**Intent**
Replace `venv` with Miniconda and update all tables and the directory tree.

**Expected Outcomes**
- `conda activate sentinel-vision` replaces `.venv` instructions.
- `--workers 1` explicit in the `uvicorn` command.
- All five `/api/video/*` endpoints in the API table.
- Directory tree updated to include all Phase 1 files.

**Todo List**
1. Replace the virtual environment section with:
   ```
   conda activate sentinel-vision
   pip install -r requirements.txt
   uvicorn app.main:app --reload --workers 1 --host 0.0.0.0 --port 8000
   ```
2. Remove all `.venv` references.
3. Add all five `/api/video/*` endpoints to the API endpoints table.
4. Update the directory tree to include:
   - `backend/app/models/video.py`
   - `backend/app/services/video_service.py`
   - `backend/app/api/video.py`
   - `backend/app/storage/uploads/`
   - `backend/tests/`
   - `frontend/src/api/videoApi.ts`
   - `frontend/src/hooks/useVideoStatus.ts`
   - `frontend/src/components/`

**Status** — `[ ] pending`

---

## New Files

| File | Sub-Task |
|---|---|
| `backend/app/models/video.py` | ST-1 |
| `backend/app/services/video_service.py` | ST-2, ST-4 |
| `backend/app/api/video.py` | ST-3 |
| `backend/app/storage/uploads/.gitkeep` | ST-4 |
| `backend/tests/__init__.py` | ST-10 |
| `backend/tests/conftest.py` | ST-10 |
| `backend/tests/test_video_service.py` | ST-10 |
| `backend/tests/test_video_api.py` | ST-10 |
| `frontend/src/api/videoApi.ts` | ST-6 |
| `frontend/src/hooks/useVideoStatus.ts` | ST-7 |
| `frontend/src/components/VideoStatusBadge.tsx` | ST-8 |
| `frontend/src/components/VideoPreview.tsx` | ST-8 |
| `frontend/src/components/VideoSourceSelector.tsx` | ST-8 |

## Modified Files

| File | Sub-Task | Change |
|---|---|---|
| `backend/requirements.txt` | ST-5 | Add opencv-python, pytest, pytest-asyncio, httpx |
| `backend/app/main.py` | ST-3, ST-4 | Register video router + lifespan |
| `frontend/src/App.tsx` | ST-9 | Add video panel |
| `README.md` | ST-11 | Miniconda + --workers 1 + new endpoints + new tree |

---

## New Dependencies

### Backend (`requirements.txt`)

| Package | Version | Reason |
|---|---|---|
| `opencv-python` | `4.10.0.84` | `VideoCapture`, frame encode |
| `pytest` | `8.2.2` | Test runner |
| `pytest-asyncio` | `0.23.7` | Async test support |
| `httpx` | `0.27.0` | FastAPI async `TestClient` transport |

### Frontend (`package.json`)
None — all features use React, TypeScript, and native browser APIs already
present.

---

## Webcam Lifecycle

```
User clicks "Start Webcam"
  → POST /api/video/webcam/start
  → VideoService._atomic_reserve():
      acquire _lock; _state == IDLE? → set STARTING; _released=False; return True
      (concurrent request arrives here → _state == STARTING → return False → 409)
  → cv2.VideoCapture(0).isOpened()?
    ├─ No  → under _lock: _state = ERROR; _release_resources() [no join, no state change];
    └─ Yes → under _lock: _cap = cap; _state = RUNNING; launch capture thread
             └─ _run_capture_webcam():
                  consecutive_failures = 0
                  loop:
                    stop_event? → break (clean exit path)
                    ret, frame = cap.read()
                    not ret → consecutive_failures++
                              if >= WEBCAM_MAX_CONSECUTIVE_FAILURES:
                                _state = ERROR; _release_resources(); return
                    else: consecutive_failures = 0
                    encode → FramePacket(seq++, ts, frame, jpeg)
                    _packet_cond: _packet = pkt; notify_all()
                  (break) → _state=STOPPING; _release_resources(); _state=IDLE

User clicks "Stop"
  → POST /api/video/stop
  → service.stop():
      under _lock: _state = STOPPING
      _release_resources():
        _released=True; _stop_event.set()
        release lock; thread is not current_thread → thread.join(3s)
        cap.release()
        _packet_cond: _packet=None; notify_all()
      under _lock: _state = IDLE; _error = None
```

---

## Upload Video Lifecycle

```
User selects file
  → Client-side guard (videoApi.ts):
    ├─ MIME not video/mp4 or video/webm → VideoValidationError → inline error
    ├─ size > 200 MB → VideoValidationError → inline error
    └─ pass → POST /api/video/upload (multipart/form-data)

  → Upload route handler:
    1. service.claim_start() [_atomic_reserve]:
       ├─ False (not IDLE) → 409 (no file written)
       └─ True → service is now STARTING; handler is sole owner
    2. Validate content_type → on fail: service.stop() → 422
    3. Validate extension → on fail: service.stop() → 422
    4. saved_path = UPLOAD_DIR / {uuid4()}.{ext}
    5. try:
         write chunks to saved_path
         if total > 200 MB → raise → except deletes partial + service.stop() → 422
       except:
         delete saved_path (partial cleanup in finally)
         service.stop(); raise HTTPException 422
    6. try:
         probe = cv2.VideoCapture(str(saved_path))
       finally:
         probe.release()  ← always released
       if not probe.isOpened(): delete saved_path; service.stop() → 422
    7. service.start_file(saved_path):
         (service is already STARTING from step 1)
         sets _upload_path = saved_path
         opens cap; reads FPS via CAP_PROP_FPS (fallback DEFAULT_FILE_FPS if ≤ 0)
         _state = RUNNING; launches _run_capture_file(fps)
    8. return VideoStartResponse(state=RUNNING)

  File capture thread (_run_capture_file):
    frame_interval = 1 / fps
    loop:
      deadline = time.monotonic() + frame_interval
      stop_event? → break
      ret, frame = cap.read()
      not ret (EOF, stop_event not set):
        _state = STOPPING; _release_resources(); _state = IDLE
        (cleanup deletes _upload_path from disk); return
      encode → FramePacket; _packet_cond: _packet=pkt; notify_all()
      remaining = deadline - time.monotonic()
      if remaining > 0: sleep(remaining)  ← monotonic deadline
    (break) → _state=STOPPING; _release_resources(); _state=IDLE
```

---

## Resource Cleanup Strategy

| Resource | Cleanup Owner | Guard |
|---|---|---|
| `cv2.VideoCapture` | `_release_resources()` | `_released` flag — idempotent |
| Upload temp file | `_release_resources()` | Only if `_upload_path` is not None |
| Partial upload (write error) | Upload route handler `finally` | Always runs |
| Probe VideoCapture | Upload route handler `finally` | Wraps probe open/check |
| Background thread | `_release_resources()` | Skips join if caller IS the thread |
| `_packet` slot | `_release_resources()` | Cleared under `_packet_cond`; `notify_all()` unblocks stream consumers |
| Service state (`_state`) | **Callers only** | `_release_resources()` never sets `_state` |

### Caller responsibility for state after `_release_resources()`

| Caller | State before call | State after call |
|---|---|---|
| `start_webcam()` — open failed | STARTING | caller sets ERROR |
| `start_file()` — open failed | STARTING | caller sets ERROR |
| `_run_capture_webcam()` — clean stop | STOPPING (set by thread) | caller sets IDLE |
| `_run_capture_webcam()` — error | caller sets ERROR | caller returns |
| `_run_capture_file()` — EOF | caller sets STOPPING | caller sets IDLE |
| `stop()` — any state | STOPPING | caller sets IDLE |

---

## Testing Plan

### Automated (pytest)

Run from `backend/`:
```
pytest tests/ -v
```

No webcam or video file required.

| Test | File | What it verifies |
|---|---|---|
| `test_initial_state_is_idle` | service | Clean service starts IDLE |
| `test_start_webcam_transitions_to_running` | service | IDLE → RUNNING |
| `test_stop_from_running_transitions_to_idle` | service | Normal stop |
| `test_stop_from_idle_is_idempotent` | service | No-op on IDLE |
| `test_stop_from_error_transitions_to_idle` | service | ERROR recovery |
| `test_concurrent_start_only_one_wins` | service | Atomic reservation race |
| `test_failed_open_transitions_to_error` | service | isOpened=False → ERROR |
| `test_file_eof_transitions_to_idle` | service | EOF → IDLE, not ERROR |
| `test_frame_packet_seq_increments` | service | Seq advances |
| `test_cleanup_from_capture_thread_no_self_join` | service | No deadlock on error |
| `test_release_resources_idempotent` | service | cap.release() called once |
| `test_wait_for_packet_returns_none_on_timeout` | service | Timeout path |
| `test_stream_does_not_deliver_same_seq_twice` | service | No duplicate seq |
| `test_health_endpoint` | api | Baseline smoke |
| `test_start_webcam_returns_200_when_idle` | api | 200 on clean start |
| `test_start_webcam_returns_409_when_running` | api | 409 on duplicate |
| `test_stop_returns_status` | api | Stop schema |
| `test_stop_from_error_returns_idle` | api | ERROR → IDLE via stop |
| `test_status_endpoint_returns_valid_schema` | api | Schema |
| `test_upload_rejects_wrong_mime` | api | MIME + extension guard |
| `test_upload_rejects_when_running` | api | 409 + no file written |
| `test_stream_returns_503_when_not_running` | api | 503 when idle |
| `test_partial_upload_is_always_deleted` | api | No orphan files |
| `test_probe_capture_is_always_released` | api | probe.release() always called |

### Manual (browser + Swagger)

1. `GET /api/video/stream` while IDLE → 503.
2. `POST /api/video/webcam/start` → `{"state":"RUNNING"}`.
3. Open `/api/video/stream` in browser tab → live MJPEG feed appears.
4. Poll `GET /api/video/status` several times → `<img>` src in preview does not
   change between polls.
5. `POST /api/video/stop` → `{"state":"IDLE"}`; preview shows placeholder.
6. `POST /api/video/stop` again → 200, no error.
7. Upload valid MP4 → feed starts; FPS throttle visible in log.
8. Upload file > 200 MB → 422; no file in `UPLOAD_DIR`.
9. Upload `.txt` file → 422.
10. Start webcam, then POST upload → 409 before any file write.
11. Disconnect webcam while RUNNING → after `WEBCAM_MAX_CONSECUTIVE_FAILURES`
    failures, state becomes ERROR.
12. `POST /api/video/stop` from ERROR → state IDLE.
13. Inspect browser devtools: stream response headers include
    `Cache-Control: no-store`.

---

## Technical Risks

| Risk | Severity | Mitigation in Plan |
|---|---|---|
| `run_in_executor` pool exhaustion under many concurrent stream clients | Medium | Acceptable for single-user prototype; document limit |
| OpenCV not finding webcam at index 0 on Windows | Medium | Document fallback to index 1 or DirectShow backend; expose as future config |
| Thread join timeout exceeded with stuck capture | Low | Thread is `daemon=True`; 3 s is generous; process exit cleans up |
| File upload not cleaned on SIGKILL | Low | Acceptable at prototype; lifespan shutdown covers normal exit |
| MIME type spoofing (browser sends wrong content_type) | Low | Extension + content_type + OpenCV probe: three independent checks |
| `FakeCapture` seq race in tests | Low | Tests acquire `wait_for_packet` or stop + join before asserting seq |
| Webcam `CAP_PROP_FPS` returning wrong value | Low | Only used for file playback; webcam blocks on read naturally |
