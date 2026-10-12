import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, it, expect, vi } from 'vitest'
import { CANCEL_RECONCILIATION_MS, useSourcePreparation } from './useSourcePreparation'
import { sourceResponse, privateCaps, preparedSnapshot, validConfiguration, sourceId, intentId, attemptRecord } from '../testUtils/appFetchStub'
let snapshot: ReturnType<typeof preparedSnapshot> | null
let calls: string[]
let override: ((url: string, init?: RequestInit) => Promise<Response> | null) | null
async function flush(ms = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
beforeEach(() => {
  vi.useFakeTimers()
  snapshot = null
  calls = []
  override = null
  vi.stubGlobal('crypto', { randomUUID: () => intentId })
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL = vi.fn(() => 'blob:test');
    static revokeObjectURL = vi.fn();
  })
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push((init?.method ?? 'GET') + ' ' + url)
    const custom = override?.(url, init)
    if (custom)
      return custom
    if (url.endsWith('/capabilities'))
      return Promise.resolve(sourceResponse(privateCaps))
    if (url.endsWith('/current'))
      return Promise.resolve(sourceResponse(snapshot, snapshot ? 200 : 204))
    if (url === '/api/video/sources') {
      snapshot = preparedSnapshot({ state: 'AWAITING_FILE' })
      return Promise.resolve(sourceResponse({ source_id: sourceId, generation: 1, state: 'AWAITING_FILE' }, 201))
    }
    if (url.includes('/file?')) {
      snapshot = preparedSnapshot()
      return Promise.resolve(sourceResponse({ state: 'PREPARED', frame_width: 640, frame_height: 480, preview_revision: 1 }))
    }
    if (url.includes('/preview?'))
      return Promise.resolve({ ok: true, headers: new Headers({ 'content-type': 'image/jpeg' }), blob: async () => new Blob(['fake']) } as Response)
    if (url.endsWith('/configuration')) {
      snapshot = preparedSnapshot({ config: { revision: 1, ready: true } })
      return Promise.resolve(sourceResponse({ revision: 1, ready: true, canonical: validConfiguration }))
    }
    if (url.endsWith('/play'))
      return Promise.resolve(sourceResponse(attemptRecord()))
    if (url.includes('/play/1'))
      return Promise.resolve(sourceResponse(attemptRecord()))
    if (init?.method === 'DELETE') {
      snapshot = null
      return Promise.resolve(sourceResponse(null, 204))
    }
    if (url === '/api/video/stop')
      return Promise.resolve(sourceResponse({ state: 'IDLE', source: null, error: null }))
    if (url.endsWith('/cleanup/retry'))
      return Promise.resolve(sourceResponse(null, 204))
    throw new Error('unexpected fake request')
  }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })
