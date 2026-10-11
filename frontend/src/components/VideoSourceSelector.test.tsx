import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VideoState } from '../api/videoApi'
import { VideoSourceSelector } from './VideoSourceSelector'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function show(state = VideoState.IDLE, pendingState: VideoState | null = null) {
  const onPendingStateChange = vi.fn()
  const onActionSettled = vi.fn()
  render(<VideoSourceSelector state={state} pendingState={pendingState} onPendingStateChange={onPendingStateChange} onActionSettled={onActionSettled} />)
  return { onPendingStateChange, onActionSettled }
}

describe('compact source actions', () => {
  it.each([409, 503, 500])('maps HTTP %i to public copy, keeps pending until fresh status, never retries', async (status) => {
    const json = vi.fn(async () => ({ detail: 'synthetic-sensitive-marker' }))
    const fetch = vi.fn(async () => ({ ok: false, status, json }))
    vi.stubGlobal('fetch', fetch)
    const callbacks = show()
    fireEvent.click(screen.getByRole('button', { name: 'Mulai kamera' }))
    await waitFor(() => expect(callbacks.onActionSettled).toHaveBeenCalledTimes(1))
    expect(callbacks.onPendingStateChange.mock.calls).toEqual([[VideoState.STARTING]])
    const expected = status === 409 ? 'Sumber video masih aktif atau sedang dihentikan. Hentikan dahulu sebelum memulai lagi.'
      : status === 503 ? 'Video belum bisa dimulai. Coba hentikan sesi, lalu mulai kembali.'
        : 'Permintaan belum berhasil. Periksa status video sebelum mencoba lagi.'
    expect(screen.getByRole('alert').textContent).toBe(expected)
    expect(screen.queryByText(/synthetic-sensitive-marker/)).toBeNull()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(json).not.toHaveBeenCalled()
  })

  it('keeps state ERROR from HTTP 200 and labels Stop as an attempt', async () => {
    const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ state: VideoState.ERROR }) }))
    vi.stubGlobal('fetch', fetch)
    const callbacks = show(VideoState.ERROR)
    fireEvent.click(screen.getByRole('button', { name: 'Coba hentikan lagi' }))
    await waitFor(() => expect(callbacks.onActionSettled).toHaveBeenCalledTimes(1))
    expect(callbacks.onPendingStateChange.mock.calls).toEqual([[VideoState.STOPPING], [VideoState.ERROR]])
    expect(fetch).toHaveBeenCalledWith('/api/video/stop', { method: 'POST' })
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Kamera' })).toHaveProperty('disabled', true)
  })

  it('blocks double Start and source changes while a request is pending', async () => {
    let resolve!: (value: unknown) => void
    const fetch = vi.fn(() => new Promise((finish) => { resolve = finish }))
    vi.stubGlobal('fetch', fetch)
    const callbacks = show()
    const start = screen.getByRole('button', { name: 'Mulai kamera' })
    fireEvent.click(start)
    expect(start).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: 'File video' })).toHaveProperty('disabled', true)
    fireEvent.click(start)
    expect(fetch).toHaveBeenCalledTimes(1)
    resolve({ ok: true, json: async () => ({ state: VideoState.RUNNING }) })
    await waitFor(() => expect(callbacks.onActionSettled).toHaveBeenCalledTimes(1))
  })

  it('file selection uploads and starts exactly once; the same file can be selected again later', async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({ ok: true, json: async () => ({ state: VideoState.RUNNING }) }))
    vi.stubGlobal('fetch', fetch)
    const callbacks = show()
    fireEvent.click(screen.getByRole('button', { name: 'File video' }))
    screen.getByText(/Setelah dipilih, file langsung diunggah dan diputar/)
    const input = screen.getByLabelText('Pilih file dan mulai') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['placeholder'], 'clip.webm', { type: 'video/webm' })] } })
    await waitFor(() => expect(callbacks.onActionSettled).toHaveBeenCalledTimes(1))
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/video/upload')
    expect(input.value).toBe('')
    expect(callbacks.onPendingStateChange.mock.calls).toEqual([[VideoState.STARTING], [VideoState.RUNNING]])
    expect(screen.queryByRole('button', { name: /Play|Jeda|Garis|Zona/ })).toBeNull()
  })

  it('network failures use fixed copy without displaying the thrown message', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('synthetic-sensitive-marker') }))
    const callbacks = show()
    fireEvent.click(screen.getByRole('button', { name: 'Mulai kamera' }))
    await waitFor(() => expect(callbacks.onActionSettled).toHaveBeenCalledTimes(1))
    screen.getByText('Layanan tidak dapat dihubungi. Periksa koneksi ke aplikasi.')
    expect(screen.queryByText(/synthetic-sensitive-marker/)).toBeNull()
  })
})
