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

/** Thrown for client-side validation failures before any request is sent. */
export class VideoValidationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'VideoValidationError'
  }
}

export const VIDEO_STREAM_URL = '/api/video/stream'

const ALLOWED_MIME_TYPES = ['video/mp4', 'video/webm']
const MAX_FILE_BYTES = 200 * 1024 * 1024 // 200 MB

export async function startWebcam(): Promise<VideoStatusResponse> {
  const res = await fetch('/api/video/webcam/start', { method: 'POST' })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body?.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<VideoStatusResponse>
}

export async function uploadVideo(file: File): Promise<VideoStatusResponse> {
  // Client-side guards — no request sent on failure
  if (!ALLOWED_MIME_TYPES.includes(file.type)) {
    throw new VideoValidationError(
      `Tipe file tidak didukung: ${file.type}. Gunakan MP4 atau WebM.`,
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
  return res.json() as Promise<VideoStatusResponse>
}

export async function stopVideo(): Promise<VideoStatusResponse> {
  const res = await fetch('/api/video/stop', { method: 'POST' })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body?.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<VideoStatusResponse>
}

export async function getVideoStatus(): Promise<VideoStatusResponse> {
  const res = await fetch('/api/video/status')
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json() as Promise<VideoStatusResponse>
}
