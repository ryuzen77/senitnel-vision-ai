import { VideoState } from '../api/videoApi'

interface Props {
  state: VideoState
}

const STATE_CONFIG: Record<VideoState, { label: string; bg: string; color: string }> = {
  [VideoState.IDLE]: { label: 'Idle', bg: '#e5e7eb', color: '#374151' },
  [VideoState.STARTING]: { label: 'Memulai…', bg: '#fef3c7', color: '#92400e' },
  [VideoState.RUNNING]: { label: 'Berjalan', bg: '#d1fae5', color: '#065f46' },
  [VideoState.STOPPING]: { label: 'Menghentikan…', bg: '#fee2e2', color: '#991b1b' },
  [VideoState.ERROR]: { label: 'Error', bg: '#fecaca', color: '#7f1d1d' },
}

export function VideoStatusBadge({ state }: Props) {
  const { label, bg, color } = STATE_CONFIG[state] ?? STATE_CONFIG[VideoState.IDLE]

  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 10px',
        borderRadius: '9999px',
        fontSize: '0.75rem',
        fontWeight: 600,
        letterSpacing: '0.03em',
        background: bg,
        color,
      }}
    >
      {label}
    </span>
  )
}
