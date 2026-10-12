"""``/api/video/sources`` — prepared file → preview → line/zone configuration → Play.

Private-parking plan §5 and §18 (P1c). Mutations exist in private mode only (legacy and
disabled modes answer ``409 private_mode_inactive``); capabilities answers in every mode.
Request bodies and query values are parsed here (never echoed back): error bodies carry
only ``{category, field?}``, plus ``source_id``/``generation`` for ``503 cleanup_pending``.
No path, file name, configuration value, token, lease or image data is logged.

Captures (decoder) never have an owner here: the store's capture slot owns them from the
constructor's return until a proven release (``begin_capture`` → ``create_capture`` →
``adopt_capture`` → use → ``finish_capture``).

Blocking work never runs on the event loop: store operations that can wait (Play reaches
VideoService, a drain can delete a file) run through ``run_in_threadpool`` from async
handlers, and the capture/decode handlers are plain ``def`` (FastAPI's threadpool). Every
ownership transfer is ONE threadpool unit (one handler call, or for the upload the writer
reservation together with the whole write/probe step), never split across two awaits. A
cancelled request then either reserved nothing (cancelled before the unit started) or the
unit runs to its end: anyio's ``run_sync`` keeps ``abandon_on_cancel=False``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import uuid
from pathlib import PurePath

import cv2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.models.private_parking import (
    MAX_CONFIG_BYTES, MAX_ZONES, MIN_LINE_LENGTH, MIN_ZONE_AREA, InvalidGeometry, PrivateParkingConfig,
    build_operator_configuration, operator_template,
)
from app.models.sources import (
    AnalysisModel, AttemptRecordModel, CapabilitiesModel, ConfigurationSavedModel, CreatedModel, CurrentModel,
    LimitsModel, PreparedModel, TemplateModel,
)
from app.models.video import VideoState
from app.services import video_decoder
from app.services.prepared_source import ACCEPTED_SUFFIXES, PreparedSourceStore, SourceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/video/sources", tags=["sources"])

MAX_UPLOAD_BYTES = 200 * 1024 * 1024        # same limit as the legacy upload route
MAX_SMALL_BODY_BYTES = 1024                 # create / play bodies
_CHUNK = 1024 * 1024
_ACCEPTED_TYPES = ["video/mp4", "video/webm"]

# category -> HTTP status (frozen for P1d, plan §18.2 item 6)
_STATUS = {
    "unknown_source": 404, "unknown_attempt": 404,
    "source_gone": 410, "outcome_evicted": 410,
    "configuration_too_large": 413,
    "unsupported_type": 422, "too_large": 422, "not_decodable": 422, "invalid_geometry": 422,
    "invalid_operation_id": 422, "invalid_request": 422, "missing_file": 422,
    "write_failed": 500, "preview_failed": 500,
    "cleanup_pending": 503, "storage_unavailable": 503, "closed": 503,
}
_CONFIG_KEYS = {"generation", "expected_revision", "frame_width", "frame_height", "line", "inside", "zones"}
_PLAY_KEYS = {"generation", "config_revision", "attempt", "operation_id"}


@dataclasses.dataclass
class SourcesContext:
    mode: str
    store: PreparedSourceStore | None
    template: PrivateParkingConfig | None
    evaluation_enabled: bool
    video: object | None
    decoder: object = video_decoder


def get_sources_context() -> SourcesContext:
    """Production context from the runtime; tests override this dependency."""
    from app.services import runtime
    return SourcesContext(mode=runtime.parking_wiring.mode, store=runtime.prepared_store,
                          template=runtime.parking_wiring.template,
                          evaluation_enabled=bool(getattr(runtime.evaluation_worker, "enabled", False)),
                          video=runtime.video_service)


class _Refusal(Exception):
    def __init__(self, response: Response) -> None:
        super().__init__("refused")
        self.response = response


def _error(category: str, *, status: int | None = None, field: str | None = None, extra: dict | None = None):
    body = {"category": category}
    if field is not None:
        body["field"] = field
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status or _STATUS.get(category, 409), content=body)


def _source_error(ctx: SourcesContext, error: SourceError, *, admission: bool = False):
    """The identity in a ``cleanup_pending`` answer comes from the error itself (captured
    under the store lock when it was raised), never from a later snapshot."""
    category = error.category
    if category == "cleanup_pending":
        if admission:                                  # POST /: admission conflict (§5)
            return _error(category, status=409)
        return _error(category, status=503,
                      extra={"source_id": error.source_id, "generation": error.generation})
    return _error(category)


def _private_store(ctx: SourcesContext) -> PreparedSourceStore:
    if ctx.mode != "private":
        raise _Refusal(_error("private_mode_inactive", status=409))
    if ctx.store is None:
        raise _Refusal(_error("storage_unavailable"))
    return ctx.store


def _int_value(value: object, field: str) -> int:
    if type(value) is not int:
        raise _Refusal(_error("invalid_request", field=field))
    return value


def _query_int(request: Request, name: str) -> int:
    raw = request.query_params.get(name)
    if raw is None or not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        raise _Refusal(_error("invalid_request", field=name))
    return int(raw)


def _path_int(raw: str, name: str) -> int:
    if not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        raise _Refusal(_error("invalid_request", field=name))
    return int(raw)


def _reject_constant(_constant: str) -> object:
    raise ValueError("non-finite constant")


def _unique_object(pairs: list) -> dict:
    document: dict = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate key")
        document[key] = value
    return document


_MAX_LENGTH_DIGITS = 15


def _declared_length(request: Request) -> int | None:
    """``Content-Length`` checked as text BEFORE any integer conversion: anything but a
    short ASCII digit string is refused (``invalid_request``); more digits than any accepted
    size is reported as too large without converting it (Python refuses to convert
    thousands of digits)."""
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    if not raw or not raw.isascii() or not raw.isdigit():
        raise _Refusal(_error("invalid_request"))
    if len(raw) > _MAX_LENGTH_DIGITS:
        return 10 ** _MAX_LENGTH_DIGITS
    return int(raw)


async def _json_body(request: Request, limit: int, *, too_large: str, too_large_status: int) -> dict:
    """Read at most ``limit`` bytes of the WHOLE request body, then parse strictly."""
    declared = _declared_length(request)
    if declared is not None and declared > limit:
        raise _Refusal(_error(too_large, status=too_large_status))
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise _Refusal(_error(too_large, status=too_large_status))
    try:
        document = json.loads(bytes(body).decode("utf-8"), parse_constant=_reject_constant,
                              object_pairs_hook=_unique_object)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise _Refusal(_error("invalid_request")) from None
    if not isinstance(document, dict):
        raise _Refusal(_error("invalid_request"))
    return document


def _analysis(ctx: SourcesContext) -> AnalysisModel | None:
    """A separate, transient observation of VideoService (outside the store lock)."""
    if ctx.video is None:
        return None
    status = ctx.video.get_status()
    return AnalysisModel(stop_required=status.state is VideoState.ERROR, state=status.state.value,
                         session_ref=status.session_ref, session_id=status.session_id)


# ---------------------------------------------------------------------------- capabilities


@router.get("/capabilities", response_model=CapabilitiesModel)
def get_capabilities(ctx: SourcesContext = Depends(get_sources_context)) -> CapabilitiesModel:
    return CapabilitiesModel(
        mode=ctx.mode, preparation_available=ctx.mode == "private" and ctx.store is not None,
        source_kinds=["file"], template_available=ctx.template is not None,
        evaluation_enabled=ctx.evaluation_enabled,
        limits=LimitsModel(max_upload_bytes=MAX_UPLOAD_BYTES, accepted_types=_ACCEPTED_TYPES, max_zones=MAX_ZONES,
                           min_line_length=MIN_LINE_LENGTH, min_zone_area=MIN_ZONE_AREA,
                           max_configuration_bytes=MAX_CONFIG_BYTES))


# ---------------------------------------------------------------------------- current


@router.get("/current", response_model=None)
def get_current(ctx: SourcesContext = Depends(get_sources_context)):
    try:
        store = _private_store(ctx)
    except _Refusal as refusal:
        return refusal.response
    snap = store.snapshot()
    if snap is None:
        return Response(status_code=204)
    return CurrentModel.of(snap, template_available=ctx.template is not None, analysis=_analysis(ctx))


# ---------------------------------------------------------------------------- create / upload


@router.post("", status_code=201, response_model=None)
async def create_source(request: Request, ctx: SourcesContext = Depends(get_sources_context)):
    try:
        store = _private_store(ctx)
        document = await _json_body(request, MAX_SMALL_BODY_BYTES, too_large="invalid_request",
                                    too_large_status=422)
        if set(document) != {"operation_id"} or not isinstance(document["operation_id"], str):
            raise _Refusal(_error("invalid_request"))
        try:
            operation_id = str(uuid.UUID(document["operation_id"]))
        except (ValueError, AttributeError):
            raise _Refusal(_error("invalid_operation_id")) from None
        snap = await run_in_threadpool(store.create, operation_id)
    except _Refusal as refusal:
        return refusal.response
    except SourceError as error:
        return _source_error(ctx, error, admission=True)
    return JSONResponse(status_code=201, content=CreatedModel(
        source_id=snap.source_id, generation=snap.generation, state=snap.state.value).model_dump())


_MULTIPART_OVERHEAD = 64 * 1024


@router.put("/{source_id}/file", response_model=None)
async def put_file(source_id: str, request: Request, ctx: SourcesContext = Depends(get_sources_context)):
    """The multipart body is parsed here (never by a declared parameter), so an invalid
    ``file`` field is answered with a category and nothing of the input is echoed. The
    write and the probe run in the threadpool as one unit that owns the uploaded file."""
    form = None
    unit = None
    try:
        store = _private_store(ctx)
        generation = _query_int(request, "generation")
        declared = _declared_length(request)
        if declared is not None and declared > MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD:
            raise _Refusal(_error("too_large"))
        try:
            form = await request.form()
        except Exception:                                   # noqa: BLE001 - malformed multipart
            raise _Refusal(_error("invalid_request")) from None
        upload = form.get("file")
        if not isinstance(upload, UploadFile):              # missing, or a plain text field
            raise _Refusal(_error("missing_file"))
        suffix = PurePath(upload.filename or "").suffix.lower()
        if suffix not in ACCEPTED_SUFFIXES:
            raise _Refusal(_error("unsupported_type"))
        # ONE threadpool unit: the writer reservation and the whole write/probe step. It
        # owns the uploaded file once it started; this request may be cancelled (even
        # natively, which anyio does not shield) without leaving a writer unexecuted.
        unit = _UploadUnit(upload)
        return await run_in_threadpool(unit.run, ctx, store, source_id, generation, suffix)
    except _Refusal as refusal:
        return refusal.response
    except SourceError as error:
        return _source_error(ctx, error)
    finally:
        if unit is not None:
            unit.abandon()                    # closes the file only if the unit never started
        elif form is not None:
            await form.close()


class _UploadUnit:
    """The upload's ownership unit: ``pending`` → ``running`` (claimed by its worker
    thread) → ``done``, or ``pending`` → ``abandoned`` (the request ended before the worker
    started: nothing was reserved). Once running, only the worker closes the uploaded file
    and it always ends the writer it registered, whatever happens to the request."""

    def __init__(self, upload: UploadFile) -> None:
        self._upload = upload
        self._lock = threading.Lock()
        self._state = "pending"

    def abandon(self) -> None:
        with self._lock:
            if self._state != "pending":
                return                        # the worker owns the file now
            self._state = "abandoned"
        self._upload.file.close()

    def run(self, ctx: SourcesContext, store: PreparedSourceStore, source_id: str, generation: int, suffix: str):
        with self._lock:
            if self._state != "pending":
                return None                   # the request was abandoned before this started
            self._state = "running"
        handle = None
        try:
            handle = store.begin_write(source_id, generation, suffix)
            return _write_and_probe(ctx, store, handle, self._upload)
        finally:
            if handle is not None and not handle.done:
                store.fail_write(handle, "write_failed")    # an unexpected error never leaks it
            with self._lock:
                self._state = "done"
            self._upload.file.close()


def _write_and_probe(ctx: SourcesContext, store: PreparedSourceStore, handle, file: UploadFile):
    """The writer owns its partial file until ``finish_write``/``fail_write``; the probe
    capture is owned by the store's capture slot."""
    total = 0
    try:
        with open(handle.path, "wb") as target:
            while True:
                if handle.cancelled.is_set():
                    break
                chunk = file.file.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    store.fail_write(handle, "too_large")
                    return _error("too_large")
                target.write(chunk)
    except OSError:
        store.fail_write(handle, "write_failed")
        return _error("write_failed")
    if handle.cancelled.is_set():
        category = store.write_refusal(handle)
        store.fail_write(handle, category)
        return _error(category)
    # Probe through the store's one capture slot.
    try:
        store.begin_capture(handle)
    except SourceError as error:
        store.fail_write(handle, error.category)
        return _source_error(ctx, error)
    try:
        capture = ctx.decoder.create_capture(handle.path)
    except Exception:                                       # noqa: BLE001 - nothing to release
        store.capture_failed(handle)
        store.fail_write(handle, "not_decodable")
        return _error("not_decodable")
    use = store.adopt_capture(handle, capture)
    size = None
    if use:
        try:
            ctx.decoder.probe(capture)
            frame = ctx.decoder.read_first_frame(capture)
            shape = getattr(frame, "shape", None)
            if shape is not None and len(shape) >= 2:
                size = (int(shape[1]), int(shape[0]))
        except Exception:                                   # noqa: BLE001 - not decodable
            size = None
    released = store.finish_capture(handle)
    if not released:
        # Decision 3: a successful probe is not enough; the file waits for the release
        # and is deleted afterwards (upload again).
        identity = {"source_id": handle.source.source_id, "generation": handle.source.generation}
        store.fail_write(handle, "probe_release_failed")
        return _error("cleanup_pending", status=503, extra=identity)
    if not use:
        category = store.write_refusal(handle)
        store.fail_write(handle, category)
        return _error(category)
    if size is None:
        store.fail_write(handle, "not_decodable")
        return _error("not_decodable")
    try:
        prepared = store.finish_write(handle, *size)
    except SourceError as error:
        return _source_error(ctx, error)
    return PreparedModel(state=prepared.state.value, frame_width=prepared.frame_width,
                         frame_height=prepared.frame_height, preview_revision=prepared.preview_revision)


