"""T3c — parking-mode wiring through the production selection ``runtime.build_parking_wiring``.

Fake model, scripted tracker, fake capture, fake dispatcher, recording audio device and a
fake clock; no server, real video, model weights, provider or speaker. Every wait is
bounded; every helper thread is joined.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from app.models.evaluation import EvaluationFailure
from app.models.video import FramePacket, VideoState
from app.services import runtime
from app.services.audio_alert_service import AudioAlertGate
from app.services.detector_service import DetectorService
from app.services.disabled_parking_trigger import DisabledParkingTrigger
from app.services.evaluation_budget import EvaluationBudget
from app.services.evaluation_worker import EvaluationWorker
from app.services.event_frame_buffer import EventFrameBuffer
from app.services.parking_candidate_trigger import ParkingCandidateTrigger
from app.services.private_parking_coordinator import FrameSinkTee, PrivateParkingCoordinator
from app.models.private_parking import OperatorConfiguration, operator_template
from app.services import video_decoder
from app.services.prepared_source import HandoffTicket, PlaybackRefused
from app.services.private_parking_evidence_sampler import EpisodeEvidenceSampler
from app.services.session_config_registry import SessionConfigRegistry
from app.services.video_service import VideoService, VideoStartRefusedError
from tests.test_evaluation_worker import FakeClient
from tests.test_private_parking_evidence_sampler import frame as buffered_frame
from tests.test_private_parking_trigger import CONFIG, PARKED, ZONE_FOOT, FakeClock, domain, person
from tests.test_video_session_lifecycle import Capture

WAIT = 5.0
INSIDE = (0.3, 0.8)
SECRET_PATH_PART = "secret-config-7c1d"
FRAME = np.zeros((1000, 1000, 1), dtype=np.uint8)   # matches W = H = 1000 of the track helpers


def _wait_for(predicate, timeout: float = WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _payload_bytes(content: str | bytes | None) -> bytes:
    data = json.dumps(CONFIG) if content is None else content
    return data.encode("utf-8") if isinstance(data, str) else data


def _config_file(tmp_path: Path, content: str | bytes | None = None, *, name: str = "private-parking.json") -> Path:
    """Write ONE config file; every caller passes its own ``name`` when several coexist."""
    directory = tmp_path / SECRET_PATH_PART
    directory.mkdir(exist_ok=True)
    path = directory / name
    assert not path.exists(), "a config fixture must never overwrite another one"
    path.write_bytes(_payload_bytes(content))
    return path


def _private_env(tmp_path: Path, content: str | bytes | None = None, *, name: str = "private-parking.json") -> dict:
    return {"SENTINEL_PARKING_MODE": "private",
            "SENTINEL_PRIVATE_PARKING_CONFIG_FILE": str(_config_file(tmp_path, content, name=name))}


def _operator_config(wiring, size=(1000, 1000)) -> OperatorConfiguration:
    """The startup template confirmed as the operator configuration (P1c: a template is
    never used for analysis until it is bound for the session)."""
    return OperatorConfiguration(size[0], size[1], operator_template(wiring.template))


class _Decoder:
    """The one decoder (P1b) over scripted captures: the capture is created here and
    owned by the VideoService session."""

    def __init__(self, captures) -> None:
        self.captures = captures

    def create_capture(self, _path):
        return self.captures.pop(0)

    probe = staticmethod(video_decoder.probe)


def _prepared_start(video, wiring, *, size=(64, 48)) -> HandoffTicket:
    """Start analysis the P1c way: a handoff ticket and the bound operator configuration."""
    ticket = HandoffTicket(_prepared_start.serial)
    _prepared_start.serial += 1
    video.start_prepared_file(Path("prepared.mp4"), ticket=ticket, session_config=_operator_config(wiring, size),
                              expected_size=size, on_finished=lambda *_args: None)
    return ticket


_prepared_start.serial = 1


class RecordingDevice:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.stops = 0

    def start(self, path: str) -> None:
        self.started.append(path)

    def stop(self) -> None:
        self.stops += 1


CRITICAL_CLIP, LOCAL_CLIP = "C:/outside/critical.wav", "C:/outside/local.wav"


class ScriptedTracker:
    def __init__(self) -> None:
        self.next_tracks: tuple = ()
        self.calls = 0

    def start_session(self, session_id: str) -> None:
        pass

    def observe(self, frame):
        self.calls += 1
        return self.next_tracks

    def end_session(self, session_id: str) -> None:
        pass

    def close(self) -> None:
        pass


class Stack:
    """Production wiring + DetectorService (fake model) + EvaluationWorker, driven the way
    VideoService drives them (tee/detector with the owner's lease)."""

    def __init__(self, env: dict, *, worker_enabled: bool | None = None, gate: bool = True) -> None:
        self.clock = FakeClock()
        self.buffer = EventFrameBuffer()
        self.device = RecordingDevice()
        self.gate = (AudioAlertGate(self.device, CRITICAL_CLIP, 0.01, local_wav_path=LOCAL_CLIP,
                                    local_duration_seconds=0.01) if gate else None)
        if self.gate is not None:
            self.gate.start()
        self.wiring = runtime.build_parking_wiring(env, event_frame_buffer=self.buffer, audio_gate=self.gate,
                                                   clock=self.clock)
        self.tracker = ScriptedTracker()
        self.delivered = 0
        self.alerts: list = []
        if self.wiring.coordinator is not None:
            observe, pop = self.wiring.coordinator.observe, self.wiring.private_trigger.pop_alerts

            def counted_observe(*args):
                observe(*args)
                self.delivered += 1

            def recorded_pop():
                alerts = pop()
                self.alerts.extend(alerts)
                return alerts

            self.wiring.coordinator.observe = counted_observe
            self.wiring.private_trigger.pop_alerts = recorded_pop
        self.detector = DetectorService(model_factory=lambda: (lambda _frame: []), tracker=self.tracker,
                                        parking_trigger=self.wiring.detector_slot,
                                        domain_snapshot_provider=lambda: domain("s1"))
        self.client = FakeClient()
        self.budget = EvaluationBudget()
        enabled = self.wiring.worker_enabled_override if worker_enabled is None else worker_enabled
        self.worker = EvaluationWorker(self.wiring.worker_trigger, self.budget, self.wiring.bundle_source,
                                       self.client, clock=self.clock, enabled=bool(enabled),
                                       candidate_strategies=self.wiring.candidate_strategies, audio_gate=self.gate)
        self.seq = 0

    def start(self, session: str = "s1", lease: str = "op-1") -> None:
        self.lease = lease
        if self.wiring.registry is not None:            # private: VideoService binds per lease (P1b)
            self.wiring.registry.bind(session, lease, _operator_config(self.wiring))
        self.wiring.buffer_slot.start_session(session, lease=lease)
        self.detector.start_session(session, lease=lease)
        self.worker.start_session(session)

    def feed(self, *tracks, dt: float = 0.2) -> None:
        self.clock.now += dt
        self.seq += 1
        ts = self.clock.now
        self.wiring.buffer_slot.offer(buffered_frame(self.seq, ts))
        self.tracker.next_tracks = tuple(tracks) + (PARKED,)
        before = self.delivered
        frame = FRAME                                   # W x H of the track geometry
        self.detector.offer(FramePacket("s1", self.seq, ts, frame, b"jpeg"), lease=self.lease)
        assert _wait_for(lambda: self.delivered == before + 1)

    def cross_in(self) -> None:
        for fy in (0.40, 0.45, 0.55):
            self.feed(person(1, 0.5, fy))
        for _ in range(6):
            self.feed(person(1, *INSIDE))

    def approach(self) -> None:
        for _ in range(3):
            self.feed(person(1, *ZONE_FOOT))

    def close(self) -> None:
        self.worker.close()
        self.detector.close()
        if self.gate is not None:
            self.gate.close()


@pytest.fixture
def stacks():
    created: list[Stack] = []

    def make(*args, **kwargs) -> Stack:
        stack = Stack(*args, **kwargs)
        created.append(stack)
        return stack

    yield make
    for stack in created:
        stack.close()


# ── 1/2/3: composition per mode ──────────────────────────────────────────────

@pytest.mark.parametrize("env", [{}, {"SENTINEL_PARKING_MODE": "legacy"}])
def test_legacy_is_the_default_and_unchanged(env) -> None:
    buffer = EventFrameBuffer()
    wiring = runtime.build_parking_wiring(env, event_frame_buffer=buffer, audio_gate=None)
    assert wiring.mode == "legacy" and isinstance(wiring.detector_slot, ParkingCandidateTrigger)
    assert wiring.worker_trigger is wiring.detector_slot
    assert wiring.buffer_slot is buffer and wiring.bundle_source is buffer
    assert (wiring.required_sinks, wiring.candidate_strategies, wiring.worker_enabled_override) == (
        frozenset(), None, None)
    assert wiring.tee is None and wiring.sampler is None and wiring.coordinator is None


# Each payload is written to its OWN file, only for the case that runs. ``parsed``: True when
# the parser must receive exactly this payload's text, False when reading stops before it.
INVALID_PAYLOADS = {
    "config too large": (" " * 4097, False),
    "config not json": ("{" + SECRET_PATH_PART, True),
    "config invalid": (json.dumps({"line": [0, 0, 0, 0]}), True),
    "config not utf-8": (b"\xff\xfe", False),
    # numbers the parser cannot convert (OverflowError inside the parser)
    "huge integer coordinate": (json.dumps(dict(CONFIG, line=[10 ** 350, 0.5, 0.8, 0.5])), True),
    "huge integer threshold": (json.dumps(dict(CONFIG, thresholds={"candidate_ttl_seconds": 10 ** 350})), True),
}


def _invalid_env(tmp_path: Path, case: str) -> dict:
    if case == "invalid mode":
        return {"SENTINEL_PARKING_MODE": "Private"}
    if case == "config unset":
        return {"SENTINEL_PARKING_MODE": "private"}
    if case == "config missing":
        return {"SENTINEL_PARKING_MODE": "private",
                "SENTINEL_PRIVATE_PARKING_CONFIG_FILE": str(tmp_path / SECRET_PATH_PART / "none.json")}
    content, _parsed = INVALID_PAYLOADS[case]
    return _private_env(tmp_path, content, name=case.replace(" ", "-") + ".json")


def test_the_invalid_config_fixtures_never_overwrite_each_other(tmp_path) -> None:
    files = {case: Path(_invalid_env(tmp_path, case)["SENTINEL_PRIVATE_PARKING_CONFIG_FILE"])
             for case in INVALID_PAYLOADS}
    assert len(set(files.values())) == len(INVALID_PAYLOADS)              # one file per case
    on_disk = {case: path.read_bytes() for case, path in files.items()}
    for case, (content, _parsed) in INVALID_PAYLOADS.items():
        assert on_disk[case] == _payload_bytes(content), case           # each file holds its own payload
    assert len(set(on_disk.values())) == len(INVALID_PAYLOADS)            # and the payloads all differ


@pytest.mark.parametrize("case", ["config unset", "config missing", *INVALID_PAYLOADS])
def test_private_mode_without_a_valid_template_stays_private_with_no_fallback(tmp_path, monkeypatch, caplog,
                                                                             case) -> None:
    """P1c: the startup file is only an optional template. Missing or invalid → private
    mode without a template (never the disabled path), and a session without a bound
    operator configuration is refused (no fallback to any configuration)."""
    env = _invalid_env(tmp_path, case)
    received: list = []
    real_parse = runtime.parse_private_parking_config

    def spying_parse(text):
        received.append(text)
        return real_parse(text)

    monkeypatch.setattr(runtime, "parse_private_parking_config", spying_parse)
    buffer = EventFrameBuffer()
    with caplog.at_level(logging.DEBUG):
        wiring = runtime.build_parking_wiring(env, event_frame_buffer=buffer, audio_gate=None)
    assert wiring.mode == "private" and wiring.template is None
    assert isinstance(wiring.registry, SessionConfigRegistry) and len(wiring.registry) == 0
    assert isinstance(wiring.detector_slot, PrivateParkingCoordinator)
    assert wiring.worker_enabled_override is False
    assert caplog.text.count("Private parking template unavailable") == 1
    assert SECRET_PATH_PART not in caplog.text and "Error" not in caplog.text and "0000000000" not in caplog.text
    if case in INVALID_PAYLOADS:
        content, parsed = INVALID_PAYLOADS[case]
        path = Path(env["SENTINEL_PRIVATE_PARKING_CONFIG_FILE"])
        assert path.read_bytes() == _payload_bytes(content)
        assert received == ([_payload_bytes(content).decode("utf-8")] if parsed else [])
    else:
        assert received == []
    # a session that was never bound is refused: no candidate, no fallback configuration
    detector = DetectorService(model_factory=lambda: (lambda _frame: []), parking_trigger=wiring.detector_slot)
    video = VideoService(detector=detector, event_frame_buffer=wiring.buffer_slot,
                         required_sinks=wiring.required_sinks)
    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", lambda *_a, **_k: Capture())
    try:
        with pytest.raises(VideoStartRefusedError):    # required refusal: no operator binding
            video.start_webcam()
        assert video.get_status().state is VideoState.ERROR
        assert wiring.private_trigger.get_snapshot().source_session_id is None
        video.stop()
        assert video.get_status().state is VideoState.IDLE
    finally:
        video.stop()
        detector.close()


@pytest.mark.parametrize("case", ["invalid mode"])
def test_invalid_mode_uses_the_disabled_trigger_until_shutdown(tmp_path, monkeypatch, caplog, case) -> None:
    env = _invalid_env(tmp_path, case)
    received: list = []
    real_parse = runtime.parse_private_parking_config

    def spying_parse(text):
        received.append(text)
        return real_parse(text)

    monkeypatch.setattr(runtime, "parse_private_parking_config", spying_parse)
    buffer = EventFrameBuffer()
    with caplog.at_level(logging.DEBUG):
        wiring = runtime.build_parking_wiring(env, event_frame_buffer=buffer, audio_gate=None)
    assert wiring.mode == "disabled" and isinstance(wiring.detector_slot, DisabledParkingTrigger)
    assert wiring.worker_trigger is wiring.detector_slot                   # never None, never legacy
    assert wiring.buffer_slot is buffer and wiring.required_sinks == frozenset()
    assert wiring.worker_enabled_override is False and wiring.tee is None
    assert SECRET_PATH_PART not in caplog.text and "Private" not in caplog.text
    assert "Error" not in caplog.text and "0000000000" not in caplog.text      # no exception, no value
    # the case really read its own payload (no fixture overwrote it)
    if case in INVALID_PAYLOADS:
        content, parsed = INVALID_PAYLOADS[case]
        path = Path(env["SENTINEL_PRIVATE_PARKING_CONFIG_FILE"])
        assert path.read_bytes() == _payload_bytes(content)
        assert received == ([_payload_bytes(content).decode("utf-8")] if parsed else [])
    else:
        assert received == []
    # the whole lifecycle runs with no candidate and no fallback, then shuts down
    monkeypatch.setattr("app.services.video_service.cv2.VideoCapture", lambda *_a, **_k: Capture())
    tracker = ScriptedTracker()
    tracker.next_tracks = (person(1, 0.5, 0.55), PARKED)
    detector = DetectorService(model_factory=lambda: (lambda _frame: []), tracker=tracker,
                               parking_trigger=wiring.detector_slot, domain_snapshot_provider=lambda: domain("s1"))
    worker = EvaluationWorker(wiring.worker_trigger, EvaluationBudget(), wiring.bundle_source, FakeClient(),
                              enabled=False)
    worker.start()
    video = VideoService(detector=detector, event_frame_buffer=wiring.buffer_slot, evaluation_worker=worker,
                         required_sinks=wiring.required_sinks)
    assert video.start_webcam() and _wait_for(lambda: buffer.get_snapshot().frame_count > 0)
    assert _wait_for(lambda: wiring.detector_slot.get_snapshot().observations_ignored > 0)
    assert video.get_status().state is VideoState.RUNNING
    assert wiring.worker_trigger.claim_next_candidate(0.0) is None
    scene = ScriptedTracker()
    runtime.shutdown_services(video, scene, detector, wiring.worker_trigger, tracker, buffer,
                              evaluation_worker=worker)
    assert video.get_status().state is VideoState.IDLE
    assert wiring.detector_slot.get_snapshot().closed and not worker.get_snapshot().running


def test_private_mode_wires_coordinator_tee_sampler_and_required_sinks(tmp_path, monkeypatch) -> None:
    buffer = EventFrameBuffer()
    wiring = runtime.build_parking_wiring(_private_env(tmp_path), event_frame_buffer=buffer, audio_gate=None)
    assert wiring.mode == "private"
    assert isinstance(wiring.detector_slot, PrivateParkingCoordinator) and wiring.detector_slot is wiring.coordinator
    assert isinstance(wiring.buffer_slot, FrameSinkTee) and wiring.buffer_slot is wiring.tee
    assert isinstance(wiring.bundle_source, EpisodeEvidenceSampler) and wiring.bundle_source is wiring.sampler
    assert wiring.worker_trigger is wiring.private_trigger and wiring.worker_enabled_override is False
    assert wiring.required_sinks == {"event frame buffer", "detector"}
    # frames reach both the EventFrameBuffer and the sampler through the tee
    assert isinstance(wiring.registry, SessionConfigRegistry) and wiring.template is not None
    detector = DetectorService(model_factory=lambda: (lambda _frame: []), parking_trigger=wiring.detector_slot)
    video = VideoService(detector=detector, event_frame_buffer=wiring.buffer_slot,
                         required_sinks=wiring.required_sinks, session_config_registry=wiring.registry,
                         decoder=_Decoder([Capture(), Capture()]))
    try:
        _prepared_start(video, wiring)
        assert _wait_for(lambda: buffer.get_snapshot().frame_count > 0 and wiring.sampler.get_snapshot().held_frames > 0)
        video.stop()
        assert video.get_status().state is VideoState.IDLE and len(wiring.registry) == 0
        # a refused required start → ERROR until Stop (the store records REFUSED)
        wiring.tee.close()
        with pytest.raises(PlaybackRefused):
            _prepared_start(video, wiring)
        assert video.get_status().state is VideoState.ERROR
        video.stop()
        assert video.get_status().state is VideoState.IDLE
    finally:
        video.stop()
        detector.close()


# ── 4: the sampler is the only private bundle source ────────────────────────

def test_private_bundles_come_only_from_the_sampler(tmp_path, stacks) -> None:
    stack = stacks(_private_env(tmp_path), worker_enabled=True, gate=False)      # enabled here only
    calls: list = []
    original = stack.buffer.wait_for_bundle
    stack.buffer.wait_for_bundle = lambda *a, **k: calls.append(1) or original(*a, **k)
    stack.start()
    stack.cross_in()
    stack.approach()
    (alert,) = stack.alerts
    assert stack.worker.process_one(stack.clock())
    (request,) = stack.client.requests
    sealed = {f.source_frame_seq for f in request.frame_bundle.frames}
    assert request.event_id == alert.event_id and sealed and max(sealed) <= stack.seq
    assert len(request.frame_bundle.frames) == 5 and calls == []      # the buffer was never asked


def test_released_evidence_fails_explicitly_without_the_buffer(tmp_path, stacks) -> None:
    stack = stacks(_private_env(tmp_path), worker_enabled=True, gate=False)
    calls: list = []
    original = stack.buffer.wait_for_bundle
    stack.buffer.wait_for_bundle = lambda *a, **k: calls.append(1) or original(*a, **k)
    stack.start()
    stack.cross_in()
    stack.approach()
    (alert,) = stack.alerts
    assert stack.wiring.sampler.release(alert.event_id, token=alert.token)
    assert stack.worker.process_one(stack.clock())
    assert stack.worker.get_snapshot().last_failure is EvaluationFailure.EVIDENCE_UNAVAILABLE
    assert stack.client.requests == [] and calls == []


# ── 5/8: cloud disabled; crossing alone vs confirmed approach ───────────────

def test_cloud_disabled_local_warning_only_after_a_confirmed_approach(tmp_path, stacks) -> None:
    stack = stacks(_private_env(tmp_path))
    assert stack.worker.enabled is False
    stack.start()
    stack.cross_in()                                                   # crossing + journey only
    assert stack.alerts == [] and stack.device.started == []
    stack.approach()                                                   # confirmed approach, unauthorized
    (alert,) = stack.alerts
    assert _wait_for(lambda: stack.device.started == [LOCAL_CLIP])   # the LOCAL clip, cloud disabled
    assert not stack.worker.process_one(stack.clock())                 # no claim
    assert stack.budget.get_snapshot(stack.clock()).total_granted == 0 and stack.client.requests == []
    assert stack.worker.get_snapshot().dispatch_attempts == 0
    for _ in range(3):
        stack.feed(person(1, *ZONE_FOOT))                              # same episode: no second alert
    assert len(stack.alerts) == 1 and stack.device.started == [LOCAL_CLIP]
    counts = stack.gate.get_snapshot()
    assert counts.offered_local == 1 and counts.granted_local == 1
    # Stop: the gate follows the session lifecycle (a later local offer is stale)
    stack.worker.end_session("s1")
    stack.gate.offer_local(alert.event_id + "-late", "s1")
    assert stack.gate.get_snapshot().granted_local == 1


# ── 6: expiry without observation ───────────────────────────────────────────

def test_a_published_candidate_expires_without_observation_and_releases_bytes(tmp_path, stacks) -> None:
    stack = stacks(_private_env(tmp_path), gate=False)
    stack.start()
    stack.cross_in()
    stack.approach()
    (alert,) = stack.alerts
    assert stack.wiring.sampler.get_snapshot().sealed == 1
    stack.clock.now += 2.5                                             # past the candidate TTL, no frames
    assert stack.wiring.private_trigger.claim_next_candidate(stack.clock()) is None
    stack.clock.now += 30.0                                            # past the sealed TTL
    snapshot = stack.wiring.sampler.get_snapshot()                     # the call performs the sweep
    assert snapshot.sealed == 0 and snapshot.count("sealed_expired") == 1   # no timer, no new frame


# ── 7: Stop / EOF / restart with a failed cleanup ───────────────────────────

def test_stop_eof_restart_and_a_failed_cleanup_keep_admission_closed(tmp_path, monkeypatch) -> None:
    buffer = EventFrameBuffer()
    wiring = runtime.build_parking_wiring(_private_env(tmp_path), event_frame_buffer=buffer, audio_gate=None)
    captures = [Capture(), Capture([np.zeros((48, 64, 3), dtype=np.uint8), "eof"], tail="eof"), Capture()]
    detector = DetectorService(model_factory=lambda: (lambda _frame: []), parking_trigger=wiring.detector_slot)
    video = VideoService(detector=detector, event_frame_buffer=wiring.buffer_slot,
                         required_sinks=wiring.required_sinks, session_config_registry=wiring.registry,
                         decoder=_Decoder(captures))
    trigger_end = wiring.private_trigger.end_session
    failures = [RuntimeError("end failed")]

    def failing_end(session_id):
        if failures:
            raise failures.pop()
        trigger_end(session_id)

    wiring.private_trigger.end_session = failing_end
    try:
        _prepared_start(video, wiring)
        assert _wait_for(lambda: video.get_status().state is VideoState.RUNNING)
        old = video._current                                           # noqa: SLF001
        video.stop()                                                   # the trigger end fails once
        assert video.get_status().state is VideoState.ERROR and video.claim_start() is None
        video.stop()                                                   # retry with the same lease
        assert video.get_status().state is VideoState.IDLE
        assert detector.get_lifecycle_diagnostics()["pending_sessions"] == 0
        _prepared_start(video, wiring)                                 # EOF ends it by itself
        assert _wait_for(lambda: video.get_status().state is VideoState.IDLE)
        assert wiring.tee.get_diagnostics()["active_sessions"] == 0
        _prepared_start(video, wiring)
        assert _wait_for(lambda: video.get_status().state is VideoState.RUNNING)
        detector.end_session(old.session_id, lease=old.token)          # late, old lease
        wiring.tee.end_session(old.session_id, lease=old.token)
        assert detector.get_lifecycle_diagnostics()["active_sessions"] == 1
        assert wiring.tee.get_diagnostics()["active_sessions"] == 1
        assert video.get_status().state is VideoState.RUNNING
    finally:
        video.stop()
        detector.close()


# ── 9: shutdown order ───────────────────────────────────────────────────────

def test_shutdown_closes_private_objects_once_in_order() -> None:
    order: list = []

    class Part:
        def __init__(self, name):
            self.name = name

        def stop(self):
            order.append(self.name + ".stop")

        def close(self):
            order.append(self.name)

    names = ("video", "scene", "detector", "trigger", "tracker", "buffer", "worker", "client", "budget", "gate",
             "coordinator", "tee", "sampler")
    parts = {name: Part(name) for name in names}
    runtime.shutdown_services(parts["video"], parts["scene"], parts["detector"], parts["trigger"], parts["tracker"],
                              parts["buffer"], evaluation_worker=parts["worker"], langflow_client=parts["client"],
                              evaluation_budget=parts["budget"], audio_alert=parts["gate"],
                              coordinator=parts["coordinator"], frame_tee=parts["tee"], sampler=parts["sampler"])
    assert order == ["video.stop", "gate", "worker", "client", "scene", "detector", "coordinator", "trigger",
                     "tracker", "tee", "buffer", "sampler", "budget"]


# ── env → module composition (subprocess import of the real runtime) ─────────

def _import_runtime(env_overrides: dict) -> subprocess.CompletedProcess:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("SENTINEL_")}
    environment.update(env_overrides)
    program = """
from app.services import runtime
w = runtime.parking_wiring
print(w.mode, type(runtime.detector_service._parking_trigger).__name__,
      type(runtime.video_service._event_frame_buffer).__name__,
      sorted(runtime.video_service._required_sinks), type(runtime.evaluation_worker._buffer).__name__,
      runtime.evaluation_worker.enabled)
"""
    return subprocess.run([sys.executable, "-c", program], cwd=Path(__file__).resolve().parents[1],
                          env=environment, capture_output=True, text=True, timeout=60, check=False)


