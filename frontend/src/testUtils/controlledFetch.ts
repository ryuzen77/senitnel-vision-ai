import { vi } from 'vitest'

export interface ControlledCall {
  url: string
  signal: AbortSignal | undefined
  /** Resolve with a JSON body; status >= 400 makes the API client throw. */
  respond: (body: unknown, status?: number) => void
}

const CONTROLLED = new Set([
  '/api/video/status',
  '/api/video/stop',
  '/api/video/webcam/start',
  '/api/evaluation/current',
])

/**
 * Stubs global fetch. Status, Start/Stop and evaluation requests stay pending
 * until the test answers them, in any order; other dashboard requests resolve
 * immediately with neutral data.
 */
export function stubControlledFetch(): { calls: (url: string) => ControlledCall[] } {
  const all: ControlledCall[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const response = (body: unknown, status = 200) => ({
      ok: status >= 200 && status < 300, status, json: async () => body,
    })
    if (CONTROLLED.has(url)) {
      return new Promise((resolve) => {
        all.push({ url, signal: init?.signal ?? undefined, respond: (body, status) => resolve(response(body, status)) })
      })
    }
    if (url === '/api/detections/latest') return Promise.resolve(response(null, 204))
    if (url === '/api/domain/current') {
      return Promise.resolve(response({
        source_session_id: null, state: 'IDLE', domain: 'UNKNOWN', observation_count: 0, evidence_hits: 0,
        confidence: 0, locked: false, last_detection_seq: null, decision_source: null, scene_model: null,
        scene_sample_count: null, scene_top_score: null, scene_mean_margin: null, scene_consistency: null,
      }))
    }
    if (url === '/api/health') return Promise.resolve(response({ status: 'online', service: 'test' }))
    return Promise.reject(new Error(`unexpected ${url}`))
  }))
  return { calls: (url) => all.filter((call) => call.url === url) }
}
