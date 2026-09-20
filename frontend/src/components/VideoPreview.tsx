import { useEffect, useRef } from 'react'
import { VIDEO_STREAM_URL, VideoState } from '../api/videoApi'

interface Props {
  state: VideoState
}

/**
 * Shows a live MJPEG stream when RUNNING, a placeholder otherwise.
 *
 * The stream URL is computed ONCE when entering RUNNING state (stored in a
 * ref) and does NOT change on every status poll. The `?sid=` cache-buster
 * is applied a single time per RUNNING session.  Browser caching is also
 * prevented by the backend's `Cache-Control: no-store` header.
 */
export function VideoPreview({ state }: Props) {
  const streamUrlRef = useRef<string | null>(null)

  useEffect(() => {
    if (state === VideoState.RUNNING && streamUrlRef.current === null) {
      // Compute once per RUNNING session
      streamUrlRef.current = `${VIDEO_STREAM_URL}?sid=${Date.now()}`
    }
    if (state !== VideoState.RUNNING) {
      streamUrlRef.current = null
    }
  }, [state])

  if (state === VideoState.RUNNING && streamUrlRef.current) {
    return (
      <div style={wrapperStyle}>
        <img
          src={streamUrlRef.current}
          alt="Preview video langsung"
          style={{ width: '100%', display: 'block' }}
        />
      </div>
    )
  }

  return (
    <div
      style={{
        ...wrapperStyle,
        background: '#f3f4f6',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: 240,
        color: '#6b7280',
        fontSize: '0.875rem',
      }}
    >
      {state === VideoState.IDLE && 'Tidak ada sumber video aktif'}
      {state === VideoState.STARTING && 'Memulai sumber video…'}
      {state === VideoState.STOPPING && 'Menghentikan sumber video…'}
      {state === VideoState.ERROR && 'Terjadi kesalahan pada sumber video'}
    </div>
  )
}

const wrapperStyle: React.CSSProperties = {
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  overflow: 'hidden',
}
