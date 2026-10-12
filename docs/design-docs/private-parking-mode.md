# Private parking mode (home / kost)

Status: T1 trigger core (`d999885`) and T2 evidence sampler (`36ffa20`) are wired into
runtime as an opt-in mode (T3c `d075207`, T3d `1771d6b`), proven with fakes only. The
operator configuration flow (upload → preview → line/zones → save → Play) exists since
P1 (`b3b7f3a`…`67a04fb`, editor fix `8a6b8e5`); with a real file the owner tested only
Play, Stop, delete and line/zone drawing (report baseline `67a04fb`; commit of the running
app not recorded), not the trigger or warnings. No gesture source, local voice asset or
real-device run yet. Plan:
[`../exec-plans/active/t3-private-parking-integration.md`](../exec-plans/active/t3-private-parking-integration.md).

## Why a mode, not a domain

The scene is still a motorcycle parking area; what changes is the owner's knowledge
of the space: an explicit entry line, motor zones and who is allowed in. It is a mode
of `PARKING_MOTORCYCLE` so domain locking and the legacy parking path stay unchanged.

## T1 — trigger (`services/private_parking_trigger.py`)

- Config (`models/private_parking.py`): entry segment P1–P2 plus an explicit
  `inside_point`; 1–8 motor zones; thresholds validated (bool, NaN, ranges, relations);
  invalid config → mode off. All thresholds provisional and uncalibrated.
- Foot point = bottom-centre of the person box. An episode starts only on an observed
  outside → inside crossing **through the segment**, within a short observation gap,
  with a hysteresis band.
- First seen inside → `UNKNOWN_INSIDE`: no episode, no event. This is a coverage gap,
  not "considered safe".
- Gesture authorization comes through a Protocol (no model yet). Signals are fetched
  outside the lock and re-validated inside it (session generation, episode, track,
  time). Authorization is per episode, never inherited across track IDs.
- A motorcycle is exempt only if it crossed in and moved with that person over several
  observations, unambiguously; it is called "entered with", never "owned by".
- Approach = person foot and a parked motorcycle in the same zone, close for N
  consecutive observations **to the same motorcycle**. An unauthorized approach issues
  exactly one local alert and one candidate with one `event_id`; never re-triggers.
- Control clock drives episode age, candidate TTL and alert TTL. Queues are bounded;
  overflow drops the newest item with a counter.

## T2 — evidence (`services/private_parking_evidence_sampler.py`)

- Five distinct ordered frames: crossing anchor, three journey frames near ¼/½/¾,
  approach anchor. Anchors use nearest-within-tolerance and are not locked early.
- Journey coverage by binary thinning plus a "latest" slot.
- RAM only; unique JPEG references counted once; byte budget never exceeded; atomic
  slot replacement frees bytes only when the slot was the last owner.
- Sealed evidence is immutable, kept until release or TTL, cleared by Stop/new session.
- Plans carry the sealed frames' actual offsets (tolerance 1e-6 s). Missing or expired
  evidence must never fall back to the legacy parking bundle. Evidence is never a
  precondition for the local warning.

## Known limits

No gesture source; `UNKNOWN_INSIDE`; track ID switches; uncalibrated thresholds;
widening journey spacing on long walks; integration (T3) unproven.
