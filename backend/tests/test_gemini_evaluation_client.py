"""Mock-transport tests for the direct Gemini client; no provider request is made."""

from __future__ import annotations

import base64
from dataclasses import replace
import gzip
import json
import logging

import httpx
import pytest

from app.models.domain import DomainName
from app.models.evaluation import EvaluationCallback, EvaluationRequest, ThreatStatus
from app.models.event_frame import EventFrameBundle, SelectedFrame
from app.services import gemini_evaluation_client
from app.services.gemini_evaluation_client import (
    ASSESSMENT_SCHEMA, MAX_RESPONSE_BYTES, READ_CHUNK_BYTES, GeminiErrorKind, GeminiEvaluationClient,
    GeminiEvaluationError,
)

KEY = "synthetic-gemini-key-123"
MODEL = "gemini-test-flash"
JPEG = b"\xff\xd8synthetic-frame\xff\xd9"
OFFSETS = (-2.0, -1.0, -.4, 0.0, .4)
REASONING = "Orang terlihat memegang setang motor; tangan tidak terlihat jelas."


def _request(domain: DomainName = DomainName.PARKING_MOTORCYCLE, *, frames: int = 5,
             jpeg: bytes = JPEG, actual: tuple[float, ...] = OFFSETS) -> EvaluationRequest:
    selected = tuple(
        SelectedFrame(index + 1, 10 + real, offset, real, abs(real - offset), 640, 480, "image/jpeg", jpeg)
        for index, (offset, real) in enumerate(zip(OFFSETS[:frames], actual))
    )
    bundle = EventFrameBundle("event-abc", "session-xyz", domain, 10.0, selected, len(jpeg) * frames)
    return EvaluationRequest("event-abc", "session-xyz", domain, 7.0, 10.0, 3,
                             5 if domain is DomainName.PARKING_MOTORCYCLE else None, {}, bundle)


def _assessment(score: int = 80, status: str = "CRITICAL", **overrides) -> dict:
    value = {
        "threatScore": score, "status": status, "reasoning": REASONING,
        "suspectProfile": {
            "personDetected": True, "clothing": "jaket gelap", "observedAction": "memegang setang",
            "visibleObjects": ["motor"], "distinguishingFeatures": ["helm"],
        },
    }
    value.update(overrides)
    return value


def _provider_body(text: str | None = None, *, finish: object = "STOP", extra_parts=(), **top) -> bytes:
    parts = [*extra_parts, {"text": json.dumps(_assessment()) if text is None else text}]
    candidate = {"content": {"role": "model", "parts": parts}}
    if finish is not None:
        candidate["finishReason"] = finish
    return json.dumps({"candidates": [candidate], **top}).encode()


