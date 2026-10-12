import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ConfigurationOverlay } from './ConfigurationOverlay'
import { emptyConfiguration } from '../geometry/privateParkingGeometry'
import { validConfiguration } from '../testUtils/appFetchStub'

beforeEach(() => vi.stubGlobal('PointerEvent', MouseEvent))
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

function setup(tool: 'line' | 'zone' = 'zone', configuration = validConfiguration) {
  const onChange = vi.fn()
  const onInteraction = vi.fn()
  const rendered = render(<ConfigurationOverlay configuration={configuration} disabled={false} onChange={onChange} onInteraction={onInteraction} tool={tool} />)
  const svg = screen.getByLabelText('Garis dan area pada gambar')
  vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ left: 10, top: 20, width: 640, height: 480 } as DOMRect)
  const click = (x: number, y: number, target: Element = svg) => fireEvent.pointerDown(target, { clientX: 10 + x * 640, clientY: 20 + y * 480 })
  return { ...rendered, svg, onChange, onInteraction, click }
}

it('keyboard moves a real-image pixel and invalidates inside confirmation', () => {
  const h = setup('line')
  fireEvent.keyDown(screen.getByRole('button', { name: /Titik P1/ }), { key: 'ArrowRight' })
  const config = h.onChange.mock.calls[0][0]
  expect(config.line.p1[0]).toBeCloseTo(.2 + 1 / 640)
  expect(config.inside).toBe('')
})

it('zone mode has no active line handles and a second click at P1 reaches zone selection', () => {
  const h = setup()
  expect(screen.queryByRole('button', { name: /Titik P1/ })).toBeNull()
  const marker = h.svg.querySelector('.point-marker')!
  fireEvent.keyDown(marker, { key: 'ArrowRight' })
  expect(h.onChange).not.toHaveBeenCalled()
  h.click(.1, .1)
  h.click(.2, .5, marker)
  expect(h.onChange).toHaveBeenCalledTimes(1)
  expect(h.onChange.mock.calls[0][0].line).toEqual(validConfiguration.line)
  expect(h.onChange.mock.calls[0][0].zones[1].rect).toEqual([.1, .1, .2, .5])
})

it('invalid initial line has no origin handles; second zone click may be the image corner', () => {
  const h = setup('zone', emptyConfiguration(640, 480) as typeof validConfiguration)
  expect(h.svg.querySelector('.point-handle')).toBeNull()
  expect(h.svg.querySelector('.inside-shade')).toBeNull()
  h.click(.5, .5)
  expect(h.svg.querySelector('.pending-corner')).not.toBeNull()
  h.click(0, 0)
  expect(h.onChange.mock.calls[0][0].zones[0].rect).toEqual([0, 0, .5, .5])
})

it.each([[.1, .1], [.11, .11]])('invalid second corner %s/%s rejects and explains selecting again', (x, y) => {
  const h = setup()
  h.click(.1, .1)
  h.click(x, y)
  expect(h.onChange).not.toHaveBeenCalled()
  expect(h.svg.querySelector('.pending-corner')).toBeNull()
  expect(h.svg.querySelector('title')?.textContent).toContain('Klik ulang dua sudut')
  h.click(.4, .4)
  h.click(.7, .8)
  expect(h.onChange).toHaveBeenCalledTimes(1)
})

it('first corner is visible with second-click help and Escape clears it', () => {
  const h = setup()
  h.click(.3, .4)
  const marker = h.svg.querySelector('.pending-corner')!
  expect(Number(marker.getAttribute('cx'))).toBeCloseTo(192)
  expect(Number(marker.getAttribute('cy'))).toBeCloseTo(192)
  expect(h.onInteraction).toHaveBeenLastCalledWith(expect.objectContaining({ pending: true, message: expect.stringContaining('Klik sudut kedua') }))
  fireEvent.keyDown(h.svg, { key: 'Escape' })
  expect(h.svg.querySelector('.pending-corner')).toBeNull()
  expect(h.onChange).not.toHaveBeenCalled()
})

it.each(['draft', 'tool', 'cancel', 'disabled'] as const)('pending corner resets on %s', change => {
  const h = setup()
  h.click(.2, .3)
  const config = change === 'draft' ? { ...validConfiguration, zones: [] } : validConfiguration
  h.rerender(<ConfigurationOverlay configuration={config} disabled={change === 'disabled'} onChange={h.onChange} tool={change === 'tool' ? 'line' : 'zone'} cancelVersion={change === 'cancel' ? 1 : 0} />)
  expect(h.svg.querySelector('.pending-corner')).toBeNull()
  expect(h.onChange).not.toHaveBeenCalled()
})

it('disabled overlay has no keyboard handles or pointer edits', () => {
  const h = setup()
  h.rerender(<ConfigurationOverlay configuration={validConfiguration} disabled onChange={h.onChange} tool="line" />)
  expect(screen.queryByRole('button', { name: /Titik/ })).toBeNull()
  fireEvent.keyDown(h.svg, { key: 'ArrowRight' })
  h.click(.2, .2)
  expect(h.onChange).not.toHaveBeenCalled()
  expect(h.svg.querySelector('.pending-corner')).toBeNull()
})

it('reversed or subminimum numeric rectangles are never drawn as valid zones', () => {
  const config = { ...validConfiguration, zones: [
    { name: 'reverse', rect: [.8, .2, .3, .6] as [number, number, number, number] },
    { name: 'tiny', rect: [.1, .1, .11, .11] as [number, number, number, number] },
  ] }
  const h = setup('zone', config)
  expect(h.svg.querySelectorAll('.zone-shape')).toHaveLength(0)
  expect(config.zones[0].rect).toEqual([.8, .2, .3, .6])
})
