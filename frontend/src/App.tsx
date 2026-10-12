import { privateMutationAvailable, SECURE_CONTEXT_MESSAGE } from './api/preparedSourceApi'
import { PrivateParkingConfiguration } from './components/PrivateParkingConfiguration'
import { useSourcePreparation } from './hooks/useSourcePreparation'
import { useCallback, useEffect, useState } from 'react'
import { VideoPreview } from './components/VideoPreview'
import { DetectionPanel } from './components/DetectionPanel'
import { EvaluationPanel, getEvaluationUiState } from './components/EvaluationPanel'
import { VideoSourceSelector } from './components/VideoSourceSelector'
import { VideoStatusBadge } from './components/VideoStatusBadge'
import { VideoState } from './api/videoApi'
import { useVideoStatus } from './hooks/useVideoStatus'
import { useLatestDetection } from './hooks/useLatestDetection'
import { useCurrentDomain } from './hooks/useCurrentDomain'
import { startsNewLifecycle, useCurrentEvaluation } from './hooks/useCurrentEvaluation'
import { DomainStatusBadge, getDomainUiState } from './components/DomainStatusBadge'

interface HealthResponse {
  status: string
  service: string
}

function App() {
  useEffect(() => { document.documentElement.lang = 'id' }, [])
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [pendingState, setPendingState] = useState<VideoState | null>(null)
  // Bumped when a Start/Stop action begins and again when it settles. Status and
  // evaluation polling restart on every bump, so a response requested before or
  // during an action can never update the state that follows it, and a quick
  // Stop→Start cannot be hidden by React batching or a slow status poll.
  const [lifecycleEpoch, setLifecycleEpoch] = useState(0)
  // True after an action settled until the first status read of the new epoch.
  const [awaitingFreshStatus, setAwaitingFreshStatus] = useState(false)
  const { status, fetchError } = useVideoStatus(lifecycleEpoch)
  const handlePendingStateChange = useCallback((next: VideoState | null) => {
    if (startsNewLifecycle(next)) {
      setLifecycleEpoch((epoch) => epoch + 1)
      setAwaitingFreshStatus(false)
    }
    setPendingState(next)
  }, [])
  const handleActionSettled = useCallback(() => {
    setLifecycleEpoch((epoch) => epoch + 1)
    setAwaitingFreshStatus(true)
  }, [])

  const handlePrivateLifecycle = useCallback(() => {
    setLifecycleEpoch(epoch => epoch + 1)
    setPendingState(null)
    setAwaitingFreshStatus(true)
  }, [])
  const preparation = useSourcePreparation(handlePrivateLifecycle)
  const privateMode = preparation.capabilities?.mode === 'private'
  const legacyMode = preparation.capabilities?.mode === 'legacy'
  // The first status read after an action settles is authoritative whatever its
  // value (e.g. IDLE after EOF, RUNNING after a failed Stop), replacing the
  // optimistic or transitional pending state.
  useEffect(() => {
    if (awaitingFreshStatus && status !== null) {
      setAwaitingFreshStatus(false)
      setPendingState(null)
    }
  }, [awaitingFreshStatus, status])

  useEffect(() => {
    fetch('/api/health')
      .then((res) => res.json())
      .then((data: HealthResponse) => setHealth(data))
      .catch(() => setHealth({ status: 'unreachable', service: '—' }))
  }, [])

  // IDLE while the current epoch has no status yet (initial load included).
  const videoState = status?.state ?? VideoState.IDLE
  // The optimistic/transitional state of an action wins until a fresh status read.
  const effectiveState = pendingState ?? videoState
  const { result: latestDetection, fetchError: detectionError } = useLatestDetection(
    videoState === VideoState.RUNNING, lifecycleEpoch, status?.session_id,
  )
  const { snapshot: domainSnapshot, fetchError: domainError } = useCurrentDomain(
    effectiveState === VideoState.RUNNING, lifecycleEpoch, status?.session_id,
  )
  const domainUi = getDomainUiState(effectiveState, domainSnapshot, domainError)
  const evaluation = useCurrentEvaluation(effectiveState === VideoState.RUNNING, lifecycleEpoch, status?.session_id)
  const evaluationUi = getEvaluationUiState(effectiveState === VideoState.RUNNING, evaluation)
  const sourceLabel = status?.source
    ? status.source === 'webcam' ? 'Kamera' : 'File video'
    : 'Tidak ada'

  return (
    <main className="app-shell">
      <header className="dashboard-header">
        <div><h1>Sentinel Vision AI</h1><p className="header-subtitle">Pemantauan video</p></div>
        <span className="backend-badge" title="Koneksi diperiksa saat halaman dibuka; bukan status kesiapan AI atau audio.">Layanan {health?.status === 'online' ? 'terhubung saat membuka halaman' : health ? 'belum terhubung' : 'sedang diperiksa'}</span>
      </header>
      <section className="monitoring-section" aria-label="Pemantauan video dan penilaian AI">
        {privateMode && effectiveState !== VideoState.RUNNING && <PrivateParkingConfiguration preparation={preparation} />}
        {!preparation.capabilities && <p className="inline-error" role="status">Mode sumber belum dapat dipastikan. Start baru ditahan.</p>}
        {preparation.capabilities?.mode === 'disabled' && <p className="inline-error" role="status">Sumber video tidak tersedia pada mode ini.</p>}
        {privateMode && effectiveState === VideoState.RUNNING && !privateMutationAvailable() && <p className="inline-error" role="alert">{SECURE_CONTEXT_MESSAGE}</p>}
        <div className="video-workspace">
          {(!privateMode || effectiveState === VideoState.RUNNING || effectiveState === VideoState.ERROR) && <section className="video-column" aria-labelledby="video-heading">
            <div className="section-heading source-heading"><h2 id="video-heading">Video aktif</h2><VideoStatusBadge state={effectiveState} /></div>
            {fetchError && <p className="inline-error" role="status">Status video belum dapat dimuat.</p>}
            <VideoPreview state={effectiveState} sessionKey={status?.session_ref} />
            {(legacyMode || effectiveState === VideoState.RUNNING || effectiveState === VideoState.ERROR) && <VideoSourceSelector
              startAllowed={legacyMode}
              mutationAllowed={true}
              state={videoState}
              pendingState={pendingState}
              onPendingStateChange={handlePendingStateChange}
              onActionSettled={handleActionSettled}
            />}
            <div className="video-status-strip" aria-label="Sumber dan lingkungan"><span>Sumber: <strong>{sourceLabel}</strong></span><span>Lingkungan: <DomainStatusBadge value={domainUi} /></span></div>
          </section>}
          <EvaluationPanel ui={evaluationUi} />
        </div>
        <div className="secondary-details">
          <details><summary>Detail objek</summary><DetectionPanel state={videoState} result={latestDetection} fetchError={detectionError} /></details>
          <details><summary>Detail sistem</summary><dl className="system-details"><dt>Deteksi objek</dt><dd>{detectorStatus(videoState, latestDetection, detectionError)}</dd><dt>Lingkungan</dt><dd>{domainUi.stage}</dd>{domainUi.detail && <><dt>Rincian klasifikasi</dt><dd>{domainUi.detail}</dd></>}</dl></details>
        </div>
        <p className="access-help">Penilaian AI berbeda dari peringatan akses lokal. Status peringatan akses lokal belum tersedia di tampilan ini.</p>
      </section>
    </main>
  )
}

function detectorStatus(state: VideoState, result: unknown, error: string | null): string {
  if (state !== VideoState.RUNNING) return 'Menunggu'
  if (error) return 'Tidak tersedia'
  return result ? 'Aktif' : 'Menganalisis'
}

export default App
