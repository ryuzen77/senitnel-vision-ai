# Product sense

Things to weigh before building a feature.

- **False alarms are the real cost.** A warning that fires for the owner, a guest or a
  passer-by teaches people to ignore the system. Prefer missing an ambiguous case over
  accusing; say plainly which cases are not covered (e.g. `UNKNOWN_INSIDE`).
- **Timing beats prose.** A model judges only the five frames it gets. If the frames
  miss the act, no prompt fixes it (the theft clip replay showed exactly this).
- **Local first, cloud second.** A local rule (e.g. private-parking access warning)
  should react quickly — under one second is a target, not yet measured; the private
  parking → speaker path is not wired. A cloud assessment takes seconds (one success
  took ~16 s from dispatch to COMPLETED, observed by polling) and costs quota. Do not
  make the local reaction wait for the cloud.
- **Every billed request needs a reason.** Free-tier quota and latency are product
  constraints: one dispatch per event, no retry, per-process caps during tests.
- **Neutral words.** "Candidate", "needs review", "entered with"; never "thief",
  "owner", "stolen".
- **Small operators.** One PC, one camera, Windows, little patience for setup: config
  must be explicit, validated and safe when wrong.
- **Honest UI.** Show what is known (score, status, observed actions) and what is not
  (identity, ownership, payment). The note "bukan bukti pelanggaran" stays.
