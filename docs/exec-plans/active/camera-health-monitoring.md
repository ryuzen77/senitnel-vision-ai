# C1H — Camera health monitoring (frame watchdog and view checks)

Status: **DRAFT — recorded backlog plan; not implemented, not authorized.** Written by
Claude on the PM instruction of 2026-10-11 from source read at `a45c986`. Priority:
**high before any use with a real CCTV camera.** Decisions marked **(open)** need the
owner/PM.

## 1. Goal

The operator can tell when the camera is not giving usable video — frames stopped,
frozen picture, view blocked, camera shifted — as a **technical status**, separate from
the AI assessment and from the local access warning, and is guided to recover and to
re-verify the line/zones. The system never infers intent (e.g. deliberate tampering)
from these signals.

## 2. Scope / non-scope

Proposed (implementation phase): a small health monitor fed by the capture path
(metadata and down-scaled statistics only, no storage of images), a status field or
endpoint for the dashboard, and fake tests. Exact files are decided at authorization
(likely `video_service.py` hook, a new `camera_health.py`, an API field, frontend in a
separate F-phase). Non-scope: identity, intent, alarms to third parties, automatic
camera control, new dependencies.

## 3. Signals (proposal)

| Signal | Meaning | Notes |
|---|---|---|
| Frame watchdog | No new frame for longer than an expected interval | Webcam/RTSP only; file playback has EOF semantics |
| Freeze vs static scene | Identical/near-identical frames for a long time | A parking area at night is legitimately static: freeze must use capture-level evidence (repeated timestamps/sequence, decoder stalls) before pixel similarity **(open)** |
| View obstructed | Very low variance/brightness or a large uniform region | Covered lens, darkness, glare; not proof of tampering |
| Camera shifted | The scene no longer matches the reference captured when line/zones were configured | Compare against the configured reference; on mismatch, private analysis should be suspended or flagged until re-verified **(open)** |

All thresholds are **uncalibrated** and must come from annotated recordings per camera;
none is fixed in this draft.

## 4. Behaviour (proposal)

- Status values are technical (`OK`, `NO_FRAMES`, `FROZEN`, `OBSTRUCTED`, `VIEW_CHANGED`,
  `UNKNOWN`) and shown apart from the AI assessment and the local warning.
- Recovery: the status clears only after healthy frames return; `VIEW_CHANGED` needs the
  operator to re-verify (or redraw) the line/zones on a new preview (depends on
  [preview configuration](private-parking-preview-configuration.md)).
- No speaker output, provider dispatch or "tampering" wording from health signals.

## 5. Acceptance (planned, fakes)

Fake capture sequences for: stalled frames, repeated frames, dark/uniform frames, a
shifted scene versus a configured reference, a legitimately static scene that must stay
`OK`; recovery after healthy frames; no images in logs; no effect on legacy behaviour
when the monitor is off.

## 6. Risks (high → low)

- **H1** False "frozen"/"obstructed" on legitimately static or dark scenes.
- **H2** Missed shift → line/zones silently wrong (wrong or no warnings).
- **M1** CPU cost on the capture path (must stay bounded and off the capture thread's
  critical path).
- **L1** Operator fatigue from noisy technical warnings.

## 7. Open decisions

Which signals first; what private analysis does while `VIEW_CHANGED`; whether the status
is a new endpoint or a field of `/api/video/status`; calibration data source.

## 8. Review status

Draft backlog (2026-10-11). Not reviewed, not authorized.
