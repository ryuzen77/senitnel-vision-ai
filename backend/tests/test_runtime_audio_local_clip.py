"""T3c — the production audio factory ``runtime._create_audio_alert_gate`` with a separate
local-warning clip. A fake WAV probe and a recording device only: placeholder files in
``tmp_path`` (outside the repository), no WAV is generated or played.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import time

import pytest

from app.models.evaluation import ThreatStatus
from app.services import runtime
from app.services.audio_alert_service import MAX_ALERT_SECONDS

WAIT = 5.0
DIR_MARK = "clipdir-5a9e"
DURATIONS = {"critical.wav": 1.0, "local.wav": 1.5, "copy.wav": 1.0, "long.wav": MAX_ALERT_SECONDS + 1.0,
             "linked.wav": 1.0, "playing.wav": 4.0}


class RecordingDevice:
    instances: list = []

    def __init__(self) -> None:
        self.started: list[str] = []
        self.stops = 0
        RecordingDevice.instances.append(self)

    def start(self, path: str) -> None:
        self.started.append(path)

    def stop(self) -> None:
        self.stops += 1


def _wait_for(predicate, timeout: float = WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def clips(tmp_path, monkeypatch):
    folder = tmp_path / DIR_MARK
    folder.mkdir()
    for name in ("critical.wav", "local.wav", "long.wav", "bad.wav", "playing.wav"):
        (folder / name).write_bytes(f"placeholder {name}".encode())
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime, "probe_wav_duration", lambda path: DURATIONS.get(Path(path).name))
    RecordingDevice.instances = []
    monkeypatch.setattr(runtime, "WinsoundDevice", RecordingDevice)
    for name in ("SENTINEL_LANGFLOW_URL", "SENTINEL_CALLBACK_TOKEN", "SENTINEL_EVALUATION_TRANSPORT",
                 "SENTINEL_AUDIO_LOCAL_ALERT_FILE", "SENTINEL_AUDIO_ALERT_COOLDOWN_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SENTINEL_AUDIO_ALERT_ENABLED", "true")
    monkeypatch.setenv("SENTINEL_AUDIO_ALERT_FILE", str(folder / "critical.wav"))
    return folder


def _gate():
    gate = runtime._create_audio_alert_gate()                       # noqa: SLF001
    if gate is not None:
        gate.start()
        gate.apply_lifecycle(1, "A")
    return gate


# ── 11. a valid local clip is passed (cloud disabled) ───────────────────────

def test_a_valid_local_clip_is_passed_to_the_gate(clips, monkeypatch) -> None:
    monkeypatch.setenv("SENTINEL_AUDIO_LOCAL_ALERT_FILE", str(clips / "local.wav"))
    gate = _gate()
    try:
        assert gate.get_snapshot().local_clip_configured
        gate.offer_local("e1", "A")
        (device,) = RecordingDevice.instances
        assert _wait_for(lambda: device.started == [str((clips / "local.wav").resolve())])
        assert gate.get_snapshot().granted_local == 1
    finally:
        gate.close()


# ── 12. missing / invalid / the CRITICAL asset → local path off, sanitized ──

def _local_cases(clips: Path, monkeypatch) -> dict:
    repo = clips / "repo"
    repo.mkdir()
    (repo / "local.wav").write_bytes(b"placeholder inside repo")
    shutil.copyfile(clips / "critical.wav", clips / "copy.wav")      # byte-identical copy
    cases = {
        "missing": str(clips / "missing.wav"),
        "probe invalid": str(clips / "bad.wav"),
        "too long": str(clips / "long.wav"),
        "inside the repository": str(repo / "local.wav"),
        "same path": str(clips / "critical.wav"),
        "other spelling": str(clips / "sub" / ".." / "critical.wav"),
        "byte-identical copy": str(clips / "copy.wav"),
    }
    try:
        os.link(clips / "critical.wav", clips / "linked.wav")
        cases["hard link"] = str(clips / "linked.wav")
    except OSError:
        pass
    monkeypatch.setattr(runtime, "_REPOSITORY_ROOT", repo)
    return cases


SAME_ASSET = {"same path", "other spelling", "byte-identical copy", "hard link"}


def test_an_invalid_or_identical_local_clip_disables_only_the_local_path(clips, monkeypatch, caplog) -> None:
    (clips / "sub").mkdir()
    cases = _local_cases(clips, monkeypatch)
    assert {"same path", "other spelling", "byte-identical copy"} <= set(cases)
    for name, local in cases.items():
        monkeypatch.setenv("SENTINEL_AUDIO_LOCAL_ALERT_FILE", local)
        RecordingDevice.instances = []
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            gate = _gate()
        try:
            assert gate is not None, name                                # the CRITICAL clip survives
            snapshot = gate.get_snapshot()
            assert not snapshot.local_clip_configured, name
            gate.offer_local("e1", "A")
            assert gate.get_snapshot().dropped_no_local_clip == 1, name
            (device,) = RecordingDevice.instances
            assert device.started == [], name                            # never the CRITICAL clip
            expected = ("the local clip must differ from the CRITICAL clip" if name in SAME_ASSET
                        else "invalid local clip configuration")
            assert f"Local audio alert disabled: {expected}" in caplog.text, name
            assert DIR_MARK not in caplog.text and ".wav" not in caplog.text, name
        finally:
            gate.close()


def test_an_unset_local_clip_is_silent(clips, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        gate = _gate()
    try:
        assert not gate.get_snapshot().local_clip_configured and "Local audio" not in caplog.text
    finally:
        gate.close()


# ── 13. the CRITICAL clip keeps its previous behaviour ──────────────────────

def test_the_critical_clip_still_plays_with_any_local_outcome(clips, monkeypatch) -> None:
    for local in (None, str(clips / "bad.wav"), str(clips / "local.wav")):
        if local is None:
            monkeypatch.delenv("SENTINEL_AUDIO_LOCAL_ALERT_FILE", raising=False)
        else:
            monkeypatch.setenv("SENTINEL_AUDIO_LOCAL_ALERT_FILE", local)
        RecordingDevice.instances = []
        gate = _gate()
        try:
            gate.offer(1, "e1", "A", ThreatStatus.CRITICAL)
            (device,) = RecordingDevice.instances
            assert _wait_for(lambda: device.started == [str((clips / "critical.wav").resolve())])
        finally:
            gate.close()


def test_the_audio_flag_and_an_invalid_critical_clip_still_disable_audio(clips, monkeypatch, caplog) -> None:
    monkeypatch.setenv("SENTINEL_AUDIO_LOCAL_ALERT_FILE", str(clips / "local.wav"))
    for enabled, critical in (("false", "critical.wav"), ("true", "bad.wav")):
        monkeypatch.setenv("SENTINEL_AUDIO_ALERT_ENABLED", enabled)
        monkeypatch.setenv("SENTINEL_AUDIO_ALERT_FILE", str(clips / critical))
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            assert runtime._create_audio_alert_gate() is None       # noqa: SLF001  (local never enables audio)
        assert "Local audio alert unused: audio alerts are disabled" in caplog.text
        assert DIR_MARK not in caplog.text
    monkeypatch.delenv("SENTINEL_AUDIO_LOCAL_ALERT_FILE")
    monkeypatch.delenv("SENTINEL_AUDIO_ALERT_ENABLED")
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        assert runtime._create_audio_alert_gate() is None           # noqa: SLF001  legacy default: off
    assert caplog.text == ""


# ── 14. lifecycle, dedup and Stop cancel with a local clip ──────────────────

def test_dedup_stale_offers_and_stop_cancel_still_apply(clips, monkeypatch) -> None:
    monkeypatch.setenv("SENTINEL_AUDIO_LOCAL_ALERT_FILE", str(clips / "playing.wav"))
    gate = _gate()
    try:
        (device,) = RecordingDevice.instances
        gate.offer_local("e1", "A")
        assert _wait_for(lambda: len(device.started) == 1)             # local clip playing (4 s)
        gate.offer(1, "e1", "A", ThreatStatus.CRITICAL)               # same event: no second grant
        snapshot = gate.get_snapshot()
        # ``granted`` counts every grant (local included): one grant in total, the CRITICAL
        # offer for the same event was dropped as a duplicate
        assert (snapshot.granted_local, snapshot.granted, snapshot.dropped_duplicate) == (1, 1, 1)
        assert len(device.started) == 1
        gate.apply_lifecycle(2, "B")                                   # Stop/new session cancels
        assert _wait_for(lambda: device.stops >= 1)
        gate.offer_local("e2", "A")                                    # stale session
        assert gate.get_snapshot().dropped_stale >= 1 and len(device.started) == 1
    finally:
        gate.close()