def _client(handler, **kwargs) -> GeminiEvaluationClient:
    return GeminiEvaluationClient(api_key=KEY, model=MODEL, timeout_seconds=20,
                                  http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def _replying(content: bytes, status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        headers = {"Location": "https://elsewhere.invalid/next"} if 300 <= status < 400 else {}
        # A real streamed body, as a network transport returns it (not pre-loaded).
        return httpx.Response(status, stream=httpx.ByteStream(content), headers=headers)
    return handler


# ── request ──────────────────────────────────────────────────────────────────

def test_request_uses_the_documented_generate_content_contract() -> None:
    seen: list[httpx.Request] = []
    callback = _client(_replying(_provider_body(), seen=seen)).dispatch(_request())
    (sent,) = seen
    assert sent.method == "POST"
    assert str(sent.url) == f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    assert sent.headers["x-goog-api-key"] == KEY and KEY not in str(sent.url)
    assert sent.headers["accept-encoding"] == "identity"
    body = json.loads(sent.content)
    assert set(body) == {"contents", "generationConfig"}
    assert body["generationConfig"] == {
        "responseFormat": {"text": {"mimeType": "application/json", "schema": ASSESSMENT_SCHEMA}}}
    parts = body["contents"][0]["parts"]
    images = [part["inlineData"] for part in parts if "inlineData" in part]
    assert len(images) == 5                                     # exactly five images
    assert all(image == {"mimeType": "image/jpeg", "data": base64.b64encode(JPEG).decode()} for image in images)
    labels = [part["text"] for part in parts[1:] if "text" in part]
    assert labels == [f"Frame {i} of 5, t={o:+.2f} s" for i, o in enumerate(OFFSETS, 1)]   # chronological
    assert [("inlineData" in part) for part in parts[1:]] == [False, True] * 5
    assert b"event-abc" not in sent.content and b"session-xyz" not in sent.content
    assert isinstance(callback, EvaluationCallback)


def test_prompts_are_domain_specific_and_bounded() -> None:
    prompts = {}
    for domain in (DomainName.PARKING_MOTORCYCLE, DomainName.RETAIL_STORE):
        seen: list[httpx.Request] = []
        _client(_replying(_provider_body(), seen=seen)).dispatch(_request(domain))
        prompts[domain] = json.loads(seen[0].content)["contents"][0]["parts"][0]["text"]
    parking, retail = prompts[DomainName.PARKING_MOTORCYCLE], prompts[DomainName.RETAIL_STORE]
    assert "motorcycle" in parking and "motor" not in retail.lower()
    for prompt in prompts.values():
        assert "Never infer identity, ownership, intent or a crime from clothing colour" in prompt
        assert "tidak terlihat jelas" in prompt and "event_id" not in prompt
        assert len(prompt) < 1800
    assert "Payment and ownership are not visible" in retail


@pytest.mark.parametrize("request_", [
    _request(DomainName.UNKNOWN), _request(frames=4), _request(jpeg=b"not-a-jpeg"),
    replace(_request(), event_id="other"),
])
def test_invalid_requests_never_reach_the_network(request_) -> None:
    calls: list = []
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_replying(_provider_body(), seen=calls)).dispatch(request_)
    assert error.value.kind is GeminiErrorKind.INVALID_REQUEST and calls == []


# ── response ─────────────────────────────────────────────────────────────────

def test_valid_assessment_is_correlated_by_the_backend() -> None:
    thought = {"text": "internal draft that is not JSON", "thought": True}
    callback = _client(_replying(_provider_body(extra_parts=[thought]))).dispatch(_request())
    assert (callback.event_id, callback.source_session_id) == ("event-abc", "session-xyz")
    assert callback.assessment.threat_score == 80 and callback.assessment.status is ThreatStatus.CRITICAL
    assert callback.assessment.subject_profile.visible_objects == ("motor",)


@pytest.mark.parametrize("content,kind", [
    (_provider_body(json.dumps(_assessment(80, "NORMAL"))), GeminiErrorKind.INVALID_RESPONSE),
    (_provider_body(json.dumps(_assessment(50, "CRITICAL"))), GeminiErrorKind.INVALID_RESPONSE),
    (_provider_body(json.dumps(_assessment(80.0))), GeminiErrorKind.INVALID_RESPONSE),
    (_provider_body("{not json"), GeminiErrorKind.INVALID_RESPONSE),
    (_provider_body(""), GeminiErrorKind.INVALID_RESPONSE),
    (_provider_body(json.dumps({**_assessment(), "event_id": "model-made"})), GeminiErrorKind.INVALID_RESPONSE),
    (_provider_body(json.dumps({k: v for k, v in _assessment().items() if k != "reasoning"})),
     GeminiErrorKind.INVALID_RESPONSE),
    (_provider_body(json.dumps(_assessment(reasoning="   "))), GeminiErrorKind.INVALID_RESPONSE),
    (json.dumps({"candidates": []}).encode(), GeminiErrorKind.INVALID_RESPONSE),
    (json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode(), GeminiErrorKind.PROMPT_BLOCKED),
    (b"", GeminiErrorKind.INVALID_RESPONSE),
    (b"<html>gateway</html>", GeminiErrorKind.INVALID_RESPONSE),
    pytest.param(b"[" + b" " * (256 * 1024) + b"]", GeminiErrorKind.RESPONSE_TOO_LARGE, id="oversized"),
])
def test_invalid_or_incomplete_responses_are_never_published(content, kind) -> None:
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_replying(content)).dispatch(_request())
    assert error.value.kind is kind
    assert error.value.invalid_response and not error.value.uncertain
    assert REASONING not in str(error.value) and KEY not in str(error.value)


