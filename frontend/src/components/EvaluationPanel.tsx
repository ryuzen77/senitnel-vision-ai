import { EvaluationCurrent, EvaluationLatestResult, ThreatStatus } from '../api/videoApi'
import { CurrentEvaluationState } from '../hooks/useCurrentEvaluation'

export type EvaluationUiKind =
  | 'waiting-video'
  | 'loading'
  | 'rebinding'
  | 'network-error'
  | 'disabled'
  | 'idle'
  | 'dispatching'
  | 'waiting-callback'
  | 'completed'
  | 'failed'
  | 'timed-out'
  | 'cancelled'
  | 'unknown'

export interface EvaluationUiState {
  kind: EvaluationUiKind
  /** Short process label; always text so state never depends on colour alone. */
  label: string
  message: string | null
  detail: string | null
  tone: 'idle' | 'progress' | 'active' | 'warning' | 'unavailable'
  pipelineActive: boolean
  activeEventId: string | null
  result: { latest: EvaluationLatestResult; previous: boolean } | null
}

const THREAT_LABELS: Record<ThreatStatus, string> = {
  NORMAL: 'Normal',
  WARNING: 'Perlu perhatian',
  CRITICAL: 'Kritis',
}

/** Only show an assessment that belongs to the session of this very response. */
function sessionResult(view: EvaluationCurrent): EvaluationLatestResult | null {
  const latest = view.latest_result
  if (latest === null || view.source_session_id === null) return null
  return latest.source_session_id === view.source_session_id ? latest : null
}

export function getEvaluationUiState(videoRunning: boolean, evaluation: CurrentEvaluationState): EvaluationUiState {
  const base = { detail: null, pipelineActive: false, activeEventId: null, result: null }
  if (!videoRunning) {
    return { ...base, kind: 'waiting-video', label: 'Belum aktif', message: 'Menunggu sumber video.', tone: 'idle' }
  }
  if (evaluation.fetchError !== null) {
    return {
      ...base, kind: 'network-error', label: 'Tidak tersedia',
      message: 'Status evaluasi tidak dapat dimuat. Dicoba lagi otomatis.',
      detail: evaluation.fetchError, tone: 'unavailable',
    }
  }
  if (evaluation.rebinding) {
    return { ...base, kind: 'rebinding', label: 'Memuat', message: 'Sesi video berganti; memuat status sesi terbaru…', tone: 'progress' }
  }
  const view = evaluation.view
  if (view === null) {
    return { ...base, kind: 'loading', label: 'Memuat', message: 'Memuat status evaluasi…', tone: 'progress' }
  }
  if (!view.enabled) {
    return { ...base, kind: 'disabled', label: 'Nonaktif', message: 'Evaluasi AI eksternal tidak diaktifkan.', tone: 'idle' }
  }

  const latest = sessionResult(view)
  const activeEventId = view.source_session_id === null ? null : view.active_event_id
  // A result is "previous" unless it is the completed evaluation itself.
  const previous = latest !== null && (
    view.process_state !== 'COMPLETED' || (activeEventId !== null && activeEventId !== latest.event_id)
  )
  const result = latest === null ? null : { latest, previous }
  const common = { detail: null, activeEventId, result }

  switch (view.process_state) {
    case 'IDLE':
      return { ...common, kind: 'idle', label: 'Siaga', message: latest ? null : 'Belum ada kandidat untuk dievaluasi pada sesi ini.', tone: 'idle', pipelineActive: true }
    case 'DISPATCHING':
      return { ...common, kind: 'dispatching', label: 'Mengirim', message: 'Mengirim kandidat untuk dianalisis…', tone: 'progress', pipelineActive: true }
    case 'WAITING_CALLBACK':
      return { ...common, kind: 'waiting-callback', label: 'Menunggu hasil', message: 'Menunggu hasil analisis AI…', tone: 'progress', pipelineActive: true }
    case 'COMPLETED':
      return { ...common, kind: 'completed', label: 'Selesai', message: latest ? null : 'Hasil evaluasi belum tersedia.', tone: 'active', pipelineActive: true }
    case 'FAILED':
      return { ...common, kind: 'failed', label: 'Gagal', message: 'Evaluasi tidak dapat diselesaikan oleh layanan analisis.', tone: 'warning', pipelineActive: true }
    case 'TIMED_OUT':
      return { ...common, kind: 'timed-out', label: 'Waktu habis', message: 'Hasil analisis tidak diterima tepat waktu.', tone: 'warning', pipelineActive: true }
    case 'CANCELLED':
      return { ...common, kind: 'cancelled', label: 'Dibatalkan', message: 'Evaluasi dibatalkan.', tone: 'idle', pipelineActive: false }
    default:
      return { ...common, kind: 'unknown', label: 'Tidak dikenal', message: 'Status evaluasi tidak dikenal.', tone: 'unavailable', pipelineActive: false }
  }
}

