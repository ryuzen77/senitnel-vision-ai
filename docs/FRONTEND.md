# Frontend

React 18 + TypeScript + Vite 6, Vitest 4 + Testing Library (jsdom). UI copy is
Indonesian. Dev server `npm run dev` (port 5173, proxies `/api` to `http://localhost:8000`);
`npm run build` type-checks and bundles; `npm test` runs Vitest.

## Structure (current)

| Path | Role |
|---|---|
| `src/App.tsx` | Layout (preview, controls, AI assessment, closed detail sections), pending-action state, session epochs |
| `src/api/videoApi.ts` | Typed fetch wrappers for every backend endpoint |
| `src/hooks/useVideoStatus.ts` | Video status polling (1 s), serialized per epoch |
| `src/hooks/useLatestDetection.ts` | Detection polling (0.5 s) |
| `src/hooks/useCurrentDomain.ts` | Domain polling (0.75 s) |
| `src/hooks/useCurrentEvaluation.ts` | Evaluation polling (1 s), generation guard, session pinning |
| `src/components/VideoPreview.tsx`, `VideoSourceSelector.tsx`, `VideoStatusBadge.tsx` | Video input and preview |
| `src/components/DetectionPanel.tsx`, `DomainStatusBadge.tsx` | Detections and domain |
| `src/components/EvaluationPanel.tsx` | Assessment card with "Penilaian otomatis, bukan bukti pelanggaran" |
| `src/api/preparedSourceApi.ts` | Private-mode sources API adapter (response and error validation, canonical operation IDs) |
| `src/hooks/useSourcePreparation.ts` | Private-mode lifecycle: capabilities, current-source polling (1 s), upload, preview, save, Play, reconciliation, cancel and recovery |
| `src/components/PrivateParkingConfiguration.tsx`, `ConfigurationOverlay.tsx`, `src/geometry/` | Preview, access-line and zone editor (pointer, keyboard and numeric input), validation |

Polling only (no WebSocket). Stale responses from an older session/epoch are ignored.

## Rules

- Never show a candidate or score as proof; keep the automated-assessment note.
- Never render raw provider text that was not validated by the backend.
- Private mode is chosen from `/api/video/sources/capabilities`, never from the domain;
  Play is offered only from the backend's `config.ready`, and mutations are never
  retried automatically. Details: [frontend plan](exec-plans/active/private-parking-configuration-ui.md).
- The frontend shows no local access warning status: no API exposes it yet.

## Backlog (not implemented)

Local-warning and gesture status UI, and webcam/CCTV preview in private mode, are
separate backlog items; nothing here describes them as done.
