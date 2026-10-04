# Frontend

React 18 + TypeScript + Vite 6, Vitest 4 + Testing Library (jsdom). UI copy is
Indonesian. Dev server `npm run dev` (port 5173, proxies `/api` to `http://localhost:8000`);
`npm run build` type-checks and bundles; `npm test` runs Vitest.

## Structure (current)

| Path | Role |
|---|---|
| `src/App.tsx` | Layout, pipeline status, pending-action state, session epochs |
| `src/api/videoApi.ts` | Typed fetch wrappers for every backend endpoint |
| `src/hooks/useVideoStatus.ts` | Video status polling (1 s), serialized per epoch |
| `src/hooks/useLatestDetection.ts` | Detection polling (0.5 s) |
| `src/hooks/useCurrentDomain.ts` | Domain polling (0.75 s) |
| `src/hooks/useCurrentEvaluation.ts` | Evaluation polling (1 s), generation guard, session pinning |
| `src/components/VideoPreview.tsx`, `VideoSourceSelector.tsx`, `VideoStatusBadge.tsx` | Video input and preview |
| `src/components/DetectionPanel.tsx`, `DomainStatusBadge.tsx` | Detections and domain |
| `src/components/EvaluationPanel.tsx` | Assessment card with "Penilaian otomatis, bukan bukti pelanggaran" |

Polling only (no WebSocket). Stale responses from an older session/epoch are ignored.

## Rules

- Never show a candidate or score as proof; keep the automated-assessment note.
- Never render raw provider text that was not validated by the backend.
- The frontend has no notion of private parking or local access warnings yet.

## Backlog (not implemented)

A frontend redesign and any private-parking / local-warning UI are separate backlog
items; nothing here describes them as done.
