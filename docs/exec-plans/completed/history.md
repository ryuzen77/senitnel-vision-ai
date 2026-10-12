# Completed change sets

Plans before H1 were agreed in chat, not stored as files; this log records what
shipped and the evidence each change has. Attribution:
- **agent run** — full backend suite executed by the implementing agent before the
  commit (unique `--basetemp`), count as printed by pytest;
- **review** — read-only review verdict as reported by the owner;
- **live / device** — real provider, video or speaker runs, with who observed them.
Counts not listed here were not recorded in this log.

| Commit | Date | Change | Evidence |
|---|---|---|---|
| `6cb499c`…`3fffdea` | 2026-09-20 | Phase 1 video input, preview, API | historical plan `phase1-video-input-plan.md` |
| `58c767c`…`916ad75` | 2026-09-20/23 | YOLO detector core, detection API and dashboard | fake tests |
| `7e08568`…`9f6b462` | 2026-09-23 | Domain lock (YOLO + CLIP sampler) | fake tests |
| `791937b`…`aea1689` | 2026-09-23 | Parking candidate trigger, temporal episodes | fake tests |
| `6baf0b1`…`8367ef5` | 2026-09-23 | Budget, event frame buffer, worker core | fake tests |
| `e6d2235`, `389256e` | 2026-09-23 | Isolated synchronous Langflow client (superseded) | fake tests |
| `9660c2f`…`3a6eff9` | 2026-09-30 | Async Webhook → callback evaluation, runtime wiring | fake tests |
| `a4e509f` | 2026-09-30 | Video startup timing instrumentation | fake tests |
| `70e09c0`, `c571c1e` | 2026-10-01/02 | Current evaluation API and dashboard panel | backend + Vitest |
| `402598f` | 2026-10-02 | Frontend tooling upgrade | `npm run build`, Vitest |
| `585208c` | 2026-10-02 | Per-process dispatch cap | fake tests; with cap 1, one owner-run live Langflow evaluation reached COMPLETED in the backend (one dispatch, callback accepted); dashboard proof and Gemini call count not captured |
| `9c4515e` | 2026-10-02 | Session-safe CRITICAL audio alerts | agent run 486 passed; device: local harness with real `WinsoundDevice` played and cut on Stop, confirmed by the owner's ear |
| `2a12995` | 2026-10-03 | Retail candidate trigger core | agent run 599 passed (pre-commit) |
| `c8d475b` | 2026-10-03 | Retail evaluation contracts, frame strategy, audio parking-only | agent run 624 passed |
| `c42d596` | 2026-10-03 | Direct Gemini transport | agent run 739 passed; review PASS; live attempts 1–2 FAILED ~8 s after dispatch (metadata not visible then) |
| `8a3ddd9` | 2026-10-03 | Safe evaluation diagnostics in app logs | agent run 761 passed; review PASS; live attempt 3 FAILED, `HTTP_REJECTED http=400` (owner copied console line) |
| `b7ff686` | 2026-10-03 | Bounded sanitized Gemini error diagnostics | agent run 796 passed; review PASS; live attempt 4: HTTP 400 `INVALID_ARGUMENT`, field `generation_config.response_format.text.mime_type` (owner copied console line) |
| `bfd3e85` | 2026-10-04 | Structured-output mime type as enum | agent run 798 passed (pre-commit); live attempt 5 COMPLETED, score 10, NORMAL, observed by agent monitor + dashboard on 2026-10-03 with the then-uncommitted change |
| `d999885` | 2026-10-04 | Private parking trigger core (T1) | agent run 876 passed; review PASS |
| `36ffa20` | 2026-10-04 | Private parking evidence sampler (T2) | 59 target / agent run 935 passed; review PASS |
| H1 commit (`docs: establish repository harness and execution plans`) | 2026-10-04 | Documentation harness ([plan](h1-documentation-harness.md)) | documentation only; agent-run link/whitespace/secret checks; review PASS |
| `424cf9d` | 2026-10-05 | T3a1 private parking integration contracts (core only) | review PASS WITH LIMITATIONS (Codex via owner); reviewer's run 385 target / 1,039 full, relayed ([T3 plan](../active/t3-private-parking-integration.md)) |
| `a23fe62` | 2026-10-08 | T3a2 video session lifecycle and cleanup | review PASS; agent runs in the T3 plan ("T3a2 results") |
| `ec9b8cb` | 2026-10-11 | T3b private parking coordinator and bounded frame tee (core only) | agent run 1,100 passed; review PASS on the overflow delta (reviewer 420 fake tests, relayed) |
| `e50d9e2` | 2026-10-11 | T3c0.1 detector cleanup retry, leases, generation-bound delivery | review PASS WITH LIMITATIONS; reviewer 74 target / 1,110 full (relayed); mutation/stability runs agent-only |
| `d479357` | 2026-10-11 | T3c0.2 video start rollback, required sinks, capture cleanup | review PASS WITH LIMITATIONS; reviewer 216 target / 1,152 full (relayed) |
| `05a95b8` | 2026-10-11 | Private parking prerequisite status (docs) | documentation only; review PASS |
| `d075207` | 2026-10-11 | T3c private parking runtime wiring (mode, fail-closed config, local clip factory) | fake integration only; closure PASS (23 targeted, reviewer); full 1,180 from the reviewer's earlier delta review, not re-run at closure |
| `1771d6b` | 2026-10-11 | T3d direct Gemini evaluation for private parking (prompt, dispatch gate) | review PASS WITH LIMITATIONS (reviewer 291 target / 1,209 full, relayed); closure PASS; fakes only, no live request; model accuracy unproven |
| `a45c986` | 2026-10-11 | F1 dashboard for Indonesian operators ([plan](f1-frontend-ui-ux.md)) | implementer (Codex): 61 Vitest tests, build, fake-browser checks at six viewports; independent review (Claude): PASS WITH LIMITATIONS, `npm test` 61 / 8 files and build re-run, no browser/contrast/screen-reader audit; three low findings open (plan §12) |
| `745cd44` | 2026-10-08 | Reviewer and scoped-fix authorization wording (`AGENTS.md` only) | documentation only |
| `b3b7f3a` | 2026-10-12 | P1a prepared source store and ownership core (core only) ([backend plan](../active/private-parking-preview-configuration.md) §15.4) | review PASS WITH LIMITATIONS (Codex via owner/PM); reviewer 140 target / 1,349 full (relayed); repetition and mutation runs implementer-only |
| `1d13095` | 2026-10-12 | P1a closure attribution and draft references (docs) | documentation only |
| `c7d4ddc` | 2026-10-12 | R1 risk and validation plan ([plan](../active/r1-risk-validation.md)) | documentation only; planning record approved by the PM |
| `3c5446a` | 2026-10-12 | P1b prepared video lifecycle and session configuration (backend plan §17.2) | independent review (Codex via owner/PM): 131 target / 1,410 full (relayed); verdict wording not recorded in the plan; fakes only |
| `eb6a3ac` | 2026-10-12 | P1c private parking preparation API and runtime (backend plan §19.3; wire contract frozen) | review PASS WITH LIMITATIONS (Codex via owner/PM); reviewer 175 target / 1,463 full (relayed); fakes only, no real server, decoder or device |
| `67a04fb` | 2026-10-12 | P1d private parking configuration workflow, frontend ([plan](../active/private-parking-configuration-ui.md) §23) | independent review (Claude): PASS WITH LIMITATIONS, `npm test` 175 / 16 files and build re-run; browser/visual checks are implementer evidence (Codex, API fakes); real-video checks: see below |
| `8a6b8e5` | 2026-10-12 | Zone-editor pointer conflicts fixed (frontend plan §24) | implementer (Codex): 192 Vitest tests / 16 files, build, fake-browser checks; independent review (Claude): PASS WITH LIMITATIONS, `npm test` 192 / 16 files and build re-run, reviewer's own fake-API browser reproduction of both click orders, corner and line-endpoint clicks, pending-corner cancel/reset, rejected small areas, reversed numbers and blocked invalid save; five low UX findings (tech-debt #22); no real-video re-test |

Limits of this evidence: the single COMPLETED live result proves the request,
validation and dashboard path for one NORMAL case. It does not prove detection
quality, the CRITICAL → speaker path with a real provider, or private-parking
behaviour: private parking is wired (T3c/T3d) but proven with fakes only — no live
private evaluation, gesture, real camera or speaker run.

Real-video checks of private mode (2026-10-12; cloud evaluation and audio off, no
provider request):
- **Agent local replay through the API** (not the UI), run on a clean `67a04fb` tree:
  upload and probe, preview,
  configuration save, Play, YOLO detections and domain lock `PARKING_MOTORCYCLE`, end of
  file, manual Stop, delete and file cleanup on one MP4. The line and zone were generic
  placeholders, so private-trigger behaviour was not assessed.
- **Owner test through the UI:** report baseline `67a04fb`; the commit the running app
  was built from was not recorded. Play, Stop, delete source, access line and ROI
  drawing only. It exposed zone-editor pointer conflicts, fixed in `8a6b8e5` with fakes and
  an independent review; the owner has not repeated the real-video test since.
Neither check proves candidate or warning quality, detection accuracy or latency.
