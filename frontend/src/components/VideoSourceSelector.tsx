import { useState } from 'react'
import {
  startWebcam,
  stopVideo,
  uploadVideo,
  VideoState,
  VideoValidationError,
  ApiRequestError,
} from '../api/videoApi'

interface Props {
  startAllowed?: boolean
  mutationAllowed?: boolean
  /** Last backend status read in the current lifecycle (IDLE while unknown). */
  state: VideoState
  /** Optimistic state owned by App; cleared by App on the first fresh status read. */
  pendingState: VideoState | null
  /** Reports the optimistic state of an action (STARTING/STOPPING, then its response). */
  onPendingStateChange: (pending: VideoState | null) => void
  /** Called once an action's request settles, successfully or not. */
  onActionSettled: () => void
}

type Tab = 'webcam' | 'upload'

function actionMessage(error: unknown): string {
  if (error instanceof VideoValidationError) return error.message
  if (error instanceof ApiRequestError) {
    if (error.status === 409) return 'Sumber video masih aktif atau sedang dihentikan. Hentikan dahulu sebelum memulai lagi.'
    if (error.status === 503) return 'Video belum bisa dimulai. Coba hentikan sesi, lalu mulai kembali.'
    if (error.status === 422) return 'File belum dapat diputar. Gunakan MP4 atau WebM, maksimal 200 MB.'
    return 'Permintaan belum berhasil. Periksa status video sebelum mencoba lagi.'
  }
  return 'Layanan tidak dapat dihubungi. Periksa koneksi ke aplikasi.'
}

export function VideoSourceSelector({ state, pendingState, onPendingStateChange, onActionSettled, startAllowed = true, mutationAllowed = true }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>('webcam')
  const [actionError, setActionError] = useState<string | null>(null)
  const [isActing, setIsActing] = useState(false)
  const setPendingState = onPendingStateChange

  const effectiveState = pendingState ?? state
  const isTransitioning =
    effectiveState === VideoState.STARTING || effectiveState === VideoState.STOPPING
  const sourceControlsDisabled =
    !startAllowed || !mutationAllowed || isTransitioning || isActing || effectiveState === VideoState.RUNNING || effectiveState === VideoState.ERROR

  // On failure the transitional pending state is kept: App replaces it with the
  // first status read after the action settles, never with a pre-action status.
  async function handleWebcamClick() {
    setActionError(null)
    setIsActing(true)
    const stopping = effectiveState === VideoState.RUNNING || effectiveState === VideoState.ERROR
    setPendingState(stopping ? VideoState.STOPPING : VideoState.STARTING)
    try {
      const res = await (stopping ? stopVideo() : startWebcam())
      setPendingState(res.state)
    } catch (err: unknown) {
      setActionError(actionMessage(err))
    } finally {
      setIsActing(false)
      onActionSettled()
    }
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
      setActionError(actionMessage(err))
    } finally {
      setIsActing(false)
      onActionSettled()
    }
  }

  const webcamLabel =
    effectiveState === VideoState.RUNNING || effectiveState === VideoState.ERROR
      ? 'Hentikan'
      : 'Mulai kamera'

  return (
    <div className="source-controls">
      <div className="source-tabs" role="group" aria-label="Pilih sumber video">
        {(['webcam', 'upload'] as Tab[]).map((tab) => (
          <button
            key={tab}
            disabled={sourceControlsDisabled}
            aria-pressed={activeTab === tab}
            onClick={() => {
              setActiveTab(tab)
              setActionError(null)
            }}
            className={activeTab === tab ? 'source-tab is-active' : 'source-tab'}
          >
            {tab === 'webcam' ? 'Kamera' : 'File video'}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="source-tab-content">
        {(effectiveState === VideoState.RUNNING || effectiveState === VideoState.ERROR || effectiveState === VideoState.STOPPING) ? (
          <button
            onClick={handleWebcamClick}
            disabled={isTransitioning || isActing || !mutationAllowed}
            className="stop-button"
          >
            {effectiveState === VideoState.STOPPING
              ? 'Menghentikan…'
              : effectiveState === VideoState.ERROR
                ? 'Coba hentikan lagi'
                : 'Hentikan video'}
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
            <span>Pilih file dan mulai</span>
            <input
              type="file"
              aria-label="Pilih file dan mulai"
              accept="video/mp4,video/webm"
              disabled={sourceControlsDisabled}
              onChange={handleFileChange}
            />
            <small>MP4 atau WebM, maks. 200 MB. Setelah dipilih, file langsung diunggah dan diputar.</small>
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
