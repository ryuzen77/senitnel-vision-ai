import { useEffect, useState } from 'react'
import { DetectionResult, getLatestDetection } from '../api/videoApi'

const POLL_INTERVAL_MS = 500

/** Polls serially while the backend video state is RUNNING. */
export function useLatestDetection(enabled: boolean): {
  result: DetectionResult | null
  fetchError: string | null
} {
  const [result, setResult] = useState<DetectionResult | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)

  useEffect(() => {
    if (!enabled) {
      setResult(null)
      setFetchError(null)
      return
    }

    let active = true
    let timeoutId: number | null = null
    let controller: AbortController | null = null

    const poll = async () => {
      controller = new AbortController()
      try {
        const next = await getLatestDetection(controller.signal)
        if (active) {
          setResult(next)
          setFetchError(null)
        }
      } catch (err: unknown) {
        if (active && !(err instanceof DOMException && err.name === 'AbortError')) {
          setFetchError(err instanceof Error ? err.message : String(err))
        }
      } finally {
        // Schedule only after this request settles: requests never overlap.
        if (active) {
          timeoutId = window.setTimeout(poll, POLL_INTERVAL_MS)
        }
      }
    }

    void poll()

    return () => {
      active = false
      controller?.abort()
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId)
      }
    }
  }, [enabled])

  return { result, fetchError }
}
