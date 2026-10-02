import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { VideoState } from './api/videoApi'
import { completedView, PendingEvaluation, stubAppFetch } from './testUtils/appFetchStub'

// Replace the selector so a test can drive Stop→Start inside one React batch,
// i.e. without any committed render where the video is not RUNNING.
let onPendingStateChange: ((next: VideoState | null) => void) | null = null
vi.mock('./components/VideoSourceSelector', () => ({
  VideoSourceSelector: (props: { onPendingStateChange: (next: VideoState | null) => void }) => {
    onPendingStateChange = props.onPendingStateChange
    return null
  },
}))

let evaluationCalls: PendingEvaluation[]

async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  onPendingStateChange = null
  evaluationCalls = stubAppFetch()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('App evaluation epoch', () => {
  it('a batched Stop→Start with no visible non-RUNNING render still starts a new lifecycle', async () => {
    render(<App />)
    await flush()
    expect(evaluationCalls).toHaveLength(1)
    const sessionARequest = evaluationCalls[0]

    act(() => {
      onPendingStateChange?.(VideoState.STOPPING)
      onPendingStateChange?.(VideoState.STARTING)
      onPendingStateChange?.(VideoState.RUNNING)
    })
    // Only the action epoch changed; RUNNING→RUNNING alone would not restart polling.
    expect(sessionARequest.signal?.aborted).toBe(true)
    expect(evaluationCalls).toHaveLength(2)

    sessionARequest.resolve(completedView('session-A', 'event-A', 'alasan sesi A'))
    await flush()
    expect(screen.queryByText('alasan sesi A')).toBeNull()

    evaluationCalls[1].resolve(completedView('session-B', 'event-B', 'alasan sesi B'))
    await flush()
    screen.getByText('alasan sesi B')
  })
})
