"""T3d — the private-parking prompt of the direct Gemini client (mock transport only; no
provider request). These tests check the REQUEST TEXT and the unchanged handling of fixed
mocked responses; they are not evidence of model accuracy.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import logging

import httpx
import pytest

from app.models.domain import DomainName
from app.models.evaluation import EvaluationCallback, ThreatStatus
from app.models.private_parking import ProximityEvidence
from app.services.event_frame_buffer import EventFrameBuffer
from app.services.gemini_evaluation_client import (
    ASSESSMENT_SCHEMA, PRIVATE_FRAME_ROLES, GeminiErrorKind, GeminiEvaluationError,
)
from tests.test_event_frame_buffer import _plan, _ready_buffer
from tests.test_gemini_evaluation_client import (
    JPEG, KEY, _assessment, _client, _provider_body, _replying, _request,
)

# SHA-256 of the encoded payloads produced by the client at d075207 (before T3d) for the
# fixed legacy and retail requests of tests.test_gemini_evaluation_client._request.
GOLDEN_PAYLOAD_SHA256 = {
    DomainName.PARKING_MOTORCYCLE: "caca8db3d78db62c4695ea02d5bae2bdc698e20515214be0c6f0c0a95a8c74a7",
    DomainName.RETAIL_STORE: "03931d513abaa8824c8cf6bec4d2f66f4b0654ec92ef19c38a84e00c585fd51b",
}
EVIDENCE = ProximityEvidence("episode-1", "z1", 4.2, 3, 0.0, 5)
OPEN_PARKING_SENTENCE = "a person stayed near a parked motorcycle"


def _private_request(*, roles=PRIVATE_FRAME_ROLES, domain=DomainName.PARKING_MOTORCYCLE, evidence=EVIDENCE):
    request = _request(domain)
    frames = tuple(replace(frame, role=role) for frame, role in zip(request.frame_bundle.frames, roles))
    return replace(request, evidence=evidence, frame_bundle=replace(request.frame_bundle, frames=frames))


def _sent(request, response: bytes | None = None):
    seen: list[httpx.Request] = []
    callback = _client(_replying(_provider_body(response), seen=seen)).dispatch(request)
    (sent,) = seen
    return json.loads(sent.content), callback


def _texts(body: dict) -> list[str]:
    return [part["text"] for part in body["contents"][0]["parts"] if "text" in part]


# ── 1. the private payload ───────────────────────────────────────────────────

def test_the_private_request_carries_the_sequence_rules_and_role_labels() -> None:
    body, _callback = _sent(_private_request())
    rules, *labels = _texts(body)
    for sentence in (
            "frame 1 (crossing) shows the person crossing the access line into the area",
            "frames 2-4 (journey) the walk inside, frame 5 (approach) the approach to a parked motorcycle",
            "Judge the visible actions across the whole crossing, journey and approach sequence",
            "the missing authorization gesture, looking around, walking through the area and approaching",
            "are not evidence of theft, alone or together",
            "interaction with the lock or the handlebar, handling of components",
            "movement of the motorcycle (pushed, lifted, rolled)",
            "Never infer identity, ownership or permission from the images",
            "'tidak terlihat jelas' or 'ambigu'",
            "this assessment is independent of that warning and neither confirms nor cancels it",
            "Keep reasoning to at most two short sentences"):
        assert sentence in rules, sentence
    assert OPEN_PARKING_SENTENCE not in rules and "Raise the score only" not in rules
    assert labels == ["Frame 1 of 5 (crossing), t=-2.00 s", "Frame 2 of 5 (journey), t=-1.00 s",
                      "Frame 3 of 5 (journey), t=-0.40 s", "Frame 4 of 5 (journey), t=+0.00 s",
                      "Frame 5 of 5 (approach), t=+0.40 s"]
    assert body["generationConfig"] == {
        "responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": ASSESSMENT_SCHEMA}}}


# ── 2. legacy and retail payloads are byte-identical to d075207 ─────────────

@pytest.mark.parametrize("domain", [DomainName.PARKING_MOTORCYCLE, DomainName.RETAIL_STORE])
def test_legacy_and_retail_payloads_are_byte_identical(domain) -> None:
    client = _client(_replying(b"{}"))
    assert hashlib.sha256(client._encode(_request(domain))).hexdigest() == GOLDEN_PAYLOAD_SHA256[domain]
    # roles on non-private evidence do not switch the prompt
    labelled = _private_request(domain=domain, evidence={})
    assert hashlib.sha256(client._encode(labelled)).hexdigest() == GOLDEN_PAYLOAD_SHA256[domain]


# ── 3. invalid private requests never reach HTTP and never fall back ────────

@pytest.mark.parametrize("roles,domain", [
    ((None,) * 5, DomainName.PARKING_MOTORCYCLE),
    (("JOURNEY", "CROSSING", "JOURNEY", "JOURNEY", "APPROACH"), DomainName.PARKING_MOTORCYCLE),
    (("CROSSING", "JOURNEY", "JOURNEY", "APPROACH", "APPROACH"), DomainName.PARKING_MOTORCYCLE),
    (("CROSSING", "JOURNEY", "JOURNEY", "JOURNEY", "approach"), DomainName.PARKING_MOTORCYCLE),
    (PRIVATE_FRAME_ROLES, DomainName.RETAIL_STORE),
])
def test_an_invalid_private_request_is_rejected_before_http(roles, domain) -> None:
    seen: list = []
    client = _client(_replying(_provider_body(), seen=seen))
    with pytest.raises(GeminiEvaluationError) as raised:
        client.dispatch(_private_request(roles=roles, domain=domain))
    assert raised.value.kind is GeminiErrorKind.INVALID_REQUEST and seen == []


# ── 4. response handling is shared and unchanged ────────────────────────────

def test_private_responses_use_the_existing_validation() -> None:
    _body, callback = _sent(_private_request())
    assert isinstance(callback, EvaluationCallback) and callback.assessment.status is ThreatStatus.CRITICAL
    mismatch = json.dumps(_assessment(score=80, status="NORMAL"))
    with pytest.raises(GeminiEvaluationError) as raised:
        _sent(_private_request(), mismatch)
    assert raised.value.kind is GeminiErrorKind.INVALID_RESPONSE


# ── 5. frame roles: sampler bundles carry them, buffer bundles do not ───────

def test_event_frame_buffer_bundles_carry_no_role() -> None:
    decision = _ready_buffer().wait_for_bundle(_plan(), 0.0)
    assert decision.bundle is not None and {frame.role for frame in decision.bundle.frames} == {None}


# ── 9-12. prompt cases (request text + unchanged handling of fixed mocks) ───

PROMPT_CASES = {
    "owner without a gesture": (5, "NORMAL", "Orang masuk dan berjalan ke motor; tidak ada interaksi terlihat."),
    "ordinary approach": (30, "NORMAL", "Orang melihat sekitar lalu berdiri di dekat motor."),
    "ambiguous interaction": (45, "WARNING", "Tangan dekat setang, tidak terlihat jelas apa yang dilakukan."),
    "visible manipulation": (85, "CRITICAL", "Tangan berulang kali memegang kunci kontak dan motor didorong."),
}
REQUIRED_BY_CASE = {
    "owner without a gesture": "are not evidence of theft, alone or together",
    "ordinary approach": "looking around, walking through the area and approaching",
    "ambiguous interaction": "'tidak terlihat jelas' or 'ambigu'",
    "visible manipulation": "interaction with the lock or the handlebar, handling of components",
}


@pytest.mark.parametrize("case", list(PROMPT_CASES))
def test_prompt_cases_send_the_rule_and_keep_the_mocked_assessment(case) -> None:
    score, status, reasoning = PROMPT_CASES[case]
    response = json.dumps(_assessment(score=score, status=status, reasoning=reasoning))
    body, callback = _sent(_private_request(), response)
    assert REQUIRED_BY_CASE[case] in _texts(body)[0]
    assessment = callback.assessment
    # no client-side rule raises or lowers what the (mocked) model returned
    assert (assessment.threat_score, assessment.status.value, assessment.reasoning) == (score, status, reasoning)


# ── 8. logs stay free of prompt, images and key ─────────────────────────────

def test_private_dispatch_logs_carry_no_prompt_image_or_key(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        _sent(_private_request())
    assert "not evidence of theft" not in caplog.text and KEY not in caplog.text
    assert JPEG.hex() not in caplog.text and "synthetic-frame" not in caplog.text
