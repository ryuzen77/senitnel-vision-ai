"""Production composition root for the single-worker application process.

Parking mode (T3c): ``SENTINEL_PARKING_MODE`` unset/``legacy`` keeps today's legacy
parking trigger; ``private`` with a valid ``SENTINEL_PRIVATE_PARKING_CONFIG_FILE`` wires the
private-parking coordinator, evidence sampler and frame tee (detector and tee required);
any other value, or a missing or invalid private configuration, wires an explicitly
disabled candidate path — never a fallback to legacy. Only that selection lives in
:func:`build_parking_wiring`; the rest of this module is unchanged. Paths and configuration
contents are never logged.

Private evaluation (T3d): :func:`evaluation_worker_enabled` decides the worker's
``enabled`` flag. Legacy mode keeps the evaluation configuration's decision; the disabled
mode never evaluates; private mode evaluates only when ``SENTINEL_PRIVATE_PARKING_EVALUATION``
is ``true`` (unset/``false`` = off, any other value = off with a warning), the selected
transport is ``gemini`` (the only one with the private prompt; Langflow = off with a
warning) and the existing Gemini configuration is ready. Enabling it is a configuration
change only; a live request still needs the owner's permission.
"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import hashlib
import logging
import math
import os
from pathlib import Path
import re
import sys
import threading
import time

from app.models.domain import DomainName
from app.models.evaluation import EvaluationRequest
from app.models.private_parking import MAX_CONFIG_BYTES, PrivateParkingConfig, parse_private_parking_config
from app.services.clip_scene_runner import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_REVISION,
    make_clip_scene_runner_factory,
)
from app.services.detector_service import DetectorService
from app.services.disabled_parking_trigger import DisabledParkingTrigger
from app.services.domain_service import domain_service
from app.services.audio_alert_service import (
    DEFAULT_COOLDOWN_SECONDS, MAX_ALERT_SECONDS, MAX_COOLDOWN_SECONDS, MIN_COOLDOWN_SECONDS,
    AudioAlertGate, WinsoundDevice, probe_wav_duration,
)
from app.services.evaluation_budget import EvaluationBudget
from app.services.evaluation_worker import MAX_DISPATCHES_LIMIT, EvaluationWorker
from app.services.event_frame_buffer import EventFrameBuffer
from app.services.gemini_evaluation_client import GeminiEvaluationClient, GeminiEvaluationError
from app.services.langflow_evaluation_client import LangflowDispatchRejected, LangflowEvaluationClient
from app.services.scene_domain_classifier import SceneDomainClassifier
from app.services.parking_candidate_trigger import ParkingCandidateTrigger
from app.services.private_parking_coordinator import FrameSinkTee, PrivateParkingCoordinator
from app.services.private_parking_evidence_sampler import EpisodeEvidenceSampler
from app.services.private_parking_trigger import PrivateParkingTrigger
from app.services.tracker_service import TrackerService
from app.services.video_service import VideoService

logger = logging.getLogger(__name__)


class _UnavailableDispatcher:
    """Never called: the disabled worker does not claim candidates or budget."""

    name = "evaluation-disabled"

    def dispatch(self, request: EvaluationRequest) -> None:
        raise RuntimeError("External evaluation is disabled")


def _create_langflow_client() -> LangflowEvaluationClient | None:
    if not os.getenv("SENTINEL_LANGFLOW_URL", "").strip():
        return None
    try:
        return LangflowEvaluationClient()
    except LangflowDispatchRejected:
        logger.warning("Langflow evaluation disabled: invalid transport configuration")
        return None


EVALUATION_TRANSPORTS = ("langflow", "gemini")


def _evaluation_transport() -> str | None:
    """Exactly one transport; unset means Langflow. Anything else disables evaluation."""
    raw = os.getenv("SENTINEL_EVALUATION_TRANSPORT")
    if raw is None:
        return "langflow"
    if raw not in EVALUATION_TRANSPORTS:
        logger.warning("External evaluation disabled: invalid transport selection")
        return None
    return raw


def _create_gemini_client(callback_timeout_seconds: float | None) -> GeminiEvaluationClient | None:
    """Direct Gemini client; its HTTP timeout must end before the result deadline."""
    try:
        client = GeminiEvaluationClient()
    except GeminiEvaluationError:
        logger.warning("Gemini evaluation disabled: invalid transport configuration")
        return None
    if callback_timeout_seconds is None or not client.timeout_seconds < callback_timeout_seconds:
        logger.warning("Gemini evaluation disabled: request timeout must be below the result deadline")
        client.close()
        return None
    return client


def _external_evaluation_enabled(client: LangflowEvaluationClient | None) -> bool:
    url_configured = bool(os.getenv("SENTINEL_LANGFLOW_URL", "").strip())
    token_configured = bool(os.getenv("SENTINEL_CALLBACK_TOKEN", "").strip())
    if url_configured and not token_configured:
        logger.warning("External evaluation disabled: callback token is missing")
    elif token_configured and not url_configured:
        logger.warning("External evaluation disabled: Langflow URL is missing")
    return client is not None and token_configured


def _evaluation_callback_timeout_seconds() -> float | None:
    raw = os.getenv("SENTINEL_EVALUATION_CALLBACK_TIMEOUT_SECONDS", "45")
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        timeout = None
    if timeout is None or not math.isfinite(timeout) or not 0 < timeout <= 60:
        logger.warning("External evaluation disabled: invalid callback timeout configuration")
        return None
    return timeout


def _evaluation_max_dispatches() -> tuple[bool, int | None]:
    """Return (valid, limit). Unset means unlimited; invalid disables evaluation.

    The limit counts dispatch attempts for the lifetime of this process only.
    """
    raw = os.getenv("SENTINEL_EVALUATION_MAX_DISPATCHES")
    if raw is None:
        return True, None
    if (re.fullmatch(r"[0-9]{1,4}", raw) is None
            or not 1 <= int(raw) <= MAX_DISPATCHES_LIMIT):
        logger.warning("External evaluation disabled: invalid dispatch limit configuration")
        return False, None
    return True, int(raw)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _validated_clip(raw_path: str) -> tuple[Path, float] | None:
    """A readable PCM WAV outside the repository within the duration limit, or None."""
    try:
        path = Path(raw_path).resolve(strict=True) if raw_path.strip() else None
    except (OSError, RuntimeError):
        path = None
    duration = probe_wav_duration(path) if path is not None and path.is_file() else None
    if (path is None or path.is_relative_to(_REPOSITORY_ROOT)
            or duration is None or duration > MAX_ALERT_SECONDS):
        return None
    return path, duration


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_asset(first: Path, second: Path) -> bool:
    """True when both resolved paths reach the same file or byte-identical content.
    In memory only; nothing about the files is logged. Unprovable → treated as same."""
    if first == second:
        return True
    try:
        if os.path.samefile(first, second):
            return True
        if first.stat().st_size != second.stat().st_size:
            return False
        return _file_digest(first) == _file_digest(second)
    except OSError:
        return True


def _local_alert_clip(critical_path: Path) -> tuple[str, float] | None:
    """The separate local-warning clip (O5), or None. Never the CRITICAL asset; an
    invalid local clip disables only the local warning."""
    raw = os.getenv("SENTINEL_AUDIO_LOCAL_ALERT_FILE", "")
    if not raw.strip():
        return None
    clip = _validated_clip(raw)
    if clip is None:
        logger.warning("Local audio alert disabled: invalid local clip configuration")
        return None
    if _same_asset(clip[0], critical_path):
        logger.warning("Local audio alert disabled: the local clip must differ from the CRITICAL clip")
        return None
    return str(clip[0]), clip[1]


def _create_audio_alert_gate() -> AudioAlertGate | None:
    """Optional local alert gate (CRITICAL clip + optional separate local clip). Any
    invalid setting disables audio only; an invalid local clip disables only the local
    warning and never the valid CRITICAL clip."""
    gate = _build_audio_alert_gate()
    if gate is None and os.getenv("SENTINEL_AUDIO_LOCAL_ALERT_FILE", "").strip():
        logger.warning("Local audio alert unused: audio alerts are disabled")
    return gate


def _build_audio_alert_gate() -> AudioAlertGate | None:
    enabled = os.getenv("SENTINEL_AUDIO_ALERT_ENABLED")
    if enabled is None or enabled == "false":
        return None
    if enabled != "true":
        logger.warning("Audio alert disabled: invalid enable flag configuration")
        return None
    if sys.platform != "win32":
        logger.warning("Audio alert disabled: local playback is supported on Windows only")
        return None
    raw_cooldown = os.getenv("SENTINEL_AUDIO_ALERT_COOLDOWN_SECONDS")
    try:
        cooldown = DEFAULT_COOLDOWN_SECONDS if raw_cooldown is None else float(raw_cooldown)
    except ValueError:
        cooldown = math.nan
    if not math.isfinite(cooldown) or not MIN_COOLDOWN_SECONDS <= cooldown <= MAX_COOLDOWN_SECONDS:
        logger.warning("Audio alert disabled: invalid cooldown configuration")
        return None
    clip = _validated_clip(os.getenv("SENTINEL_AUDIO_ALERT_FILE", ""))
    if clip is None:
        logger.warning("Audio alert disabled: invalid alert file configuration")
        return None
    path, duration = clip
    local = _local_alert_clip(path)
    local_kwargs = {} if local is None else {"local_wav_path": local[0], "local_duration_seconds": local[1]}
    return AudioAlertGate(WinsoundDevice(), str(path), duration, cooldown_seconds=cooldown, **local_kwargs)


# ── parking mode selection (T3c) ─────────────────────────────────────────────

PARKING_MODES = ("legacy", "private")
PRIVATE_REQUIRED_SINKS = frozenset({"event frame buffer", "detector"})


def _parking_mode(env: Mapping[str, str]) -> str:
    """``legacy`` (default) | ``private`` | ``disabled`` (invalid value; no fallback)."""
    raw = env.get("SENTINEL_PARKING_MODE")
    if raw is None or raw == "legacy":
        return "legacy"
    if raw == "private":
        return "private"
    logger.warning("Parking candidates disabled: invalid parking mode")
    return "disabled"


def _read_private_parking_config(env: Mapping[str, str]) -> PrivateParkingConfig | None:
    """Read SENTINEL_PRIVATE_PARKING_CONFIG_FILE once (size-capped) and validate it.
    Neither the path nor the content is logged."""
    raw = env.get("SENTINEL_PRIVATE_PARKING_CONFIG_FILE", "")
    config = None
    if raw.strip():
        try:
            with open(raw, "rb") as handle:
                data = handle.read(MAX_CONFIG_BYTES + 1)
        except OSError:
            data = None
        if data is not None and len(data) <= MAX_CONFIG_BYTES:
            # Fail closed at the parse boundary only: undecodable bytes or numbers the
            # parser cannot convert (e.g. a 10**350 integer → OverflowError) disable the
            # private path; service construction errors elsewhere are NOT caught here.
            try:
                config = parse_private_parking_config(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, TypeError, OverflowError, RecursionError):
                config = None
    if config is None:
        logger.warning("Private parking disabled: invalid configuration")
    return config


def _private_parking_evaluation_requested(env: Mapping[str, str]) -> bool:
    """SENTINEL_PRIVATE_PARKING_EVALUATION: unset/false = off; true = requested."""
    raw = env.get("SENTINEL_PRIVATE_PARKING_EVALUATION")
    if raw is None or raw == "false":
        return False
    if raw == "true":
        return True
    logger.warning("Private parking evaluation disabled: invalid enable flag configuration")
    return False


def evaluation_worker_enabled(wiring: "ParkingWiring", env: Mapping[str, str], *,
                              transport: str | None, evaluation_enabled: bool) -> bool:
    """Legacy: the evaluation configuration decides. Disabled mode: never. Private (T3d):
    only with SENTINEL_PRIVATE_PARKING_EVALUATION=true, the direct Gemini transport (the
    only one with the private prompt) and the existing Gemini readiness. Enabling it is a
    configuration change only; a live request still needs the owner's permission."""
    if wiring.worker_enabled_override is None:
        return evaluation_enabled
    if wiring.mode != "private" or not _private_parking_evaluation_requested(env):
        return False
    if transport != "gemini":
        if transport is not None:
            logger.warning("Private parking evaluation disabled: the selected transport has no private prompt")
        return False
    return evaluation_enabled