# ---------------------------------------------------------------------------- preview / template


@router.get("/{source_id}/preview", response_model=None)
def get_preview(source_id: str, request: Request, ctx: SourcesContext = Depends(get_sources_context)):
    try:
        store = _private_store(ctx)
        generation = _query_int(request, "generation")
        handle = store.begin_read(source_id, generation)
    except _Refusal as refusal:
        return refusal.response
    except SourceError as error:
        return _source_error(ctx, error)
    try:
        try:
            store.begin_capture(handle)
        except SourceError as error:
            return _source_error(ctx, error)
        try:
            capture = ctx.decoder.create_capture(handle.path)
        except Exception:                                   # noqa: BLE001 - nothing to release
            store.capture_failed(handle)
            return _error("preview_failed")
        use = store.adopt_capture(handle, capture)
        frame = None
        if use:
            try:
                frame = ctx.decoder.read_first_frame(capture)
            except Exception:                               # noqa: BLE001
                frame = None
        released = store.finish_capture(handle)             # cleanup first, whatever happens next
        if not released:
            return _error("cleanup_pending", status=503, extra={"source_id": handle.source.source_id,
                                                                "generation": handle.source.generation})
        if not use:
            return _error(store.read_refusal(handle))
        # The decode may have outlived its operation (expiry, delete, shutdown): revalidate
        # the read's identity before sending anything; a stale frame is discarded.
        stale = store.read_still_valid(handle)
        if stale is not None:
            return _error(stale)
        if frame is None:
            return _error("preview_failed")
        ok, jpeg = cv2.imencode(".jpg", frame)
        if not ok:
            return _error("preview_failed")
        return Response(content=bytes(jpeg), media_type="image/jpeg")
    finally:
        store.end_read(handle)


