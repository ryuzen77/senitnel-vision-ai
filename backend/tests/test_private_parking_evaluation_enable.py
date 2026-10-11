"""T3d — private dispatch activation (D6) and the private evaluation path end to end with
fakes: production wiring, a fake model and tracker, the real Gemini client over an
``httpx.MockTransport`` and a recording audio device. No provider request, server, video
or speaker.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.models.evaluation import EvaluationFailure
from app.services import runtime
from app.services.evaluation_worker import EvaluationWorker
from app.services.event_frame_buffer import EventFrameBuffer
from app.services.gemini_evaluation_client import PRIVATE_FRAME_ROLES, GeminiEvaluationClient
from tests.test_evaluation_runtime_integration import GEMINI_KEY, _runtime_program
from tests.test_gemini_evaluation_client import _assessment, _provider_body
from tests.test_private_parking_runtime_wiring import LOCAL_CLIP, Stack, _private_env, _wait_for
from tests.test_private_parking_trigger import ZONE_FOOT, person

FLAG = "SENTINEL_PRIVATE_PARKING_EVALUATION"


def _wiring(tmp_path, mode: str):
    env = {"private": lambda: _private_env(tmp_path), "legacy": dict,
           "disabled": lambda: {"SENTINEL_PARKING_MODE": "bogus"}}[mode]()   # build only this one
    return runtime.build_parking_wiring(env, event_frame_buffer=EventFrameBuffer(), audio_gate=None)


# ── 6. activation matrix ─────────────────────────────────────────────────────

@pytest.mark.parametrize("flag,transport,ready,expected", [
    (None, "gemini", True, False),
    ("false", "gemini", True, False),
    ("yes", "gemini", True, False),
    ("true", "langflow", True, False),
    ("true", None, False, False),
    ("true", "gemini", False, False),
    ("true", "gemini", True, True),
])
def test_private_dispatch_needs_the_flag_gemini_and_readiness(tmp_path, caplog, flag, transport, ready,
                                                              expected) -> None:
    wiring = _wiring(tmp_path, "private")
    env = {} if flag is None else {FLAG: flag}
    with caplog.at_level(logging.DEBUG):
        enabled = runtime.evaluation_worker_enabled(wiring, env, transport=transport, evaluation_enabled=ready)
    assert enabled is expected
    if flag == "yes":
        assert "Private parking evaluation disabled: invalid enable flag configuration" in caplog.text
        assert "yes" not in caplog.text.replace("Private parking evaluation disabled", "")
    if transport == "langflow":
        assert "the selected transport has no private prompt" in caplog.text


@pytest.mark.parametrize("ready", [True, False])
def test_legacy_and_disabled_modes_ignore_the_flag(tmp_path, ready) -> None:
    env = {FLAG: "true"}
    legacy = runtime.evaluation_worker_enabled(_wiring(tmp_path, "legacy"), env, transport="langflow",
                                               evaluation_enabled=ready)
    disabled = runtime.evaluation_worker_enabled(_wiring(tmp_path, "disabled"), env, transport="gemini",
                                                 evaluation_enabled=ready)
    assert legacy is ready and disabled is False


def test_the_runtime_module_enables_private_dispatch_only_with_the_flag(tmp_path) -> None:
    base = {"SENTINEL_EVALUATION_TRANSPORT": "gemini", "SENTINEL_GEMINI_API_KEY": GEMINI_KEY,
            "SENTINEL_GEMINI_MODEL": "gemini-test-flash", **_private_env(tmp_path)}
    program = "from app.services import runtime\nprint(runtime.parking_wiring.mode, runtime.evaluation_worker.enabled)\n"
    off = _runtime_program(base, program, removed=(FLAG,))
    on = _runtime_program({**base, FLAG: "true"}, program)
    assert off.returncode == 0 and on.returncode == 0, off.stderr + on.stderr
    assert off.stdout.split() == ["private", "False"] and on.stdout.split() == ["private", "True"]
    assert GEMINI_KEY not in off.stderr + on.stderr + off.stdout + on.stdout


# ── 7. end to end with fakes ─────────────────────────────────────────────────

class Provider:
    """MockTransport handler: records calls, replies with one fixed body/status."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body, self.status, self.calls = body, status, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(json.loads(request.content))
        return httpx.Response(self.status, stream=httpx.ByteStream(self.body))


