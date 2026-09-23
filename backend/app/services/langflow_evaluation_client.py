"""Isolated Langflow v1 run client. Never logs, persists, or retries image data."""

from __future__ import annotations

import base64
import dataclasses
import enum
import json
import math
import os
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

from app.models.evaluation import EvaluationRequest, SubjectProfile, ThreatAssessment, ThreatStatus


class LangflowErrorKind(str, enum.Enum):
    INVALID_CONFIG = "INVALID_CONFIG"
    INVALID_REQUEST = "INVALID_REQUEST"
    HTTP_STATUS = "HTTP_STATUS"
    TIMEOUT = "TIMEOUT"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    MALFORMED_JSON = "MALFORMED_JSON"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class LangflowEvaluationError(RuntimeError):
    """Safe diagnostic: never embeds URL, key, response body, or frame data."""

    def __init__(self, kind: LangflowErrorKind, *, status_code: int | None = None) -> None:
        self.kind = kind
        self.status_code = status_code
        suffix = f" ({status_code})" if status_code is not None else ""
        super().__init__(f"Langflow evaluation {kind.value}{suffix}")


class LangflowEvaluationClient:
    """Synchronous EvaluationWorker client for a complete Langflow run URL."""

    name = "langflow-http"
    DEFAULT_COMPONENT_ID = "Webhook-exTZU"
    DEFAULT_MODEL = "gemini-3-flash-preview"

    def __init__(
        self, *, url: str | None = None, api_key: str | None = None,
        timeout_seconds: float | None = None, component_id: str | None = None,
        model: str | None = None, http_client: httpx.Client | None = None,
    ) -> None:
        configured_url = url if url is not None else os.getenv("SENTINEL_LANGFLOW_URL", "")
        configured_key = api_key if api_key is not None else os.getenv("SENTINEL_LANGFLOW_API_KEY", "")
        raw_timeout = (timeout_seconds if timeout_seconds is not None else
                       os.getenv("SENTINEL_LANGFLOW_TIMEOUT_SECONDS", "30"))
        configured_component = (component_id if component_id is not None else
                                os.getenv("SENTINEL_LANGFLOW_COMPONENT_ID", self.DEFAULT_COMPONENT_ID))
        configured_model = (model if model is not None else
                            os.getenv("SENTINEL_LANGFLOW_MODEL", self.DEFAULT_MODEL))
        try:
            parsed = urlsplit(configured_url)
            timeout = float(raw_timeout)
            if (parsed.scheme not in ("http", "https") or not parsed.netloc
                    or parsed.username is not None or parsed.password is not None
                    or re.fullmatch(r"/api/v1/run/[^/]+/?", parsed.path) is None
                    or parsed.query or parsed.fragment
                    or not math.isfinite(timeout) or timeout <= 0
                    or not isinstance(configured_component, str) or not configured_component.strip()
                    or not isinstance(configured_model, str) or not configured_model.strip()):
                raise ValueError
        except (ValueError, TypeError):
            raise LangflowEvaluationError(LangflowErrorKind.INVALID_CONFIG) from None
        self._url = configured_url
        self._api_key = configured_key.strip()
        self._timeout = timeout
        self._component_id = configured_component.strip()
        self._model = configured_model.strip()
        self._client = http_client if http_client is not None else httpx.Client(follow_redirects=False)
        self._owns_client = http_client is None

    def evaluate(self, request: EvaluationRequest) -> ThreatAssessment:
        payload = self._build_payload(request)
        try:
            encoded_payload = json.dumps(
                payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise LangflowEvaluationError(LangflowErrorKind.INVALID_REQUEST) from None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        try:
            response = self._client.post(
                self._url, content=encoded_payload, headers=headers, timeout=self._timeout,
            )
        except httpx.TimeoutException:
            raise LangflowEvaluationError(LangflowErrorKind.TIMEOUT) from None
        except httpx.RequestError:
            raise LangflowEvaluationError(LangflowErrorKind.CONNECTION_ERROR) from None
        if response.status_code < 200 or response.status_code >= 300:
            raise LangflowEvaluationError(
                LangflowErrorKind.HTTP_STATUS, status_code=response.status_code,
            )
        if not response.content or not response.content.strip():
            raise LangflowEvaluationError(LangflowErrorKind.EMPTY_RESPONSE)
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            raise LangflowEvaluationError(LangflowErrorKind.MALFORMED_JSON) from None
        text = self._extract_output_text(body)
        if not text.strip():
            raise LangflowEvaluationError(LangflowErrorKind.EMPTY_RESPONSE)
        normalized = self._strip_json_fence(text)
        try:
            assessment_data = json.loads(normalized)
        except (ValueError, TypeError):
            raise LangflowEvaluationError(LangflowErrorKind.MALFORMED_JSON) from None
        return self._parse_assessment(assessment_data)

    def _build_payload(self, request: EvaluationRequest) -> dict[str, Any]:
        bundle = request.frame_bundle
        if (bundle.event_id != request.event_id or bundle.source_session_id != request.source_session_id
                or bundle.domain is not request.domain or len(bundle.frames) != 5
                or len({frame.source_frame_seq for frame in bundle.frames}) != 5
                or any(not isinstance(frame.jpeg, bytes) or not frame.jpeg or frame.mime_type != "image/jpeg"
                       for frame in bundle.frames)):
            raise LangflowEvaluationError(LangflowErrorKind.INVALID_REQUEST)
        try:
            evidence: Any = (dataclasses.asdict(request.evidence)
                             if dataclasses.is_dataclass(request.evidence) else request.evidence)
            event_data = {
                "event_id": request.event_id,
                "source_session_id": request.source_session_id,
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
            }
            metadata = json.dumps(
                event_data, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            )
            prompt = (
                "Analyze the five CCTV frames as observations, not proof of a crime. "
                "Return only JSON with threatScore (0-100), status (NORMAL, WARNING, or CRITICAL), "
                "reasoning, and suspectProfile containing personDetected, clothing, observedAction, "
                "visibleObjects, and distinguishingFeatures. "
                "Use NORMAL for 0-34, WARNING for 35-64, CRITICAL for 65-100. "
                "Do not infer identity, ownership, or guilt. Event metadata: " + metadata
            )
            prompt = " ".join(prompt.splitlines())
            provider_payload = {
                "input": [
                    {"type": "text", "text": prompt},
                    *(
                        {"type": "image", "data": "data:image/jpeg;base64,"
                         + base64.b64encode(frame.jpeg).decode("ascii")}
                        for frame in bundle.frames
                    ),
                ],
                "model": self._model,
            }
        except (TypeError, ValueError):
            raise LangflowEvaluationError(LangflowErrorKind.INVALID_REQUEST) from None
        return {
            "input_value": "cctv_event",
            "input_type": "text",
            "output_type": "chat",
            "session_id": request.event_id,
            "tweaks": {self._component_id: {"data": provider_payload}},
        }

    @staticmethod
    def _extract_output_text(body: Any) -> str:
        if not isinstance(body, Mapping):
            raise LangflowEvaluationError(LangflowErrorKind.INVALID_RESPONSE)
        outputs = body.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise LangflowEvaluationError(LangflowErrorKind.EMPTY_RESPONSE)
        for outer in outputs:
            if not isinstance(outer, Mapping):
                continue
            inner_outputs = outer.get("outputs")
            if not isinstance(inner_outputs, list):
                continue
            for inner in inner_outputs:
                if not isinstance(inner, Mapping):
                    continue
                results = inner.get("results")
                message = results.get("message") if isinstance(results, Mapping) else None
                if isinstance(message, str) and message.strip():
                    return message
                if isinstance(results, Mapping) and isinstance(results.get("text"), str):
                    text = results["text"]
                    if text.strip():
                        return text
                if message is None:
                    message = inner.get("message")
                if isinstance(message, Mapping):
                    text = message.get("text")
                    if not isinstance(text, str) and isinstance(message.get("data"), Mapping):
                        text = message["data"].get("text")
                    if isinstance(text, str) and text.strip():
                        return text
        raise LangflowEvaluationError(LangflowErrorKind.EMPTY_RESPONSE)

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            first_newline = stripped.find("\n")
            if first_newline >= 0:
                language = stripped[3:first_newline].strip().lower()
                if language in ("", "json"):
                    return stripped[first_newline + 1:-3].strip()
        return stripped

    @staticmethod
    def _parse_assessment(data: Any) -> ThreatAssessment:
        if not isinstance(data, Mapping):
            raise LangflowEvaluationError(LangflowErrorKind.INVALID_RESPONSE)
        profile_data = data.get("subject_profile", data.get("suspectProfile"))
        if not isinstance(profile_data, Mapping):
            raise LangflowEvaluationError(LangflowErrorKind.INVALID_RESPONSE)
        try:
            score = data.get("threat_score", data.get("threatScore"))
            status = ThreatStatus(data.get("status"))
            profile = SubjectProfile(
                person_detected=profile_data.get("person_detected", profile_data.get("personDetected")),
                clothing=profile_data.get("clothing"),
                observed_action=profile_data.get("observed_action", profile_data.get("observedAction")),
                visible_objects=LangflowEvaluationClient._string_tuple(
                    profile_data.get("visible_objects", profile_data.get("visibleObjects")),
                ),
                distinguishing_features=LangflowEvaluationClient._string_tuple(
                    profile_data.get("distinguishing_features", profile_data.get("distinguishingFeatures")),
                ),
            )
            assessment = ThreatAssessment(score, status, data.get("reasoning"), profile)
            assessment.validate()
            return assessment
        except (ValueError, TypeError, AttributeError):
            raise LangflowEvaluationError(LangflowErrorKind.INVALID_RESPONSE) from None

    @staticmethod
    def _string_tuple(value: Any) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or any(not isinstance(item, str) for item in value):
            raise ValueError("invalid profile list")
        return tuple(value)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