@dataclasses.dataclass(frozen=True)
class ParkingWiring:
    """The mode-dependent objects of the composition (everything else is shared)."""

    mode: str                                  # "legacy" | "private" | "disabled"
    detector_slot: object                      # the detector's parking-trigger slot
    worker_trigger: object                     # the evaluation worker's candidate trigger
    buffer_slot: object                        # VideoService's event-frame-buffer slot
    bundle_source: object                      # the evaluation worker's bundle source
    candidate_strategies: dict | None
    required_sinks: frozenset
    worker_enabled_override: bool | None       # None: the evaluation configuration decides
    private_trigger: PrivateParkingTrigger | None = None
    sampler: EpisodeEvidenceSampler | None = None
    coordinator: PrivateParkingCoordinator | None = None
    tee: FrameSinkTee | None = None


def build_parking_wiring(env: Mapping[str, str], *, event_frame_buffer: EventFrameBuffer,
                         audio_gate: AudioAlertGate | None, clock=time.monotonic) -> ParkingWiring:
    """Select the parking objects for ``env`` (pure apart from reading the private
    configuration file once). Private: the coordinator in the detector slot, the tee
    (EventFrameBuffer + sampler) in the buffer slot, the SAMPLER as the only bundle
    source, detector and tee required, and ``worker_enabled_override=False`` so the
    worker's ``enabled`` flag is decided by :func:`evaluation_worker_enabled` (on only
    with ``SENTINEL_PRIVATE_PARKING_EVALUATION=true``, the ``gemini`` transport and a
    ready Gemini configuration). Invalid mode/config: one DisabledParkingTrigger, never
    the legacy trigger, and evaluation never enabled."""
    mode = _parking_mode(env)
    if mode == "legacy":
        trigger = ParkingCandidateTrigger()
        return ParkingWiring("legacy", trigger, trigger, event_frame_buffer, event_frame_buffer,
                             None, frozenset(), None)
    config = _read_private_parking_config(env) if mode == "private" else None
    if config is None:
        disabled = DisabledParkingTrigger()
        return ParkingWiring("disabled", disabled, disabled, event_frame_buffer, event_frame_buffer,
                             None, frozenset(), False)
    # No gesture/authorization source exists yet: every unauthorized approach warns.
    private_trigger = PrivateParkingTrigger(config, clock=clock)
    sampler = EpisodeEvidenceSampler(clock=clock)
    coordinator = PrivateParkingCoordinator(private_trigger, sampler, audio_gate=audio_gate)
    tee = FrameSinkTee(event_frame_buffer, sampler)
    return ParkingWiring(
        "private", coordinator, private_trigger, tee, sampler,
        {DomainName.PARKING_MOTORCYCLE: sampler.plan_for_candidate},
        PRIVATE_REQUIRED_SINKS, False,
        private_trigger=private_trigger, sampler=sampler, coordinator=coordinator, tee=tee,
    )

