# Reliability

## Process model

One Uvicorn worker, no `--reload`; all state in memory. Restarting the backend loses
sessions, pending evaluations and the per-process dispatch counter.

## Failure handling that exists

| Situation | Behaviour |
|---|---|
| Stop / EOF / new session | All services end the session; pending evaluation cancelled; late results dropped |
| Provider timeout, connection or 5xx | `DISPATCH_UNCERTAIN`, pending closed (direct) or left to deadline (Langflow); no retry |
| Provider 4xx / invalid response | Classified, pending closed, sanitized diagnosis logged |
| Result after deadline | `TIMED_OUT`, never published |
| Bundle incomplete | No dispatch, budget and trigger released |
| Audio device stop fails | Audio disabled until restart (`device_fault`) |
| Audio lifecycle update fails | Gate fails closed for the process |
| Invalid configuration | Only the affected feature is disabled |

Timeouts:
- Gemini HTTP timeout (`SENTINEL_GEMINI_TIMEOUT_SECONDS`): default 30 s, configurable
  up to 55 s. The effective value must be strictly below the result deadline
  (`SENTINEL_EVALUATION_CALLBACK_TIMEOUT_SECONDS`, default 45 s); any configuration that
  breaks this relation disables Gemini evaluation at startup.
- The HTTP timeout is passed to httpx as one value that bounds each phase (connect,
  read, write, pool) separately; it is **not** a guaranteed total wall-clock limit for
  a request. The worker's result deadline is what stops waiting for a result.
- Langflow Webhook ACK timeout: ≤ 10 s. Budget pending expiry = result deadline + 15 s.

## Observability

Evaluation worker and Gemini client log metadata to stderr under default Uvicorn
(fallback handler since `8a3ddd9`; external logging config is respected and may hide
them). Failures appear as WARNING lines with outcome, category, kind, HTTP code,
allowlisted provider diagnosis and durations. Agents cannot read the owner's console;
the owner copies the lines.

## Known reliability gaps

- Ctrl+C twice forces exit and skips lifespan shutdown (tech debt #13).
- Latency and cost of provider calls are not budgeted (tech debt #7).
- No durable storage: crash = lost history.

## Live-test protocol

Owner permission per run; owner restarts the backend from their PowerShell (secrets
stay there); `SENTINEL_EVALUATION_MAX_DISPATCHES=1`; exactly one upload; no retry or
second session; agent may run a metadata-only monitor, Vite and a browser tab and
closes only those; dashboard screenshots are viewed transiently and not stored.
