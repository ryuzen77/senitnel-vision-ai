# Gemini `generateContent` — verified facts used by the direct transport

Checked against the official Gemini API reference and guides (ai.google.dev) between
2026-10-03 and 2026-10-04, without inference requests unless stated. Re-check before
changing the client; Google documents may change.

Sources: `https://ai.google.dev/api/generate-content`,
`https://ai.google.dev/gemini-api/docs/generate-content/structured-output`,
`https://ai.google.dev/gemini-api/docs/models`, `https://ai.google.dev/gemini-api/docs/api-errors`.

## Request

- `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`,
  header `x-goog-api-key`.
- `contents[].parts[]` with `text` or `inlineData{mimeType, data(base64)}`.
- Structured output: `generationConfig.responseFormat.text{mimeType, schema}`.
  **`mimeType` is the enum `MimeType`** (`MIME_TYPE_UNSPECIFIED`, `APPLICATION_JSON`,
  `TEXT_PLAIN`). The structured-output guide's curl example shows `"application/json"`,
  but the provider rejected that value with HTTP 400 `INVALID_ARGUMENT` on
  `generation_config.response_format.text.mime_type` (live, 2026-10-03); the enum
  value was then accepted (live COMPLETED, same day).
- `responseMimeType`/`responseSchema` are the older fields (marked deprecated in the
  reference); the official JS SDK `@google/genai` 2.4.0 and 2.27.0 still send them
  for the Developer API and never send `responseFormat` there (source read 2026-10-04).
- Supported schema keywords used: `type`, `properties`, `required`,
  `additionalProperties`, `enum`, `minimum`, `maximum`, `items`, `maxItems`, `description`.

## Response

- Text in `candidates[0].content.parts[].text`; parts with `thought` are skipped.
- Only `finishReason == STOP` is accepted; `MAX_TOKENS`, safety/policy reasons, other
  documented reasons and unknown values are rejected (mapped in the client).
- `promptFeedback.blockReason` means the prompt was blocked.
- The error body shape for `generateContent` is not documented by Gemini; the client
  extracts only allowlisted `google.rpc` fields from 4xx bodies (≤ 16 KiB).

## Model

`gemini-3.5-flash-lite` is listed as stable with image input, structured outputs and
thinking supported (models page, 2026-10-03), although the structured-output guide's
model table did not list it. Thinking level and output token limit are left at model
defaults (open decision).

## Not used

The Interactions API (`/v1beta/interactions`) is the newer surface; it stores
interactions by default (`store=true`). Migration is a separate task and must set
`store=false`.