@router.get("/{source_id}/template", response_model=None)
def get_template(source_id: str, request: Request, ctx: SourcesContext = Depends(get_sources_context)):
    try:
        store = _private_store(ctx)
        generation = _query_int(request, "generation")
        if ctx.template is None:
            store.validate_source(source_id, generation)   # no template: no TTL reset (D5)
            return Response(status_code=204)
        width, height = store.touch_template(source_id, generation)
    except _Refusal as refusal:
        return refusal.response
    except SourceError as error:
        return _source_error(ctx, error)
    template = operator_template(ctx.template)
    line = template.line
    return TemplateModel(line={"p1": [line.x1, line.y1], "p2": [line.x2, line.y2]},
                         inside=line.inside_side.value,
                         zones=[{"name": z.name, "rect": [z.x1, z.y1, z.x2, z.y2]} for z in template.zones],
                         frame_width=width, frame_height=height, confirmed=False)


# ---------------------------------------------------------------------------- configuration


@router.put("/{source_id}/configuration", response_model=None)
async def put_configuration(source_id: str, request: Request, ctx: SourcesContext = Depends(get_sources_context)):
    try:
        store = _private_store(ctx)
        # Decision 5: 4096 bytes apply to the WHOLE JSON envelope, before parsing.
        document = await _json_body(request, MAX_CONFIG_BYTES, too_large="configuration_too_large",
                                    too_large_status=413)
        if set(document) != _CONFIG_KEYS:
            raise _Refusal(_error("invalid_request"))
        generation = _int_value(document["generation"], "generation")
        expected = document["expected_revision"]
        if expected is not None:
            expected = _int_value(expected, "expected_revision")
        try:
            configuration = build_operator_configuration(
                frame_width=document["frame_width"], frame_height=document["frame_height"],
                line=document["line"], inside=document["inside"], zones=document["zones"],
                thresholds=ctx.template.thresholds if ctx.template is not None else None)
        except InvalidGeometry as error:
            raise _Refusal(_error("invalid_geometry", field=error.field)) from None
        revision, snap = await run_in_threadpool(_save_and_read, store, source_id, generation, expected,
                                                 configuration)
    except _Refusal as refusal:
        return refusal.response
    except SourceError as error:
        return _source_error(ctx, error)
    ready = bool(snap is not None and snap.source_id == source_id and snap.config_ready)
    return ConfigurationSavedModel(revision=revision, ready=ready, canonical=configuration.canonical())


