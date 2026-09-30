"""Isolated Langflow Webhook dispatcher. Never logs, persists, or retries frames."""

from __future__ import annotations

import base64
import dataclasses
import enum
import json
import math
import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models.evaluation import EvaluationRequest


class LangflowErrorKind(str, enum.Enum):
    INVALID_CONFIG = "INVALID_CONFIG"
    INVALID_REQUEST = "INVALID_REQUEST"
    HTTP_STATUS = "HTTP_STATUS"
    TIMEOUT = "TIMEOUT"
    CONNECTION_ERROR = "CONNECTION_ERROR"


class LangflowEvaluationError(RuntimeError):
    """Sanitized error; never includes URL, key, body, prompt, or image data."""

    def __init__(self, kind: LangflowErrorKind, *, status_code: int | None = None) -> None:
        self.kind = kind
        self.status_code = status_code
        suffix = f" ({status_code})" if status_code is not None else ""
        super().__init__(f"Langflow dispatch {kind.value}{suffix}")


class LangflowDispatchRejected(LangflowEvaluationError):
    """Request definitely not accepted; worker must fail budget and candidate."""


class LangflowDispatchUncertain(LangflowEvaluationError):
    """Remote flow may have started; worker must await callback without retry."""


class LangflowEvaluationClient:
    """POST one five-frame event to a complete Langflow Webhook URL."""

    name = "langflow-webhook"
    DEFAULT_MODEL = "gemini-3-flash-preview"

    def __init__(
        self, *, url: str | None = None, api_key: str | None = None,
        timeout_seconds: float | None = None, model: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        configured_url = url if url is not None else os.getenv("SENTINEL_LANGFLOW_URL", "")
        configured_key = api_key if api_key is not None else os.getenv("SENTINEL_LANGFLOW_API_KEY", "")
        raw_timeout = (timeout_seconds if timeout_seconds is not None else
                       os.getenv("SENTINEL_LANGFLOW_TIMEOUT_SECONDS", "5"))
        configured_model = (model if model is not None else
                            os.getenv("SENTINEL_LANGFLOW_MODEL", self.DEFAULT_MODEL))
        try:
            parsed = urlsplit(configured_url)
            timeout = float(raw_timeout)
            if (parsed.scheme not in ("http", "https") or not parsed.netloc
                    or parsed.username is not None or parsed.password is not None
                    or re.fullmatch(r"/api/v1/webhook/[^/]+/?", parsed.path) is None
                    or parsed.query or parsed.fragment
                    or not math.isfinite(timeout) or not 0 < timeout <= 10
                    or not isinstance(configured_key, str)
                    or not isinstance(configured_model, str) or not configured_model.strip()):
                raise ValueError
        except (ValueError, TypeError):
            raise LangflowDispatchRejected(LangflowErrorKind.INVALID_CONFIG) from None
        self._url = configured_url
        self._api_key = configured_key.strip()
        self._timeout = timeout
        self._model = configured_model.strip()
        self._client = http_client if http_client is not None else httpx.Client(follow_redirects=False)
        self._owns_client = http_client is None

    def dispatch(self, request: EvaluationRequest) -> None:
        """A 2xx is only an ACK. The assessment arrives via a separate callback."""
        payload = self._build_payload(request)
        try:
            encoded_payload = json.dumps(
                payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise LangflowDispatchRejected(LangflowErrorKind.INVALID_REQUEST) from None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        try:
            response = self._client.post(
                self._url, content=encoded_payload, headers=headers,
                timeout=self._timeout, follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise LangflowDispatchUncertain(LangflowErrorKind.TIMEOUT) from None
        except httpx.RequestError:
            raise LangflowDispatchUncertain(LangflowErrorKind.CONNECTION_ERROR) from None
        if 200 <= response.status_code < 300:
            return
        error_type = (LangflowDispatchUncertain if response.status_code >= 500
                      else LangflowDispatchRejected)
        raise error_type(LangflowErrorKind.HTTP_STATUS, status_code=response.status_code)

    def _build_payload(self, request: EvaluationRequest) -> dict[str, Any]:
        if not isinstance(request, EvaluationRequest):
            raise LangflowDispatchRejected(LangflowErrorKind.INVALID_REQUEST)
        bundle = request.frame_bundle
        if (bundle.event_id != request.event_id or bundle.source_session_id != request.source_session_id
                or bundle.domain is not request.domain or len(bundle.frames) != 5
                or len({frame.source_frame_seq for frame in bundle.frames}) != 5
                or any(not isinstance(frame.jpeg, bytes) or not frame.jpeg.startswith(b"\xff\xd8")
                       or not frame.jpeg.endswith(b"\xff\xd9") or frame.mime_type != "image/jpeg"
                       for frame in bundle.frames)
                or bundle.total_jpeg_bytes != sum(len(frame.jpeg) for frame in bundle.frames)):
            raise LangflowDispatchRejected(LangflowErrorKind.INVALID_REQUEST)
        try:
            evidence: Any = (dataclasses.asdict(request.evidence)
                             if dataclasses.is_dataclass(request.evidence) else request.evidence)
            metadata = json.dumps({
                "domain": request.domain.value,
                "trigger": {
                    "started_at": request.started_at,
                    "triggered_at": request.triggered_at,
                    "person_track_id": request.person_track_id,
                    "object_track_id": request.object_track_id,
                    "evidence": evidence,
                },
                "frames": [
                    {
                        "source_frame_seq": frame.source_frame_seq,
                        "source_timestamp": frame.source_timestamp,
                        "target_offset_seconds": frame.target_offset_seconds,
                        "actual_offset_seconds": frame.actual_offset_seconds,
                        "deviation_seconds": frame.deviation_seconds,
                        "frame_width": frame.frame_width,
                        "frame_height": frame.frame_height,
                        "mime_type": frame.mime_type,
                    } for frame in bundle.frames
                ],
            }, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            prompt = (
                "Analyze the five CCTV frames as observations, not proof of a crime. "
                "Return only JSON with threatScore (integer 0-100), status (NORMAL, WARNING, or CRITICAL), "
                "reasoning, and suspectProfile containing personDetected, clothing, observedAction, "
                "visibleObjects, and distinguishingFeatures. "
                "Use NORMAL for 0-34, WARNING for 35-64, CRITICAL for 65-100. "
                "Do not infer identity, ownership, or guilt. Event metadata: " + metadata
            )
            prompt = " ".join(prompt.splitlines())
            return {
                "event_id": request.event_id,
                "source_session_id": request.source_session_id,
                "model": self._model,
                "input": [
                    {"type": "text", "text": prompt},
                    *(
                        {"type": "image", "data": base64.b64encode(frame.jpeg).decode("ascii"),
                         "mime_type": "image/jpeg"}
                        for frame in bundle.frames
                    ),
                ],
            }
        except (TypeError, ValueError, AttributeError):
            raise LangflowDispatchRejected(LangflowErrorKind.INVALID_REQUEST) from None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
