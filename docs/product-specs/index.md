# Product specs

What the product promises to the person watching the dashboard. Behaviour not listed
as "runtime" is not available to users yet.

## Users and setting

A small operator (home, kost, small shop) runs the app locally on a Windows PC with a
webcam or an uploaded clip. One camera, one session at a time.

## Domains

| Domain | Meaning | Status |
|---|---|---|
| `UNKNOWN` | No confident scene lock (CLIP may lock it deliberately) | runtime |
| `PARKING_MOTORCYCLE` | Motorcycle parking area; legacy person↔motorcycle candidates | runtime |
| — private mode | Home/kost parking with entry line, zones and authorization | core only (T1/T2), integration draft |
| `RETAIL_STORE` | Shop display zones; candidates when a person stays in a zone | core only |

## Behaviour the user sees (runtime)

- Live preview, detection list, domain badge, evaluation panel (Indonesian copy).
- When evaluation is enabled and a parking candidate is evaluated, the panel shows score,
  status (NORMAL/WARNING/CRITICAL), reasoning and observed profile, with the note
  "Penilaian otomatis, bukan bukti pelanggaran. Perlu ditinjau oleh petugas."
- Optional local audio (off by default): the owner-configured WAV (kept outside the
  repository) plays once for a CRITICAL parking assessment, with cooldown; Stop cuts it.

## Private parking (planned behaviour, not available)

1. A person crosses the entry line → watchful episode starts.
2. A valid gesture (source not built yet) authorizes that episode only.
3. An unauthorized episode that approaches a parked motorcycle → one **local access
   warning** that does not wait for the cloud (target under one second, unverified)
   and one cloud assessment from journey evidence.
4. Entering with a motorcycle does not authorize approaching other motorcycles.
The local warning is a rule outcome, not a CRITICAL score; the assessment may arrive
later and may be NORMAL. See [`../design-docs/private-parking-mode.md`](../design-docs/private-parking-mode.md).

## Out of scope today

Identity recognition, incident history/database, multi-camera, cashier domain,
notifications beyond local audio.
