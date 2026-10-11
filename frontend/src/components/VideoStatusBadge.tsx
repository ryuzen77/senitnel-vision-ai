import { VideoState } from '../api/videoApi'

interface Props {
  state: VideoState
}

const STATE_CONFIG: Record<VideoState, { label: string; tone: string }> = {
  [VideoState.IDLE]: { label: 'Belum berjalan', tone: 'idle' },
  [VideoState.STARTING]: { label: 'Memulai', tone: 'starting' },
  [VideoState.RUNNING]: { label: 'Video berjalan', tone: 'running' },
  [VideoState.STOPPING]: { label: 'Menghentikan', tone: 'stopping' },
  [VideoState.ERROR]: { label: 'Video bermasalah', tone: 'error' },
}

export function VideoStatusBadge({ state }: Props) {
  const { label, tone } = STATE_CONFIG[state] ?? STATE_CONFIG[VideoState.IDLE]

  return (
    <span className={`video-state-badge ${tone}`}><span className="status-dot" aria-hidden="true" />
      {label}
    </span>
  )
}