def _save_and_read(store: PreparedSourceStore, source_id: str, generation: int, expected, configuration):
    return store.save_configuration(source_id, generation, expected, configuration), store.snapshot()


# ---------------------------------------------------------------------------- delete


@router.delete("/{source_id}", response_model=None)
def delete_source(source_id: str, request: Request, ctx: SourcesContext = Depends(get_sources_context)):
    try:
        store = _private_store(ctx)
        generation = _query_int(request, "generation")
        state = store.delete(source_id, generation)
    except _Refusal as refusal:
        return refusal.response
    except SourceError as error:
        return _source_error(ctx, error)
    if state == "GONE":
        return Response(status_code=204)
    return JSONResponse(status_code=202, content={"state": "DELETING"})


# ---------------------------------------------------------------------------- play


@router.post("/{source_id}/play", response_model=None)
async def play(source_id: str, request: Request, ctx: SourcesContext = Depends(get_sources_context)):
    """``200`` with the attempt record for every Play that passed the CAS (``REFUSED``
    included); ``409``/``410``/``503`` only before the CAS; no server retry."""
    try:
        store = _private_store(ctx)
        document = await _json_body(request, MAX_SMALL_BODY_BYTES, too_large="invalid_request",
                                    too_large_status=422)
        if set(document) != _PLAY_KEYS or not isinstance(document["operation_id"], str):
            raise _Refusal(_error("invalid_request"))
        generation = _int_value(document["generation"], "generation")
        revision = _int_value(document["config_revision"], "config_revision")
        attempt = _int_value(document["attempt"], "attempt")
        try:
            operation_id = str(uuid.UUID(document["operation_id"]))
        except (ValueError, AttributeError):
            raise _Refusal(_error("invalid_operation_id")) from None
        # Play reaches VideoService (reservation, sink starts): never on the event loop.
        record = await run_in_threadpool(store.play, source_id, generation, revision, attempt, operation_id)
    except _Refusal as refusal:
        return refusal.response
    except SourceError as error:
        return _source_error(ctx, error)
    return AttemptRecordModel.of(record)


@router.get("/{source_id}/play/{attempt}", response_model=None)
def get_attempt(source_id: str, attempt: str, ctx: SourcesContext = Depends(get_sources_context)):
    try:
        store = _private_store(ctx)
        record = store.attempt(source_id, _path_int(attempt, "attempt"))
    except _Refusal as refusal:
        return refusal.response
    except SourceError as error:
        return _source_error(ctx, error)
    return AttemptRecordModel.of(record)


# ---------------------------------------------------------------------------- recovery


@router.post("/cleanup/retry", response_model=None)
def retry_cleanup(ctx: SourcesContext = Depends(get_sources_context)):
    """Operator recovery (§0.2.2): one attempt per stalled obligation the store recorded
    itself; counts only."""
    try:
        store = _private_store(ctx)
        counts = store.retry_cleanup()
    except _Refusal as refusal:
        return refusal.response
    except SourceError as error:
        return _source_error(ctx, error)
    if counts["pending"] == 0 and counts["stalled"] == 0:
        return Response(status_code=204)
    return JSONResponse(status_code=202, content={"cleanup": counts})
