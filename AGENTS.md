# AGENTS.md — Sentinel Vision AI

Entry point for coding agents (Codex, Claude Code). Keep it short; details live in
the linked documents. Always start from `git log -1` and `git status --short`.

**Sources of truth.** Source code shows what is implemented now; the owner sets goals
and product decisions. Documents can lag behind both. When code and an owner decision
disagree, record it as a gap (plan or tech-debt tracker) instead of letting the code win
by default; report it if it blocks the task.

## Find context

| Need | Read |
|---|---|
| Components, contracts, what is wired vs core-only | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| What to work on next | [`PLAN.md`](PLAN.md) → [`docs/exec-plans/active/`](docs/exec-plans/active/) |
| How plans are written and reviewed | [`docs/PLANS.md`](docs/PLANS.md) |
| Shipped change sets and their evidence | [`docs/exec-plans/completed/history.md`](docs/exec-plans/completed/history.md) |
| Known debt | [`docs/exec-plans/tech-debt-tracker.md`](docs/exec-plans/tech-debt-tracker.md) |
| Principles that override convenience | [`docs/design-docs/core-beliefs.md`](docs/design-docs/core-beliefs.md) |
| Product behaviour and domains | [`docs/product-specs/index.md`](docs/product-specs/index.md) |
| Evidence and gaps per component | [`docs/QUALITY_SCORE.md`](docs/QUALITY_SCORE.md) |
| Failure modes, live-test protocol | [`docs/RELIABILITY.md`](docs/RELIABILITY.md) |
| Secrets, privacy, logging | [`docs/SECURITY.md`](docs/SECURITY.md) |
| Frontend | [`docs/FRONTEND.md`](docs/FRONTEND.md) |
| Provider facts verified against official docs | [`docs/references/`](docs/references/) |

`README.md` and `phase1-video-input-plan.md` are historical and partly stale; prefer
the documents above.

## Verify (Windows, repository root)

```powershell
$py = 'C:\Users\zenin\miniconda3\envs\sentinel-vision\python.exe'
$testTemp = Join-Path $env:TEMP ('sentinel-tests-' + [guid]::NewGuid().ToString())
& $py -m pytest backend/tests -q --basetemp $testTemp     # full backend suite
& $py -m pip check
git diff --check                                           # plus whitespace/final newline of untracked files
# frontend changes only (from frontend/): npm run build; npm test
```

Run the backend only as `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`
from `backend/` — one worker, no `--reload`. Tests must not need GPU, webcam,
network or weights (inject fakes, clocks, gates).

## Action boundaries

Never without an explicit owner instruction for that action:
- stage, commit or push (stage exact paths only; check `git diff --cached --check`);
- run a live provider request (Gemini/Langflow), real video inference, webcam or speaker;
- start/restart the owner's backend, install packages, edit a Langflow flow;
- read or print secrets (`.env*`, another process's environment), JPEG/base64, raw provider bodies.

Never at all: put secrets, images, videos, weights, caches or runtime reports in Git,
logs or prompts; retry a billed request automatically; present a candidate or model
score as proof of theft, identity or ownership.

## Scope

Work on one bounded change set. Change only the files the task assigns plus the
smallest adjacent tests. If another file is needed, report why and wait. Existing
uncommitted changes belong to their author: never reset, checkout, stash, delete or
silently amend them. Reference files at the repository root (`geminiService.ts`,
`package.json`) belong to an older project and are out of scope.

## Workflow: programmer → reviewer → owner

1. **Owner** defines the task (or approves an exec plan under `docs/exec-plans/active/`).
   An owner instruction that authorizes a task is enough to start work inside its
   scope; do not ask for re-approval by default.
2. **Programmer** (one agent) runs preflight (HEAD, staging, status, owned files),
   implements with fake tests, verifies, then reports changed files, behaviour, test
   commands and results, risks and `git status` — and stops. Wait for extra approval
   only when the owner explicitly asks for a plan review first, or when the work would
   expand scope or need an action not yet authorized (see Action boundaries).
3. **Reviewer** (the other agent) reads diff and tests without editing and returns
   findings with file/line references. One finding has one fixer.
4. **Owner** decides: more fixes, commit (exact files, message), or stop.

Parallel edits only on explicitly disjoint file sets; shared files (`runtime.py`,
`main.py`, contracts, `AGENTS.md`) are edited sequentially. On surprising Git state or
conflicting plans: pause, preserve the worktree, ask. No `reset --hard`, forced
checkout or automatic merge.