# This is the one shared production GPU gate.  It is injected, never looked up
# by worker modules, so capture/MJPEG paths cannot acquire it accidentally.
shared_cuda_gate = threading.Lock()

tracker_service = TrackerService()
event_frame_buffer = EventFrameBuffer()
audio_alert_gate = _create_audio_alert_gate()
parking_wiring = build_parking_wiring(os.environ, event_frame_buffer=event_frame_buffer,
                                      audio_gate=audio_alert_gate)
# Legacy: the ParkingCandidateTrigger; private: the private trigger; disabled: the
# DisabledParkingTrigger. The detector slot holds the coordinator in private mode.
parking_candidate_trigger = parking_wiring.worker_trigger
detector_service = DetectorService(
    result_consumer=domain_service,
    tracker=tracker_service,
    parking_trigger=parking_wiring.detector_slot,
    domain_snapshot_provider=domain_service.get_snapshot,
    cuda_gate=shared_cuda_gate,
)
scene_domain_classifier = SceneDomainClassifier(
    domain_service=domain_service,
    runner_factory=make_clip_scene_runner_factory(shared_cuda_gate),
    clock=time.monotonic,
    scene_model=f"{DEFAULT_MODEL_ID}@{DEFAULT_MODEL_REVISION}",
)
evaluation_callback_timeout_seconds = _evaluation_callback_timeout_seconds()
effective_callback_timeout_seconds = evaluation_callback_timeout_seconds or 45.0
evaluation_budget = EvaluationBudget(
    pending_timeout_seconds=effective_callback_timeout_seconds + 15,
)
evaluation_transport = _evaluation_transport()
# Only the selected transport is constructed; there is no fallback between them.
langflow_client = _create_langflow_client() if evaluation_transport == "langflow" else None
gemini_client = (_create_gemini_client(evaluation_callback_timeout_seconds)
                 if evaluation_transport == "gemini" else None)