async function prepared() { const h = renderHook(() => useSourcePreparation(vi.fn())); await flush(); await act(async () => { await h.result.current.prepare(new File(['fake'], 'file.mp4', { type: 'video/mp4' })) }); await flush(); return h }
async function ready() { const h = await prepared(); await act(async () => { await h.result.current.loadPreview() }); await flush(); act(() => { h.result.current.verifyImage(640, 480); h.result.current.changeDraft(validConfiguration) }); await act(async () => { await h.result.current.save() }); await flush(); return h }
it('prepares without start, requires verified image and canonical save ACK before Play', async () => { const h = await prepared(); expect(calls.some(x => x.includes('/play'))).toBe(false); expect(h.result.current.canPlay).toBe(false); await act(async () => { await h.result.current.loadPreview() }); await flush(); act(() => { h.result.current.verifyImage(640, 480); h.result.current.changeDraft(validConfiguration) }); await act(async () => { await h.result.current.save() }); await flush(); expect(h.result.current.canPlay).toBe(true); await act(async () => { await h.result.current.play() }); await flush(); expect(calls.filter(x => x.startsWith('POST') && x.endsWith('/play'))).toHaveLength(1); expect(calls.some(x => x.includes('webcam') || x.includes('/api/video/upload'))).toBe(false) })
it('cancel before create ACK reconciles its canonical intent and never PUTs', async () => { let respond!: (r: Response) => void; override = (url) => url === '/api/video/sources' ? new Promise(resolve => { respond = resolve }) : null; const h = renderHook(() => useSourcePreparation(vi.fn())); await flush(); let work!: Promise<void>; act(() => { work = h.result.current.prepare(new File(['fake'], 'a.mp4', { type: 'video/mp4' })) }); await flush(); await act(async () => { await h.result.current.cancel() }); await flush(); expect(h.result.current.cancelling).toBe(true); expect(calls.filter(x => x === 'POST /api/video/sources')).toHaveLength(1); await act(async () => { respond(sourceResponse({ source_id: sourceId, generation: 1, state: 'AWAITING_FILE' }, 201)); await work }); await flush(); expect(calls.some(x => x.includes('/file?'))).toBe(false); expect(calls.filter(x => x.startsWith('DELETE'))).toHaveLength(1) })
it('lost Play ACK uses lookup without automatic new POST', async () => { const h = await ready(); override = (url) => url.endsWith('/play') ? Promise.reject(new Error('lost')) : null; await act(async () => { await h.result.current.play() }); await flush(); expect(calls.filter(x => x.startsWith('POST') && x.endsWith('/play'))).toHaveLength(1); expect(calls.some(x => x.includes('/play/1'))).toBe(true); expect(h.result.current.unknown).toBeNull() })
it('known pre-CAS refusal does not become an unknown attempt or retry', async () => { const h = await ready(); override = (url) => url.endsWith('/play') ? Promise.resolve(sourceResponse({ category: 'config_not_ready' }, 409)) : null; await act(async () => { await h.result.current.play() }); await flush(); expect(h.result.current.unknown).toBeNull(); expect(calls.some(x => x.includes('/play/1'))).toBe(false); expect(calls.filter(x => x.startsWith('POST') && x.endsWith('/play'))).toHaveLength(1) })
it('lost save ACK never accepts current.ready from another writer', async () => {
  const h = await prepared(); await act(async () => { await h.result.current.loadPreview() }); await flush(); act(() => { h.result.current.verifyImage(640, 480); h.result.current.changeDraft(validConfiguration) }); override = (url) => {
    if (url.endsWith('/configuration')) {
      snapshot = preparedSnapshot({ config: { revision: 1, ready: true } })
      return Promise.reject(new Error('lost'))
    } return null
  }; await act(async () => { await h.result.current.save() }); await flush(); expect(h.result.current.unknown).toBe('save'); expect(h.result.current.canPlay).toBe(false)
})
it('probe cleanup requires a new upload', async () => { override = (url) => url.includes('/file?') ? Promise.resolve(sourceResponse({ category: 'cleanup_pending', source_id: sourceId, generation: 1 }, 503)) : null; const h = await prepared(); expect(h.result.current.draft).toBeNull(); expect(h.result.current.message).toContain('Unggah ulang'); expect(calls.some(x => x.includes('/preview?'))).toBe(false) })
it('preview cleanup retains its source without retry or forced upload', async () => { const h = await prepared(); override = (url) => url.includes('/preview?') ? Promise.resolve(sourceResponse({ category: 'cleanup_pending', source_id: sourceId, generation: 1 }, 503)) : null; await act(async () => { await h.result.current.loadPreview() }); await flush(3000); expect(h.result.current.identity?.source_id).toBe(sourceId); expect(h.result.current.draft).not.toBeNull(); expect(h.result.current.message).not.toContain('Unggah ulang'); expect(calls.filter(x => x.includes('/preview?'))).toHaveLength(1) })
it('failed DELETE keeps admission closed and only an explicit click retries once', async () => { const h = await prepared(); override = (_url, init) => init?.method === 'DELETE' ? Promise.resolve(sourceResponse({ category: 'cleanup_pending', source_id: sourceId, generation: 1 }, 503)) : null; await act(async () => { await h.result.current.cancel() }); await flush(3000); expect(h.result.current.cancelling).toBe(true); expect(calls.filter(x => x.startsWith('DELETE'))).toHaveLength(1); await act(async () => { await h.result.current.prepare(new File(['fake'], 'b.mp4', { type: 'video/mp4' })) }); expect(calls.filter(x => x === 'POST /api/video/sources')).toHaveLength(1); override = null; await act(async () => { await h.result.current.cancel() }); await flush(); expect(calls.filter(x => x.startsWith('DELETE'))).toHaveLength(2); expect(h.result.current.cancelling).toBe(false) })
it('ready config never permits another Play while playing or requiring manual Stop', async () => { const h = await ready(); snapshot = preparedSnapshot({ state: 'PLAYING', config: { revision: 1, ready: true } }); await flush(1000); expect(h.result.current.canPlay).toBe(false); snapshot = preparedSnapshot({ config: { revision: 1, ready: true }, analysis: { stop_required: true, state: 'ERROR', session_ref: null, session_id: null } }); await flush(1000); expect(h.result.current.canPlay).toBe(false) })
it('geometry instability remains unplayable even with an IDLE analysis snapshot', async () => { const h = await ready(); snapshot = preparedSnapshot({ geometry_unstable: true, config: { revision: 1, ready: false } }); await flush(1000); expect(h.result.current.canPlay).toBe(false); expect(h.result.current.acceptedRevision).toBeNull() })
it('unavailable UUID blocks every private create mutation with explanation', async () => { const h = renderHook(() => useSourcePreparation(vi.fn())); await flush(); vi.stubGlobal('crypto', {}); await act(async () => { await h.result.current.prepare(new File(['fake'], 'a.mp4', { type: 'video/mp4' })) }); expect(h.result.current.message).toContain('HTTPS atau localhost'); expect(calls.some(x => x.startsWith('POST'))).toBe(false) })
it('cancel during PUT holds admission through DELETE202 and ignores late PREPARED', async () => {
  let release!: (r: Response) => void
  override = (url, init) => {
    if (url.includes('/file?'))
      return new Promise(resolve => { release = resolve }); if (init?.method === 'DELETE') {
        snapshot = preparedSnapshot({ state: 'DELETING', cleanup: { pending: true, reason: 'cancelled', attempts: 0, capture: null, capture_attempts: 0 } })
        return Promise.resolve(sourceResponse({ state: 'DELETING' }, 202))
      } return null
  }
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush()
  let work!: Promise<void>
  act(() => { work = h.result.current.prepare(new File(['fake'], 'a.mp4', { type: 'video/mp4' })) })
  await flush()
  await act(async () => { await h.result.current.cancel() })
  await flush(1000)
  expect(h.result.current.cancelling).toBe(true)
  expect(h.result.current.draft).toBeNull()
  await act(async () => { release(sourceResponse({ state: 'PREPARED', frame_width: 640, frame_height: 480, preview_revision: 1 })); await work })
  await flush()
  expect(h.result.current.draft).toBeNull()
  expect(h.result.current.cancelling).toBe(true)
  expect(calls.filter(x => x.startsWith('DELETE'))).toHaveLength(1)
  snapshot = null
  await flush(1000)
  expect(h.result.current.identity).toBeNull()
  expect(h.result.current.cancelling).toBe(false)
})
it('two manual cancel clicks cannot overlap DELETE executors', async () => {
  const h = await prepared()
  let release!: (r: Response) => void
  override = (_url, init) => init?.method === 'DELETE' ? new Promise(resolve => { release = resolve }) : null
  let first!: Promise<void>
  act(() => { first = h.result.current.cancel() })
  await flush()
  await act(async () => { await h.result.current.cancel() })
  expect(calls.filter(x => x.startsWith('DELETE'))).toHaveLength(1)
  snapshot = null
  await act(async () => { release(sourceResponse(null, 204)); await first })
  await flush()
  expect(h.result.current.cancelling).toBe(false)
})
it('leftover cleanup without identity blocks upload and uses only explicit recovery', async () => {
  snapshot = preparedSnapshot({ source_id: null, generation: null, upload_operation_id: null, state: 'CLEANUP_STALLED', cleanup: { pending: true, reason: 'startup', attempts: 10, capture: null, capture_attempts: 0 } })
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush(3000)
  await act(async () => { await h.result.current.prepare(new File(['fake'], 'a.mp4', { type: 'video/mp4' })); await h.result.current.cancel() })
  expect(calls.some(x => x.startsWith('POST') || x.startsWith('DELETE'))).toBe(false)
  await act(async () => { await h.result.current.recover() })
  await flush()
  expect(calls.filter(x => x === 'POST /api/video/sources/cleanup/retry')).toHaveLength(1)
  expect(h.result.current.current?.cleanup.pending).toBe(true)
})
it('C1: a capabilities failure recovers legacy without guessing a mode', async () => {
  let reads = 0
  override = url => url.endsWith('/capabilities') ? (++reads === 1 ? Promise.reject(new Error('offline')) : Promise.resolve(sourceResponse({ ...privateCaps, mode: 'legacy', preparation_available: false }))) : null
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush()
  expect(h.result.current.capabilities).toBeNull()
  await flush(999)
  expect(reads).toBe(1)
  await flush(1)
  expect(h.result.current.capabilities?.mode).toBe('legacy')
  expect(calls.some(x => x.endsWith('/current'))).toBe(false)
})
it('capabilities reads remain serial and unmount aborts and discards a late read', async () => {
  let release!: (r: Response) => void
  let signal: AbortSignal | undefined
  override = (url, init) => url.endsWith('/capabilities') ? new Promise(resolve => { release = resolve; signal = init?.signal as AbortSignal }) : null
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush(60000)
  expect(calls.filter(x => x.endsWith('/capabilities'))).toHaveLength(1)
  h.unmount()
  expect(signal?.aborted).toBe(true)
  await act(async () => { release(sourceResponse(privateCaps)) })
  await flush(60000)
  expect(calls.filter(x => x.endsWith('/capabilities'))).toHaveLength(1)
})
it.each(['outcome_evicted', 'source_gone', 'attempt_limit', 'expired'])('terminal %s ends unknown Play without a new POST', async (category) => {
  const h = await ready()
  override = url => url.endsWith('/play') ? Promise.reject(new Error('lost')) : url.includes('/play/1') ? Promise.resolve(sourceResponse({ category }, category === 'attempt_limit' ? 409 : 410)) : null
  await act(async () => { await h.result.current.play() })
  await flush(5000)
  expect(h.result.current.unknown).toBeNull()
  expect(h.result.current.canPlay).toBe(false)
  expect(calls.filter(x => x.includes('/play/1'))).toHaveLength(1)
  expect(calls.filter(x => x.startsWith('POST') && x.endsWith('/play'))).toHaveLength(1)
})
it('404 pauses lookup and manual replay retains the exact Play request tuple', async () => {
  const h = await ready()
  const bodies: string[] = []
  override = (url, init) => {
    if (url.endsWith('/play')) {
      bodies.push(String(init?.body))
      return bodies.length === 1 ? Promise.reject(new Error('lost')) : Promise.resolve(sourceResponse(attemptRecord('REFUSED')))
    } if (url.includes('/play/1'))
      return Promise.resolve(sourceResponse({ category: 'unknown_attempt' }, 404)); return null
  }
  await act(async () => { await h.result.current.play() })
  await flush(10000)
  expect(h.result.current.reconciliationPaused).toBe(true)
  expect(h.result.current.unknown).toBe('play')
  expect(calls.filter(x => x.includes('/play/1'))).toHaveLength(1)
  await act(async () => { await h.result.current.replayPlay() })
  await flush()
  expect(bodies).toHaveLength(2)
  expect(bodies[1]).toBe(bodies[0])
  expect(h.result.current.unknown).toBeNull()
  await flush(5000)
  expect(bodies).toHaveLength(2)
})
it('fresh disappearance ends lost-save reconciliation and discards its canonical ACK', async () => {
  const h = await prepared()
  await act(async () => { await h.result.current.loadPreview() })
  await flush()
  act(() => { h.result.current.verifyImage(640, 480); h.result.current.changeDraft(validConfiguration) })
  override = url => url.endsWith('/configuration') ? Promise.reject(new Error('lost')) : null
  await act(async () => { await h.result.current.save() })
  await flush()
  expect(h.result.current.unknown).toBe('save')
  snapshot = null
  await flush(1000)
  expect(h.result.current.unknown).toBeNull()
  expect(h.result.current.identity).toBeNull()
  expect(h.result.current.canPlay).toBe(false)
})
it('fresh source B clears unknown A without accepting B readiness or issuing another Play', async () => {
  const h = await ready()
  override = url => url.endsWith('/play') ? Promise.reject(new Error('lost')) : url.includes('/play/1') ? Promise.resolve(sourceResponse({ category: 'unknown_attempt' }, 404)) : null
  await act(async () => { await h.result.current.play() })
  await flush()
  snapshot = preparedSnapshot({ source_id: '44444444-4444-4444-8444-444444444444', generation: 2, config: { revision: 1, ready: true } })
  await flush(1000)
  expect(h.result.current.unknown).toBeNull()
  expect(h.result.current.reconciliationPaused).toBe(false)
  expect(h.result.current.identity?.generation).toBe(2)
  expect(h.result.current.canPlay).toBe(false)
  expect(h.result.current.draft).toBeNull()
  expect(calls.filter(x => x.startsWith('POST') && x.endsWith('/play'))).toHaveLength(1)
})
it('cancel without ACK has a bounded wait; reset blocks a late create from PUT', async () => {
  let release!: (r: Response) => void
  override = url => url === '/api/video/sources' ? new Promise(resolve => { release = resolve }) : null
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush()
  let work!: Promise<void>
  act(() => { work = h.result.current.prepare(new File(['fake'], 'a.mp4', { type: 'video/mp4' })) })
  await flush()
  await act(async () => { await h.result.current.cancel() })
  await flush(CANCEL_RECONCILIATION_MS)
  expect(h.result.current.reconciliationPaused).toBe(true)
  expect(h.result.current.message).toContain('operasi server mungkin')
  act(() => { h.result.current.resetWaiting() })
  await flush()
  await act(async () => { release(sourceResponse({ source_id: sourceId, generation: 1, state: 'AWAITING_FILE' }, 201)); await work })
  await flush()
  expect(calls.some(x => x.includes('/file?'))).toBe(false)
  expect(calls.some(x => x.startsWith('DELETE'))).toBe(false)
  expect(h.result.current.pending).toBeNull()
  expect(h.result.current.identity).toBeNull()
})
it('unmount while create is held never PUTs or DELETEs on its late return', async () => {
  let release!: (r: Response) => void
  override = url => url === '/api/video/sources' ? new Promise(resolve => { release = resolve }) : null
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush()
  let work!: Promise<void>
  act(() => { work = h.result.current.prepare(new File(['fake'], 'a.mp4', { type: 'video/mp4' })) })
  await flush()
  h.unmount()
  await act(async () => { release(sourceResponse({ source_id: sourceId, generation: 1, state: 'AWAITING_FILE' }, 201)); await work })
  expect(calls.some(x => x.includes('/file?') || x.startsWith('DELETE'))).toBe(false)
})
it('HTTP LAN without UUID retains Stop, DELETE and cleanup recovery controls', async () => {
  const h = await prepared()
  vi.stubGlobal('crypto', {})
  await act(async () => { await h.result.current.stop(); await h.result.current.recover(); await h.result.current.cancel() })
  await flush()
  expect(calls).toContain('POST /api/video/stop')
  expect(calls).toContain('POST /api/video/sources/cleanup/retry')
  expect(calls.filter(x => x.startsWith('DELETE'))).toHaveLength(1)
})

