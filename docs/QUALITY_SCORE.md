# Quality: evidence and gaps per component

No numeric scores: there is no agreed scoring method yet. Each row lists the evidence
that exists and the gaps that remain. Evidence kinds follow [`PLANS.md`](PLANS.md):
fake unit (FU), fake integration (FI), local replay (LR), live provider (LP), device (DV).
Latest full backend suite recorded: 1,463 passed at the P1c review (`eb6a3ac`, reviewer
run relayed by the owner/PM, 2026-10-12). Latest frontend suite recorded: 192 Vitest
tests / 16 files for `8a6b8e5` (implementer run and independent review run, 2026-10-12).

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
| Private parking T1 | FU (78 tests), review PASS; wired in private mode (T3c) | No gesture source; uncalibrated; not assessed on real footage |
| Private parking T2 | FU (59 tests), review PASS; wired in private mode (T3c) | Uncalibrated |
| Private parking T3 (coordinator, wiring, private prompt) | FU, FI; T3c/T3d closures PASS | No live private evaluation; no local clip asset; no real speaker run |
| P1 prepared sources and `/api/video/sources` | FU, FI (reviewer 1,463 full at `eb6a3ac`); LR by the agent through the API on one MP4 at `67a04fb`: upload, preview, save, Play, EOF, Stop, delete, cleanup | Line/zone placeholders in that run, so the private trigger was not assessed; same-size scene changes undetected; one machine |
| P1 private-parking UI | Vitest (175 at `67a04fb`, 192 at `8a6b8e5`, both re-run in independent reviews); fake-browser checks (implementer; reviewer reproduction for `8a6b8e5`); owner UI test with a real file (report baseline `67a04fb`; commit of the running app not recorded): Play, Stop, delete, line, ROI only | The owner test found zone-editor pointer conflicts, fixed in `8a6b8e5` but not re-tested with real video; five low UX findings (tech-debt #22); no screen-reader or full contrast audit; no HTTPS/LAN deployment |

A NORMAL live result proves the request path, not detection quality, and says nothing
about the CRITICAL → speaker path.
