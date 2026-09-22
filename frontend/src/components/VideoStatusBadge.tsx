import { VideoState } from '../api/videoApi'

interface Props {
  state: VideoState
}

const STATE_CONFIG: Record<VideoState, { label: string; tone: string }> = {
  [VideoState.IDLE]: { label: 'IDLE', tone: 'idle' },
  [VideoState.STARTING]: { label: 'STARTING', tone: 'starting' },
  [VideoState.RUNNING]: { label: 'RUNNING', tone: 'running' },
  [VideoState.STOPPING]: { label: 'STOPPING', tone: 'stopping' },
  [VideoState.ERROR]: { label: 'ERROR', tone: 'error' },
}

export function VideoStatusBadge({ state }: Props) {
  const { label, tone } = STATE_CONFIG[state] ?? STATE_CONFIG[VideoState.IDLE]

  return (
    <span className={`video-state-badge ${tone}`}><span className="status-dot" aria-hidden="true" />
      {label}
    </span>
  )
}
