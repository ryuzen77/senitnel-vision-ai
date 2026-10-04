# Quality: evidence and gaps per component

No numeric scores: there is no agreed scoring method yet. Each row lists the evidence
that exists and the gaps that remain. Evidence kinds follow [`PLANS.md`](PLANS.md):
fake unit (FU), fake integration (FI), local replay (LR), live provider (LP), device (DV).
Latest full backend suite: 935 passed at `36ffa20` (agent run, 2026-10-04).

| Component | Evidence | Gaps |
|---|---|---|
| Video input / MJPEG / upload guards | FU, FI | Long-running webcam stability not measured |
| YOLO detection | FU (fake model); LR on one clip | Accuracy on owner cameras unknown |
| Tracker | FU, FI | ID switches under occlusion not quantified |
| Domain lock (YOLO + CLIP) | FU, FI; YOLO lock seen in LR/LP sessions | CLIP accuracy on retail clips untested; CUDA required for CLIP |
| Parking trigger + frame buffer | FU, FI; LR on one theft clip | Replay showed wrong first candidate and frames missing the act (debt #1) |
| Evaluation worker (handoff, quota, budget) | FU, FI with concurrency/mutation tests | Behaviour under sustained load not measured |
| Langflow transport + callback | FU (mock transport); one owner-run LP COMPLETED | Flow prompt not versioned here; reachability setup manual |
| Gemini direct transport | FU (mock transport, bounded reads, diagnostics); LP: 4 failures diagnosed, 1 COMPLETED (NORMAL) | Single success; no CRITICAL from provider; latency/cost unmeasured |
| Dashboard | Vitest; dashboard verified visually in LP run 5 | No end-to-end browser tests |
| Audio alert gate | FU incl. races/mutations; DV harness with real `WinsoundDevice`, owner heard play and cut | CRITICAL → speaker never exercised with a real provider result |
| Retail trigger core | FU (122 tests) | Not wired; no data |
| Private parking T1 | FU (78 tests), review PASS | Not wired; no gesture source; uncalibrated |
| Private parking T2 | FU (59 tests), review PASS | Not wired; uncalibrated |
| Private parking T3 | — | Draft plan only |

A NORMAL live result proves the request path, not detection quality, and says nothing
about the CRITICAL → speaker path.
