# Plans — how work is planned, reviewed and closed

## When a written plan is required

Any change that touches more than one component, changes a contract, wires something
into runtime, or affects a live provider/device needs an exec plan in
`docs/exec-plans/active/`. A single-file fix with fake tests can be planned in the task
message instead.

## Authorization

- An owner instruction that authorizes a bounded task is enough to start work inside
  that scope, including writing or updating its plan; no automatic re-approval.
- Separate approval is needed only when the owner explicitly asks for it (e.g. "plan
  first, wait for approval") or when the work expands scope or needs an action not yet
  authorized (commit, live provider, device, package install, runtime start).
- A plan document by itself never authorizes anything; authorization always comes from
  the owner.
- Source code shows current behaviour; the owner sets goals and product decisions. A
  mismatch is recorded as a gap in the plan or tech-debt tracker, not resolved in
  favour of the code by default.

## Required sections of an exec plan

1. **Goal** — the user-visible or system outcome.
2. **Scope / non-scope** — files to change (new vs existing) and what is explicitly excluded.
3. **Dependencies** — commits, cores or decisions it relies on; open owner decisions.
4. **Design** — contracts, ordering/locking, timelines, failure policy.
5. **Acceptance** — observable criteria, including what must stay unchanged.
6. **Risks / limitations** — known false positives/negatives, uncalibrated values.
7. **Verification** — test kinds (fake unit, fake integration, live) and exact commands.
8. **Review status** — draft / under review / approved (who, when; only when a plan
   review was required) / implemented / completed.

## Lifecycle

```
owner authorizes the task (instruction or approved plan)
  → [only if the task requires it: plan review → owner approval]
  → implement in phases inside the authorized scope
  → each phase: verify → programmer report → read-only review → fixes if needed
  → commit only as the applicable permission allows (explicit owner instruction)
  → completed (owner closing decision): move the plan to completed/ and add a line
    to completed/history.md
```

The bracketed step is conditional: it applies when the owner explicitly asks for a
plan review first, or when the work expands scope or needs an action not yet
authorized (see Authorization). Otherwise work starts right after authorization.

A plan marked draft is not authorization to implement it unless an owner instruction
authorizes that work (see Authorization). Phases are committed separately. If implementation deviates from the plan, update the plan first.

## Evidence vocabulary (use it in plans and reports)

- **Fake unit** — isolated component with fake clocks/gates/transports.
- **Fake integration** — several real components wired with fakes at the edges
  (no GPU, network, webcam, speaker).
- **Local replay** — real video and local model inference, fake dispatcher; no provider.
- **Live provider** — a real Gemini/Langflow request (owner permission, quota 1).
- **Device** — real speaker/webcam (owner permission).
Always state who ran it (agent execution vs owner/reviewer report), the commit, date
and command. A NORMAL live result proves the request path, not detection quality.
