import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { completedView, PendingEvaluation, stubAppFetch } from './testUtils/appFetchStub'

let evaluationCalls: PendingEvaluation[]

async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  evaluationCalls = stubAppFetch()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('App evaluation lifecycle', () => {
  it('Stop and Start actions start new lifecycles; a late session-A result never appears in session B', async () => {
    render(<App />)
    await flush()
    expect(evaluationCalls).toHaveLength(1)
    const sessionARequest = evaluationCalls[0]

    fireEvent.click(screen.getByRole('button', { name: 'Hentikan Video' }))
    expect(sessionARequest.signal?.aborted).toBe(true)
    await flush()

    fireEvent.click(screen.getByRole('button', { name: 'Mulai Webcam' }))
    await flush()
    // The new lifecycle issued its own evaluation request.
    expect(evaluationCalls).toHaveLength(2)

    // Session A's response arrives late, after session B's lifecycle started.
    sessionARequest.resolve(completedView('session-A', 'event-A', 'alasan sesi A'))
    await flush()
    expect(screen.queryByText('alasan sesi A')).toBeNull()
    expect(screen.queryByText('Hasil analisis AI — perlu ditinjau')).toBeNull()

    evaluationCalls[1].resolve(completedView('session-B', 'event-B', 'alasan sesi B'))
    await flush()
    screen.getByText('alasan sesi B')
    screen.getByText('Hasil analisis AI — perlu ditinjau')
    expect(screen.queryByText('alasan sesi A')).toBeNull()
    // Pipeline step reflects the backend process state, not the assessment level.
    screen.getByText('Selesai')
  })

  it('hides the result as soon as Stop is clicked, before the backend answers', async () => {
    render(<App />)
    await flush()
    evaluationCalls[0].resolve(completedView('session-A', 'event-A', 'alasan sesi A'))
    await flush()
    screen.getByText('alasan sesi A')

    fireEvent.click(screen.getByRole('button', { name: 'Hentikan Video' }))
    expect(screen.queryByText('alasan sesi A')).toBeNull()
    screen.getByText('Menunggu sumber video.')
  })
})