@pytest.mark.parametrize("reason,kind", [
    ("MAX_TOKENS", GeminiErrorKind.TRUNCATED),
    *[(reason, GeminiErrorKind.SAFETY_STOP) for reason in (
        "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "ESCALATION")],
    *[(reason, GeminiErrorKind.OTHER_STOP) for reason in (
        "RECITATION", "LANGUAGE", "OTHER", "MALFORMED_RESPONSE", "UNEXPECTED_TOOL_CALL")],
    *[(reason, GeminiErrorKind.UNKNOWN_STOP) for reason in (
        None, "FINISH_REASON_UNSPECIFIED", "SOMETHING_NEW", 3, "stop")],
])
def test_finish_reasons_follow_the_documented_mapping(reason, kind) -> None:
    # Even a complete, valid assessment is not published unless finishReason is STOP.
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_replying(_provider_body(finish=reason))).dispatch(_request())
    assert error.value.kind is kind and error.value.invalid_response


@pytest.mark.parametrize("status,kind,uncertain", [
    (302, GeminiErrorKind.HTTP_REJECTED, False), (400, GeminiErrorKind.HTTP_REJECTED, False),
    (401, GeminiErrorKind.HTTP_REJECTED, False), (403, GeminiErrorKind.HTTP_REJECTED, False),
    (429, GeminiErrorKind.HTTP_REJECTED, False), (500, GeminiErrorKind.HTTP_SERVER_ERROR, True),
    (503, GeminiErrorKind.HTTP_SERVER_ERROR, True),
])
def test_http_errors_are_classified_sanitized_and_never_followed(status, kind, uncertain) -> None:
    calls: list = []
    secret_body = b'{"error": {"message": "quota for key synthetic"}}'
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_replying(secret_body, status=status, seen=calls)).dispatch(_request())
    assert (error.value.kind, error.value.status_code, len(calls)) == (kind, status, 1)
    assert error.value.uncertain is uncertain and not error.value.invalid_response
    assert "quota" not in str(error.value)


@pytest.mark.parametrize("exception,kind", [
    (httpx.ReadTimeout("slow"), GeminiErrorKind.TIMEOUT),
    (httpx.ConnectTimeout("slow"), GeminiErrorKind.TIMEOUT),
    (httpx.ConnectError("down"), GeminiErrorKind.CONNECTION_ERROR),
])
def test_transport_failures_are_definitive_and_not_retried(exception, kind) -> None:
    calls: list = []

    def handler(request):
        calls.append(request)
        raise exception
    with pytest.raises(GeminiEvaluationError) as error:
        _client(handler).dispatch(_request())
    assert error.value.kind is kind and len(calls) == 1
    assert error.value.uncertain and not error.value.invalid_response


# ── configuration and logging ────────────────────────────────────────────────

@pytest.mark.parametrize("overrides", [
    {"api_key": ""}, {"api_key": "   "}, {"api_key": "two words"}, {"model": ""}, {"model": "Gemini-Pro"},
    {"model": "models/gemini"}, {"model": "../x"}, {"model": "a" * 65}, {"timeout_seconds": 0},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")}, {"timeout_seconds": 56},
    {"timeout_seconds": True}, {"timeout_seconds": "soon"},
])
def test_invalid_configuration_is_rejected_without_echoing_it(overrides) -> None:
    arguments = {"api_key": KEY, "model": MODEL, "timeout_seconds": 20} | overrides
    with pytest.raises(GeminiEvaluationError) as error:
        GeminiEvaluationClient(**arguments)
    assert error.value.kind is GeminiErrorKind.INVALID_CONFIG and KEY not in str(error.value)


def test_environment_configuration_and_redacted_repr(monkeypatch) -> None:
    monkeypatch.setenv("SENTINEL_GEMINI_API_KEY", KEY)
    monkeypatch.setenv("SENTINEL_GEMINI_MODEL", MODEL)
    monkeypatch.delenv("SENTINEL_GEMINI_TIMEOUT_SECONDS", raising=False)
    client = GeminiEvaluationClient(http_client=httpx.Client())
    assert client.timeout_seconds == 30.0 and KEY not in repr(client) and MODEL in repr(client)
    monkeypatch.setenv("SENTINEL_GEMINI_TIMEOUT_SECONDS", "12.5")
    assert GeminiEvaluationClient(http_client=httpx.Client()).timeout_seconds == 12.5


