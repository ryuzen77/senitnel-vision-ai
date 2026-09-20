"""
Integration tests for the Video API endpoints.

Uses FastAPI TestClient with monkeypatched VideoService singleton.
No physical webcam or video file required.
"""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.video import VideoState
import app.services.video_service as svc_module
from tests.conftest import FakeCapture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_state(state: VideoState, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if svc_module.video_service.get_status().state == state:
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_service(monkeypatch: pytest.MonkeyPatch):
    """
    Ensure the global video_service singleton is stopped before and after
    each API test, and that cv2.VideoCapture is patched to FakeCapture.
    """
    created_caps: list[FakeCapture] = []

    def make_fake(source: int | str = 0, **_kw) -> FakeCapture:
        cap = FakeCapture(source, opened=True)
        created_caps.append(cap)
        return cap

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", make_fake)

    svc_module.video_service.stop()
    yield created_caps
    svc_module.video_service.stop()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health baseline
# ---------------------------------------------------------------------------


def test_health_endpoint(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "online"
    assert "sentinel" in body["service"].lower()


# ---------------------------------------------------------------------------
# Webcam start
# ---------------------------------------------------------------------------


def test_start_webcam_returns_200_when_idle(client: TestClient) -> None:
    res = client.post("/api/video/webcam/start")
    assert res.status_code == 200
    assert _wait_state(VideoState.RUNNING)


def test_start_webcam_returns_409_when_running(client: TestClient) -> None:
    client.post("/api/video/webcam/start")
    assert _wait_state(VideoState.RUNNING)
    res = client.post("/api/video/webcam/start")
    assert res.status_code == 409


def test_start_webcam_concurrent_exactly_one_200(client: TestClient) -> None:
    """
    Two concurrent POST /api/video/webcam/start requests must produce exactly
    one HTTP 200 and one HTTP 409.
    """
    results: list[int] = []
    lock = threading.Lock()

    def do_post() -> None:
        # Use a fresh client per thread to avoid shared connection issues
        c = TestClient(app)
        res = c.post("/api/video/webcam/start")
        with lock:
            results.append(res.status_code)

    barrier = threading.Barrier(2)

    def post_with_barrier() -> None:
        barrier.wait()
        do_post()

    t1 = threading.Thread(target=post_with_barrier)
    t2 = threading.Thread(target=post_with_barrier)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert sorted(results) == [200, 409], (
        f"Expected [200, 409], got {sorted(results)}"
    )
    svc_module.video_service.stop()


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


def test_stop_returns_status(client: TestClient) -> None:
    client.post("/api/video/webcam/start")
    assert _wait_state(VideoState.RUNNING)
    res = client.post("/api/video/stop")
    assert res.status_code == 200
    body = res.json()
    assert "state" in body


def test_stop_from_error_returns_idle(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    # Force ERROR by making VideoCapture fail to open
    def make_bad(source=0, **_kw):
        return FakeCapture(source, opened=False)

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", make_bad)
    client.post("/api/video/webcam/start")
    assert _wait_state(VideoState.ERROR)
    res = client.post("/api/video/stop")
    assert res.status_code == 200
    assert res.json()["state"] == VideoState.IDLE


def test_stop_is_idempotent(client: TestClient) -> None:
    r1 = client.post("/api/video/stop")
    r2 = client.post("/api/video/stop")
    assert r1.status_code == 200
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_endpoint_returns_valid_schema(client: TestClient) -> None:
    res = client.get("/api/video/status")
    assert res.status_code == 200
    body = res.json()
    assert "state" in body
    assert body["state"] in [s.value for s in VideoState]


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------


def test_upload_rejects_wrong_mime(client: TestClient) -> None:
    fake_file = io.BytesIO(b"not a video")
    res = client.post(
        "/api/video/upload",
        files={"file": ("document.txt", fake_file, "text/plain")},
    )
    assert res.status_code == 422
    # Service must be rolled back to IDLE
    assert svc_module.video_service.get_status().state == VideoState.IDLE


def test_upload_rejects_wrong_extension(client: TestClient) -> None:
    fake_file = io.BytesIO(b"fake content")
    res = client.post(
        "/api/video/upload",
        files={"file": ("clip.avi", fake_file, "video/mp4")},
    )
    assert res.status_code == 422
    assert svc_module.video_service.get_status().state == VideoState.IDLE


def test_upload_rejects_when_running(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    """Upload attempt while RUNNING must return 409 without writing any file."""
    client.post("/api/video/webcam/start")
    assert _wait_state(VideoState.RUNNING)

    upload_dir = svc_module.UPLOAD_DIR
    files_before = set(upload_dir.iterdir()) if upload_dir.exists() else set()

    fake_file = io.BytesIO(b"dummy")
    res = client.post(
        "/api/video/upload",
        files={"file": ("clip.mp4", fake_file, "video/mp4")},
    )
    assert res.status_code == 409

    files_after = set(upload_dir.iterdir()) if upload_dir.exists() else set()
    new_files = files_after - files_before
    assert len(new_files) == 0, f"Unexpected files created: {new_files}"


# ---------------------------------------------------------------------------
# Stream
# ---------------------------------------------------------------------------


def test_stream_returns_503_when_not_running(client: TestClient) -> None:
    res = client.get("/api/video/stream")
    assert res.status_code == 503


@pytest.mark.anyio
async def test_stream_uses_mjpeg_media_type() -> None:
    """
    Call stream_video() directly and inspect the StreamingResponse object.
    Does NOT consume the body iterator — avoids hanging on the infinite stream.
    """
    from app.api.video import stream_video

    # Put the singleton into RUNNING state using the fake cap already installed
    # by the autouse reset_service fixture (which patches cv2.VideoCapture).
    svc_module.video_service.stop()
    svc_module.video_service.start_webcam()
    assert _wait_state(VideoState.RUNNING), "Service did not reach RUNNING"

    try:
        response = await stream_video()
        assert response.media_type == "multipart/x-mixed-replace; boundary=frame"
    finally:
        svc_module.video_service.stop()


@pytest.mark.anyio
async def test_stream_has_cache_control_no_store() -> None:
    """
    Call stream_video() directly and inspect Cache-Control header.
    Does NOT consume the body iterator.
    """
    from app.api.video import stream_video

    svc_module.video_service.stop()
    svc_module.video_service.start_webcam()
    assert _wait_state(VideoState.RUNNING), "Service did not reach RUNNING"

    try:
        response = await stream_video()
        cc = response.headers.get("cache-control", "")
        assert "no-store" in cc, f"Missing no-store in Cache-Control: {cc}"
    finally:
        svc_module.video_service.stop()


# ---------------------------------------------------------------------------
# Partial upload cleanup (Fix 4)
# ---------------------------------------------------------------------------


def test_partial_upload_is_always_deleted(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    """
    When upload fails (e.g. MIME validation), no orphan file must remain.
    """
    upload_dir = svc_module.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    files_before = set(upload_dir.iterdir())

    # Send a text file — MIME validation fails before any disk write.
    fake_file = io.BytesIO(b"garbage" * 100)
    client.post(
        "/api/video/upload",
        files={"file": ("junk.txt", fake_file, "text/plain")},
    )

    files_after = set(upload_dir.iterdir())
    new_files = files_after - files_before
    assert len(new_files) == 0, f"Orphan files found: {new_files}"


def test_oversized_upload_after_chunks_is_deleted(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    """
    File exceeding the limit after partial write must be deleted and state IDLE.
    Uses a lowered limit so we don't allocate a real 200 MB array.
    """
    import app.api.video as video_api_mod

    upload_dir = svc_module.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    files_before = set(upload_dir.iterdir())

    # Lower the limit for this test to 512 bytes
    original_limit = video_api_mod._MAX_UPLOAD_BYTES
    monkeypatch.setattr(video_api_mod, "_MAX_UPLOAD_BYTES", 512)

    # Also lower chunk size so we write at least one chunk before hitting limit
    monkeypatch.setattr(video_api_mod, "_CHUNK_SIZE", 256)

    # Monkeypatch probe so file writing triggers the size check
    # (the probe is never reached because the write fails first)

    # Create content > 512 bytes but in two chunks of 256
    content = b"X" * 768  # 768 bytes > 512 limit
    fake_file = io.BytesIO(content)

    res = client.post(
        "/api/video/upload",
        files={"file": ("clip.mp4", fake_file, "video/mp4")},
    )

    monkeypatch.setattr(video_api_mod, "_MAX_UPLOAD_BYTES", original_limit)

    assert res.status_code == 422
    assert "200 MB" in res.json()["detail"]

    files_after = set(upload_dir.iterdir())
    new_files = files_after - files_before
    assert len(new_files) == 0, f"Orphan partial file found: {new_files}"
    assert svc_module.video_service.get_status().state == VideoState.IDLE


def test_write_failure_after_partial_write_is_cleaned(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    tmp_path: Path,
) -> None:
    """
    A write error after a partial write must delete the partial file,
    return HTTP 500, and leave the service IDLE.

    The route uses Path.open("wb"), not builtins.open, so we patch
    pathlib.Path.open.  The FailingFile wrapper writes a small prefix
    on the very first write() call and then raises OSError — guaranteeing
    a partial file exists and an exception is raised even when the entire
    upload arrives in a single chunk.
    """
    import pathlib
    import app.api.video as video_api_mod

    upload_dir = svc_module.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    files_before = set(upload_dir.iterdir())

    original_path_open = pathlib.Path.open

    class FailingFile:
        """
        On the first write() call: write a small prefix to the real file
        (so a partial file genuinely exists on disk), then raise OSError.
        This is deterministic regardless of how many chunks the route sends.
        """
        def __init__(self, real_fh):
            self._fh = real_fh

        def write(self, data: bytes) -> int:
            # Write just 4 bytes so a real partial file exists
            self._fh.write(data[:4])
            self._fh.flush()
            raise OSError("Simulated partial write failure")

        def close(self):
            try:
                self._fh.close()
            except Exception:
                pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            try:
                self._fh.__exit__(exc_type, exc_val, exc_tb)
            except Exception:
                pass
            return False  # do not suppress the exception

    def patched_path_open(self, mode="r", *args, **kwargs):
        # Only intercept write opens targeting the upload directory
        if "w" in str(mode) and str(upload_dir) in str(self):
            real_fh = original_path_open(self, mode, *args, **kwargs)
            return FailingFile(real_fh)
        return original_path_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", patched_path_open)

    content = b"A" * 64
    fake_file = io.BytesIO(content)

    res = client.post(
        "/api/video/upload",
        files={"file": ("clip.mp4", fake_file, "video/mp4")},
    )

    assert res.status_code == 500, (
        f"Expected 500 for write failure, got {res.status_code}"
    )
    # No internal path must appear in the response body
    assert str(upload_dir) not in res.text, "Filesystem path leaked in response"

    files_after = set(upload_dir.iterdir())
    new_files = files_after - files_before
    assert len(new_files) == 0, f"Orphan partial file found: {new_files}"
    assert svc_module.video_service.get_status().state == VideoState.IDLE


def test_probe_constructor_exception_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    tmp_path: Path,
) -> None:
    """
    If cv2.VideoCapture raises in the probe step, the file is deleted and state IDLE.
    """
    upload_dir = svc_module.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    files_before = set(upload_dir.iterdir())

    call_count = [0]

    def probe_raises(source=0, **_kw):
        call_count[0] += 1
        if isinstance(source, str):
            raise RuntimeError("Probe constructor exploded")
        return FakeCapture(source, opened=True)

    # Patch both the service and the api module cv2
    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", probe_raises)
    monkeypatch.setattr("app.api.video.cv2.VideoCapture", probe_raises)

    fake_file = io.BytesIO(b"\x00" * 128)
    res = client.post(
        "/api/video/upload",
        files={"file": ("clip.mp4", fake_file, "video/mp4")},
    )

    assert res.status_code == 500

    files_after = set(upload_dir.iterdir())
    new_files = files_after - files_before
    assert len(new_files) == 0, f"Orphan file found after probe constructor exception: {new_files}"
    assert svc_module.video_service.get_status().state == VideoState.IDLE


def test_probe_is_opened_exception_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    tmp_path: Path,
) -> None:
    """
    If probe.isOpened() raises, the file is deleted and state is IDLE.
    """
    upload_dir = svc_module.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    files_before = set(upload_dir.iterdir())

    released_count = [0]

    class RaisingIsOpenedCap:
        def __init__(self, source=0):
            pass

        def isOpened(self):
            raise RuntimeError("isOpened exploded")

        def release(self):
            released_count[0] += 1

    def make_raising(source=0, **_kw):
        if isinstance(source, str):
            return RaisingIsOpenedCap(source)
        return FakeCapture(source, opened=True)

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", make_raising)
    monkeypatch.setattr("app.api.video.cv2.VideoCapture", make_raising)

    fake_file = io.BytesIO(b"\x00" * 128)
    res = client.post(
        "/api/video/upload",
        files={"file": ("clip.mp4", fake_file, "video/mp4")},
    )

    assert res.status_code == 500

    files_after = set(upload_dir.iterdir())
    new_files = files_after - files_before
    assert len(new_files) == 0, f"Orphan file found after probe isOpened exception: {new_files}"
    assert svc_module.video_service.get_status().state == VideoState.IDLE
    # Probe was released despite exception
    assert released_count[0] >= 1, "Probe was not released after isOpened() exception"


def test_probe_returns_false_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    tmp_path: Path,
) -> None:
    """
    When probe.isOpened() returns False, file is deleted and state is IDLE.
    """
    upload_dir = svc_module.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    files_before = set(upload_dir.iterdir())

    released_probe_count = [0]

    class ClosedProbeCap:
        def __init__(self, source=0):
            pass

        def isOpened(self) -> bool:
            return False

        def release(self):
            released_probe_count[0] += 1

    def make_closed_probe(source=0, **_kw):
        if isinstance(source, str):
            return ClosedProbeCap(source)
        return FakeCapture(source, opened=True)

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", make_closed_probe)
    monkeypatch.setattr("app.api.video.cv2.VideoCapture", make_closed_probe)

    fake_file = io.BytesIO(b"\x00" * 128)
    res = client.post(
        "/api/video/upload",
        files={"file": ("clip.mp4", fake_file, "video/mp4")},
    )

    assert res.status_code == 422
    assert "valid video" in res.json()["detail"]

    files_after = set(upload_dir.iterdir())
    new_files = files_after - files_before
    assert len(new_files) == 0, f"Orphan file found after probe returns False: {new_files}"
    assert svc_module.video_service.get_status().state == VideoState.IDLE
    assert released_probe_count[0] >= 1, "Probe was not released when isOpened() returned False"


# ---------------------------------------------------------------------------
# Probe VideoCapture always released
# ---------------------------------------------------------------------------


def test_probe_capture_is_always_released(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    tmp_path: Path,
) -> None:
    """
    Every cv2.VideoCapture instance created during an upload (probe) must
    have release() called, even when the probe fails.
    """
    all_caps: list[FakeCapture] = []

    # The probe cap will report isOpened() = False to trigger the failure path.
    call_count = [0]

    def tracking_factory(source=0, **_kw):
        cap = FakeCapture(source, opened=(call_count[0] == 0))  # first open (service start) ok
        # For the probe call (source is a str path), force False
        if isinstance(source, str):
            cap = FakeCapture(source, opened=False)
        all_caps.append(cap)
        call_count[0] += 1
        return cap

    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", tracking_factory)
    # Also patch the cv2 used directly in the upload route
    monkeypatch.setattr("app.api.video.cv2.VideoCapture", tracking_factory)

    # Write a small "video" file to tmp_path so the write loop succeeds
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"\x00" * 1024)

    with fake_video.open("rb") as fh:
        client.post(
            "/api/video/upload",
            files={"file": ("fake.mp4", fh, "video/mp4")},
        )

    probe_caps = [c for c in all_caps if isinstance(c._source, str)]
    for cap in probe_caps:
        assert cap.release_call_count >= 1, "Probe VideoCapture was not released"


# ---------------------------------------------------------------------------
# Stop/Upload race (Fix 5)
# ---------------------------------------------------------------------------


def test_stop_during_upload_does_not_revive_service(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    tmp_path: Path,
) -> None:
    """
    If stop() is called between claim_start() and start_file(), the upload
    must not revive the service to RUNNING.  Final state must be IDLE.
    """
    # We'll simulate the race by intercepting start_file() and calling stop()
    # just before it executes.

    original_start_file = svc_module.VideoService.start_file
    stop_called = [False]

    def racing_start_file(self, path: Path, token: str) -> None:
        if not stop_called[0]:
            stop_called[0] = True
            # Stop the service between claim and start_file
            self.stop()
        # Now call the real start_file — should detect token mismatch
        original_start_file(self, path, token)

    monkeypatch.setattr(svc_module.VideoService, "start_file", racing_start_file)
    monkeypatch.setattr("app.api.video.cv2.VideoCapture", lambda source=0, **_: FakeCapture(source, opened=True))

    fake_file = io.BytesIO(b"\x00" * 128)
    res = client.post(
        "/api/video/upload",
        files={"file": ("clip.mp4", fake_file, "video/mp4")},
    )

    # The response should be 409 (race detected) or 422/500
    assert res.status_code in (409, 422, 500)

    # Final state must be IDLE — not RUNNING or STARTING
    state = svc_module.video_service.get_status().state
    assert state == VideoState.IDLE, f"Expected IDLE after stop race, got {state}"

    # No orphan file in upload dir
    upload_dir = svc_module.UPLOAD_DIR
    if upload_dir.exists():
        uploads = [f for f in upload_dir.iterdir() if f.suffix in (".mp4", ".webm")]
        assert len(uploads) == 0, f"Orphan files after stop race: {uploads}"
