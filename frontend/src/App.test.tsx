import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { completedView, PendingEvaluation, stubAppFetch, privateCaps, preparedSnapshot, sourceId, sourceResponse } from './testUtils/appFetchStub'
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
    fireEvent.click(screen.getByRole('button', { name: 'Hentikan video' }))
    expect(sessionARequest.signal?.aborted).toBe(true)
    await flush()
    fireEvent.click(screen.getByRole('button', { name: 'Mulai kamera' }))
    await flush()
    // The new lifecycle issued its own evaluation request.
    expect(evaluationCalls).toHaveLength(2)
    // Session A's response arrives late, after session B's lifecycle started.
    sessionARequest.resolve(completedView('session-A', 'event-A', 'alasan sesi A'))
    await flush()
    expect(screen.queryByText('alasan sesi A')).toBeNull()
    expect(screen.queryByText('Hasil penilaian — perlu ditinjau')).toBeNull()
    evaluationCalls[1].resolve(completedView('session-B', 'event-B', 'alasan sesi B'))
    await flush()
    screen.getByText('alasan sesi B')
    screen.getByText('Hasil penilaian — perlu ditinjau')
    expect(screen.queryByText('alasan sesi A')).toBeNull()
    // The panel reports process state separately from the assessment level.
    screen.getByText('Status: Selesai')
  })
  it('hides the result as soon as Stop is clicked, before the backend answers', async () => {
    render(<App />)
    await flush()
    evaluationCalls[0].resolve(completedView('session-A', 'event-A', 'alasan sesi A'))
    await flush()
    screen.getByText('alasan sesi A')
    fireEvent.click(screen.getByRole('button', { name: 'Hentikan video' }))
    expect(screen.queryByText('alasan sesi A')).toBeNull()
    screen.getByText('Mulai video untuk melihat penilaian AI.')
  })
})
describe('App private source identity', () => {
  it('a held preview A and its late finally neither alter nor unlock preview B', async () => {
    let snapshot = preparedSnapshot()
    let releaseA!: (response: Response) => void
    let releaseB!: (response: Response) => void
    vi.stubGlobal('crypto', { randomUUID: () => '22222222-2222-4222-8222-222222222222' })
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL = vi.fn(() => 'blob:current-B');
      static revokeObjectURL = vi.fn();
    })
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/capabilities'))
        return Promise.resolve(sourceResponse(privateCaps))
      if (url.endsWith('/current'))
        return Promise.resolve(sourceResponse(snapshot))
      if (url === '/api/health')
        return Promise.resolve(sourceResponse({ status: 'online', service: 'fake' }))
      if (url === '/api/video/status')
        return Promise.resolve(sourceResponse({ state: 'IDLE', source: null, error: null, session_ref: null, session_id: null }))
      if (url.includes('/preview?'))
        return new Promise<Response>(resolve => {
          if (url.includes(sourceId))
            releaseA = resolve
          else
            releaseB = resolve
        })
      throw new Error('Unexpected fake read')
    }))
    render(<App />)
    await flush()
    fireEvent.click(screen.getByRole('button', { name: 'Muat preview' }))
    await flush()
    snapshot = preparedSnapshot({ source_id: '44444444-4444-4444-8444-444444444444', generation: 2, config: { revision: 1, ready: true } })
    await flush(1000)
    expect((screen.getByRole('button', { name: 'Muat preview' }) as HTMLButtonElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: 'Mulai analisis' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Muat preview' }))
    await flush()
    const jpeg = () => ({ ok: true, headers: new Headers({ 'content-type': 'image/jpeg' }), blob: async () => new Blob(['fake']) }) as Response
    await act(async () => { releaseA(jpeg()) })
    await flush()
    expect(URL.createObjectURL).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: 'Muat preview' }) as HTMLButtonElement).disabled).toBe(true)
    await act(async () => { releaseB(jpeg()) })
    await flush()
    expect(screen.getByAltText('Preview file untuk pengaturan area').getAttribute('src')).toBe('blob:current-B')
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    expect((screen.getByRole('button', { name: 'Muat preview' }) as HTMLButtonElement).disabled).toBe(false)
  })
})

it('HTTP LAN private RUNNING keeps the actual App Stop button enabled', async () => {
  vi.stubGlobal('crypto', {})
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/capabilities')) return Promise.resolve(sourceResponse(privateCaps))
    if (url === '/api/video/sources/current') return Promise.resolve(sourceResponse(null, 204))
    if (url === '/api/health') return Promise.resolve(sourceResponse({ status: 'online', service: 'fake' }))
    if (url === '/api/video/status') return Promise.resolve(sourceResponse({ state: 'RUNNING', source: 'prepared', error: null, session_ref: 'lease-A', session_id: sourceId }))
    if (url === '/api/video/stop') return Promise.resolve(sourceResponse({ state: 'IDLE', source: null, error: null }))
    if (url === '/api/detections/latest') return Promise.resolve(sourceResponse(null, 204))
    if (url === '/api/evaluation/current') return Promise.resolve(sourceResponse({ enabled: false, source_session_id: sourceId, process_state: 'IDLE', active_event_id: null, latest_result: null }))
    if (url === '/api/domain/current') return Promise.resolve(sourceResponse({ source_session_id: sourceId, state: 'IDLE', domain: 'UNKNOWN' }))
    throw new Error('Unexpected fake read')
  }))
  render(<App />)
  await flush()
  const stop = screen.getByRole('button', { name: 'Hentikan video' }) as HTMLButtonElement
  expect(stop.disabled).toBe(false)
  fireEvent.click(stop)
  await flush()
  expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => url === '/api/video/stop' && init?.method === 'POST')).toHaveLength(1)
})
