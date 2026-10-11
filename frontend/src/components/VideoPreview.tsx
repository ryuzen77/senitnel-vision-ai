import { useMemo, useRef, useState } from 'react'
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
  const streamGeneration = useRef(0)
  const [failedStream, setFailedStream] = useState<string | null>(null)
  const streamUrl = useMemo(
    () => {
      if (state !== VideoState.RUNNING) return null
      streamGeneration.current += 1
      return `${VIDEO_STREAM_URL}?sid=${Date.now()}-${streamGeneration.current}`
    },
    [state],
  )

  if (streamUrl !== null && streamUrl !== failedStream) {
    return (
      <div className="video-preview is-running">
        <img
          src={streamUrl}
          alt="Preview video langsung"
          onError={() => setFailedStream(streamUrl)}
        />
      </div>
    )
  }

  return (
    <div className="video-preview video-empty" role="status"><div>
      <span className="video-empty-icon" aria-hidden="true">▣</span>
      <p>{state === VideoState.IDLE && 'Belum ada video aktif'}</p>
      <p>{state === VideoState.STARTING && 'Memulai sumber video…'}</p>
      <p>{state === VideoState.STOPPING && 'Menghentikan sumber video…'}</p>
      <p>{state === VideoState.ERROR && 'Sumber video belum tersedia'}</p>
      {state === VideoState.RUNNING && <><p>Preview tidak tersedia</p><small>Status video tetap diperiksa. Hentikan dan mulai kembali jika diperlukan.</small></>}
      {state === VideoState.IDLE && <small>Pilih kamera atau file video untuk memulai pemantauan.</small>}
    </div>
    </div>
  )
}
