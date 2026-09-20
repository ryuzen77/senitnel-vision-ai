import { useState } from 'react'
import {
  startWebcam,
  stopVideo,
  uploadVideo,
  VideoState,
  VideoValidationError,
} from '../api/videoApi'

interface Props {
  state: VideoState
}

type Tab = 'webcam' | 'upload'

export function VideoSourceSelector({ state }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>('webcam')
  const [actionError, setActionError] = useState<string | null>(null)
  const [isActing, setIsActing] = useState(false)

  const isTransitioning =
    state === VideoState.STARTING || state === VideoState.STOPPING
  const disabled = isTransitioning || isActing

  async function handleWebcamClick() {
    setActionError(null)
    setIsActing(true)
    try {
      if (state === VideoState.RUNNING || state === VideoState.ERROR) {
        await stopVideo()
      } else {
        await startWebcam()
      }
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setIsActing(false)
    }
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    // Reset input so the same file can be re-selected after an error
    e.target.value = ''
    setActionError(null)
    setIsActing(true)
    try {
      await uploadVideo(file)
    } catch (err: unknown) {
      if (err instanceof VideoValidationError) {
        setActionError(err.message)
      } else {
        setActionError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setIsActing(false)
    }
  }

  const webcamLabel =
    state === VideoState.RUNNING || state === VideoState.ERROR
      ? 'Hentikan'
      : 'Mulai Webcam'

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
      {/* Tab bar */}
      <div style={{ display: 'flex', borderBottom: '1px solid #e5e7eb' }}>
        {(['webcam', 'upload'] as Tab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => {
              setActiveTab(tab)
              setActionError(null)
            }}
            style={{
              flex: 1,
              padding: '8px 0',
              border: 'none',
              cursor: 'pointer',
              background: activeTab === tab ? '#f9fafb' : '#ffffff',
              fontWeight: activeTab === tab ? 600 : 400,
              borderBottom: activeTab === tab ? '2px solid #3b82d4' : '2px solid transparent',
              fontSize: '0.875rem',
            }}
          >
            {tab === 'webcam' ? 'Webcam' : 'Upload Video'}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div style={{ padding: '1rem' }}>
        {activeTab === 'webcam' && (
          <button
            onClick={handleWebcamClick}
            disabled={disabled}
            style={btnStyle(disabled)}
          >
            {webcamLabel}
          </button>
        )}

        {activeTab === 'upload' && (
          <label style={{ display: 'block' }}>
            <span style={{ fontSize: '0.875rem', color: '#374151' }}>
              Pilih file MP4 atau WebM (maks. 200 MB)
            </span>
            <input
              type="file"
              accept="video/mp4,video/webm"
              disabled={disabled}
              onChange={handleFileChange}
              style={{ display: 'block', marginTop: 8 }}
            />
          </label>
        )}

        {actionError && (
          <p
            style={{
              marginTop: 8,
              fontSize: '0.8rem',
              color: '#991b1b',
              background: '#fee2e2',
              padding: '6px 10px',
              borderRadius: 4,
            }}
          >
            {actionError}
          </p>
        )}
      </div>
    </div>
  )
}

function btnStyle(disabled: boolean): React.CSSProperties {
  return {
    padding: '8px 20px',
    border: 'none',
    borderRadius: 6,
    background: disabled ? '#d1d5db' : '#3b82d4',
    color: disabled ? '#6b7280' : '#ffffff',
    cursor: disabled ? 'not-allowed' : 'pointer',
    fontWeight: 600,
    fontSize: '0.875rem',
  }
}
