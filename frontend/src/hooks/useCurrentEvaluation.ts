import { useEffect, useRef, useState } from 'react'
import { EvaluationCurrent, getCurrentEvaluation, VideoState } from '../api/videoApi'

export const EVALUATION_POLL_INTERVAL_MS = 1000

/** Start (STARTING) and Stop/reset (STOPPING) actions begin a new evaluation lifecycle. */
export function startsNewLifecycle(next: VideoState | null): boolean {
  return next === VideoState.STARTING || next === VideoState.STOPPING
}

export interface CurrentEvaluationState {
  /** Latest accepted view for the pinned session of this lifecycle, or null. */
  view: EvaluationCurrent | null
  /** Frontend fetch failure (network / non-2xx); never a backend evaluation outcome. */
  fetchError: string | null
  /** No accepted view and no error yet in this lifecycle. */
  loading: boolean
  /** A different session was observed; the old view was dropped until confirmed. */
  rebinding: boolean
}

interface StoredState {
  epoch: number
  view: EvaluationCurrent | null
  fetchError: string | null
  rebinding: boolean
}

const EMPTY: CurrentEvaluationState = { view: null, fetchError: null, loading: true, rebinding: false }

/**
 * Polls GET /api/evaluation/current serially for one video lifecycle.
 *
 * A lifecycle is identified by (enabled, epoch). The app bumps `epoch` on every
 * Start/Stop action, so Stop→Start invalidates requests even when the polled
 * video state never visibly leaves RUNNING. Each lifecycle gets a fresh
 * generation; responses from an older generation are discarded, not only aborted.
 *
 * Session policy within one lifecycle: the first non-null source_session_id is
 * pinned. A valid response for a different session drops the old view, re-pins
 * to that session and is itself discarded; the next response for the same
 * session is accepted. A new session is therefore never rejected forever.
 *
 * Limitation: /api/video/status carries no session ID, so a backend session
 * change that did not go through this UI is only noticed when an evaluation
 * poll observes it. The poll interval is the delay between one settled request
 * and the next, not an upper bound on detection: a slow or hanging request
 * delays detection further, and re-binding needs one more response.
 */
export function useCurrentEvaluation(enabled: boolean, epoch: number): CurrentEvaluationState {
  const generation = useRef(0)
  const [stored, setStored] = useState<StoredState>({
    epoch: -1, view: null, fetchError: null, rebinding: false,
  })

  useEffect(() => {
    generation.current += 1
    const myGeneration = generation.current
    setStored({ epoch, view: null, fetchError: null, rebinding: false })
    if (!enabled) {
      return () => {
        generation.current += 1
      }
    }

    let timeoutId: number | null = null
    let controller: AbortController | null = null
    let pinnedSession: string | null = null
    const isCurrent = () => generation.current === myGeneration

    const poll = async () => {
      controller = new AbortController()
      try {
        const next = await getCurrentEvaluation(controller.signal)
        if (!isCurrent()) return
        if (next.source_session_id !== null) {
          if (pinnedSession !== null && next.source_session_id !== pinnedSession) {
            pinnedSession = next.source_session_id
            setStored({ epoch, view: null, fetchError: null, rebinding: true })
            return
          }
          pinnedSession = next.source_session_id
        }
        setStored({ epoch, view: next, fetchError: null, rebinding: false })
      } catch (err: unknown) {
        if (!isCurrent() || (err instanceof DOMException && err.name === 'AbortError')) return
        // Never keep an older view on screen as if it were current.
        setStored({
          epoch,
          view: null,
          fetchError: err instanceof Error ? err.message : String(err),
          rebinding: false,
        })
      } finally {
        // Serial polling: the next request is scheduled only after this one settles.
        if (isCurrent()) timeoutId = window.setTimeout(poll, EVALUATION_POLL_INTERVAL_MS)
      }
    }

    void poll()

    return () => {
      generation.current += 1
      controller?.abort()
      if (timeoutId !== null) window.clearTimeout(timeoutId)
    }
  }, [enabled, epoch])

  // Render-time guard: in the render where `enabled`/`epoch` change, the effect
  // has not run yet, so state from the previous lifecycle must not be shown.
  if (!enabled || stored.epoch !== epoch) return EMPTY
  return {
    view: stored.view,
    fetchError: stored.fetchError,
    loading: stored.view === null && stored.fetchError === null,
    rebinding: stored.rebinding,
  }
}