it('capabilities backoff grows to a 15s ceiling and never overlaps', async () => {
  const at: number[] = []
  const start = Date.now()
  override = url => {
    if (!url.endsWith('/capabilities')) return null
    at.push(Date.now() - start)
    return Promise.reject(new Error('offline'))
  }
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush(45000)
  expect(at).toEqual([0, 1000, 3000, 7000, 15000, 30000, 45000])
  expect(h.result.current.capabilities).toBeNull()
})

it('a stale capabilities reply cannot replace a manually refreshed mode', async () => {
  let release!: (response: Response) => void
  let signal: AbortSignal | undefined
  let reads = 0
  override = (url, init) => {
    if (!url.endsWith('/capabilities')) return null
    if (++reads > 1) return Promise.resolve(sourceResponse({ ...privateCaps, mode: 'legacy', preparation_available: false }))
    signal = init?.signal as AbortSignal
    return new Promise(resolve => { release = resolve })
  }
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush()
  act(() => { h.result.current.refreshCapabilities() })
  await flush()
  expect(signal?.aborted).toBe(true)
  await act(async () => { release(sourceResponse(privateCaps)) })
  await flush()
  expect(h.result.current.capabilities?.mode).toBe('legacy')
})


it('R6: abort-reject plus current204 holds cancelled create and B until manual release', async () => {
  let aborts = 0
  override = (url, init) => url === '/api/video/sources'
    ? new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => {
        aborts++
        reject(new DOMException('Aborted', 'AbortError'))
      }, { once: true })
    }) : null
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush()
  let work!: Promise<void>
  act(() => { work = h.result.current.prepare(new File(['fake'], 'a.mp4', { type: 'video/mp4' })) })
  await flush()
  await act(async () => { await h.result.current.cancel(); await work })
  await flush(1000)
  expect(aborts).toBe(1)
  expect(h.result.current.current).toBeNull()
  expect(h.result.current.pending).toBeNull()
  expect(h.result.current.cancelling).toBe(true)
  await act(async () => { await h.result.current.prepare(new File(['fake'], 'b.mp4', { type: 'video/mp4' })) })
  expect(calls.filter(x => x === 'POST /api/video/sources')).toHaveLength(1)
  await flush(CANCEL_RECONCILIATION_MS - 1001)
  expect(h.result.current.reconciliationPaused).toBe(false)
  await flush(1)
  expect(h.result.current.reconciliationPaused).toBe(true)
  expect(h.result.current.cancelling).toBe(true)
  expect(h.result.current.message).toContain('tidak membatalkan pekerjaan backend')
  await flush(30000)
  await act(async () => { await h.result.current.prepare(new File(['fake'], 'b.mp4', { type: 'video/mp4' })) })
  expect(calls.filter(x => x === 'POST /api/video/sources')).toHaveLength(1)
})

