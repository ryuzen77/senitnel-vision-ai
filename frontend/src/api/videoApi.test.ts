import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiRequestError, getCurrentDomain, getCurrentEvaluation, getLatestDetection,
  getVideoStatus, startWebcam, stopVideo, uploadVideo, VideoValidationError,
} from './videoApi'

afterEach(() => vi.unstubAllGlobals())

describe('public HTTP errors', () => {
  it.each([startWebcam, stopVideo, getVideoStatus, getLatestDetection, getCurrentDomain, getCurrentEvaluation])(
    'keeps the HTTP status without reading or retaining an arbitrary body', async (request) => {
      const json = vi.fn(async () => ({ detail: 'synthetic-sensitive-marker' }))
      const fetch = vi.fn(async () => ({ ok: false, status: 503, json }))
      vi.stubGlobal('fetch', fetch)
      const error = await request().catch((value: unknown) => value)
      expect(error).toBeInstanceOf(ApiRequestError)
      expect(error).toMatchObject({ status: 503, message: 'HTTP 503' })
      expect(json).not.toHaveBeenCalled()
      expect(fetch).toHaveBeenCalledTimes(1)
    },
  )

  it.each([409, 422, 500, 503])('upload preserves HTTP %i without retry or response body', async (status) => {
    const json = vi.fn(async () => ({ detail: 'synthetic-sensitive-marker' }))
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({ ok: false, status, json }))
    vi.stubGlobal('fetch', fetch)
    const file = new File(['placeholder'], 'clip.mp4', { type: 'video/mp4' })
    await expect(uploadVideo(file)).rejects.toMatchObject({ status, message: `HTTP ${status}` })
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/video/upload')
    const body = (fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData
    expect(body.get('file')).toBe(file)
    expect(json).not.toHaveBeenCalled()
  })

  it('keeps 204 detection and successful payload semantics', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce({ status: 204 })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ state: 'ERROR', message: 'Start failed' }) })
    vi.stubGlobal('fetch', fetch)
    expect(await getLatestDetection()).toBeNull()
    expect(await startWebcam()).toEqual({ state: 'ERROR', message: 'Start failed' })
  })
})

describe('upload validation before any POST', () => {
  it.each([
    new File(['placeholder'], 'clip.mp4', { type: 'text/plain' }),
    new File(['placeholder'], 'clip.exe', { type: 'video/mp4' }),
  ])('rejects unsupported MIME or extension', async (file) => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(uploadVideo(file)).rejects.toBeInstanceOf(VideoValidationError)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('rejects oversized input without allocating a video or sending a request', async () => {
    const file = new File(['placeholder'], 'clip.mp4', { type: 'video/mp4' })
    Object.defineProperty(file, 'size', { value: 200 * 1024 * 1024 + 1 })
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(uploadVideo(file)).rejects.toBeInstanceOf(VideoValidationError)
    expect(fetch).not.toHaveBeenCalled()
  })
})
