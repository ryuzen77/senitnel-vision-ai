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

Limits of this evidence: the single COMPLETED live result proves the request,
validation and dashboard path for one NORMAL case. It does not prove detection
quality, the CRITICAL → speaker path with a real provider, or private-parking
behaviour (not wired).
