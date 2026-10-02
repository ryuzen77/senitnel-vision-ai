import { useEffect, useRef, useState } from 'react'
import { getVideoStatus, VideoStatusResponse } from '../api/videoApi'

export const VIDEO_STATUS_POLL_INTERVAL_MS = 1000

interface StoredStatus {
  epoch: number
  status: VideoStatusResponse | null
  fetchError: string | null
}

/**
 * Polls GET /api/video/status serially for one lifecycle epoch.
 *
 * The app bumps `epoch` when a Start/Stop action begins and when it settles.
 * Each epoch aborts and invalidates the previous request (a response requested
 * earlier is discarded even if the transport ignores the abort) and starts a
 * fresh read immediately. `status` is null until the first read of the current
 * epoch completes, so callers never see a pre-action status as current.
 */
export function useVideoStatus(epoch = 0): {
  status: VideoStatusResponse | null
  fetchError: string | null
} {
  const generation = useRef(0)
  const [stored, setStored] = useState<StoredStatus>({ epoch: -1, status: null, fetchError: null })

  useEffect(() => {
    generation.current += 1
    const myGeneration = generation.current
    setStored({ epoch, status: null, fetchError: null })

    let timeoutId: number | null = null
    let controller: AbortController | null = null
    const isCurrent = () => generation.current === myGeneration

    const poll = async () => {
      controller = new AbortController()
      try {
        const data = await getVideoStatus(controller.signal)
        if (!isCurrent()) return
        setStored({ epoch, status: data, fetchError: null })
      } catch (err: unknown) {
        if (!isCurrent() || (err instanceof DOMException && err.name === 'AbortError')) return
        // Keep a status already read in this epoch; report the failed refresh.
        setStored((prev) => ({
          epoch,
          status: prev.epoch === epoch ? prev.status : null,
          fetchError: err instanceof Error ? err.message : String(err),
        }))
      } finally {
        // Serial polling: the next request starts only after this one settles.
        if (isCurrent()) timeoutId = window.setTimeout(poll, VIDEO_STATUS_POLL_INTERVAL_MS)
      }
    }

    void poll()

    return () => {
      generation.current += 1
      controller?.abort()
      if (timeoutId !== null) window.clearTimeout(timeoutId)
    }
  }, [epoch])

  // Render-time guard for the render in which `epoch` changed.
  if (stored.epoch !== epoch) return { status: null, fetchError: null }
  return { status: stored.status, fetchError: stored.fetchError }
}
