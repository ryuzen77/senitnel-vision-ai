/**
 * Sentinel Vision AI — Video API client
 *
 * All backend communication for the /api/video/* endpoints lives here.
 * VIDEO_STREAM_URL is a plain constant — cache-busting is handled once
 * per RUNNING session inside VideoPreview (via useRef), not here.
 */

export enum VideoState {
  IDLE = 'IDLE',
  STARTING = 'STARTING',
  RUNNING = 'RUNNING',
  STOPPING = 'STOPPING',
  ERROR = 'ERROR',
}

export interface VideoStatusResponse {
  state: VideoState
  source: string | null
  error: string | null
}

export interface VideoStartResponse {
  state: VideoState
  message: string
}

export interface Detection {
  class_id: number
  class_name: string
  confidence: number
  xyxy: [number, number, number, number]
}

export interface DetectionResult {
  detection_seq: number
  source_session_id: string
  source_frame_seq: number
  source_timestamp: number
  detections: Detection[]
}

export type DomainState = 'IDLE' | 'PROBING' | 'LOCKED'
export type DomainName = 'UNKNOWN' | 'PARKING_MOTORCYCLE' | 'RETAIL_STORE'
export type DecisionSource = 'YOLO' | 'CLIP'

export interface DomainSnapshot {
  source_session_id: string | null
  state: DomainState
  domain: DomainName
  observation_count: number
  evidence_hits: number
  confidence: number
  locked: boolean
  last_detection_seq: number | null
  decision_source: DecisionSource | null
  scene_model: string | null
  scene_sample_count: number | null
  scene_top_score: number | null
  scene_mean_margin: number | null
  scene_consistency: number | null
}

/** Mirrors backend EvaluationWorkerState; this is process state, not a threat level. */
export type EvaluationProcessState =
  | 'IDLE'
  | 'DISPATCHING'
  | 'WAITING_CALLBACK'
  | 'COMPLETED'
  | 'FAILED'
  | 'TIMED_OUT'
  | 'CANCELLED'

/** Mirrors backend ThreatStatus of a validated AI assessment. */
export type ThreatStatus = 'NORMAL' | 'WARNING' | 'CRITICAL'

export interface EvaluationSubjectProfile {
  personDetected: boolean
  clothing: string
  observedAction: string
  visibleObjects: string[]
  distinguishingFeatures: string[]
}

export interface EvaluationAssessment {
  threatScore: number
  status: ThreatStatus
  reasoning: string
  suspectProfile: EvaluationSubjectProfile
}

export interface EvaluationLatestResult {
  event_id: string
  source_session_id: string
  assessment: EvaluationAssessment
}

/** GET /api/evaluation/current — process-local, session-scoped view. */
export interface EvaluationCurrent {
  enabled: boolean
  source_session_id: string | null
  process_state: EvaluationProcessState
  active_event_id: string | null
  latest_result: EvaluationLatestResult | null
}

/** Thrown for client-side validation failures before any request is sent. */
export class VideoValidationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'VideoValidationError'
  }
}

export const VIDEO_STREAM_URL = '/api/video/stream'

const ALLOWED_MIME_TYPES = ['video/mp4', 'video/webm']
const ALLOWED_EXTENSIONS = ['.mp4', '.webm']
const MAX_FILE_BYTES = 200 * 1024 * 1024 // 200 MB

export async function startWebcam(): Promise<VideoStartResponse> {
  const res = await fetch('/api/video/webcam/start', { method: 'POST' })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body?.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<VideoStartResponse>
}

export async function uploadVideo(file: File): Promise<VideoStartResponse> {
  // Client-side guards — no request sent on failure
  if (!ALLOWED_MIME_TYPES.includes(file.type)) {
    throw new VideoValidationError(
      `Tipe file tidak didukung: ${file.type}. Gunakan MP4 atau WebM.`,
    )
  }
  const dotIndex = file.name.lastIndexOf('.')
  const extension = dotIndex >= 0 ? file.name.slice(dotIndex).toLowerCase() : ''
  if (!ALLOWED_EXTENSIONS.includes(extension)) {
    throw new VideoValidationError(
      `Ekstensi file tidak didukung: "${extension || '(tidak ada)'}". Gunakan .mp4 atau .webm.`,
    )
  }
  if (file.size > MAX_FILE_BYTES) {
    throw new VideoValidationError(
      `Ukuran file melebihi batas 200 MB (${(file.size / 1024 / 1024).toFixed(1)} MB).`,
    )
  }

  const form = new FormData()
  form.append('file', file)

  const res = await fetch('/api/video/upload', { method: 'POST', body: form })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body?.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<VideoStartResponse>
}

export async function stopVideo(): Promise<VideoStatusResponse> {
  const res = await fetch('/api/video/stop', { method: 'POST' })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body?.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<VideoStatusResponse>
}

export async function getVideoStatus(signal?: AbortSignal): Promise<VideoStatusResponse> {
  const res = await fetch('/api/video/status', { signal })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json() as Promise<VideoStatusResponse>
}

/** Returns null when the detector has not completed a result for this session. */
export async function getLatestDetection(signal?: AbortSignal): Promise<DetectionResult | null> {
  const res = await fetch('/api/detections/latest', { signal })
  if (res.status === 204) {
    return null
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body?.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<DetectionResult>
}

/** Returns the read-only evaluation view; any non-2xx is a frontend fetch error. */
export async function getCurrentEvaluation(signal?: AbortSignal): Promise<EvaluationCurrent> {
  const res = await fetch('/api/evaluation/current', { signal })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json() as Promise<EvaluationCurrent>
}

/** Returns the current read-only, session-scoped domain snapshot. */
export async function getCurrentDomain(signal?: AbortSignal): Promise<DomainSnapshot> {
  const res = await fetch('/api/domain/current', { signal })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body?.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<DomainSnapshot>
}