it('R6: lost create ACK without cancel stays unknown across current204 and the 30s bound', async () => {
  override = url => url === '/api/video/sources' ? Promise.reject(new Error('Lost create ACK')) : null
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush()
  await act(async () => { await h.result.current.prepare(new File(['fake'], 'a.mp4', { type: 'video/mp4' })) })
  await flush(1000)
  expect(h.result.current.current).toBeNull()
  expect(h.result.current.unknown).toBe('create')
  expect(h.result.current.cancelling).toBe(false)
  await flush(CANCEL_RECONCILIATION_MS)
  expect(h.result.current.reconciliationPaused).toBe(true)
  expect(h.result.current.unknown).toBe('create')
  await act(async () => { await h.result.current.prepare(new File(['fake'], 'b.mp4', { type: 'video/mp4' })) })
  expect(calls.filter(x => x === 'POST /api/video/sources')).toHaveLength(1)
})

it('R6: manual release then B ignores A late ACK and rediscovers A only from its own snapshot', async () => {
  const sourceB = '44444444-4444-4444-8444-444444444444'
  const opB = '55555555-5555-4555-8555-555555555555'
  vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce(intentId).mockReturnValueOnce(opB) })
  let releaseA!: (response: Response) => void
  let releaseB!: (response: Response) => void
  let creates = 0
  override = (url) => {
    if (url === '/api/video/sources') {
      if (++creates === 1) return new Promise(resolve => { releaseA = resolve })
      snapshot = preparedSnapshot({ source_id: sourceB, generation: 2, upload_operation_id: opB, state: 'AWAITING_FILE' })
      return Promise.resolve(sourceResponse({ source_id: sourceB, generation: 2, state: 'AWAITING_FILE' }, 201))
    }
    if (url.includes(sourceB) && url.includes('/file?')) return new Promise(resolve => { releaseB = resolve })
    return null
  }
  const h = renderHook(() => useSourcePreparation(vi.fn()))
  await flush()
  let workA!: Promise<void>
  act(() => { workA = h.result.current.prepare(new File(['fake'], 'a.mp4', { type: 'video/mp4' })) })
  await flush()
  await act(async () => { await h.result.current.cancel() })
  await flush(CANCEL_RECONCILIATION_MS)
  act(() => { h.result.current.resetWaiting() })
  await flush()
  let workB!: Promise<void>
  act(() => { workB = h.result.current.prepare(new File(['fake'], 'b.mp4', { type: 'video/mp4' })) })
  await flush()
  expect(h.result.current.identity).toMatchObject({ source_id: sourceB, generation: 2 })
  await act(async () => { releaseA(sourceResponse({ source_id: sourceId, generation: 1, state: 'AWAITING_FILE' }, 201)); await workA })
  await flush()
  expect(h.result.current.identity).toMatchObject({ source_id: sourceB, generation: 2 })
  expect(h.result.current.pending).toBe('upload')
  expect(calls.some(x => x.includes(sourceId) && x.includes('/file?'))).toBe(false)
  snapshot = preparedSnapshot({ source_id: sourceB, generation: 2, upload_operation_id: opB })
  await act(async () => { releaseB(sourceResponse({ state: 'PREPARED', frame_width: 640, frame_height: 480, preview_revision: 1 })); await workB })
  await flush()
  expect(h.result.current.current?.upload_operation_id).toBe(opB)
  expect(calls.filter(x => x === 'POST /api/video/sources')).toHaveLength(2)
  snapshot = preparedSnapshot()
  await flush(1000)
  expect(h.result.current.identity).toEqual({ source_id: sourceId, generation: 1 })
  expect(h.result.current.current?.upload_operation_id).toBe(intentId)
  expect(h.result.current.message).toContain('ditemukan kembali')
  expect(h.result.current.message).toContain('bukan konfirmasi unggahan baru')
  expect(h.result.current.draft).toBeNull()
  expect(h.result.current.canPlay).toBe(false)
  expect(calls.some(x => x.startsWith('DELETE'))).toBe(false)
})
