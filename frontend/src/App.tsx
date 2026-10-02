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
    videoState === VideoState.RUNNING,
  )
  const { snapshot: domainSnapshot, fetchError: domainError } = useCurrentDomain(
    effectiveState === VideoState.RUNNING,
  )
  const domainUi = getDomainUiState(effectiveState, domainSnapshot, domainError)
  const evaluation = useCurrentEvaluation(effectiveState === VideoState.RUNNING, lifecycleEpoch)
  const evaluationUi = getEvaluationUiState(effectiveState === VideoState.RUNNING, evaluation)
  const sourceLabel = status?.source
    ? status.source === 'webcam' ? 'Webcam' : 'Uploaded Video'
    : 'Tidak ada'

  return (
    <main className="app-shell">
      <header className="dashboard-header">
        <div><p className="eyebrow">MONITORING CONSOLE</p><h1>SENTINEL VISION AI</h1><p className="header-subtitle">Autonomous Video Analytics &amp; Real-Time Object Detection</p></div>
        <span className={`backend-badge ${health?.status === 'online' ? 'is-online' : 'is-offline'}`}><span className="status-dot" aria-hidden="true" />Backend {health?.status === 'online' ? 'Online' : health ? 'Offline' : 'Menghubungkan'}</span>
      </header>
      <section className="pipeline-card" aria-labelledby="pipeline-heading">
        <div className="section-heading"><div><p className="eyebrow">SYSTEM PIPELINE</p><h2 id="pipeline-heading">Status Analitik</h2></div></div>
        <ol className="pipeline-list">
          <PipelineStep label="Video Feed" detail={videoState === VideoState.RUNNING ? 'Aktif' : 'Menunggu'} active={videoState === VideoState.RUNNING} />
          <PipelineStep label="Object Detection" detail={latestDetection ? 'Aktif' : 'Menunggu'} active={latestDetection !== null} />
          <PipelineStep label="Domain Analysis" detail={domainUi.stage} active={domainUi.active} tone={domainUi.tone} />
          <PipelineStep label="Response System" detail={evaluationUi.label} active={evaluationUi.pipelineActive} />
        </ol>
      </section>
      <section className="monitoring-section">
        <div className="section-heading source-heading"><div><p className="eyebrow">VIDEO INPUT</p><h2>Sumber Video</h2></div>
          <VideoStatusBadge state={effectiveState} />
        </div>

        {fetchError && (
          <p className="inline-error" role="status">
            Status video tidak tersedia: {fetchError}
          </p>
        )}

        <div className="video-workspace">
          <div className="video-column">
            <VideoPreview state={effectiveState} />
            <div className="video-status-strip" aria-label="Status video dan AI"><span><small>Sumber aktif</small><strong>{sourceLabel}</strong></span><span><small>Status AI detector</small><strong>{detectorStatus(videoState, latestDetection, detectionError)}</strong></span><span><small>Status domain</small><strong><DomainStatusBadge value={domainUi} /></strong>{domainUi.detail && <small className="domain-detail">{domainUi.detail}</small>}</span></div>
            <VideoSourceSelector
              state={videoState}
              pendingState={pendingState}
              onPendingStateChange={handlePendingStateChange}
              onActionSettled={handleActionSettled}
            />
          </div>
          <DetectionPanel
            state={videoState}
            result={latestDetection}
            fetchError={detectionError}
          />
        </div>
        <EvaluationPanel ui={evaluationUi} />
      </section>
    </main>
  )
}

function PipelineStep({ label, detail, active, tone }: { label: string; detail: string; active: boolean; tone?: string }) {
  return <li className={`${active ? 'pipeline-step is-active' : 'pipeline-step'} ${tone ? `is-${tone}` : ''}`}><span className="pipeline-marker" aria-hidden="true" /><span><strong>{label}</strong><small>{detail}</small></span></li>
}

function detectorStatus(state: VideoState, result: unknown, error: string | null): string {
  if (state !== VideoState.RUNNING) return 'Menunggu'
  if (error) return 'Tidak tersedia'
  return result ? 'Aktif' : 'Menganalisis'
}

export default App
