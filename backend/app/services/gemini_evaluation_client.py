"""Direct Gemini evaluation transport (REST ``models.generateContent``).

Separate from the Langflow Webhook client: one HTTPS request returns the
assessment itself, which the worker publishes through the same acceptance path
as a callback (``EvaluationWorker.accept_callback``). Nothing here retries,
falls back to another transport, or logs the key, prompt, images, payload or
response body. Correlation IDs come from the backend request, never from the
model, and the model output must contain exactly the assessment fields.

Contract verified against the official Gemini API reference (generateContent):
``POST /v1beta/models/{model}:generateContent`` with ``x-goog-api-key``;
images as ``parts[].inlineData{mimeType, data(base64)}``; structured output via
``generationConfig.responseFormat.text{mimeType: application/json, schema}``;
text in ``candidates[0].content.parts[].text`` (``thought`` parts skipped),
``candidates[0].finishReason`` and ``promptFeedback.blockReason``. The newer
Interactions API is not used: its compatibility was not assumed, and this
stateless endpoint keeps no server-side interaction record.

The response is streamed in ``READ_CHUNK_BYTES`` pieces and the accumulated
body buffer never exceeds ``MAX_RESPONSE_BYTES``: each piece is checked before
it is appended, and reading stops (the response is closed) as soon as the next
piece would cross the limit. Error bodies are never read. ``identity`` encoding
is requested; a gzip body is decompressed incrementally with the same output
bound, and any other encoding is refused. This bounds this client's body
buffer only, not process memory or the network stack's internal buffers
(httpx/the transport may hold one raw read before it is re-chunked).

Failure classes (``GeminiEvaluationError``): ``rejected`` (local request
error, redirect or HTTP 4xx: the provider did not run the evaluation),
``uncertain`` (timeout, connection/stream error, HTTP 5xx: whether the
provider processed it is unknown), ``invalid_response`` (an answer arrived but
is not a publishable assessment). The worker closes the pending event for all
of them because this transport has no later callback; it never retries.
"""

from __future__ import annotations

import base64
import enum
import zlib
import json
import logging
import math
import os
import re
import time
from typing import Any

import httpx
from pydantic import ValidationError

from app.models.domain import DomainName
from app.models.evaluation import EvaluationCallback, EvaluationCallbackPayload, EvaluationRequest

logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 55.0
MAX_RESPONSE_BYTES = 256 * 1024
READ_CHUNK_BYTES = 16 * 1024
MAX_BUNDLE_JPEG_BYTES = 12 * 1024 * 1024        # base64 stays well below the 20 MB request cap
_MODEL = re.compile(r"[a-z0-9][a-z0-9.\-]{0,63}")
_ASSESSMENT_FIELDS = {"threatScore", "status", "reasoning", "suspectProfile"}

# JSON Schema subset documented for structured output (type, properties,
# required, additionalProperties, enum, minimum, maximum, items, maxItems,
# description). The score band rule itself is enforced by validate().
ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "threatScore": {"type": "integer", "minimum": 0, "maximum": 100,
                        "description": "0-34 NORMAL, 35-64 WARNING, 65-100 CRITICAL."},
        "status": {"type": "string", "enum": ["NORMAL", "WARNING", "CRITICAL"],
                   "description": "Must match the threatScore band."},
        "reasoning": {"type": "string",
                      "description": "Short Indonesian explanation based only on visible frames."},
        "suspectProfile": {
            "type": "object",
            "properties": {
                "personDetected": {"type": "boolean"},
                "clothing": {"type": "string"},
                "observedAction": {"type": "string"},
                "visibleObjects": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
                "distinguishingFeatures": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
            },
            "required": ["personDetected", "clothing", "observedAction", "visibleObjects",
                         "distinguishingFeatures"],
            "additionalProperties": False,
        },
    },
    "required": ["threatScore", "status", "reasoning", "suspectProfile"],
    "additionalProperties": False,
}

