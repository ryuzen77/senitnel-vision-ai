import { useEffect, useRef, useState } from 'react'
import { DomainSnapshot, getCurrentDomain } from '../api/videoApi'

const POLL_INTERVAL_MS = 750

/** Polls the read-only domain snapshot serially for the active video session. */
export function useCurrentDomain(enabled: boolean): {
  snapshot: DomainSnapshot | null
  fetchError: string | null
} {
  const [snapshot, setSnapshot] = useState<DomainSnapshot | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const acceptedSessionId = useRef<string | null>(null)

  useEffect(() => {
    if (!enabled) {
      acceptedSessionId.current = null
      setSnapshot(null)
      setFetchError(null)
      return
    }

    let active = true
    let timeoutId: number | null = null
    let controller: AbortController | null = null

    const poll = async () => {
      controller = new AbortController()
      try {
        const next = await getCurrentDomain(controller.signal)
        if (!active) return
        if (
          acceptedSessionId.current !== null
          && next.source_session_id !== null
          && next.source_session_id !== acceptedSessionId.current
        ) {
          acceptedSessionId.current = next.source_session_id
          setSnapshot(null)
          setFetchError(null)
          return
        }
        if (next.source_session_id !== null) acceptedSessionId.current = next.source_session_id
        setSnapshot(next)
        setFetchError(null)
      } catch (err: unknown) {
        if (active && !(err instanceof DOMException && err.name === 'AbortError')) {
          setFetchError(err instanceof Error ? err.message : String(err))
        }
      } finally {
        if (active) timeoutId = window.setTimeout(poll, POLL_INTERVAL_MS)
      }
    }

    void poll()
    return () => {
      active = false
      controller?.abort()
      if (timeoutId !== null) window.clearTimeout(timeoutId)
    }
  }, [enabled])

  return { snapshot, fetchError }
}
