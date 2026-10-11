import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { completedView } from './testUtils/appFetchStub'
import { ControlledCall, stubControlledFetch } from './testUtils/controlledFetch'

const STATUS = '/api/video/status'
const STOP = '/api/video/stop'
const START = '/api/video/webcam/start'
const EVALUATION = '/api/evaluation/current'

let net: { calls: (url: string) => ControlledCall[] }

function videoStatus(state: string) {
  return { state, source: state === 'RUNNING' ? 'webcam' : null, error: null }
}

function last(url: string): ControlledCall {
  const calls = net.calls(url)
  return calls[calls.length - 1]
}

async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

async function renderRunning(): Promise<void> {
  render(<App />)
  await flush()
  net.calls(STATUS)[0].respond(videoStatus('RUNNING'))
  await flush()
  screen.getByRole('button', { name: 'Hentikan video' })
}

beforeEach(() => {
  vi.useFakeTimers()
  net = stubControlledFetch()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('App video status lifecycle (real selector)', () => {
  it('status responses requested before or during Stop cannot revive RUNNING after IDLE', async () => {
    await renderRunning()
    await flush(1000)
    const beforeStop = last(STATUS)
    expect(net.calls(STATUS)).toHaveLength(2)

    fireEvent.click(screen.getByRole('button', { name: 'Hentikan video' }))
    expect(beforeStop.signal?.aborted).toBe(true)
    const duringStop = last(STATUS)
    expect(duringStop).not.toBe(beforeStop)

    net.calls(STOP)[0].respond(videoStatus('IDLE'))
    await flush()
    expect(duringStop.signal?.aborted).toBe(true)
    last(STATUS).respond(videoStatus('IDLE'))
    await flush()
    screen.getByRole('button', { name: 'Mulai kamera' })
    const evaluationRequests = net.calls(EVALUATION).length

    // Old RUNNING responses arrive after IDLE was confirmed.
    beforeStop.respond(videoStatus('RUNNING'))
    duringStop.respond(videoStatus('RUNNING'))
    await flush()
    screen.getByText('Belum berjalan')
    screen.getByRole('button', { name: 'Mulai kamera' })
    expect(screen.queryByRole('button', { name: 'Hentikan video' })).toBeNull()
    screen.getByText('Mulai video untuk melihat penilaian AI.')
    expect(net.calls(EVALUATION)).toHaveLength(evaluationRequests)
  })

  it('a failed Stop recovers from a fresh status read, never from a pre-Stop response', async () => {
    await renderRunning()
    await flush(1000)
    const beforeStop = last(STATUS)

    fireEvent.click(screen.getByRole('button', { name: 'Hentikan video' }))
    net.calls(STOP)[0].respond({ detail: 'Stop gagal' }, 500)
    await flush()
    screen.getByRole('alert')
    // Transitional state is kept until a status read issued after the failure.
    expect(screen.getByRole('button', { name: 'Menghentikan…' })).toHaveProperty('disabled', true)

    beforeStop.respond(videoStatus('RUNNING'))
    await flush()
    screen.getByRole('button', { name: 'Menghentikan…' })

    const evaluationRequests = net.calls(EVALUATION).length
    last(STATUS).respond(videoStatus('RUNNING'))
    await flush()
    screen.getByRole('button', { name: 'Hentikan video' })
    screen.getByText('Video berjalan')
    // A new lifecycle started for evaluation as well.
    expect(net.calls(EVALUATION).length).toBe(evaluationRequests + 1)
  })

  it('a failed Start recovers from a fresh status read, never from a pre-Start response', async () => {
    render(<App />)
    await flush()
    net.calls(STATUS)[0].respond(videoStatus('IDLE'))
    await flush()
    await flush(1000)
    const beforeStart = last(STATUS)

    fireEvent.click(screen.getByRole('button', { name: 'Mulai kamera' }))
    expect(beforeStart.signal?.aborted).toBe(true)
    net.calls(START)[0].respond({ detail: 'Webcam tidak tersedia' }, 500)
    await flush()
    screen.getByText('Permintaan belum berhasil. Periksa status video sebelum mencoba lagi.')
    screen.getByRole('button', { name: 'Memulai…' })

    beforeStart.respond(videoStatus('RUNNING'))
    await flush()
    expect(screen.queryByRole('button', { name: 'Hentikan video' })).toBeNull()
    expect(net.calls(EVALUATION)).toHaveLength(0)

    last(STATUS).respond(videoStatus('IDLE'))
    await flush()
    screen.getByRole('button', { name: 'Mulai kamera' })
    screen.getByText('Belum berjalan')
    expect(net.calls(EVALUATION)).toHaveLength(0)
  })

  it('EOF (status IDLE without any action) stops evaluation polling and clears the result', async () => {
    await renderRunning()
    net.calls(EVALUATION)[0].respond(completedView('session-A', 'event-A', 'alasan sesi A'))
    await flush()
    screen.getByText('alasan sesi A')

    await flush(1000)
    last(STATUS).respond(videoStatus('IDLE'))
    await flush()
    expect(screen.queryByText('alasan sesi A')).toBeNull()
    screen.getByText('Mulai video untuk melihat penilaian AI.')
    const evaluationRequests = net.calls(EVALUATION).length
    expect(net.calls(EVALUATION).every((call) => call.signal?.aborted || call === net.calls(EVALUATION)[0])).toBe(true)
    await flush(5000)
    expect(net.calls(EVALUATION)).toHaveLength(evaluationRequests)
  })

  it('quick Stop→Start: stale status and session-A evaluation never reach session B', async () => {
    await renderRunning()
    const sessionAEvaluation = net.calls(EVALUATION)[0]
    await flush(1000)
    const beforeStop = last(STATUS)

    fireEvent.click(screen.getByRole('button', { name: 'Hentikan video' }))
    net.calls(STOP)[0].respond(videoStatus('IDLE'))
    await flush()
    // Start again before any status read confirmed IDLE.
    fireEvent.click(screen.getByRole('button', { name: 'Mulai kamera' }))
    net.calls(START)[0].respond({ state: 'RUNNING', message: 'ok' })
    await flush()
    last(STATUS).respond(videoStatus('RUNNING'))
    await flush()
    const sessionBEvaluation = last(EVALUATION)
    expect(sessionBEvaluation).not.toBe(sessionAEvaluation)
    expect(sessionAEvaluation.signal?.aborted).toBe(true)

    beforeStop.respond(videoStatus('IDLE'))
    sessionAEvaluation.respond(completedView('session-A', 'event-A', 'alasan sesi A'))
    await flush()
    expect(screen.queryByText('alasan sesi A')).toBeNull()
    screen.getByRole('button', { name: 'Hentikan video' })

    sessionBEvaluation.respond(completedView('session-B', 'event-B', 'alasan sesi B'))
    await flush()
    screen.getByText('alasan sesi B')
    expect(screen.queryByText('alasan sesi A')).toBeNull()
  })
})
