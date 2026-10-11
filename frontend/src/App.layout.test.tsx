import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { VideoPreview } from './components/VideoPreview'
import { VideoState } from './api/videoApi'
import { stubAppFetch } from './testUtils/appFetchStub'

beforeEach(() => { vi.useFakeTimers(); stubAppFetch() })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('monitoring hierarchy and honest data', () => {
  it('places video and controls before the assessment; secondary details are closed, not separate transports', async () => {
    const { container } = render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const video = screen.getByRole('region', { name: 'Video aktif' })
    expect(document.documentElement.lang).toBe('id')
    const evaluation = screen.getByRole('region', { name: 'Penilaian AI' })
    expect(video.compareDocumentPosition(evaluation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    within(video).getByRole('img', { name: 'Preview video langsung' })
    within(video).getByRole('button', { name: 'Hentikan video' })
    for (const label of ['Detail objek', 'Detail sistem']) {
      const summary = screen.getByText(label)
      expect(summary.parentElement).toHaveProperty('open', false)
    }
    const fetch = vi.mocked(globalThis.fetch)
    const count = fetch.mock.calls.length
    fireEvent.click(screen.getByText('Detail objek'))
    fireEvent.click(screen.getByText('Detail sistem'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetch.mock.calls).toHaveLength(count)
    expect(container.querySelector('.pipeline-card')).toBeNull()
    expect(container.querySelector('.evaluation-profile')).toBeNull()
    expect(screen.queryByText(/Skor penilaian AI/)).toBeNull()
    expect(screen.queryByRole('button', { name: /gesture|mode privat|zona|garis|speaker|Play/i })).toBeNull()
    screen.getByText('Penilaian AI berbeda dari peringatan akses lokal. Status peringatan akses lokal belum tersedia di tampilan ini.')
  })
})

describe('stream failure and lifecycle', () => {
  it('keeps one URL per run, shows a nonfatal failure without retry, and recovers only on a new run', () => {
    const { rerender } = render(<VideoPreview state={VideoState.RUNNING} />)
    const image = screen.getByRole('img')
    const firstUrl = image.getAttribute('src')
    rerender(<VideoPreview state={VideoState.RUNNING} />)
    expect(screen.getByRole('img').getAttribute('src')).toBe(firstUrl)
    fireEvent.error(image)
    screen.getByText('Preview tidak tersedia')
    expect(screen.queryByRole('img')).toBeNull()
    rerender(<VideoPreview state={VideoState.RUNNING} />)
    expect(screen.queryByRole('img')).toBeNull()
    expect(vi.mocked(globalThis.fetch)).not.toHaveBeenCalled()
    rerender(<VideoPreview state={VideoState.IDLE} />)
    screen.getByText('Belum ada video aktif')
    rerender(<VideoPreview state={VideoState.RUNNING} />)
    expect(screen.getByRole('img').getAttribute('src')).not.toBe(firstUrl)
    expect(screen.queryByText('Preview tidak tersedia')).toBeNull()
  })
})
