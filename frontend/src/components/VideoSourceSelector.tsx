import { useEffect, useState } from 'react'
import {
  startWebcam,
  stopVideo,
  uploadVideo,
  VideoState,
  VideoValidationError,
} from '../api/videoApi'

interface Props {
  state: VideoState
  /** Called whenever pendingState changes so App can derive effectiveState. */
  onPendingStateChange: (pending: VideoState | null) => void
}

type Tab = 'webcam' | 'upload'

export function VideoSourceSelector({ state, onPendingStateChange }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>('webcam')
  const [actionError, setActionError] = useState<string | null>(null)
  const [isActing, setIsActing] = useState(false)
  const [pendingState, setPendingStateRaw] = useState<VideoState | null>(null)

  // Keep parent in sync whenever pendingState changes.
  function setPendingState(next: VideoState | null) {
    setPendingStateRaw(next)
    onPendingStateChange(next)
  }

  // Clear pendingState once the polled backend state confirms it.
  useEffect(() => {
    if (pendingState !== null && state === pendingState) {
      setPendingState(null)
    }
  }, [state, pendingState]) // eslint-disable-line react-hooks/exhaustive-deps

  const effectiveState = pendingState ?? state
  const isTransitioning =
    effectiveState === VideoState.STARTING || effectiveState === VideoState.STOPPING
  const sourceControlsDisabled =
    isTransitioning || isActing || effectiveState === VideoState.RUNNING || effectiveState === VideoState.ERROR

  async function handleWebcamClick() {
    setActionError(null)
    setIsActing(true)
    if (effectiveState === VideoState.RUNNING || effectiveState === VideoState.ERROR) {
      setPendingState(VideoState.STOPPING)
      try {
        const res = await stopVideo()
        setPendingState(res.state)
      } catch (err: unknown) {
        setPendingState(null)
        setActionError(err instanceof Error ? err.message : String(err))
      }
    } else {
      setPendingState(VideoState.STARTING)
      try {
        const res = await startWebcam()
        setPendingState(res.state)
      } catch (err: unknown) {
        setPendingState(null)
        setActionError(err instanceof Error ? err.message : String(err))
      }
    }
    setIsActing(false)
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    // Reset input so the same file can be re-selected after an error
    e.target.value = ''
    setActionError(null)
    setIsActing(true)
    setPendingState(VideoState.STARTING)
    try {
      const res = await uploadVideo(file)
      setPendingState(res.state)
    } catch (err: unknown) {
      setPendingState(null)
      if (err instanceof VideoValidationError) {
        setActionError(err.message)
      } else {
        setActionError(err instanceof Error ? err.message : String(err))
      }
    }
    setIsActing(false)
  }

  const webcamLabel =
    effectiveState === VideoState.RUNNING || effectiveState === VideoState.ERROR
      ? 'Hentikan'
      : 'Mulai Webcam'

  return (
    <div className="source-controls">
      {/* Tab bar */}
      <div className="source-tabs" role="tablist" aria-label="Pilih sumber video">
        {(['webcam', 'upload'] as Tab[]).map((tab) => (
          <button
            key={tab}
            disabled={sourceControlsDisabled}
            onClick={() => {
              setActiveTab(tab)
              setActionError(null)
            }}
            className={activeTab === tab ? 'source-tab is-active' : 'source-tab'}
          >
            {tab === 'webcam' ? 'Webcam' : 'Upload Video'}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="source-tab-content">
        {(effectiveState === VideoState.RUNNING || effectiveState === VideoState.ERROR || effectiveState === VideoState.STOPPING) ? (
          <button
            onClick={handleWebcamClick}
            disabled={isTransitioning || isActing}
            className="stop-button"
          >
            {effectiveState === VideoState.STOPPING
              ? 'Menghentikan…'
              : effectiveState === VideoState.ERROR
                ? 'Kembali ke Idle'
                : 'Hentikan Video'}
          </button>
        ) : activeTab === 'webcam' ? (
          <button
            onClick={handleWebcamClick}
            disabled={sourceControlsDisabled}
            className="primary-button"
          >
            {effectiveState === VideoState.STARTING ? 'Memulai…' : webcamLabel}
          </button>
        ) : (
          <label className="file-input-label">
            <span>Pilih file MP4 atau WebM (maks. 200 MB)</span>
            <input
              type="file"
              accept="video/mp4,video/webm"
              disabled={sourceControlsDisabled}
              onChange={handleFileChange}
            />
            {effectiveState === VideoState.STARTING && <small>Memulai sumber video…</small>}
          </label>
        )}

        {actionError && (
          <p className="action-error" role="alert">
            {actionError}
          </p>
        )}
      </div>
    </div>
  )
}
