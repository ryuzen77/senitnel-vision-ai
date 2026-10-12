import { useState } from 'react'
import { Configuration } from '../geometry/privateParkingGeometry'
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


function EditableForm({ save, configuration = validConfiguration }: { save: () => void; configuration?: Configuration }) {
  const [draft, setDraft] = useState<Configuration>(configuration)
  const p = {
    capabilities: privateCaps, current: preparedSnapshot(), draft, preview: 'blob:fixture',
    imageVerified: true, errorField: null, message: null, pending: null, cancelling: false,
    unknown: null, canPlay: false, acceptedRevision: null, identity: null, fresh: true,
    reconciliationPaused: false, canReplay: false, save, changeDraft: setDraft,
  } as unknown as ReturnType<typeof useSourcePreparation>
  return <PrivateParkingConfiguration preparation={p} />
}

it('Escape in the panel preserves overlay keyboard undo and makes no save request', () => {
  const save = vi.fn()
  render(<EditableForm save={save} />)
  const handle = screen.getByRole('button', { name: 'Titik P1; panah untuk menggeser' })
  const horizontal = screen.getByLabelText('P1 horizontal (%)') as HTMLInputElement
  const original = horizontal.value
  fireEvent.keyDown(handle, { key: 'ArrowRight' })
  expect(horizontal.value).not.toBe(original)
  fireEvent.keyDown(handle, { key: 'Escape' })
  expect(horizontal.value).toBe(original)
  expect(save).not.toHaveBeenCalled()
})

it('numeric reverse order is retained, flagged immediately and Save focuses Kiri with no request', () => {
  const save = vi.fn()
  const { container } = render(<EditableForm save={save} />)
  const left = screen.getByLabelText('Kiri (%)') as HTMLInputElement
  const right = screen.getByLabelText('Kanan (%)') as HTMLInputElement
  fireEvent.change(left, { target: { value: '70' } })
  expect(left.value).toBe('70')
  expect(right.value).toBe('50')
  expect(left.getAttribute('aria-invalid')).toBe('true')
  expect(right.getAttribute('aria-invalid')).toBe('true')
  expect(document.getElementById(left.getAttribute('aria-describedby')!)?.textContent).toContain('Kiri harus lebih kecil')
  expect(container.querySelector('.zone-shape')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Simpan pengaturan' }))
  expect(document.activeElement).toBe(left)
  expect(save).not.toHaveBeenCalled()
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/configuration'))).toBe(false)
})

it('top/bottom stay typed, zero and small areas are rejected and focus the first offending axis', () => {
  const save = vi.fn()
  const { container } = render(<EditableForm save={save} />)
  const top = screen.getByLabelText('Atas (%)') as HTMLInputElement
  const bottom = screen.getByLabelText('Bawah (%)') as HTMLInputElement
  fireEvent.change(top, { target: { value: '80' } })
  expect(top.value).toBe('80')
  expect(bottom.value).toBe('50')
  fireEvent.click(screen.getByRole('button', { name: 'Simpan pengaturan' }))
  expect(document.activeElement).toBe(top)
  expect(save).not.toHaveBeenCalled()
  expect(container.querySelector('.zone-shape')).toBeNull()
  fireEvent.change(top, { target: { value: '10' } })
  fireEvent.change(bottom, { target: { value: '10' } })
  expect(bottom.getAttribute('aria-invalid')).toBe('true')
  fireEvent.change(bottom, { target: { value: '10.1' } })
  expect(screen.getByText(/Luas minimum/)).toBeTruthy()
  expect(container.querySelector('.zone-shape')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Simpan pengaturan' }))
  expect(document.activeElement).toBe(screen.getByLabelText('Kiri (%)'))
  expect(save).not.toHaveBeenCalled()
})

it('pending selection is visible outside the image and resets after a form edit or Escape', () => {
  vi.stubGlobal('PointerEvent', MouseEvent)
  const { container } = render(<EditableForm save={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Pilih sudut area pada gambar' }))
  const svg = screen.getByLabelText('Garis dan area pada gambar')
  vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 480 } as DOMRect)
  fireEvent.pointerDown(svg, { clientX: 192, clientY: 192 })
  expect(container.querySelector('.pending-corner')).not.toBeNull()
  expect(screen.getByRole('button', { name: 'Batalkan pemilihan' })).toBeTruthy()
  expect(container.querySelector('.editor-feedback')?.textContent).toContain('Klik sudut kedua')
  fireEvent.change(screen.getByLabelText('Kiri (%)'), { target: { value: '20' } })
  expect(container.querySelector('.pending-corner')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Batalkan pemilihan' })).toBeNull()
  fireEvent.pointerDown(svg, { clientX: 200, clientY: 200 })
  fireEvent.keyDown(screen.getByLabelText('Kiri (%)'), { key: 'Escape' })
  expect(container.querySelector('.pending-corner')).toBeNull()
  fireEvent.pointerDown(svg, { clientX: 220, clientY: 220 })
  fireEvent.click(screen.getByRole('button', { name: 'Batalkan pemilihan' }))
  expect(container.querySelector('.pending-corner')).toBeNull()
})