def test_the_runtime_module_selects_the_mode_from_the_environment(tmp_path) -> None:
    legacy = _import_runtime({})
    assert legacy.returncode == 0, legacy.stderr
    assert legacy.stdout.split()[:4] == ["legacy", "ParkingCandidateTrigger", "EventFrameBuffer", "[]"]
    private = _import_runtime(_private_env(tmp_path))
    assert private.returncode == 0, private.stderr
    out = private.stdout
    assert out.startswith("private PrivateParkingCoordinator FrameSinkTee")
    assert "['detector', 'event frame buffer'] EpisodeEvidenceSampler False" in out
    missing = _import_runtime({"SENTINEL_PARKING_MODE": "private",
                               "SENTINEL_PRIVATE_PARKING_CONFIG_FILE": str(tmp_path / SECRET_PATH_PART / "x.json")})
    assert missing.returncode == 0 and missing.stdout.startswith("private PrivateParkingCoordinator FrameSinkTee")
    assert "Private parking template unavailable" in missing.stderr        # private, no template
    assert SECRET_PATH_PART not in missing.stderr + missing.stdout
    invalid_mode = _import_runtime({"SENTINEL_PARKING_MODE": "Private"})
    assert invalid_mode.returncode == 0
    assert invalid_mode.stdout.startswith("disabled DisabledParkingTrigger EventFrameBuffer")


