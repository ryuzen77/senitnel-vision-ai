import { useEffect, useState } from 'react'
import { getVideoStatus, VideoStatusResponse } from '../api/videoApi'

/**
 * Polls GET /api/video/status every second.
 * Clears the interval automatically on unmount.
 */
export function useVideoStatus(): {
  status: VideoStatusResponse | null
  fetchError: string | null
} {
  const [status, setStatus] = useState<VideoStatusResponse | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)

  useEffect(() => {
    let active = true

    const poll = () => {
      getVideoStatus()
        .then((data) => {
          if (active) {
            setStatus(data)
            setFetchError(null)
          }
        })
        .catch((err: unknown) => {
          if (active) {
            setFetchError(err instanceof Error ? err.message : String(err))
          }
        })
    }

    poll() // Immediate first fetch
    const id = setInterval(poll, 1000)

    return () => {
      active = false
      clearInterval(id)
    }
  }, [])

  return { status, fetchError }
}