_COMMON_RULES = (
    "You review five CCTV frames from one fixed camera, in chronological order; each image "
    "is preceded by its time relative to the trigger moment. {context} "
    "Judge only actions that are visible across the sequence. If something is unclear, "
    "occluded or not visible, say so explicitly (for example 'tidak terlihat jelas') instead "
    "of guessing. Never infer identity, ownership, intent or a crime from clothing colour, "
    "appearance or mere presence. Score 0-34 NORMAL, 35-64 WARNING, 65-100 CRITICAL, and the "
    "status must match the score band. Write reasoning, clothing and observedAction in "
    "Indonesian. clothing and distinguishingFeatures describe only visible, non-identifying "
    "details; visibleObjects lists objects actually visible. Return only the JSON object "
    "defined by the response schema."
)
_DOMAIN_CONTEXT = {
    DomainName.PARKING_MOTORCYCLE: (
        "Context: a motorcycle parking area; a person stayed near a parked motorcycle. Walking "
        "past, parking, sitting on or riding a motorcycle are normal: ownership cannot be seen. "
        "Raise the score only for clearly visible tampering, such as forcing a lock, handling "
        "the ignition or wiring, or pushing the motorcycle away while it is still locked."
    ),
    DomainName.RETAIL_STORE: (
        "Context: a store display area; a person stayed near merchandise. Browsing, picking up "
        "and putting back items, holding clothes against the body and staff arranging stock "
        "are normal. Payment and ownership are not visible and must not be inferred. Raise the "
        "score only for clearly visible concealment of merchandise in a bag or under clothing."
    ),
}


class GeminiErrorKind(str, enum.Enum):
    # rejected: the provider did not run (or never received) the evaluation
    INVALID_CONFIG = "INVALID_CONFIG"
    INVALID_REQUEST = "INVALID_REQUEST"
    HTTP_REJECTED = "HTTP_REJECTED"            # redirect or 4xx (incl. 429)
    # uncertain: the provider may or may not have processed the request
    TIMEOUT = "TIMEOUT"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    HTTP_SERVER_ERROR = "HTTP_SERVER_ERROR"    # 5xx
    # invalid response: an answer arrived but is not a publishable assessment
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    PROMPT_BLOCKED = "PROMPT_BLOCKED"          # promptFeedback.blockReason
    SAFETY_STOP = "SAFETY_STOP"                # safety/policy finishReason
    TRUNCATED = "TRUNCATED"                    # MAX_TOKENS
    OTHER_STOP = "OTHER_STOP"                  # another documented finishReason
    UNKNOWN_STOP = "UNKNOWN_STOP"              # missing, unspecified or undocumented


_UNCERTAIN = frozenset({GeminiErrorKind.TIMEOUT, GeminiErrorKind.CONNECTION_ERROR,
                        GeminiErrorKind.HTTP_SERVER_ERROR})
_INVALID_RESPONSE = frozenset({
    GeminiErrorKind.RESPONSE_TOO_LARGE, GeminiErrorKind.INVALID_RESPONSE, GeminiErrorKind.PROMPT_BLOCKED,
    GeminiErrorKind.SAFETY_STOP, GeminiErrorKind.TRUNCATED, GeminiErrorKind.OTHER_STOP,
    GeminiErrorKind.UNKNOWN_STOP,
})
# Candidate.finishReason values from the official generateContent reference.
_SAFETY_FINISH = frozenset({"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY",
                            "IMAGE_PROHIBITED_CONTENT", "ESCALATION", "PUP_LIMITED_DISABLED"})
_OTHER_FINISH = frozenset({"RECITATION", "LANGUAGE", "OTHER", "MALFORMED_FUNCTION_CALL", "IMAGE_OTHER",
                           "NO_IMAGE", "IMAGE_RECITATION", "UNEXPECTED_TOOL_CALL", "TOO_MANY_TOOL_CALLS",
                           "MISSING_THOUGHT_SIGNATURE", "MALFORMED_RESPONSE"})


class GeminiEvaluationError(RuntimeError):
    """Sanitized failure (no key, URL, prompt, image or body); see module docstring."""

    def __init__(self, kind: GeminiErrorKind, *, status_code: int | None = None) -> None:
        self.kind = kind
        self.status_code = status_code
        self.uncertain = kind in _UNCERTAIN
        self.invalid_response = kind in _INVALID_RESPONSE
        suffix = f" ({status_code})" if status_code is not None else ""
        super().__init__(f"Gemini evaluation {kind.value}{suffix}")


