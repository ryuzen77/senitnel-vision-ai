"""P1c: /api/video/sources over HTTP, capture-slot ownership, recovery, status identity,
legacy-route refusal and the runtime/lifespan composition (plan §18).

FastAPI TestClient with the sources context overridden: the real PreparedSourceStore with
a fake filesystem and fake clock, the P1a fake VideoService side, a fake decoder with
gates. No server, real video, decoder, model, provider, webcam or speaker. Gates are
threading.Events with timeouts; polling only waits for a state a gate makes inevitable.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import sources
from app.api import video as video_api
from app.api.sources import SourcesContext, get_sources_context
from app.models.private_parking import parse_private_parking_config
from app.models.video import VideoState, VideoStatusResponse
from app.services import video_decoder
from app.services.prepared_source import CaptureState, PreparedSourceStore
from tests.test_prepared_source import FakeClock, FakeFiles, FakeVideo

WAIT = 5.0
FRAME = np.zeros((48, 64, 3), dtype=np.uint8)
SECRET = "secret-dir-91f3"
TEMPLATE = parse_private_parking_config(json.dumps({
    "line": [0.2, 0.5, 0.8, 0.5], "inside_point": [0.5, 0.8],
    "zones": [{"name": "z1", "rect": [0.1, 0.1, 0.5, 0.5]}], "thresholds": {"approach_observations": 4}}))


def wait_until(predicate, timeout: float = WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return predicate()


class Gate:
    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()

    def pass_through(self) -> None:
        self.reached.set()
        assert self.release.wait(WAIT), "gate never released"


class FakeCapture:
    def __init__(self, decoder: "Decoder", frames, release_failures: int) -> None:
        self.decoder = decoder
        self.frames = list(frames)
        self.release_failures = release_failures
        self.released = 0
        self.release_attempts = 0

    def isOpened(self) -> bool:  # noqa: N802
        return True

    def get(self, _prop) -> float:
        return 25.0

    def read(self):
        self.decoder.reads += 1
        if self.decoder.read_gate is not None:
            self.decoder.read_gate.pass_through()
        return (True, self.frames.pop(0)) if self.frames else (False, None)

    def release(self) -> None:
        self.release_attempts += 1
        if self.release_failures > 0:
            self.release_failures -= 1
            raise OSError("locked")
        self.released += 1


class Decoder:
    """The one decoder, faked: counts constructor, probe and decode calls."""

    def __init__(self) -> None:
        self.frames = [FRAME]
        self.release_failures = 0
        self.create_error: Exception | None = None
        self.create_gate: Gate | None = None
        self.probe_gate: Gate | None = None
        self.read_gate: Gate | None = None
        self.captures: list[FakeCapture] = []
        self.created = 0
        self.probes = 0
        self.reads = 0

    def create_capture(self, _path):
        self.created += 1
        if self.create_gate is not None:
            self.create_gate.pass_through()
        if self.create_error is not None:
            raise self.create_error
        capture = FakeCapture(self, self.frames, self.release_failures)
        self.captures.append(capture)
        return capture

    def probe(self, capture):
        self.probes += 1
        if self.probe_gate is not None:
            self.probe_gate.pass_through()
        return video_decoder.probe(capture)

    read_first_frame = staticmethod(video_decoder.read_first_frame)


class FakeStatus:
    def __init__(self) -> None:
        self.status = VideoStatusResponse(state=VideoState.IDLE)

    def get_status(self) -> VideoStatusResponse:
        return self.status


class Api:
    def __init__(self, tmp_path, *, mode: str = "private", template=TEMPLATE, files: FakeFiles | None = None,
                 **store_options) -> None:
        self.clock = FakeClock()
        self.files = files or FakeFiles()
        self.video = FakeVideo()
        self.status = FakeStatus()
        self.directory = tmp_path / SECRET
        self.directory.mkdir(exist_ok=True)
        self.store = (PreparedSourceStore(self.directory, video=self.video, files=self.files, clock=self.clock,
                                          shutdown_budget_seconds=0.5, **store_options)
                      if mode == "private" else None)
        self.decoder = Decoder()
        self.ctx = SourcesContext(mode=mode, store=self.store, template=template, evaluation_enabled=False,
                                  video=self.status, decoder=self.decoder)
        self.app = FastAPI()
        self.app.include_router(sources.router, prefix="/api")
        self.app.dependency_overrides[get_sources_context] = lambda: self.ctx
        self.client = TestClient(self.app)
        self.threads: list[threading.Thread] = []

    # ---------------------------------------------------------------- helpers
    def create(self, operation_id: str | None = None):
        return self.client.post("/api/video/sources", json={"operation_id": operation_id or str(uuid.uuid4())})

    def upload(self, source_id, generation, *, name="clip.mp4", data=b"0" * 64, client=None):
        return (client or self.client).put(f"/api/video/sources/{source_id}/file?generation={generation}",
                                           files={"file": (name, data, "video/mp4")})

    def prepared(self):
        created = self.create().json()
        answer = self.upload(created["source_id"], created["generation"])
        assert answer.status_code == 200, answer.text
        return created["source_id"], created["generation"]

    def config_body(self, generation, expected=None, **overrides):
        body = {"generation": generation, "expected_revision": expected, "frame_width": 64, "frame_height": 48,
                "line": {"p1": [0.2, 0.5], "p2": [0.8, 0.5]}, "inside": "right",
                "zones": [{"name": "z1", "rect": [0.1, 0.1, 0.5, 0.5]}]}
        body.update(overrides)
        return body

    def configured(self):
        source_id, generation = self.prepared()
        answer = self.client.put(f"/api/video/sources/{source_id}/configuration", json=self.config_body(generation))
        assert answer.status_code == 200, answer.text
        return source_id, generation, answer.json()["revision"]

    def play(self, source_id, generation, revision, attempt, operation_id=None):
        return self.client.post(f"/api/video/sources/{source_id}/play", json={
            "generation": generation, "config_revision": revision, "attempt": attempt,
            "operation_id": operation_id or str(uuid.uuid4())})

    def current(self):
        return self.client.get("/api/video/sources/current")

    def background(self, function, *args, **kwargs):
        box: dict = {}

        def run() -> None:
            try:
                box["result"] = function(*args, **kwargs)
            except BaseException as error:  # noqa: BLE001
                box["error"] = error

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.threads.append(thread)
        return thread, box


@pytest.fixture
def api(tmp_path):
    created: list[Api] = []

    def make(**kwargs) -> Api:
        instance = Api(tmp_path, **kwargs)
        created.append(instance)
        return instance

    yield make
    for instance in created:
        for gate in (instance.decoder.create_gate, instance.decoder.probe_gate, instance.decoder.read_gate):
            if gate is not None:
                gate.release.set()
        for thread in instance.threads:
            thread.join(WAIT)
            assert not thread.is_alive()
        if instance.store is not None:
            instance.store.close()


def finish(thread, box):
    thread.join(WAIT)
    assert not thread.is_alive()
    return box["result"]


# =========================================================================== modes and capabilities


@pytest.mark.parametrize("mode", ["private", "legacy", "disabled"])
def test_capabilities_in_every_mode(api, mode):
    instance = api(mode=mode)
    body = instance.client.get("/api/video/sources/capabilities").json()
    assert body["mode"] == mode and body["source_kinds"] == ["file"]
    assert body["preparation_available"] is (mode == "private")
    assert body["limits"]["max_configuration_bytes"] == 4096 and body["limits"]["max_zones"] == 8


@pytest.mark.parametrize("mode", ["legacy", "disabled"])
def test_mutations_outside_private_mode(api, mode):
    instance = api(mode=mode)
    answer = instance.create()
    assert answer.status_code == 409 and answer.json() == {"category": "private_mode_inactive"}
    assert instance.current().status_code == 409


# =========================================================================== the flow over HTTP


def test_create_upload_preview_template_configure_play_lookup_and_delete(api):
    instance = api()
    operation_id = str(uuid.uuid4())
    created = instance.create(operation_id)
    assert created.status_code == 201 and created.json()["state"] == "AWAITING_FILE"
    assert instance.create(operation_id).json()["source_id"] == created.json()["source_id"]   # same op
    source_id, generation = created.json()["source_id"], created.json()["generation"]
    uploaded = instance.upload(source_id, generation)
    assert uploaded.status_code == 200
    assert uploaded.json() == {"state": "PREPARED", "frame_width": 64, "frame_height": 48, "preview_revision": 1}
    assert instance.decoder.captures[-1].released == 1                      # the probe capture released
    preview = instance.client.get(f"/api/video/sources/{source_id}/preview?generation={generation}")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    assert preview.content[:2] == b"\xff\xd8" and instance.decoder.captures[-1].released == 1
    template = instance.client.get(f"/api/video/sources/{source_id}/template?generation={generation}").json()
    assert template["confirmed"] is False and template["inside"] == "right"
    assert (template["frame_width"], template["frame_height"]) == (64, 48)
    saved = instance.client.put(f"/api/video/sources/{source_id}/configuration",
                                json=instance.config_body(generation))
    assert saved.status_code == 200 and saved.json()["revision"] == 1 and saved.json()["ready"] is True
    assert saved.json()["canonical"]["inside"] == "right"
    current = instance.current().json()
    assert current["config"] == {"revision": 1, "ready": True} and current["template_available"] is True
    assert current["analysis"]["stop_required"] is False and current["cleanup"]["pending"] is False
    op = str(uuid.uuid4())
    played = instance.play(source_id, generation, 1, 1, op)
    assert played.status_code == 200 and played.json()["outcome"] == "STARTED"
    assert played.json()["session"] == {"phase": "RUNNING", "end_reason": None, "cleanup_ok": None}
    assert instance.play(source_id, generation, 1, 1, op).json() == played.json()          # replay identical
    assert instance.client.get(f"/api/video/sources/{source_id}/play/1").json() == played.json()
    instance.video.sessions[0].finish("stop", True)
    deleted = instance.client.delete(f"/api/video/sources/{source_id}?generation={generation}")
    assert deleted.status_code in (202, 204)
    assert instance.client.get(f"/api/video/sources/{source_id}/play/1").status_code == 410


def test_template_absent_answers_204(api):
    instance = api(template=None)
    source_id, generation = instance.prepared()
    answer = instance.client.get(f"/api/video/sources/{source_id}/template?generation={generation}")
    assert answer.status_code == 204
    assert instance.client.get("/api/video/sources/capabilities").json()["template_available"] is False


def test_error_bodies_carry_only_categories(api, caplog):
    instance = api()
    with caplog.at_level(logging.DEBUG):
        bad = instance.client.post("/api/video/sources", json={"operation_id": "not-a-uuid " + SECRET})
        assert bad.status_code == 422 and bad.json() == {"category": "invalid_operation_id"}
        source_id, generation = instance.prepared()
        geometry = instance.client.put(f"/api/video/sources/{source_id}/configuration", json=instance.config_body(
            generation, line={"p1": [0.123456, 0.5], "p2": [7.5, 0.5]}))
        assert geometry.status_code == 422 and geometry.json() == {"category": "invalid_geometry", "field": "line.p2"}
        query = instance.client.get(f"/api/video/sources/{source_id}/preview?generation=x{SECRET}")
        assert query.status_code == 422 and query.json() == {"category": "invalid_request", "field": "generation"}
    app_logs = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("app."))
    text = app_logs + bad.text + geometry.text + query.text           # the test client logs its own URL
    assert SECRET not in text and "0.123456" not in text and "7.5" not in text


def test_an_unsupported_upload_type_is_refused_before_writing(api):
    instance = api()
    created = instance.create().json()
    answer = instance.upload(created["source_id"], created["generation"], name=SECRET + ".avi")
    assert answer.status_code == 422 and answer.json() == {"category": "unsupported_type"}
    assert SECRET not in answer.text and instance.decoder.created == 0
    assert instance.current().json()["state"] == "AWAITING_FILE"


# =========================================================================== Play contract (§18.2 item 6, 9)


def test_refusals_before_the_cas_never_use_an_attempt(api):
    instance = api()
    source_id, generation = instance.prepared()
    assert instance.play(source_id, generation, 0, 1).json() == {"category": "config_not_ready"}
    revision = instance.client.put(f"/api/video/sources/{source_id}/configuration",
                                   json=instance.config_body(generation)).json()["revision"]
    stale = instance.play(source_id, generation, revision + 1, 1)
    assert stale.status_code == 409 and stale.json() == {"category": "revision_stale"}
    ahead = instance.play(source_id, generation, revision, 5)
    assert ahead.status_code == 409 and ahead.json() == {"category": "attempt_conflict"}
    assert instance.current().json()["next_attempt"] == 1 and instance.video.starts == 0


def test_a_refusal_after_the_cas_is_200_refused_and_uses_the_attempt(api):
    instance = api()
    source_id, generation, revision = instance.configured()
    instance.video.refuse_before_consume = "video_busy"
    op = str(uuid.uuid4())
    answer = instance.play(source_id, generation, revision, 1, op)
    assert answer.status_code == 200
    assert (answer.json()["outcome"], answer.json()["reason"]) == ("REFUSED", "video_busy")
    assert instance.current().json()["next_attempt"] == 2
    assert instance.play(source_id, generation, revision, 1, op).json() == answer.json()   # replay
    assert instance.play(source_id, generation, revision, 1).json() == {"category": "attempt_superseded"}


@pytest.mark.parametrize("cleanup_ok", [True, False, None])
def test_frame_size_changed_refuses_before_the_cas(api, cleanup_ok):
    instance = api()
    source_id, generation, revision = instance.configured()
    assert instance.play(source_id, generation, revision, 1).json()["outcome"] == "STARTED"
    before_hook = instance.play(source_id, generation, revision, 2)                 # hook not yet applied
    assert before_hook.status_code == 409 and before_hook.json() == {"category": "play_in_progress"}
    instance.video.sessions[0].finish("frame_size_changed", cleanup_ok)
    current = instance.current().json()
    assert current["geometry_unstable"] is True and current["config"]["ready"] is False
    assert current["last_attempt"]["session"]["end_reason"] == "frame_size_changed"
    refused = instance.play(source_id, generation, revision, 2)
    assert refused.status_code == 409 and refused.json() == {"category": "source_geometry_unstable"}
    resave = instance.client.put(f"/api/video/sources/{source_id}/configuration",
                                 json=instance.config_body(generation, expected=revision))
    assert resave.json() == {"category": "source_geometry_unstable"}
    assert instance.current().json()["next_attempt"] == 2 and instance.video.starts == 1


def test_analysis_is_informational_only(api):
    instance = api()
    source_id, generation, revision = instance.configured()
    instance.status.status = VideoStatusResponse(state=VideoState.ERROR, session_ref="ref-x", session_id="sid-x")
    current = instance.current().json()
    assert current["analysis"] == {"stop_required": True, "state": "ERROR", "session_ref": "ref-x",
                                   "session_id": "sid-x"}
    assert current["config"]["ready"] is True                                       # never a readiness input


# =========================================================================== configuration envelope (decision 5)


def test_configuration_envelope_limit(api):
    instance = api()
    source_id, generation = instance.prepared()
    body = instance.config_body(generation)
    base = json.dumps(body, separators=(",", ":"))
    padded_4096 = base[:-1] + " " * (4096 - len(base)) + "}"
    assert len(padded_4096.encode()) == 4096
    ok = instance.client.put(f"/api/video/sources/{source_id}/configuration", content=padded_4096,
                             headers={"content-type": "application/json"})
    assert ok.status_code == 200
    expires = instance.current().json()["expires_in_seconds"]
    instance.clock.advance(10.0)
    too_big = padded_4096[:-1] + " }"
    refused = instance.client.put(f"/api/video/sources/{source_id}/configuration", content=too_big,
                                  headers={"content-type": "application/json"})
    assert refused.status_code == 413 and refused.json() == {"category": "configuration_too_large"}
    assert instance.current().json()["config"]["revision"] == 1                     # nothing changed
    assert instance.current().json()["expires_in_seconds"] == expires - 10.0        # TTL not reset

    def chunks():                                                                   # no length header
        yield too_big.encode()

    streamed = instance.client.put(f"/api/video/sources/{source_id}/configuration", content=chunks(),
                                   headers={"content-type": "application/json"})
    assert streamed.status_code == 413


# =========================================================================== capture slot gates (acceptance 2, 3, 11)


def _upload_in_background(instance, source_id, generation):
    client = TestClient(instance.app)
    return instance.background(instance.upload, source_id, generation, client=client)


@pytest.mark.parametrize("event", ["delete", "expiry", "shutdown"])
def test_event_while_the_probe_constructor_runs(api, event):
    instance = api()
    created = instance.create().json()
    source_id, generation = created["source_id"], created["generation"]
    instance.decoder.create_gate = Gate()
    thread, box = _upload_in_background(instance, source_id, generation)
    assert instance.decoder.create_gate.reached.wait(WAIT)
    second = TestClient(instance.app).get(f"/api/video/sources/{source_id}/preview?generation={generation}")
    assert second.status_code == 409                                            # still WRITING / slot busy
    if event == "delete":
        instance.client.delete(f"/api/video/sources/{source_id}?generation={generation}")
        expected = (409, {"category": "cancelled"})
    elif event == "expiry":
        instance.clock.advance(300.0)
        expected = (409, {"category": "deadline_exceeded"})
    else:
        instance.store.begin_shutdown()
        expected = (503, {"category": "closed"})
    assert instance.files.calls == 0                                            # capture and writer still hold it
    instance.decoder.create_gate.release.set()
    answer = finish(thread, box)
    assert (answer.status_code, answer.json()) == expected
    capture = instance.decoder.captures[0]
    assert instance.decoder.probes == 0 and instance.decoder.reads == 0          # never used after the event
    assert capture.released == 1 and instance.decoder.created == 1
    if event != "shutdown":
        assert len(instance.files.removed) == 1                                 # file after the release
    else:
        instance.store.finish_shutdown()
        assert len(instance.files.removed) == 1 and instance.video.stops == 0


def test_a_constructor_that_raises_frees_the_slot(api):
    instance = api()
    created = instance.create().json()
    instance.decoder.create_error = OSError("cannot open " + SECRET)
    answer = instance.upload(created["source_id"], created["generation"])
    assert answer.status_code == 422 and answer.json() == {"category": "not_decodable"}
    assert instance.decoder.captures == [] and len(instance.files.removed) == 1
    assert instance.create().status_code == 201


@pytest.mark.parametrize("event", ["delete", "expiry"])
def test_event_while_the_probe_runs_waits_for_its_end(api, event):
    instance = api()
    created = instance.create().json()
    source_id, generation = created["source_id"], created["generation"]
    instance.decoder.probe_gate = Gate()
    thread, box = _upload_in_background(instance, source_id, generation)
    assert instance.decoder.probe_gate.reached.wait(WAIT)
    if event == "delete":
        instance.client.delete(f"/api/video/sources/{source_id}?generation={generation}")
    else:
        instance.clock.advance(300.0)
        instance.store.tick()
    capture = instance.decoder.captures[0]
    assert capture.release_attempts == 0                                       # busy: never released under it
    instance.decoder.probe_gate.release.set()
    answer = finish(thread, box)
    assert answer.status_code == 409
    assert capture.released == 1 and len(instance.files.removed) == 1


@pytest.mark.parametrize("event, swept", [("expiry", True), ("expiry", False), ("shutdown", False)])
def test_a_decode_that_outlives_its_operation_never_answers_a_jpeg(api, event, swept):
    instance = api()
    source_id, generation = instance.prepared()
    instance.decoder.read_gate = Gate()
    client = TestClient(instance.app)
    thread, box = instance.background(client.get, f"/api/video/sources/{source_id}/preview?generation={generation}")
    assert instance.decoder.read_gate.reached.wait(WAIT)
    capture = instance.decoder.captures[-1]
    busy = TestClient(instance.app).get(f"/api/video/sources/{source_id}/preview?generation={generation}")
    assert busy.status_code == 409 and busy.json() == {"category": "capture_busy"}
    assert instance.decoder.created == 2                                       # no second constructor call
    if event == "expiry":
        instance.clock.advance(30 * 60)                                        # TTL from the read's own reset
        if swept:
            instance.store.tick()
        expected = (409, {"category": "expired"})
    else:
        instance.store.begin_shutdown()
        expected = (503, {"category": "closed"})
    assert capture.release_attempts == 0 and instance.files.calls == 0         # busy: never released under it
    instance.decoder.read_gate.release.set()
    answer = finish(thread, box)
    assert (answer.status_code, answer.json()) == expected                     # the stale frame is discarded
    assert answer.headers["content-type"] != "image/jpeg"
    assert capture.released == 1                                               # cleanup still done
    if event == "shutdown":
        instance.store.finish_shutdown()
    assert len(instance.files.removed) == 1


def test_adoption_after_a_delete_never_decodes(api):
    instance = api()
    source_id, generation = instance.prepared()
    instance.decoder.create_gate = Gate()
    client = TestClient(instance.app)
    thread, box = instance.background(client.get, f"/api/video/sources/{source_id}/preview?generation={generation}")
    assert instance.decoder.create_gate.reached.wait(WAIT)
    instance.client.delete(f"/api/video/sources/{source_id}?generation={generation}")
    reads = instance.decoder.reads
    instance.decoder.create_gate.release.set()
    answer = finish(thread, box)
    assert answer.status_code in (409, 410) and instance.decoder.reads == reads   # adopted idle, no decode
    assert instance.decoder.captures[-1].released == 1 and len(instance.files.removed) == 1


# =========================================================================== failed release (decision 3) and recovery


def test_a_failed_probe_release_is_not_prepared_and_blocks_new_work(api):
    instance = api()
    created = instance.create().json()
    source_id, generation = created["source_id"], created["generation"]
    instance.decoder.release_failures = 1
    answer = instance.upload(source_id, generation)
    assert answer.status_code == 503
    assert answer.json() == {"category": "cleanup_pending", "source_id": source_id, "generation": generation}
    current = instance.current().json()
    assert current["cleanup"]["capture"] == "RELEASE_PENDING" and current["cleanup"]["pending"] is True
    assert instance.create().status_code == 409 and instance.create().json() == {"category": "cleanup_pending"}
    assert instance.files.calls == 0 and instance.decoder.created == 1
    instance.clock.advance(60.0)
    instance.store.tick()
    assert instance.decoder.captures[0].released == 1 and len(instance.files.removed) == 1
    assert instance.create().status_code == 201                                 # upload again


def test_a_failed_preview_release_refuses_config_and_play_until_it_succeeds(api):
    instance = api()
    source_id, generation, revision = instance.configured()
    instance.decoder.release_failures = 1
    preview = instance.client.get(f"/api/video/sources/{source_id}/preview?generation={generation}")
    assert preview.status_code == 503
    assert preview.json() == {"category": "cleanup_pending", "source_id": source_id, "generation": generation}
    pending = {"category": "cleanup_pending", "source_id": source_id, "generation": generation}
    assert instance.play(source_id, generation, revision, 1).json() == pending
    assert instance.client.put(f"/api/video/sources/{source_id}/configuration",
                               json=instance.config_body(generation, expected=revision)).json() == pending
    again = instance.client.get(f"/api/video/sources/{source_id}/preview?generation={generation}")
    assert again.status_code == 503 and instance.decoder.created == 2
    assert instance.current().json()["next_attempt"] == 1 and instance.video.starts == 0
    instance.clock.advance(60.0)
    instance.store.tick()
    assert instance.play(source_id, generation, revision, 1).json()["outcome"] == "STARTED"


def test_a_stalled_capture_is_recovered_by_one_explicit_attempt(api):
    instance = api()
    source_id, generation = instance.prepared()
    instance.decoder.release_failures = 99
    instance.client.get(f"/api/video/sources/{source_id}/preview?generation={generation}")
    capture = instance.decoder.captures[-1]
    for _ in range(9):
        instance.clock.advance(60.0)
        instance.store.tick()
    assert instance.current().json()["cleanup"]["capture"] == "RELEASE_STALLED"
    assert capture.release_attempts == 10
    instance.clock.advance(3600.0)                                             # the idle TTL also passes
    instance.store.tick()
    assert capture.release_attempts == 10                                      # no automatic attempt
    assert instance.files.calls == 0                                           # the expired file waits for it
    retry = instance.client.post("/api/video/sources/cleanup/retry")
    # pending: the expired file's deletion, waiting for the stalled capture (a file user)
    assert retry.status_code == 202 and retry.json() == {"cleanup": {"pending": 1, "stalled": 1, "attempted": 1}}
    assert capture.release_attempts == 11 and instance.files.calls == 0
    capture.release_failures = 0
    done = instance.client.post("/api/video/sources/cleanup/retry")
    assert done.status_code == 204 and capture.released == 1
    assert len(instance.files.removed) == 1                                    # deleted after the release
    assert SECRET not in retry.text


def test_a_stalled_startup_leftover_is_recovered_without_a_source_id(api, tmp_path):
    files = FakeFiles(leftovers=("old.mp4",))
    files.fail_always = True
    instance = api(files=files)
    for _ in range(9):
        instance.clock.advance(60.0)
        instance.store.tick()
    current = instance.current().json()
    assert current["source_id"] is None and current["state"] == "CLEANUP_STALLED"
    assert instance.create().json() == {"category": "cleanup_pending"}
    files.fail_always = False
    retry = instance.client.post("/api/video/sources/cleanup/retry")
    assert retry.status_code == 204 and [p.name for p in files.removed] == ["old.mp4"]
    assert instance.create().status_code == 201


# =========================================================================== shutdown (decision 6)


def test_the_store_phases_never_stop_video(api):
    instance = api()
    source_id, generation, revision = instance.configured()
    instance.play(source_id, generation, revision, 1)
    instance.store.begin_shutdown()
    assert instance.video.stops == 0 and instance.video.cancelled == []
    instance.video.sessions[0].finish("stop", True)                              # the runtime's single Stop
    instance.store.finish_shutdown()
    assert instance.video.stops == 0 and len(instance.files.removed) == 1


def test_lifespan_has_exactly_one_stop_owner(monkeypatch):
    import app.main as main

    calls: list[str] = []

    class Store:
        def begin_shutdown(self):
            calls.append("begin")

        def finish_shutdown(self):
            calls.append("finish")

    class Worker:
        def start(self):
            calls.append("worker.start")

    monkeypatch.setattr(main, "start_prepared_store", lambda: Store())
    monkeypatch.setattr(main, "evaluation_worker", Worker())
    monkeypatch.setattr(main, "audio_alert_gate", None)
    monkeypatch.setattr(main, "shutdown_services", lambda *a, **k: calls.append("shutdown_services"))
    with TestClient(main.app):
        pass
    assert calls == ["worker.start", "begin", "shutdown_services", "finish"]


# =========================================================================== status identity, factory, legacy routes, runtime


def test_status_identity_is_read_in_one_section():
    from app.services.prepared_source import HandoffTicket
    from app.services.session_config_registry import SessionConfigRegistry
    from app.services.video_service import VideoService
    from tests.test_video_prepared_playback import Capture, Decoder as PlaybackDecoder, Sink, config

    registry = SessionConfigRegistry()
    gate = Gate()
    detector = Sink(registry)
    detector.start_gate = gate
    video = VideoService(detector=detector, session_config_registry=registry,
                         decoder=PlaybackDecoder(Capture([FRAME, "eof"])))
    ticket = HandoffTicket(1)
    thread = threading.Thread(target=lambda: video.start_prepared_file(
        "p.mp4", ticket=ticket, session_config=config(), expected_size=(64, 48), on_finished=lambda *a: None),
        daemon=True)
    thread.start()
    try:
        assert gate.reached.wait(WAIT)                                          # between reservation and sink start
        status = video.get_status()
        assert status.state is VideoState.STARTING
        assert (status.session_ref, status.session_id) == ticket.identity()
    finally:
        gate.release.set()
        thread.join(WAIT)
        video.stop()
    assert video.get_status().session_ref is None and video.get_status().session_id is None


def test_the_composed_runtime_uses_the_default_session_id_factory():
    from app.services import runtime
    factory = runtime.video_service._session_id_factory                       # noqa: SLF001
    first, second = factory(), factory()
    assert first != second and str(uuid.UUID(first)) == first and str(uuid.UUID(second)) == second


@pytest.mark.parametrize("route", ["/api/video/webcam/start", "/api/video/upload"])
def test_private_mode_refuses_the_legacy_start_routes_before_reserving(monkeypatch, route):
    reservations: list = []
    monkeypatch.setattr(video_api, "_parking_mode", lambda: "private")
    monkeypatch.setattr(video_api.video_service, "claim_start", lambda: reservations.append(1))
    monkeypatch.setattr(video_api.video_service, "start_webcam", lambda: reservations.append(1))
    app = FastAPI()
    app.include_router(video_api.router, prefix="/api")
    client = TestClient(app)
    kwargs = {"files": {"file": ("a.mp4", b"x", "video/mp4")}} if route.endswith("upload") else {}
    answer = client.post(route, **kwargs)
    assert answer.status_code == 409 and answer.json() == {"category": "private_flow_required"}
    assert reservations == []


def test_start_prepared_store_only_in_private_mode(monkeypatch, tmp_path):
    import dataclasses
    from app.services import runtime
    monkeypatch.setattr(runtime, "prepared_store", None)
    monkeypatch.setattr(runtime, "parking_wiring", dataclasses.replace(runtime.parking_wiring, mode="legacy"))
    assert runtime.start_prepared_store(tmp_path / "prepared") is None
    monkeypatch.setattr(runtime, "parking_wiring", dataclasses.replace(runtime.parking_wiring, mode="private"))
    (tmp_path / "prepared").mkdir()
    (tmp_path / "prepared" / "leftover.mp4").write_bytes(b"x")
    (tmp_path / "outside.mp4").write_bytes(b"x")
    store = runtime.start_prepared_store(tmp_path / "prepared", sweeper_max_wait_seconds=0.01)
    try:
        assert store is runtime.prepared_store and runtime.start_prepared_store(tmp_path / "x") is store
        assert not (tmp_path / "prepared" / "leftover.mp4").exists()            # startup sweep: its dir only
        assert (tmp_path / "outside.mp4").exists()
    finally:
        store.begin_shutdown()
        store.finish_shutdown()
    # the production directory is inside the git-ignored uploads tree, not UPLOAD_DIR itself
    from app.services.video_service import UPLOAD_DIR
    assert runtime.PREPARED_DIR.parent == UPLOAD_DIR and runtime.PREPARED_DIR != UPLOAD_DIR
    assert {state.value for state in CaptureState} == {"CREATING", "OWNED", "RELEASE_PENDING", "RELEASE_STALLED"}


# =========================================================================== P1c review fixes


def test_a_held_play_does_not_block_a_delete_on_the_same_event_loop(api):
    """One ASGI event loop: Play is held inside the VideoService start (threadpool); a
    DELETE on the same loop must be answered while Play is still held."""
    import asyncio

    import httpx

    instance = api()
    source_id, generation, revision = instance.configured()
    gate = Gate()
    instance.video.before_consume = gate
    safety = threading.Timer(4.0, gate.release.set)                            # never hang the test
    safety.start()
    order: list[str] = []

    async def scenario():
        transport = httpx.ASGITransport(app=instance.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            play = asyncio.create_task(client.post(f"/api/video/sources/{source_id}/play", json={
                "generation": generation, "config_revision": revision, "attempt": 1,
                "operation_id": str(uuid.uuid4())}))
            while not gate.reached.is_set():                                   # Play is inside the start
                await asyncio.sleep(0.002)
            deleted = await asyncio.wait_for(
                client.delete(f"/api/video/sources/{source_id}?generation={generation}"), timeout=3.0)
            order.append("delete" if not gate.release.is_set() else "delete-after-release")
            gate.release.set()
            played = await asyncio.wait_for(play, timeout=WAIT)
            return deleted, played

    try:
        deleted, played = asyncio.run(scenario())
    finally:
        safety.cancel()
        gate.release.set()
    assert order == ["delete"]                                                  # served while Play was held
    assert deleted.status_code in (202, 204)
    assert played.status_code == 200 and played.json()["outcome"] == "CANCELLED"
    assert instance.video.sessions == []                                        # the cancelled ticket never reserved


class HoldingStore:
    """Delegates to the real store; holds a refused Play after the refusal was raised."""

    def __init__(self, real, gate: Gate) -> None:
        self._real, self._gate = real, gate

    def play(self, *args, **kwargs):
        try:
            return self._real.play(*args, **kwargs)
        except Exception:
            self._gate.pass_through()
            raise

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_an_error_answer_keeps_the_identity_of_its_own_source(api):
    instance = api()
    source_a, generation_a, revision_a = instance.configured()
    instance.decoder.release_failures = 1
    assert instance.client.get(f"/api/video/sources/{source_a}/preview?generation={generation_a}").status_code == 503
    gate = Gate()
    instance.ctx.store = HoldingStore(instance.store, gate)
    client = TestClient(instance.app)
    thread, box = instance.background(lambda: client.post(f"/api/video/sources/{source_a}/play", json={
        "generation": generation_a, "config_revision": revision_a, "attempt": 1,
        "operation_id": str(uuid.uuid4())}))
    assert gate.reached.wait(WAIT)                                              # A refused, answer not sent yet
    instance.clock.advance(60.0)
    instance.store.tick()                                                       # A's capture released
    assert instance.store.delete(source_a, generation_a) in ("GONE", "DELETING")
    instance.store.tick()
    created_b = instance.store.create(str(uuid.uuid4()))                        # B exists now
    assert created_b.source_id != source_a
    gate.release.set()
    answer = finish(thread, box)
    assert answer.status_code == 503
    assert answer.json() == {"category": "cleanup_pending", "source_id": source_a, "generation": generation_a}


MARKER = "MARKER-7d2c-secret"


@pytest.mark.parametrize("kind", ["text field", "malformed body", "missing field"])
def test_an_invalid_multipart_upload_echoes_nothing(api, caplog, kind):
    instance = api()
    created = instance.create().json()
    url = f"/api/video/sources/{created['source_id']}/file?generation={created['generation']}"
    with caplog.at_level(logging.DEBUG):
        if kind == "text field":
            answer = instance.client.put(url, data={"file": MARKER})
            expected = {"category": "missing_file"}
        elif kind == "malformed body":
            answer = instance.client.put(url, content=("--" + MARKER + "\r\nnot multipart").encode(),
                                         headers={"content-type": "multipart/form-data; boundary=" + MARKER})
            expected = None
        else:
            answer = instance.client.put(url, files={"other": (MARKER + ".mp4", b"x", "video/mp4")})
            expected = {"category": "missing_file"}
    assert answer.status_code == 422
    if expected is not None:
        assert answer.json() == expected
    else:
        assert answer.json()["category"] in ("invalid_request", "missing_file")
    logs = " ".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    assert MARKER not in answer.text and MARKER not in logs
    assert instance.current().json()["state"] == "AWAITING_FILE" and instance.decoder.created == 0


def _raw_request(app, method: str, path: str, headers: list, body: bytes):
    """Call the ASGI app directly (a client library would not send these headers)."""
    import asyncio

    messages: list = []

    async def run():
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                 "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "headers": headers, "client": ("test", 1), "server": ("test", 80)}
        await app(scope, receive, send)

    asyncio.run(run())
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    content = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(content) if content else None


@pytest.mark.parametrize("value, expected", [
    (b"9" * 5000, (413, {"category": "configuration_too_large"})),
    (b"12abc", (422, {"category": "invalid_request"})),
    (b"-5", (422, {"category": "invalid_request"})),
    ("\u0663".encode(), (422, {"category": "invalid_request"})),              # a non-ASCII digit
    (b"", (422, {"category": "invalid_request"})),
], ids=["5000 digits", "letters", "negative", "non-ASCII digit", "empty"])
def test_an_extreme_or_malformed_content_length_is_controlled(api, value, expected):
    instance = api()
    source_id, generation = instance.prepared()
    body = json.dumps(instance.config_body(generation)).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", value)]
    status, payload = _raw_request(instance.app, "PUT", f"/api/video/sources/{source_id}/configuration",
                                   headers, body)
    assert (status, payload) == expected
    assert instance.current().json()["config"] is None                          # nothing changed
    for path in ("/api/video/sources", f"/api/video/sources/{source_id}/play"):
        status, payload = _raw_request(instance.app, "POST", path, headers, b"{}")
        assert status == 422 and payload == {"category": "invalid_request"}


def test_a_template_204_does_not_extend_the_idle_ttl(api):
    instance = api(template=None)
    source_id, generation = instance.prepared()
    instance.clock.advance(29 * 60)
    answer = instance.client.get(f"/api/video/sources/{source_id}/template?generation={generation}")
    assert answer.status_code == 204
    assert instance.current().json()["expires_in_seconds"] == 60.0             # not reset to 30 min
    instance.clock.advance(60.0)
    assert instance.current().json()["state"] in ("DELETING", "CLEANUP_PENDING") or \
        instance.current().status_code == 204                                   # expired at 30 min
    stale = instance.client.get(f"/api/video/sources/{source_id}/template?generation={generation}")
    assert stale.status_code in (409, 410)


def test_an_available_template_still_resets_the_idle_ttl(api):
    instance = api()
    source_id, generation = instance.prepared()
    instance.clock.advance(29 * 60)
    assert instance.client.get(f"/api/video/sources/{source_id}/template?generation={generation}").status_code == 200
    assert instance.current().json()["expires_in_seconds"] == 1800.0


# =========================================================================== cancellation of the upload task


class HoldingWriteStore:
    """Delegates to the real store; holds inside the worker right after ``begin_write``
    registered the writer, before it returns."""

    def __init__(self, real, gate: Gate) -> None:
        self._real, self._gate = real, gate

    def begin_write(self, *args, **kwargs):
        handle = self._real.begin_write(*args, **kwargs)
        self._gate.pass_through()
        return handle

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.mark.parametrize("cancel_with", ["asyncio task cancel", "anyio cancel scope"])
def test_a_cancelled_upload_request_never_leaves_a_writer_without_an_executor(api, cancel_with):
    """The ASGI request task is really cancelled while the writer reservation is held in
    its worker: the reservation and the whole write/probe step are one unit, so the work
    runs to its end and no writer stays registered without an executor."""
    import asyncio

    import anyio
    import httpx

    instance = api()
    created = instance.create().json()
    source_id, generation = created["source_id"], created["generation"]
    gate = Gate()
    instance.ctx.store = HoldingWriteStore(instance.store, gate)
    safety = threading.Timer(4.0, gate.release.set)
    safety.start()
    url = f"/api/video/sources/{source_id}/file?generation={generation}"
    outcome: dict = {}

    async def request(client):
        return await client.put(url, files={"file": ("clip.mp4", b"0" * 64, "video/mp4")})

    async def scenario():
        transport = httpx.ASGITransport(app=instance.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            if cancel_with == "asyncio task cancel":
                task = asyncio.create_task(request(client))
                while not gate.reached.is_set():
                    await asyncio.sleep(0.002)
                task.cancel()                                                  # the request task is cancelled
                await asyncio.sleep(0.02)
                gate.release.set()
                try:
                    await asyncio.wait_for(task, timeout=WAIT)
                    outcome["cancelled"] = False
                except asyncio.CancelledError:
                    outcome["cancelled"] = True
            else:
                scope = anyio.CancelScope()

                async def run():
                    with scope:                                                # only the request task
                        await request(client)
                    outcome["cancelled"] = scope.cancelled_caught

                async with anyio.create_task_group() as group:
                    group.start_soon(run)
                    while not gate.reached.is_set():
                        await anyio.sleep(0.002)
                    scope.cancel()                                             # cancels the request task
                    await anyio.sleep(0.02)
                    gate.release.set()

    try:
        asyncio.run(scenario())
    finally:
        safety.cancel()
        gate.release.set()
    # asyncio: the request task is cancelled (the await is abandoned); AnyIO: the worker
    # await is shielded, so the request may still complete. Either way:
    if cancel_with == "asyncio task cancel":
        assert outcome["cancelled"] is True
    # the unit ran to its end: the writer finished, the probe ran, nothing is left owned
    assert wait_until(lambda: instance.store.file_users() == 0)
    assert instance.decoder.created == 1 and instance.decoder.captures[0].released == 1
    current = instance.current().json()
    assert current["state"] == "PREPARED" and current["cleanup"]["pending"] is False
    deleted = instance.client.delete(f"/api/video/sources/{source_id}?generation={generation}")
    assert deleted.status_code in (202, 204)
    assert wait_until(lambda: instance.store.snapshot() is None) and len(instance.files.removed) == 1
    assert instance.create().status_code == 201                               # admission open again


class _SpyFile:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _SpyUpload:
    def __init__(self) -> None:
        self.file = _SpyFile()


def test_an_upload_unit_abandoned_before_it_started_reserves_nothing(api):
    instance = api()
    created = instance.create().json()
    upload = _SpyUpload()
    unit = sources._UploadUnit(upload)
    unit.abandon()                                                             # the request ended first
    assert unit.run(instance.ctx, instance.store, created["source_id"], created["generation"], ".mp4") is None
    assert upload.file.closed == 1 and instance.store.file_users() == 0
    assert instance.current().json()["state"] == "AWAITING_FILE"               # no writer registered
    unit.abandon()
    assert upload.file.closed == 1                                             # closed once


def test_an_unexpected_error_inside_the_upload_unit_still_ends_the_writer(api, monkeypatch):
    instance = api()
    created = instance.create().json()

    def broken(*_args, **_kwargs):
        raise ValueError("I/O operation on closed file " + SECRET)

    monkeypatch.setattr(sources, "_write_and_probe", broken)
    client = TestClient(instance.app, raise_server_exceptions=False)
    answer = client.put(f"/api/video/sources/{created['source_id']}/file?generation={created['generation']}",
                        files={"file": ("clip.mp4", b"0" * 64, "video/mp4")})
    assert answer.status_code == 500 and SECRET not in answer.text
    assert instance.store.file_users() == 0                                    # the writer was ended
    assert len(instance.files.removed) == 1 and instance.store.snapshot() is None
    assert instance.create().status_code == 201