def test_timing_log_has_durations_and_no_sensitive_content(caplog) -> None:
    ticks = iter([0.0, 0.002, 1.502, 1.503])                     # monotonic fake timer
    client = _client(_replying(_provider_body()), timer=lambda: next(ticks))
    with caplog.at_level(logging.INFO, logger="app.services.gemini_evaluation_client"):
        client.dispatch(_request())
    (message,) = caplog.messages
    assert "event=event-ab outcome=CRITICAL http=200" in message
    assert "prepare_ms=2.0 provider_ms=1500.0 validate_ms=1.0" in message
    encoded = base64.b64encode(JPEG).decode()
    for secret in (KEY, encoded, REASONING, "Never infer", "session-xyz"):
        assert secret not in caplog.text


def test_failed_call_still_logs_its_outcome_kind(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.gemini_evaluation_client"):
        with pytest.raises(GeminiEvaluationError):
            _client(_replying(b"{}", status=429)).dispatch(_request())
    assert "outcome=HTTP_REJECTED http=429" in caplog.messages[0] and KEY not in caplog.text


def test_local_request_errors_are_rejections() -> None:
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_replying(_provider_body())).dispatch(_request(frames=4))
    assert not error.value.uncertain and not error.value.invalid_response


def test_labels_use_actual_offsets_in_chronological_order() -> None:
    actual = (-1.97, -1.05, -0.38, 0.02, 0.41)                  # within the 0.3 s tolerance
    seen: list[httpx.Request] = []
    _client(_replying(_provider_body(), seen=seen)).dispatch(_request(actual=actual))
    parts = json.loads(seen[0].content)["contents"][0]["parts"]
    labels = [part["text"] for part in parts[1:] if "text" in part]
    assert labels == ["Frame 1 of 5, t=-1.97 s", "Frame 2 of 5, t=-1.05 s", "Frame 3 of 5, t=-0.38 s",
                      "Frame 4 of 5, t=+0.02 s", "Frame 5 of 5, t=+0.41 s"]


# ── bounded streaming reads ──────────────────────────────────────────────────

class TrackingStream(httpx.SyncByteStream):
    """Yields chunks one by one and records how many were consumed and closing."""

    def __init__(self, chunks: list[bytes], *, fail_at: int | None = None) -> None:
        self.chunks, self.fail_at = chunks, fail_at
        self.yielded = 0
        self.closed = False

    def __iter__(self):
        for index, chunk in enumerate(self.chunks):
            if index == self.fail_at:
                raise httpx.ReadError("stream cut")
            self.yielded += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


CHUNK = 4096


def _chunks(data: bytes) -> list[bytes]:
    return [data[index:index + CHUNK] for index in range(0, len(data), CHUNK)]


def _padded_body(size: int) -> bytes:
    body = _provider_body()
    return body[:-1] + b" " * (size - len(body)) + b"}"


def _streaming(stream: TrackingStream, status: int = 200, headers: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers or {}, stream=stream)
    return handler


class RecordingBuffer(bytearray):
    """Test instrumentation for the client's body buffer (patched into the module)."""

    created: list["RecordingBuffer"] = []

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.largest = 0
        self.largest_piece = 0
        RecordingBuffer.created.append(self)

    def extend(self, piece) -> None:
        self.largest_piece = max(self.largest_piece, len(piece))
        super().extend(piece)
        self.largest = max(self.largest, len(self))


@pytest.fixture
def body_buffer(monkeypatch):
    RecordingBuffer.created = []
    monkeypatch.setattr(gemini_evaluation_client, "bytearray", RecordingBuffer, raising=False)

    def single() -> RecordingBuffer:
        (buffer,) = RecordingBuffer.created
        return buffer
    return single