evaluation_client = langflow_client if evaluation_transport == "langflow" else gemini_client
evaluation_max_dispatches_valid, evaluation_max_dispatches = _evaluation_max_dispatches()
# Langflow needs a callback token before spending a request; Gemini returns the
# assessment directly and needs only its own API key (checked by the client).
transport_ready = (_external_evaluation_enabled(langflow_client) if evaluation_transport == "langflow"
                   else gemini_client is not None)
evaluation_enabled = (transport_ready
                      and evaluation_callback_timeout_seconds is not None
                      and evaluation_max_dispatches_valid)
evaluation_worker = EvaluationWorker(
    parking_wiring.worker_trigger, evaluation_budget, parking_wiring.bundle_source,
    evaluation_client if evaluation_client is not None else _UnavailableDispatcher(),
    callback_timeout_seconds=effective_callback_timeout_seconds,
    enabled=evaluation_worker_enabled(parking_wiring, os.environ, transport=evaluation_transport,
                                      evaluation_enabled=evaluation_enabled),
    max_dispatches=evaluation_max_dispatches,
    audio_gate=audio_alert_gate,
    candidate_strategies=parking_wiring.candidate_strategies,
)
video_service = VideoService(
    detector=detector_service,
    scene_classifier=scene_domain_classifier,
    event_frame_buffer=parking_wiring.buffer_slot,
    evaluation_worker=evaluation_worker,
    required_sinks=parking_wiring.required_sinks,
)


def shutdown_services(
    video, scene, detector, parking_trigger, tracker, frame_buffer=None,
    *, evaluation_worker=None, langflow_client=None, evaluation_budget=None,
    audio_alert=None, coordinator=None, frame_tee=None, sampler=None,
) -> None:  # noqa: ANN001
    """Stop the worker before its client; in-flight request errors stay isolated.

    Private mode (T3c): the detector ends the coordinator's obligations first, then the
    coordinator closes, then its trigger (``parking_trigger``; the coordinator never
    closes its dependencies), and the tee before the buffer and the sampler."""
    video.stop()
    if audio_alert is not None:
        audio_alert.close()  # drops the slot, signals cancel; join <= 2 s outside every lock
    if evaluation_worker is not None:
        evaluation_worker.close()
    if langflow_client is not None:
        langflow_client.close()
    scene.close()
    detector.close()
    if coordinator is not None:
        coordinator.close()
    parking_trigger.close()
    tracker.close()
    if frame_tee is not None:
        frame_tee.close()
    if frame_buffer is not None:
        frame_buffer.close()
    if sampler is not None:
        sampler.close()
    if evaluation_budget is not None:
        evaluation_budget.close()
