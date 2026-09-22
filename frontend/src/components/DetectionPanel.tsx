import { DetectionResult, VideoState } from '../api/videoApi'

interface Props {
  state: VideoState
  result: DetectionResult | null
  fetchError: string | null
}

export function DetectionPanel({ state, result, fetchError }: Props) {
  if (state !== VideoState.RUNNING) {
    return <PanelShell><p className="detection-muted">AI detector menunggu sumber video</p></PanelShell>
  }

  if (fetchError) {
    return (
      <PanelShell>
        <p className="detection-error">Data deteksi sementara tidak tersedia</p>
        <p className="detection-muted">{fetchError}</p>
      </PanelShell>
    )
  }

  if (result === null) {
    return <PanelShell><p className="detection-muted">Menganalisis frame…</p></PanelShell>
  }

  if (result.detections.length === 0) {
    return (
      <PanelShell result={result}>
        <p className="detection-muted">Tidak ada objek pada frame terbaru</p>
      </PanelShell>
    )
  }

  return (
    <PanelShell result={result}>
      <p className="detection-count">{result.detections.length} objek terdeteksi</p>
      <ul className="detection-list">
        {result.detections.map((detection, index) => (
          <li key={`${detection.class_id}-${index}`} className="detection-item">
            <span>{detection.class_name}</span>
            <strong>{(detection.confidence * 100).toFixed(1)}%</strong>
          </li>
        ))}
      </ul>
    </PanelShell>
  )
}

function PanelShell({ children, result }: { children: React.ReactNode; result?: DetectionResult }) {
  return (
    <aside className="detection-panel" aria-live="polite">
      <h2>Deteksi Objek</h2>
      {result && (
        <div className="detection-meta">
          <span>Frame #{result.source_frame_seq}</span>
          <span>Deteksi #{result.detection_seq}</span>
          <span title={result.source_session_id}>Sesi {shortSessionId(result.source_session_id)}</span>
        </div>
      )}
      {children}
    </aside>
  )
}

function shortSessionId(sessionId: string): string {
  return sessionId.length > 10 ? `${sessionId.slice(0, 8)}…` : sessionId
}
