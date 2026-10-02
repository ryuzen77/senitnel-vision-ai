import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EvaluationCurrent, getCurrentEvaluation, VideoState } from '../api/videoApi'
import {
  CurrentEvaluationState, EVALUATION_POLL_INTERVAL_MS, startsNewLifecycle, useCurrentEvaluation,
} from './useCurrentEvaluation'

vi.mock('../api/videoApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/videoApi')>()),
  getCurrentEvaluation: vi.fn(),
}))

interface Pending {
  signal: AbortSignal | undefined
  resolve: (view: EvaluationCurrent) => void
  reject: (err: unknown) => void
}

let calls: Pending[]

function view(session: string | null, overrides: Partial<EvaluationCurrent> = {}): EvaluationCurrent {
  return {
    enabled: true, source_session_id: session, process_state: 'IDLE',
    active_event_id: null, latest_result: null, ...overrides,
  }
}

async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function render(enabled: boolean, epoch: number) {
  const seen: CurrentEvaluationState[] = []
  const hook = renderHook(
    ({ enabled: on, epoch: e }) => {
      const state = useCurrentEvaluation(on, e)
      seen.push(state)
      return state
    },
    { initialProps: { enabled, epoch } },
  )
  return { ...hook, seen }
}

beforeEach(() => {
  vi.useFakeTimers()
  calls = []
  vi.mocked(getCurrentEvaluation).mockImplementation((signal?: AbortSignal) => new Promise((resolve, reject) => {
    calls.push({ signal, resolve, reject })
  }))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useCurrentEvaluation lifecycle', () => {
  it('polls serially: one request in flight, next scheduled one interval after it settles', async () => {
    const { result } = render(true, 0)
    expect(calls).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(0)
    await flush(5 * EVALUATION_POLL_INTERVAL_MS)
    expect(calls).toHaveLength(1)

    calls[0].resolve(view('A'))
    await flush()
    expect(result.current.view?.source_session_id).toBe('A')
    expect(vi.getTimerCount()).toBe(1)
    await flush(EVALUATION_POLL_INTERVAL_MS - 1)
    expect(calls).toHaveLength(1)
    await flush(1)
    expect(calls).toHaveLength(2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('discards a late response after Stop even though the transport ignored abort', async () => {
    const { result, rerender } = render(true, 0)
    rerender({ enabled: false, epoch: 1 })
    expect(calls[0].signal?.aborted).toBe(true)
    calls[0].resolve(view('A', { process_state: 'COMPLETED' }))
    await flush(5 * EVALUATION_POLL_INTERVAL_MS)
    expect(result.current.view).toBeNull()
    expect(calls).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('a new epoch invalidates the old request while enabled stays true', async () => {
    const { result, rerender } = render(true, 0)
    rerender({ enabled: true, epoch: 1 })
    expect(calls).toHaveLength(2)
    expect(calls[0].signal?.aborted).toBe(true)
    calls[0].resolve(view('A'))
    await flush()
    expect(result.current.view).toBeNull()
    expect(result.current.loading).toBe(true)
    calls[1].resolve(view('B'))
    await flush()
    expect(result.current.view?.source_session_id).toBe('B')
  })

  it('Stop→Start quickly: session A response arriving after session B started is rejected', async () => {
    const { result, rerender } = render(true, 0)
    rerender({ enabled: false, epoch: 1 })
    rerender({ enabled: true, epoch: 2 })
    expect(calls).toHaveLength(2)
    calls[0].resolve(view('A', { process_state: 'COMPLETED' }))
    await flush()
    expect(result.current.view).toBeNull()
    calls[1].resolve(view('B'))
    await flush()
    expect(result.current.view?.source_session_id).toBe('B')
    // The stale lifecycle never scheduled a follow-up poll.
    await flush(EVALUATION_POLL_INTERVAL_MS)
    expect(calls).toHaveLength(3)
  })

  it('a stale response arriving after the new lifecycle answered cannot overwrite it', async () => {
    const { result, rerender } = render(true, 0)
    rerender({ enabled: true, epoch: 1 })
    calls[1].resolve(view('B', { process_state: 'WAITING_CALLBACK', active_event_id: 'e-b' }))
    await flush()
    expect(result.current.view?.source_session_id).toBe('B')
    calls[0].resolve(view('A', { process_state: 'COMPLETED' }))
    await flush()
    expect(result.current.view?.source_session_id).toBe('B')
    expect(result.current.view?.active_event_id).toBe('e-b')
    expect(result.current.loading).toBe(false)
  })

  it('never renders the previous lifecycle state, even before the new effect runs', async () => {
    const { rerender, seen } = render(true, 0)
    calls[0].resolve(view('A'))
    await flush()
    expect(seen[seen.length - 1].view?.source_session_id).toBe('A')
    const before = seen.length
    rerender({ enabled: true, epoch: 1 })
    const after = seen.slice(before)
    expect(after.length).toBeGreaterThan(0)
    expect(after.every((state) => state.view === null)).toBe(true)
  })

  it('unmount aborts, discards the late response and leaves no timer', async () => {
    const { unmount } = render(true, 0)
    unmount()
    expect(calls[0].signal?.aborted).toBe(true)
    calls[0].resolve(view('A'))
    await flush(5 * EVALUATION_POLL_INTERVAL_MS)
    expect(calls).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('useCurrentEvaluation session pinning', () => {
  it('re-binds to a new session observed without any RUNNING transition', async () => {
    const { result } = render(true, 0)
    calls[0].resolve(view('A', { process_state: 'COMPLETED' }))
    await flush()
    expect(result.current.view?.source_session_id).toBe('A')

    await flush(EVALUATION_POLL_INTERVAL_MS)
    calls[1].resolve(view('B'))
    await flush()
    // Old view is dropped immediately; the first B response is not shown yet.
    expect(result.current.view).toBeNull()
    expect(result.current.rebinding).toBe(true)

    await flush(EVALUATION_POLL_INTERVAL_MS)
    calls[2].resolve(view('B', { process_state: 'WAITING_CALLBACK', active_event_id: 'e-b' }))
    await flush()
    // Recovery: the new session is accepted, not rejected forever.
    expect(result.current.rebinding).toBe(false)
    expect(result.current.view?.source_session_id).toBe('B')
    expect(result.current.view?.active_event_id).toBe('e-b')
  })

  it('a session-less view does not reset the pin', async () => {
    const { result } = render(true, 0)
    calls[0].resolve(view('A'))
    await flush()
    await flush(EVALUATION_POLL_INTERVAL_MS)
    calls[1].resolve(view(null))
    await flush()
    expect(result.current.view?.source_session_id).toBeNull()
    await flush(EVALUATION_POLL_INTERVAL_MS)
    calls[2].resolve(view('A'))
    await flush()
    expect(result.current.rebinding).toBe(false)
    expect(result.current.view?.source_session_id).toBe('A')
  })
})

describe('useCurrentEvaluation errors', () => {
  it('network error clears the view, keeps the normal interval and recovers', async () => {
    const { result } = render(true, 0)
    calls[0].resolve(view('A', { process_state: 'COMPLETED' }))
    await flush()
    await flush(EVALUATION_POLL_INTERVAL_MS)
    calls[1].reject(new Error('HTTP 503'))
    await flush()
    expect(result.current.fetchError).toBe('HTTP 503')
    expect(result.current.view).toBeNull()

    await flush(EVALUATION_POLL_INTERVAL_MS - 1)
    expect(calls).toHaveLength(2)
    await flush(1)
    expect(calls).toHaveLength(3)
    calls[2].resolve(view('A'))
    await flush()
    expect(result.current.fetchError).toBeNull()
    expect(result.current.view?.source_session_id).toBe('A')
  })

  it('an abort error of the current request is not reported as a fetch error', async () => {
    const { result } = render(true, 0)
    calls[0].reject(new DOMException('aborted', 'AbortError'))
    await flush()
    expect(result.current.fetchError).toBeNull()
  })
})

describe('startsNewLifecycle', () => {
  it('Start and Stop actions start a new lifecycle; confirmations do not', () => {
    expect(startsNewLifecycle(VideoState.STARTING)).toBe(true)
    expect(startsNewLifecycle(VideoState.STOPPING)).toBe(true)
    expect(startsNewLifecycle(VideoState.RUNNING)).toBe(false)
    expect(startsNewLifecycle(VideoState.IDLE)).toBe(false)
    expect(startsNewLifecycle(null)).toBe(false)
  })
})
