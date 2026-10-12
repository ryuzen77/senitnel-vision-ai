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
    if (url === '/api/video/sources/capabilities') return Promise.resolve(jsonResponse({mode:'legacy',preparation_available:false,template_available:false,evaluation_enabled:false,source_kinds:['file'],limits:{max_upload_bytes:209715200,accepted_types:['video/mp4','video/webm'],max_zones:8,min_line_length:.05,min_zone_area:.005,max_configuration_bytes:4096}}))
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

/** Fake P1c responses for tests only; never imported by product code. */
export const sourceId='11111111-1111-4111-8111-111111111111'
export const intentId='22222222-2222-4222-8222-222222222222'
export const sessionId='33333333-3333-4333-8333-333333333333'
export const privateCaps={mode:'private',preparation_available:true,template_available:false,evaluation_enabled:false,source_kinds:['file'],limits:{max_upload_bytes:209715200,accepted_types:['video/mp4','video/webm'],max_zones:8,min_line_length:.05,min_zone_area:.005,max_configuration_bytes:4096}}
export const validConfiguration={frame_width:640,frame_height:480,line:{p1:[.2,.5] as [number,number],p2:[.8,.5] as [number,number]},inside:'right' as const,zones:[{name:'zona-1',rect:[.1,.1,.5,.5] as [number,number,number,number]}]}
export function preparedSnapshot(overrides:Record<string,unknown>={}) {
 return {source_id:sourceId,generation:1,state:'PREPARED',upload_operation_id:intentId,frame_width:640,frame_height:480,preview_revision:1,config:null,geometry_unstable:false,template_available:false,next_attempt:1,last_attempt:null,expires_in_seconds:1800,deadline_in_seconds:null,cleanup:{pending:false,reason:null,attempts:0,capture:null,capture_attempts:0},analysis:{stop_required:false,state:'IDLE',session_ref:null,session_id:null},...overrides}
}
export function attemptRecord(outcome='STARTED',overrides:Record<string,unknown>={}) {return {attempt:1,operation_id:intentId,outcome,reason:null,session_ref:sessionId,session_id:sessionId,session:{phase:'RUNNING',end_reason:null,cleanup_ok:null},...overrides}}
export function sourceResponse(body:unknown,status=200) {return {ok:status>=200&&status<300,status,json:async()=>body} as Response}