def test_body_of_exactly_the_limit_is_read_and_accepted(body_buffer) -> None:
    body = _padded_body(MAX_RESPONSE_BYTES)
    assert len(body) == MAX_RESPONSE_BYTES
    stream = TrackingStream(_chunks(body))
    callback = _client(_streaming(stream)).dispatch(_request())
    assert callback.assessment.threat_score == 80
    assert stream.yielded == len(stream.chunks) and stream.closed
    buffer = body_buffer()
    assert buffer.largest == MAX_RESPONSE_BYTES and buffer.largest_piece <= READ_CHUNK_BYTES


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "100"}, {"Content-Length": "not-a-number"}])
def test_reading_stops_before_the_buffer_crosses_the_limit(headers, body_buffer) -> None:
    body = _padded_body(MAX_RESPONSE_BYTES + 100 * 1024)       # no or misleading Content-Length
    stream = TrackingStream(_chunks(body))
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_streaming(stream, headers=headers)).dispatch(_request())
    assert error.value.kind is GeminiErrorKind.RESPONSE_TOO_LARGE
    buffer = body_buffer()
    assert buffer.largest <= MAX_RESPONSE_BYTES                 # never grew past the limit
    assert buffer.largest_piece <= READ_CHUNK_BYTES             # small, explicit read pieces
    # Reading stopped at the first piece that would cross the limit; the rest was not consumed.
    consumed_raw = (MAX_RESPONSE_BYTES // READ_CHUNK_BYTES + 1) * READ_CHUNK_BYTES
    assert stream.yielded == consumed_raw // CHUNK < len(stream.chunks)
    assert stream.closed


def test_declared_oversize_is_refused_before_reading() -> None:
    stream = TrackingStream(_chunks(_padded_body(MAX_RESPONSE_BYTES + CHUNK)))
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_streaming(stream, headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)})).dispatch(_request())
    assert error.value.kind is GeminiErrorKind.RESPONSE_TOO_LARGE
    assert stream.yielded == 0 and stream.closed


@pytest.mark.parametrize("status", [404, 500])
def test_error_bodies_are_never_read(status) -> None:
    stream = TrackingStream(_chunks(b"x" * (MAX_RESPONSE_BYTES * 2)))
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_streaming(stream, status=status)).dispatch(_request())
    assert error.value.status_code == status
    assert stream.yielded == 0 and stream.closed


def test_stream_error_is_uncertain_and_closes_the_response() -> None:
    stream = TrackingStream(_chunks(_padded_body(64 * 1024)), fail_at=3)
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_streaming(stream)).dispatch(_request())
    assert error.value.kind is GeminiErrorKind.CONNECTION_ERROR and error.value.uncertain
    assert stream.yielded == 3 and stream.closed


def test_gzip_is_decompressed_under_the_same_bound() -> None:
    stream = TrackingStream(_chunks(gzip.compress(_provider_body())))
    callback = _client(_streaming(stream, headers={"Content-Encoding": "gzip"})).dispatch(_request())
    assert callback.assessment.threat_score == 80 and stream.closed


def test_gzip_bomb_stops_at_the_decoded_limit(body_buffer) -> None:
    compressed = gzip.compress(b" " * (MAX_RESPONSE_BYTES * 64))  # tiny on the wire, 16 MiB decoded
    stream = TrackingStream(_chunks(compressed) + [b"\x00" * CHUNK] * 50)
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_streaming(stream, headers={"Content-Encoding": "gzip"})).dispatch(_request())
    assert error.value.kind is GeminiErrorKind.RESPONSE_TOO_LARGE
    assert body_buffer().largest <= MAX_RESPONSE_BYTES
    assert stream.yielded < len(stream.chunks) and stream.closed


def test_gzip_body_just_under_the_limit_keeps_the_buffer_bounded(body_buffer) -> None:
    body = _padded_body(MAX_RESPONSE_BYTES)
    stream = TrackingStream(_chunks(gzip.compress(body)))
    callback = _client(_streaming(stream, headers={"Content-Encoding": "gzip"})).dispatch(_request())
    assert callback.assessment.threat_score == 80
    assert body_buffer().largest == MAX_RESPONSE_BYTES and stream.closed


def test_unsupported_encoding_is_refused_before_reading_even_a_valid_body() -> None:
    stream = TrackingStream(_chunks(_provider_body()))
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_streaming(stream, headers={"Content-Encoding": "br"})).dispatch(_request())
    assert error.value.kind is GeminiErrorKind.INVALID_RESPONSE
    assert stream.yielded == 0 and stream.closed


@pytest.mark.parametrize("headers,body", [
    ({"Content-Encoding": "gzip"}, b"not gzip at all"),
    ({"Content-Encoding": "gzip"}, gzip.compress(_provider_body())[:-12]),   # truncated gzip
])
def test_unsupported_or_corrupt_encoding_is_an_invalid_response(headers, body) -> None:
    stream = TrackingStream(_chunks(body))
    with pytest.raises(GeminiEvaluationError) as error:
        _client(_streaming(stream, headers=headers)).dispatch(_request())
    assert error.value.kind is GeminiErrorKind.INVALID_RESPONSE and stream.closed
