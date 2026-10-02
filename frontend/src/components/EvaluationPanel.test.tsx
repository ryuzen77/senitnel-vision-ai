import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import {
  EvaluationCurrent, EvaluationLatestResult, EvaluationProcessState, ThreatStatus,
} from '../api/videoApi'
import { CurrentEvaluationState } from '../hooks/useCurrentEvaluation'
import { EvaluationPanel, getEvaluationUiState } from './EvaluationPanel'

afterEach(cleanup)

function latest(eventId: string, session = 'session-1', overrides: Partial<EvaluationLatestResult['assessment']> = {}): EvaluationLatestResult {
  return {
    event_id: eventId,
    source_session_id: session,
    assessment: {
      threatScore: 72,
      status: 'CRITICAL' as ThreatStatus,
      reasoning: 'Subjek terlihat lama di dekat motor.',
      suspectProfile: {
        personDetected: true,
        clothing: 'jaket gelap',
        observedAction: 'berjalan di dekat motor',
        visibleObjects: ['motor', 'helm'],
        distinguishingFeatures: [],
      },
      ...overrides,
    },
  }
}

function current(overrides: Partial<EvaluationCurrent> = {}): EvaluationCurrent {
  return {
    enabled: true, source_session_id: 'session-1', process_state: 'IDLE',
    active_event_id: null, latest_result: null, ...overrides,
  }
}

function hookState(view: EvaluationCurrent | null, extra: Partial<CurrentEvaluationState> = {}): CurrentEvaluationState {
  return { view, fetchError: null, loading: view === null, rebinding: false, ...extra }
}

describe('getEvaluationUiState', () => {
  it('maps lifecycle and frontend-only states', () => {
    expect(getEvaluationUiState(false, hookState(current())).kind).toBe('waiting-video')
    expect(getEvaluationUiState(true, hookState(null)).kind).toBe('loading')
    expect(getEvaluationUiState(true, hookState(null, { rebinding: true })).kind).toBe('rebinding')
    expect(getEvaluationUiState(true, hookState(current({ enabled: false }))).kind).toBe('disabled')
  })

  it.each<[EvaluationProcessState, string]>([
    ['IDLE', 'idle'], ['DISPATCHING', 'dispatching'], ['WAITING_CALLBACK', 'waiting-callback'],
    ['COMPLETED', 'completed'], ['FAILED', 'failed'], ['TIMED_OUT', 'timed-out'], ['CANCELLED', 'cancelled'],
  ])('maps backend process_state %s', (state, kind) => {
    expect(getEvaluationUiState(true, hookState(current({ process_state: state }))).kind).toBe(kind)
  })

  it('treats an unexpected enum value as unknown instead of crashing', () => {
    const ui = getEvaluationUiState(true, hookState(current({ process_state: 'BOGUS' as EvaluationProcessState })))
    expect(ui.kind).toBe('unknown')
  })

  it('network error is distinct from backend failure and hides any view', () => {
    const view = current({ process_state: 'COMPLETED', latest_result: latest('e1') })
    const ui = getEvaluationUiState(true, hookState(view, { fetchError: 'HTTP 503' }))
    expect(ui.kind).toBe('network-error')
    expect(ui.result).toBeNull()
    expect(getEvaluationUiState(true, hookState(current({ process_state: 'FAILED' }))).kind).toBe('failed')
  })

  it('never shows an assessment whose session does not match the response', () => {
    const mismatch = current({ process_state: 'COMPLETED', latest_result: latest('e1', 'other-session') })
    expect(getEvaluationUiState(true, hookState(mismatch)).result).toBeNull()
    const sessionless = current({ source_session_id: null, process_state: 'COMPLETED', latest_result: latest('e1') })
    expect(getEvaluationUiState(true, hookState(sessionless)).result).toBeNull()
  })

  it('labels a result as previous while a new event runs and after failure/timeout', () => {
    const waiting = getEvaluationUiState(true, hookState(current({
      process_state: 'WAITING_CALLBACK', active_event_id: 'event-new', latest_result: latest('event-old'),
    })))
    expect(waiting.result?.previous).toBe(true)
    expect(waiting.activeEventId).toBe('event-new')
    for (const state of ['FAILED', 'TIMED_OUT'] as EvaluationProcessState[]) {
      const ui = getEvaluationUiState(true, hookState(current({ process_state: state, latest_result: latest('event-old') })))
      expect(ui.result?.previous).toBe(true)
    }
    const done = getEvaluationUiState(true, hookState(current({ process_state: 'COMPLETED', latest_result: latest('event-old') })))
    expect(done.result?.previous).toBe(false)
  })
})

describe('EvaluationPanel rendering', () => {
  function show(view: EvaluationCurrent | null, extra: Partial<CurrentEvaluationState> = {}, running = true) {
    return render(<EvaluationPanel ui={getEvaluationUiState(running, hookState(view, extra))} />)
  }

  it('renders a completed assessment as a review prompt with process and assessment status separated', () => {
    show(current({ process_state: 'COMPLETED', latest_result: latest('event-1') }))
    screen.getByText('Hasil analisis AI — perlu ditinjau')
    screen.getByText('Status: Selesai')
    screen.getByText('Status penilaian: Kritis')
    screen.getByText('Profil subjek teramati')
    screen.getByText('72')
    screen.getByText('jaket gelap')
    screen.getByText('motor, helm')
    expect(screen.queryByText(/Hasil evaluasi sebelumnya/)).toBeNull()
  })

  it('shows the previous result with its own event identity during a new evaluation', () => {
    show(current({
      process_state: 'WAITING_CALLBACK', active_event_id: 'event-new', latest_result: latest('event-old'),
    }))
    screen.getByText('Status: Menunggu hasil')
    screen.getByText('Event diproses: event-new')
    screen.getByText(/Hasil evaluasi sebelumnya/)
    expect(screen.getAllByText('Event event-old').length).toBeGreaterThan(0)
  })

  it('keeps the previous label after timeout', () => {
    show(current({ process_state: 'TIMED_OUT', latest_result: latest('event-old') }))
    screen.getByText('Hasil analisis tidak diterima tepat waktu.')
    screen.getByText(/Hasil evaluasi sebelumnya/)
  })

  it('network error shows no stored result', () => {
    show(null, { fetchError: 'HTTP 503' })
    screen.getByText('Status evaluasi tidak dapat dimuat. Dicoba lagi otomatis.')
    expect(screen.queryByText('Hasil analisis AI — perlu ditinjau')).toBeNull()
  })

  it('renders disabled, loading and waiting-video states as text', () => {
    show(current({ enabled: false }))
    screen.getByText('Evaluasi AI eksternal tidak diaktifkan.')
    cleanup()
    show(null)
    screen.getByText('Memuat status evaluasi…')
    cleanup()
    show(current(), {}, false)
    screen.getByText('Menunggu sumber video.')
  })

  it('renders dangerous markup literally without creating elements', () => {
    const payload = '<img src=x onerror="alert(1)"><script>window.pwned=1</script><b>tebal</b>'
    const { container } = show(current({
      process_state: 'COMPLETED',
      latest_result: latest('event-1', 'session-1', {
        reasoning: payload,
        suspectProfile: {
          personDetected: false, clothing: payload, observedAction: payload,
          visibleObjects: [payload], distinguishingFeatures: [payload],
        },
      }),
    }))
    expect(container.querySelector('img, script, b')).toBeNull()
    expect(screen.getAllByText(payload, { exact: true }).length).toBeGreaterThanOrEqual(4)
  })
})
