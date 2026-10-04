# Security and privacy

## Secrets

- Configuration comes from environment variables in the owner's shell; nothing loads
  `.env` automatically. `.env*` files are git-ignored; `backend/.env.smoke.local` may
  exist locally and must never be printed, staged or copied.
- Agents never read another process's environment or print keys, tokens, private URLs.
- The Gemini key is sent only as the `x-goog-api-key` header, never in a URL, log,
  exception or `repr`. A key was once exposed in chat while reading a Langflow flow
  export; the owner was advised to rotate it. Flow exports with credentials must never
  be committed or printed.
- The Langflow callback uses a static shared token header compared in constant time;
  deploy only over HTTPS with a long random token.

## Network surface

The backend is meant to bind `127.0.0.1`. CORS allows only the local Vite origin.
Only the callback endpoint is authenticated; exposing the API beyond localhost needs a
separate decision.

## Images and personal data

JPEG bytes live in RAM only (frame buffer, evidence sampler, outgoing request);
uploads are deleted after playback and partial uploads always. Bytes fields use
`repr=False`. Logs carry short IDs, states, codes and durations only — no prompts,
images, base64, reasoning or provider bodies. Provider 4xx bodies are read bounded
(16 KiB) and reduced to allowlisted tokens. Tracks are anonymous and session-scoped;
no face or identity recognition exists, and gesture authorization is not identity.

## Supply chain

Install packages only with owner permission; do not run `npm audit fix --force`.
Third-party reference files at the repository root (`geminiService.ts`,
`package.json`) belong to an older project and are not part of the build.