def _private_stack(tmp_path, stacks, provider: Provider):
    stack = stacks(_private_env(tmp_path))
    enabled = runtime.evaluation_worker_enabled(stack.wiring, {FLAG: "true"}, transport="gemini",
                                                evaluation_enabled=True)
    assert enabled is True
    client = GeminiEvaluationClient(api_key="synthetic-key", model="gemini-test-flash", timeout_seconds=20,
                                    http_client=httpx.Client(transport=httpx.MockTransport(provider)))
    stack.worker.close()
    stack.worker = EvaluationWorker(stack.wiring.worker_trigger, stack.budget, stack.wiring.bundle_source, client,
                                    clock=stack.clock, enabled=enabled,
                                    candidate_strategies=stack.wiring.candidate_strategies, audio_gate=stack.gate)
    stack.start()
    return stack


@pytest.fixture
def stacks():
    created: list = []

    def make(*args, **kwargs):
        stack = Stack(*args, **kwargs)
        created.append(stack)
        return stack

    yield make
    for stack in created:
        stack.close()


def test_one_private_dispatch_with_roles_and_critical_after_the_local_warning_is_deduplicated(tmp_path,
                                                                                            stacks) -> None:
    provider = Provider(_provider_body(json.dumps(_assessment(score=85, status="CRITICAL"))))
    stack = _private_stack(tmp_path, stacks, provider)
    stack.cross_in()
    stack.approach()
    (alert,) = stack.alerts
    assert _wait_for(lambda: stack.device.started == [LOCAL_CLIP])          # local warning first
    assert stack.worker.process_one(stack.clock())
    (body,) = provider.calls
    labels = [part["text"] for part in body["contents"][0]["parts"] if "text" in part][1:]
    assert [label.split("(")[1].split(")")[0].upper() for label in labels] == list(PRIVATE_FRAME_ROLES)
    assert stack.worker.get_snapshot().dispatch_attempts == 1
    snapshot = stack.gate.get_snapshot()
    assert snapshot.offered_critical == 1 and snapshot.dropped_duplicate == 1   # same event: no second grant
    assert snapshot.granted == 1 and stack.device.started == [LOCAL_CLIP]
    for _ in range(3):
        stack.feed(person(1, *ZONE_FOOT))                                   # same episode: no new candidate
    assert not stack.worker.process_one(stack.clock()) and len(provider.calls) == 1
    assert len(stack.alerts) == 1 and alert.event_id == stack.alerts[0].event_id


def test_a_failed_private_dispatch_is_not_retried(tmp_path, stacks) -> None:
    provider = Provider(b"{}", status=500)
    stack = _private_stack(tmp_path, stacks, provider)
    stack.cross_in()
    stack.approach()
    assert stack.worker.process_one(stack.clock())
    assert stack.worker.get_snapshot().last_failure is not None
    stack.clock.now += 60.0
    assert not stack.worker.process_one(stack.clock())
    assert len(provider.calls) == 1


def test_an_expired_private_candidate_is_never_sent(tmp_path, stacks) -> None:
    provider = Provider(_provider_body())
    stack = _private_stack(tmp_path, stacks, provider)
    stack.cross_in()
    stack.approach()
    stack.clock.now += 2.5                                                  # past the candidate TTL
    assert not stack.worker.process_one(stack.clock())
    assert provider.calls == [] and stack.worker.get_snapshot().dispatch_attempts == 0
    assert stack.worker.get_snapshot().last_failure in (None, EvaluationFailure.EVIDENCE_UNAVAILABLE)