# ── delta 1: fail closed only at the config boundary ─────────────────────────

def test_a_huge_number_in_a_small_config_fails_closed_through_the_builder(tmp_path, caplog) -> None:
    content = json.dumps(dict(CONFIG, line=[10 ** 350, 0.5, 0.8, 0.5]))
    assert len(content.encode("utf-8")) <= 4096                          # small enough to be parsed
    with caplog.at_level(logging.DEBUG):
        wiring = runtime.build_parking_wiring(_private_env(tmp_path, content=content),
                                              event_frame_buffer=EventFrameBuffer(), audio_gate=None)
    assert wiring.mode == "private" and wiring.template is None          # the template fails closed
    assert caplog.text.count("Private parking template unavailable") == 1
    assert "OverflowError" not in caplog.text and SECRET_PATH_PART not in caplog.text


def test_a_service_construction_error_is_not_hidden_by_the_config_boundary(tmp_path, monkeypatch) -> None:
    def broken_trigger(*_args, **_kwargs):
        raise RuntimeError("construction failed")

    monkeypatch.setattr(runtime, "PrivateParkingTrigger", broken_trigger)
    with pytest.raises(RuntimeError):
        runtime.build_parking_wiring(_private_env(tmp_path), event_frame_buffer=EventFrameBuffer(), audio_gate=None)


# ── delta 2: .env.example carries no control characters ──────────────────────

def test_env_example_has_no_control_characters_and_documents_the_new_keys() -> None:
    data = (Path(__file__).resolve().parents[1] / ".env.example").read_bytes()
    control = sorted({byte for byte in data if (byte < 0x20 and byte != 0x0A) or byte == 0x7F})
    assert control == []                                                 # only LF; no CR, TAB, NUL
    for key in (b"SENTINEL_PARKING_MODE=", b"SENTINEL_PRIVATE_PARKING_CONFIG_FILE=",
                b"SENTINEL_AUDIO_LOCAL_ALERT_FILE="):
        assert key in data