class GeminiEvaluationClient:
    """One generateContent call per event; returns a validated callback."""

    name = "gemini-direct"
    returns_assessment = True

    def __init__(
        self, *, api_key: str | None = None, model: str | None = None,
        timeout_seconds: float | str | None = None, http_client: httpx.Client | None = None,
        timer=time.perf_counter,
    ) -> None:
        key = api_key if api_key is not None else os.getenv("SENTINEL_GEMINI_API_KEY", "")
        configured_model = model if model is not None else os.getenv("SENTINEL_GEMINI_MODEL", "")
        raw_timeout = (timeout_seconds if timeout_seconds is not None
                       else os.getenv("SENTINEL_GEMINI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
        try:
            timeout = float(raw_timeout)
            if (not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key.strip())
                    or not isinstance(configured_model, str)
                    or _MODEL.fullmatch(configured_model) is None
                    or isinstance(raw_timeout, bool) or not math.isfinite(timeout)
                    or not 0 < timeout <= MAX_TIMEOUT_SECONDS):
                raise ValueError
        except (TypeError, ValueError):
            raise GeminiEvaluationError(GeminiErrorKind.INVALID_CONFIG) from None
        self._key = key.strip()
        self._model = configured_model
        self.timeout_seconds = timeout
        self._url = f"{API_ROOT}/{configured_model}:generateContent"
        self._timer = timer
        self._client = http_client if http_client is not None else httpx.Client(follow_redirects=False)
        self._owns_client = http_client is None

    def __repr__(self) -> str:
        return f"GeminiEvaluationClient(model={self._model!r})"

    def dispatch(self, request: EvaluationRequest) -> EvaluationCallback:
        """Return the backend-correlated, validated assessment or raise."""
        started = self._timer()
        prepared: float | None = None
        answered: float | None = None
        status_code: int | None = None
        outcome = "error"
        try:
            body = self._encode(request)
            prepared = self._timer()
            try:
                with self._client.stream(
                    "POST", self._url, content=body, timeout=self.timeout_seconds, follow_redirects=False,
                    headers={"Content-Type": "application/json", "Accept": "application/json",
                             "Accept-Encoding": "identity", "x-goog-api-key": self._key},
                ) as response:
                    status_code = response.status_code
                    if not 200 <= response.status_code < 300:   # error body is never read
                        kind = (GeminiErrorKind.HTTP_SERVER_ERROR if response.status_code >= 500
                                else GeminiErrorKind.HTTP_REJECTED)
                        raise GeminiEvaluationError(kind, status_code=response.status_code)
                    raw = self._read_bounded(response)
            except httpx.TimeoutException:
                raise GeminiEvaluationError(GeminiErrorKind.TIMEOUT) from None
            except httpx.RequestError:
                raise GeminiEvaluationError(GeminiErrorKind.CONNECTION_ERROR) from None
            except httpx.StreamError:
                raise GeminiEvaluationError(GeminiErrorKind.INVALID_RESPONSE) from None
            finally:
                answered = self._timer()
            callback = self._parse(raw, request)
            outcome = callback.assessment.status.value
            return callback
        except GeminiEvaluationError as error:
            outcome = error.kind.value
            raise
        finally:
            finished = self._timer()
            self._log_timing(
                request, outcome, status_code,
                (prepared if prepared is not None else finished) - started,
                answered - prepared if answered is not None and prepared is not None else 0.0,
                finished - answered if answered is not None else 0.0,
            )

    # ── request ──────────────────────────────────────────────────────────────

    def _encode(self, request: EvaluationRequest) -> bytes:
        if not isinstance(request, EvaluationRequest) or request.domain not in _DOMAIN_CONTEXT:
            raise GeminiEvaluationError(GeminiErrorKind.INVALID_REQUEST)
        bundle = request.frame_bundle
        frames = bundle.frames
        if (bundle.event_id != request.event_id or bundle.source_session_id != request.source_session_id
                or bundle.domain is not request.domain or len(frames) != 5
                or len({frame.source_frame_seq for frame in frames}) != 5
                or any(left.source_timestamp >= right.source_timestamp for left, right in zip(frames, frames[1:]))
                or any(not isinstance(frame.jpeg, bytes) or not frame.jpeg.startswith(b"\xff\xd8")
                       or not frame.jpeg.endswith(b"\xff\xd9") or frame.mime_type != "image/jpeg"
                       for frame in frames)
                or bundle.total_jpeg_bytes != sum(len(frame.jpeg) for frame in frames)
                or bundle.total_jpeg_bytes > MAX_BUNDLE_JPEG_BYTES):
            raise GeminiEvaluationError(GeminiErrorKind.INVALID_REQUEST)
        parts: list[dict[str, Any]] = [
            {"text": _COMMON_RULES.format(context=_DOMAIN_CONTEXT[request.domain])},
        ]
        for index, frame in enumerate(frames, 1):
            parts.append({"text": f"Frame {index} of 5, t={frame.actual_offset_seconds:+.2f} s"})
            parts.append({"inlineData": {"mimeType": "image/jpeg",
                                         "data": base64.b64encode(frame.jpeg).decode("ascii")}})
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseFormat": {"text": {"mimeType": "application/json", "schema": ASSESSMENT_SCHEMA}},
            },
        }
        try:
            return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            raise GeminiEvaluationError(GeminiErrorKind.INVALID_REQUEST) from None

    # ── response ─────────────────────────────────────────────────────────────

    @staticmethod
    def _read_bounded(response: httpx.Response) -> bytes:
        """Return the decoded body; the buffer never grows past MAX_RESPONSE_BYTES."""
        too_large = GeminiEvaluationError(GeminiErrorKind.RESPONSE_TOO_LARGE)
        declared = response.headers.get("content-length")
        if declared is not None and declared.strip().isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            raise too_large                     # extra check only; the stream bound below is authoritative
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        if encoding not in ("identity", "gzip"):
            raise GeminiEvaluationError(GeminiErrorKind.INVALID_RESPONSE)
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        body = bytearray()

        def append(piece: bytes) -> None:
            if len(body) + len(piece) > MAX_RESPONSE_BYTES:   # checked before growing the buffer
                raise too_large
            body.extend(piece)

        try:
            # raw: no implicit, unbounded decompression; small explicit pieces
            for chunk in response.iter_raw(chunk_size=READ_CHUNK_BYTES):
                if decoder is not None:
                    # Decoded output per piece is capped one byte past the remaining room.
                    chunk = decoder.decompress(chunk, MAX_RESPONSE_BYTES + 1 - len(body))
                    if decoder.unconsumed_tail:
                        raise too_large
                append(chunk)
            if decoder is not None:
                append(decoder.flush(MAX_RESPONSE_BYTES + 1 - len(body)))
                if not decoder.eof:
                    raise GeminiEvaluationError(GeminiErrorKind.INVALID_RESPONSE)
        except zlib.error:
            raise GeminiEvaluationError(GeminiErrorKind.INVALID_RESPONSE) from None
        return bytes(body)

    @staticmethod
    def _parse(raw: bytes, request: EvaluationRequest) -> EvaluationCallback:
        invalid = GeminiEvaluationError(GeminiErrorKind.INVALID_RESPONSE)
        if not raw:
            raise invalid
        try:
            document = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise invalid from None
        if not isinstance(document, dict):
            raise invalid
        feedback = document.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise GeminiEvaluationError(GeminiErrorKind.PROMPT_BLOCKED)
        candidates = document.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise invalid
        candidate = candidates[0]
        reason = candidate.get("finishReason")
        if reason != "STOP":
            raise GeminiEvaluationError(
                GeminiErrorKind.TRUNCATED if reason == "MAX_TOKENS"
                else GeminiErrorKind.SAFETY_STOP if reason in _SAFETY_FINISH
                else GeminiErrorKind.OTHER_STOP if reason in _OTHER_FINISH
                else GeminiErrorKind.UNKNOWN_STOP)
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise invalid
        text = "".join(part["text"] for part in parts
                       if isinstance(part, dict) and not part.get("thought")
                       and isinstance(part.get("text"), str))
        try:
            fields = json.loads(text)
        except ValueError:
            raise invalid from None
        if not isinstance(fields, dict) or set(fields) != _ASSESSMENT_FIELDS:
            raise invalid                      # the model must not supply IDs or extras
        try:
            # Same strict wire contract and validate() as the HTTP callback; IDs from the backend.
            return EvaluationCallbackPayload.model_validate(
                {"event_id": request.event_id, "source_session_id": request.source_session_id, **fields},
            ).to_callback()
        except (ValidationError, ValueError, TypeError):
            raise invalid from None

    def _log_timing(self, request: object, outcome: str, status_code: int | None,
                    prepare: float, provider: float, validate: float) -> None:
        try:
            event = getattr(request, "event_id", "")
            domain = getattr(getattr(request, "domain", None), "value", "-")
            succeeded = outcome in ("NORMAL", "WARNING", "CRITICAL")
            logger.log(
                logging.INFO if succeeded else logging.WARNING,
                "gemini evaluation event=%s domain=%s outcome=%s http=%s prepare_ms=%.1f provider_ms=%.1f "
                "validate_ms=%.1f", str(event)[:8], domain, outcome,
                status_code if status_code is not None else "-",
                prepare * 1000, provider * 1000, validate * 1000,
            )
        except Exception:
            pass                                 # observability never changes the outcome

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