function shortId(id: string): string {
  return id.length > 10 ? `${id.slice(0, 8)}…` : id
}

function listText(items: string[]): string {
  return items.length > 0 ? items.join(', ') : '—'
}

export function EvaluationPanel({ ui }: { ui: EvaluationUiState }) {
  const result = ui.result
  return (
    <section className="evaluation-panel" aria-labelledby="evaluation-heading" aria-live="polite">
      <div className="detection-heading">
        <div><p className="eyebrow">RESPONSE SYSTEM</p><h2 id="evaluation-heading">Evaluasi AI</h2></div>
        <span className={`evaluation-state is-${ui.tone}`}>Status: {ui.label}</span>
      </div>
      {ui.message && <p className={ui.kind === 'network-error' ? 'detection-error' : 'detection-muted evaluation-message'}>{ui.message}</p>}
      {ui.detail && <p className="detection-muted">{ui.detail}</p>}
      {ui.activeEventId && (
        <p className="evaluation-meta"><span title={ui.activeEventId}>Event diproses: {shortId(ui.activeEventId)}</span></p>
      )}
      {result && (
        <article className="evaluation-result" aria-label={result.previous ? 'Hasil evaluasi sebelumnya' : 'Hasil analisis AI'}>
          {result.previous && (
            <p className="evaluation-previous">
              Hasil evaluasi sebelumnya · <span title={result.latest.event_id}>Event {shortId(result.latest.event_id)}</span>
            </p>
          )}
          <h3>Hasil analisis AI — perlu ditinjau</h3>
          <p className="evaluation-meta">
            <span title={result.latest.event_id}>Event {shortId(result.latest.event_id)}</span>
            <span title={result.latest.source_session_id}>Sesi {shortId(result.latest.source_session_id)}</span>
          </p>
          <div className="evaluation-score">
            <span>Skor AI <strong>{result.latest.assessment.threatScore}</strong>/100</span>
            <span className={`threat-status is-${result.latest.assessment.status.toLowerCase()}`}>
              Status penilaian: {THREAT_LABELS[result.latest.assessment.status] ?? result.latest.assessment.status}
            </span>
          </div>
          <p className="evaluation-note">Penilaian otomatis, bukan bukti pelanggaran. Perlu ditinjau oleh petugas.</p>
          <h4>Alasan</h4>
          <p className="evaluation-text">{result.latest.assessment.reasoning}</p>
          <h4>Profil subjek teramati</h4>
          <dl className="evaluation-profile">
            <dt>Orang terdeteksi</dt><dd>{result.latest.assessment.suspectProfile.personDetected ? 'Ya' : 'Tidak'}</dd>
            <dt>Pakaian</dt><dd>{result.latest.assessment.suspectProfile.clothing || '—'}</dd>
            <dt>Aksi teramati</dt><dd>{result.latest.assessment.suspectProfile.observedAction || '—'}</dd>
            <dt>Objek terlihat</dt><dd>{listText(result.latest.assessment.suspectProfile.visibleObjects)}</dd>
            <dt>Ciri pembeda</dt><dd>{listText(result.latest.assessment.suspectProfile.distinguishingFeatures)}</dd>
          </dl>
        </article>
      )}
    </section>
  )
}
