import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, it, expect, vi } from 'vitest'
import { PrivateParkingConfiguration } from './PrivateParkingConfiguration'
import { CANCEL_RECONCILIATION_MS, useSourcePreparation } from '../hooks/useSourcePreparation'
import { privateCaps, sourceResponse, preparedSnapshot, validConfiguration } from '../testUtils/appFetchStub'
beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('crypto', { randomUUID: () => '22222222-2222-4222-8222-222222222222' }); vi.stubGlobal('fetch', vi.fn((url: RequestInfo | URL) => Promise.resolve(String(url).endsWith('capabilities') ? sourceResponse(privateCaps) : sourceResponse(preparedSnapshot())))) })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })
function Editor() { const p = useSourcePreparation(() => { }); return <PrivateParkingConfiguration preparation={p} /> }
it('retained source has no fabricated configuration or readiness', async () => { render(<Editor />); await act(async () => { await vi.advanceTimersByTimeAsync(0) }); expect((screen.getByRole('button', { name: 'Mulai analisis' }) as HTMLButtonElement).disabled).toBe(true); expect(screen.getByLabelText('P1 horizontal (%)').matches(':disabled')).toBe(true); expect(screen.queryByText(/gesture aktif|audio siap|Aman/)).toBeNull() })
it('missing UUID holds new mutation but keeps explicit DELETE available', async () => { vi.stubGlobal('crypto', {}); render(<Editor />); await act(async () => { await vi.advanceTimersByTimeAsync(0) }); screen.getByText(/HTTPS atau localhost/); expect((screen.getByLabelText('Pilih file untuk preview') as HTMLInputElement).disabled).toBe(true); fireEvent.click(screen.getByRole('button', { name: 'Hapus sumber' })); expect(vi.mocked(fetch).mock.calls.some(([, i]) => i?.method === 'DELETE')).toBe(true) })
it.each([['line.p2', 'P2 horizontal (%)'], ['inside', 'Kiri terhadap P1 ke P2'], ['zones[0].rect', 'Kiri (%)']])('server field %s marks and focuses its operator input', (field, label) => {
  const p = { capabilities: privateCaps, current: preparedSnapshot(), draft: validConfiguration, preview: 'blob:fixture', imageVerified: true, errorField: field, message: 'Pengaturan area belum valid.', pending: null, cancelling: false, unknown: null, canPlay: false, acceptedRevision: null, identity: null, fresh: true, reconciliationPaused: false, canReplay: false } as unknown as ReturnType<typeof useSourcePreparation>
  render(<PrivateParkingConfiguration preparation={p} />)
  const input = screen.getByLabelText(label)
  expect(input.getAttribute('aria-invalid')).toBe('true')
  expect(document.activeElement).toBe(input)
  expect(screen.queryByText(field)).toBeNull()
})
it.each(['configuration', 'revision', 'attempt'])('general server field %s focuses the form with a safe explanation', field => {
  const p = { capabilities: privateCaps, current: preparedSnapshot(), draft: validConfiguration, preview: 'blob:fixture', imageVerified: true, errorField: field, message: 'Permintaan belum valid.', pending: null, cancelling: false, unknown: null, canPlay: false, acceptedRevision: null, identity: null, fresh: true, reconciliationPaused: false, canReplay: false } as unknown as ReturnType<typeof useSourcePreparation>
  const { container } = render(<PrivateParkingConfiguration preparation={p} />)
  expect(document.activeElement).toBe(container.querySelector('form'))
  expect(screen.queryByText(field)).toBeNull()
  expect(screen.getByRole('alert').textContent).not.toBe('')
})


it.each([true, false])('R6: create unknown/cancelled=%s exposes only manual release after 30s', async cancelled => {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    requests.push((init?.method ?? 'GET') + ' ' + url)
    if (url.endsWith('/capabilities')) return Promise.resolve(sourceResponse(privateCaps))
    if (url.endsWith('/current')) return Promise.resolve(sourceResponse(null, 204))
    if (url === '/api/video/sources') {
      if (!cancelled) return Promise.reject(new Error('Lost ACK'))
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      })
    }
    throw new Error('Unexpected fake request')
  }))
  render(<Editor />)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  const fileInput = screen.getByLabelText('Pilih file untuk preview') as HTMLInputElement
  fireEvent.change(fileInput, { target: { files: [new File(['fake'], 'a.mp4', { type: 'video/mp4' })] } })
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  if (cancelled) {
    fireEvent.click(screen.getByRole('button', { name: 'Batalkan unggahan' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  }
  expect(fileInput.disabled).toBe(true)
  expect(screen.queryByRole('button', { name: 'Lepaskan penantian lokal' })).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(CANCEL_RECONCILIATION_MS) })
  const release = screen.getByRole('button', { name: 'Lepaskan penantian lokal' })
  expect(fileInput.disabled).toBe(true)
  expect(screen.getByText(/tidak membatalkan pekerjaan backend/)).toBeTruthy()
  expect(requests.filter(request => request === 'POST /api/video/sources')).toHaveLength(1)
  fireEvent.click(release)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(fileInput.disabled).toBe(false)
  expect(screen.getByText(/Operasi server mungkin masih selesai kemudian/)).toBeTruthy()
  expect(requests.filter(request => request === 'POST /api/video/sources')).toHaveLength(1)
})
