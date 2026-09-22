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
      <div className="video-preview is-running">
        <img
          src={streamUrl}
          alt="Preview video langsung"
        />
      </div>
    )
  }

  return (
    <div className="video-preview video-empty" role="status"><div>
      <span className="video-empty-icon" aria-hidden="true">▣</span>
      <p>{state === VideoState.IDLE && 'Tidak ada sumber video aktif'}</p><p>{state === VideoState.STARTING && 'Memulai sumber video…'}</p><p>{state === VideoState.STOPPING && 'Menghentikan sumber video…'}</p><p>{state === VideoState.ERROR && 'Terjadi kesalahan pada sumber video'}</p>
      {state === VideoState.IDLE && <small>Pilih Webcam atau upload video untuk memulai pemantauan.</small>}
    </div>
    </div>
  )
}
