# Exec plan H1 — documentation harness

**Review status: completed — reviewer verdict PASS (Codex, relayed by the owner,
2026-10-04); closed by the owner's instruction to commit H1.**
Documentation only; no application source, dependency, config or test changes.
Baseline: HEAD `36ffa2042371efaf7b4ce08f75db87235d4472ba` — "feat: add isolated
private parking evidence sampler", author Muhamad Zen Fikri, 2026-10-04 19:01 +0700;
touches only `backend/app/models/private_parking_evidence.py`,
`backend/app/services/private_parking_evidence_sampler.py`,
`backend/tests/test_private_parking_evidence_sampler.py` (verified with `git log -1`
and `git show --stat HEAD`, agent run, 2026-10-04).

## 1. Goal

Agents find context, boundaries and evidence quickly: a short `AGENTS.md`, a pointer
`PLAN.md`, an `ARCHITECTURE.md` that separates wired from core-only code, and topic
documents under `docs/` that record evidence and gaps honestly.

## 2. Scope / non-scope

In scope (new unless noted): `AGENTS.md` (rewritten), `PLAN.md` (rewritten),
`ARCHITECTURE.md`, `docs/{DESIGN,FRONTEND,PLANS,PRODUCT_SENSE,QUALITY_SCORE,RELIABILITY,SECURITY}.md`,
`docs/design-docs/{index,core-beliefs,private-parking-mode}.md`,
`docs/exec-plans/active/t3-private-parking-integration.md`,
`docs/exec-plans/completed/h1-documentation-harness.md` (moved from `active/` at closing),
`docs/exec-plans/completed/history.md`, `docs/exec-plans/tech-debt-tracker.md`,
`docs/product-specs/index.md`, `docs/references/gemini-generatecontent.md`.

Non-scope: application source, tests, dependencies, `.env*`, screenshots, `README.md`,
`phase1-video-input-plan.md` (marked historical), root reference files
`geminiService.ts` and `package.json`, any generated schema document.

## 3. Dependencies

Owner authorization for H1 (rewrite of `AGENTS.md` and `PLAN.md` allowed). T2 commit
`36ffa20` is the factual baseline.

## 4. Design

- Source code is the evidence for current behaviour; owner instructions set goals and
  product decisions; mismatches are recorded as gaps (`AGENTS.md`, `docs/PLANS.md`).
- Every claim of evidence names its kind (fake unit, fake integration, local replay,
  live provider, device) and who ran it.
- Targets that are not measured are labelled as targets.

## 5. Acceptance

- All local links resolve; backticked repository paths exist.
- No secrets, private URLs, images or base64 in any document.
- LF line endings, no trailing whitespace, exactly one final newline; `git diff --check` clean.
- No two documents contradict each other on authorization, timeouts or latency.

## 6. Risks / limitations

- Owner-reported results (audio heard, console lines, review PASS) are attributed but
  not re-verifiable by agents.
- The ~16 s Gemini latency is a single polling observation, not a measurement.
- Documents can lag behind code; `ARCHITECTURE.md` states its verification commit.

## 7. Verification

Delta 1 (2026-10-04, agent run, HEAD `36ffa20`, documentation-only, no runtime tests):

| Check | Command / method | Result |
|---|---|---|
| HEAD identity | `git log -1 --format=...`, `git show --stat --format= HEAD` | matches baseline above |
| Links, paths, whitespace, secrets | scratchpad script over `AGENTS.md`, `ARCHITECTURE.md`, `PLAN.md`, `docs/**/*.md` (19 files) | all links and paths resolve; LF, no trailing whitespace, one final newline; secret scan clean (only the word "base64" in rule text in `AGENTS.md` and `docs/SECURITY.md`) |
| Diff whitespace | `git diff --check` | clean (exit 0) |
| Contradiction scan | grep for "code wins", "immediate", "under a second", approval wording | no remaining conflicting statements |
| Staging | `git diff --cached --name-only` | empty |
| Gemini timeout claims | read `gemini_evaluation_client.py` (`DEFAULT_TIMEOUT_SECONDS = 30.0`, `MAX_TIMEOUT_SECONDS = 55.0`, httpx `timeout=` single value) and `runtime.py` (`_create_gemini_client` requires timeout < result deadline, default deadline 45) | consistent |

## 8. Delta log

**Delta 1 (Codex review of H1):**
1. Authorization and sources of truth — `AGENTS.md` (intro, workflow) and
   `docs/PLANS.md` (new Authorization section, lifecycle note): an owner instruction
   authorizing a task suffices inside its scope; extra approval only on explicit
   request or scope/action expansion; code vs owner-decision mismatches become gaps.
   Removed "the code wins".
2. Gemini timeout — `docs/RELIABILITY.md`: default 30 s, max 55 s, effective value must
   be below the 45 s default result deadline, violation disables Gemini evaluation;
   HTTP timeout is per phase, not a wall-clock guarantee.
3. Local latency — "immediate"/"under a second" rewritten as an unverified target and
   the private parking → speaker path marked not wired in `ARCHITECTURE.md`,
   `docs/PRODUCT_SENSE.md`, `docs/product-specs/index.md`, and the T3 plan (goal
   wording + new acceptance item for latency measurement).

**Delta 2 (finalisation, 2026-10-04, agent run, HEAD `36ffa20`):**
- `docs/PLANS.md` lifecycle diagram no longer implies a mandatory second approval:
  work starts after owner authorization; plan review + approval is a bracketed,
  conditional step (only when the task requires it); each phase is verified, reported
  and reviewed; commits follow the applicable permission; "completed" needs the
  owner's closing decision. Review-status item 8 now marks "approved" as applying only
  when a plan review was required. Aligned with the reviewed Authorization section in
  `docs/PLANS.md` and the workflow in `AGENTS.md` (unchanged in this delta).
- Verification: local links/paths script over `AGENTS.md`, `ARCHITECTURE.md`,
  `PLAN.md`, `docs/**/*.md`; `git diff --check`; staging empty. Results in the delta
  report.

## 9. Closing

- **Reviewer verdict:** PASS after deltas 1–2 (Codex, read-only review, relayed by
  the owner on 2026-10-04). The reviewer's own commands and outputs were not included
  in the relay, so they are not reproduced or claimed here.
- **Agent-run checks at closing** (2026-10-04, HEAD `36ffa20`, before staging): file
  timestamps show no document changed after delta 2; local links/paths, LF, trailing
  whitespace, final newline and secret scan over all harness documents; `git diff
  --check`; staged diff inspected with `git diff --cached --check`.
- **Closing changes only:** this status and section, the move from `active/` to
  `completed/`, the H1 row in `completed/history.md`, and the H1 entry in `PLAN.md`.
- Commit: `docs: establish repository harness and execution plans` (no push).
