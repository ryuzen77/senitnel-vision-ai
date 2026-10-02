import { vi } from 'vitest'
import { EvaluationCurrent } from '../api/videoApi'

export interface PendingEvaluation {
  signal: AbortSignal | undefined
  resolve: (view: EvaluationCurrent) => void
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

/**
 * Stubs global fetch for App tests. Evaluation requests stay pending until the
 * test resolves them. Video status follows a minimal backend: RUNNING
 * initially, IDLE after Stop and RUNNING again after Start.
 */
export function stubAppFetch(): PendingEvaluation[] {
  const evaluationCalls: PendingEvaluation[] = []
  let backendState = 'RUNNING'
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/evaluation/current') {
      return new Promise((resolve) => {
        evaluationCalls.push({
          signal: init?.signal ?? undefined,
          resolve: (view) => resolve(jsonResponse(view)),
        })
      })
    }
    if (url === '/api/video/status') {
      return Promise.resolve(jsonResponse({ state: backendState, source: backendState === 'RUNNING' ? 'webcam' : null, error: null }))
    }
    if (url === '/api/video/stop') {
      backendState = 'IDLE'
      return Promise.resolve(jsonResponse({ state: 'IDLE', source: null, error: null }))
    }
    if (url === '/api/video/webcam/start') {
      backendState = 'RUNNING'
      return Promise.resolve(jsonResponse({ state: 'RUNNING', message: 'ok' }))
    }
    if (url === '/api/detections/latest') return Promise.resolve(jsonResponse(null, 204))
    if (url === '/api/domain/current') {
      return Promise.resolve(jsonResponse({
        source_session_id: null, state: 'IDLE', domain: 'UNKNOWN', observation_count: 0, evidence_hits: 0,
        confidence: 0, locked: false, last_detection_seq: null, decision_source: null, scene_model: null,
        scene_sample_count: null, scene_top_score: null, scene_mean_margin: null, scene_consistency: null,
      }))
    }
    if (url === '/api/health') return Promise.resolve(jsonResponse({ status: 'online', service: 'test' }))
    return Promise.reject(new Error(`unexpected ${url}`))
  }))
  return evaluationCalls
}

export function completedView(session: string, eventId: string, reasoning: string): EvaluationCurrent {
  return {
    enabled: true,
    source_session_id: session,
    process_state: 'COMPLETED',
    active_event_id: null,
    latest_result: {
      event_id: eventId,
      source_session_id: session,
      assessment: {
        threatScore: 40,
        status: 'WARNING',
        reasoning,
        suspectProfile: {
          personDetected: true, clothing: 'jaket', observedAction: 'berjalan',
          visibleObjects: ['motor'], distinguishingFeatures: [],
        },
      },
    },
  }
}
