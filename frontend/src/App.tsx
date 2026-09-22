import { useEffect, useState } from 'react'
import { VideoPreview } from './components/VideoPreview'
import { DetectionPanel } from './components/DetectionPanel'
import { VideoSourceSelector } from './components/VideoSourceSelector'
import { VideoStatusBadge } from './components/VideoStatusBadge'
import { VideoState } from './api/videoApi'
import { useVideoStatus } from './hooks/useVideoStatus'
import { useLatestDetection } from './hooks/useLatestDetection'
import { useCurrentDomain } from './hooks/useCurrentDomain'
import { DomainStatusBadge, getDomainUiState } from './components/DomainStatusBadge'

interface HealthResponse {
  status: string
  service: string
}

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const { status, fetchError } = useVideoStatus()
  const [pendingState, setPendingState] = useState<VideoState | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((res) => res.json())
      .then((data: HealthResponse) => setHealth(data))
      .catch(() => setHealth({ status: 'unreachable', service: '—' }))
  }, [])

  const videoState = status?.state ?? VideoState.IDLE
  // Use the optimistic pending state from VideoSourceSelector until
  // the backend poll confirms it, then fall back to the polled state.
  const effectiveState = pendingState ?? videoState
  const { result: latestDetection, fetchError: detectionError } = useLatestDetection(
    videoState === VideoState.RUNNING,
  )
  const { snapshot: domainSnapshot, fetchError: domainError } = useCurrentDomain(
    effectiveState === VideoState.RUNNING,
  )
  const domainUi = getDomainUiState(effectiveState, domainSnapshot, domainError)
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
          <PipelineStep label="Response System" detail="Belum aktif" active={false} />
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
            <VideoSourceSelector state={videoState} onPendingStateChange={setPendingState} />
          </div>
          <DetectionPanel
            state={videoState}
            result={latestDetection}
            fetchError={detectionError}
          />
        </div>
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
