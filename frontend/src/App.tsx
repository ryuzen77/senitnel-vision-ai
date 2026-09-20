import { useEffect, useState } from 'react'
import { VideoPreview } from './components/VideoPreview'
import { VideoSourceSelector } from './components/VideoSourceSelector'
import { VideoStatusBadge } from './components/VideoStatusBadge'
import { VideoState } from './api/videoApi'
import { useVideoStatus } from './hooks/useVideoStatus'

interface HealthResponse {
  status: string
  service: string
}

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const { status, fetchError } = useVideoStatus()

  useEffect(() => {
    fetch('/api/health')
      .then((res) => res.json())
      .then((data: HealthResponse) => setHealth(data))
      .catch(() => setHealth({ status: 'unreachable', service: '—' }))
  }, [])

  const videoState = status?.state ?? VideoState.IDLE

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', padding: '2rem', maxWidth: 800, margin: '0 auto' }}>
      <h1 style={{ marginBottom: '0.25rem' }}>Sentinel Vision AI</h1>

      {/* Health */}
      {health ? (
        <p style={{ margin: '0 0 1.5rem', color: '#57606a', fontSize: '0.875rem' }}>
          Backend: <strong>{health.status}</strong> — {health.service}
        </p>
      ) : (
        <p style={{ margin: '0 0 1.5rem', color: '#57606a', fontSize: '0.875rem' }}>
          Menghubungkan ke backend…
        </p>
      )}

      {/* Video panel */}
      <section>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: '0.75rem' }}>
          <h2 style={{ margin: 0, fontSize: '1rem' }}>Sumber Video</h2>
          <VideoStatusBadge state={videoState} />
        </div>

        {fetchError && (
          <p style={{ color: '#991b1b', fontSize: '0.8rem', marginBottom: 8 }}>
            Status error: {fetchError}
          </p>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
          <VideoSourceSelector state={videoState} />
          <VideoPreview state={videoState} />
        </div>
      </section>
    </main>
  )
}

export default App
