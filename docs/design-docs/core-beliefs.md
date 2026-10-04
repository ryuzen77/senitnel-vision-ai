# Core beliefs

Principles that win over convenience. A change that breaks one needs an explicit
owner decision recorded in the relevant exec plan.

1. **Candidate ≠ verdict.** Triggers produce prompts for review. Model scores are
   validated assessments of a few frames, shown as "automated, needs review". Nothing
   in the system infers identity, ownership, intent or guilt. A gesture authorizes an
   episode; it is not identity or ownership either.
2. **Images stay in memory.** JPEG bytes are never written to disk, logs, exceptions,
   reprs or Git. Logs carry metadata only (short IDs, states, codes, durations).
3. **Fail closed on configuration.** Invalid or ambiguous config disables the feature
   it controls, logs a generic warning without the value, and never guesses a default
   that widens behaviour (no whole-frame ROI, no transport fallback).
4. **Bounded everything.** Queues, histories, caches, episodes and byte buffers have
   validated limits; overflow has a deterministic policy and a counter, never silent loss.
5. **Sessions are walls.** Every service is session-scoped; results from an old session
   are dropped, never published; late input never recreates state.
6. **Two clocks.** Observation timestamps order events; an injected control clock
   drives TTLs and deadlines, so expiry works when frames stop.
7. **Billed calls are precious.** One atomic handoff per dispatch, per-process quota,
   no automatic retry, no fallback between transports, spent quota never returned.
8. **The hot path never waits.** Capture and MJPEG only `offer`; no model loading,
   inference, HTTP, device calls or GPU gate there; no external call under a lock.
9. **Separate signals stay separate.** A local access warning (rule) and a CRITICAL
   assessment (model) are different facts sharing an `event_id`, never merged.
10. **Evidence is labelled honestly.** Fake tests, local replay, live provider and
    device runs are different kinds of proof; reports say which, who ran it and when.
11. **Core first, then integration**, each reviewed separately; one writer per change.
