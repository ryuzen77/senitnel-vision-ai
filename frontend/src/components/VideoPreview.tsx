import { useMemo } from 'react'
import { VIDEO_STREAM_URL, VideoState } from '../api/videoApi'

interface Props {
  state: VideoState
}

/**
 * Shows a live MJPEG stream when RUNNING, a placeholder otherwise.
 *
 * The stream URL is computed ONCE per RUNNING session via useMemo.
 * When `state` transitions to RUNNING the memo re-runs and produces a
 * new `?sid=` URL; that URL remains stable for all subsequent renders
 * while state stays RUNNING.  When state leaves RUNNING the memo returns
 * null and the image is unmounted.
 *
 * useMemo runs synchronously during the render that first observes
 * RUNNING, so the <img> appears in that same render pass — no extra
 * polling tick needed.
 */
export function VideoPreview({ state }: Props) {
  const streamUrl = useMemo(
    () =>
      state === VideoState.RUNNING
        ? `${VIDEO_STREAM_URL}?sid=${Date.now()}`
        : null,
    [state],
  )

  if (streamUrl !== null) {
    return (
      <div style={wrapperStyle}>
        <img
          src={streamUrl}
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
