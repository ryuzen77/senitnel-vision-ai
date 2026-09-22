import { DomainSnapshot, VideoState } from '../api/videoApi'

export interface DomainUiState {
  stage: string
  domain: string
  detail: string | null
  tone: 'idle' | 'probing' | 'active' | 'unknown' | 'unavailable'
  active: boolean
}

export function getDomainUiState(videoState: VideoState, snapshot: DomainSnapshot | null, fetchError: string | null): DomainUiState {
  if (videoState !== VideoState.RUNNING) return { stage: 'Belum aktif', domain: 'Menunggu sumber video', detail: null, tone: 'idle', active: false }
  if (fetchError) return { stage: 'Status domain tidak tersedia', domain: 'Status domain tidak tersedia', detail: null, tone: 'unavailable', active: false }
  if (snapshot === null || snapshot.state === 'IDLE') return { stage: 'Menganalisis', domain: 'Mendeteksi lingkungan', detail: null, tone: 'probing', active: true }
  if (snapshot.state === 'PROBING') return { stage: 'Menganalisis', domain: 'Mendeteksi lingkungan', detail: `${Math.min(snapshot.observation_count, 5)}/5 sampel`, tone: 'probing', active: true }
  if (snapshot.domain === 'PARKING_MOTORCYCLE') return lockedUi('Parkiran Motor', snapshot, 'active')
  if (snapshot.domain === 'RETAIL_STORE') return lockedUi('Toko / Retail', snapshot, 'active')
  return lockedUi('Lingkungan tidak dikenali', snapshot, 'unknown', 'Di luar domain')
}

function lockedUi(domain: string, snapshot: DomainSnapshot, tone: DomainUiState['tone'], stage = 'Aktif'): DomainUiState {
  const score = snapshot.decision_source === 'CLIP' ? snapshot.scene_top_score : snapshot.confidence
  const scoreLabel = score === null ? null : `${snapshot.decision_source === 'CLIP' ? 'Skor' : 'Keyakinan'} ${(score * 100).toFixed(1)}%`
  const sampleLabel = snapshot.scene_sample_count === null ? null : `${snapshot.scene_sample_count} sampel`
  return { stage, domain, detail: [snapshot.decision_source, scoreLabel, sampleLabel].filter(Boolean).join(' · ') || null, tone, active: true }
}

export function DomainStatusBadge({ value }: { value: DomainUiState }) {
  return <span className={`domain-state ${value.tone}`}>{value.domain}</span>
}
