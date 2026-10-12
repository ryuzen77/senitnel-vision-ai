import { useEffect, useRef, useState } from 'react'
import { Configuration, Point, insidePolygon, lineIsValid, pointerPoint, zoneGeometryIssues } from '../geometry/privateParkingGeometry'

export interface OverlayInteraction { pending: boolean; message: string }
interface Props {
  configuration: Configuration
  disabled: boolean
  onChange: (configuration: Configuration) => void
  tool: 'line' | 'zone'
  cancelVersion?: number
  minZoneArea?: number
  minLineLength?: number
  maxZones?: number
  onInteraction?: (interaction: OverlayInteraction) => void
}
interface Selection {
  point: Point | null
  message: string
  configuration: Configuration
  tool: Props['tool']
  cancelVersion: number
}

export function ConfigurationOverlay({
  configuration: c, disabled, onChange, tool, cancelVersion = 0,
  minZoneArea = .005, minLineLength = .05, maxZones = 8, onInteraction,
}: Props) {
  const svg = useRef<SVGSVGElement | null>(null)
  const keyboardStart = useRef<Configuration | null>(null)
  const [scale, setScale] = useState(1)
  const [selection, setSelection] = useState<Selection | null>(null)
  const contextMatches = !disabled && selection?.configuration === c
    && selection.tool === tool && selection.cancelVersion === cancelVersion
  const first = contextMatches ? selection.point : null
  const hint = contextMatches ? selection.message
    : tool === 'zone' ? 'Klik sudut pertama area motor, lalu sudut yang berlawanan. Form angka juga tersedia.'
      : 'Klik titik P1, lalu P2 pada lintasan masuk. Form angka juga tersedia.'
  const validLine = lineIsValid(c, minLineLength)
  const handlesActive = !disabled && tool === 'line' && validLine
  const clip = validLine ? insidePolygon(c) : []

  useEffect(() => {
    const element = svg.current
    if (!element) return
    const measure = () => {
      const box = element.getBoundingClientRect()
      if (box.width && box.height) setScale(Math.min(box.width / c.frame_width, box.height / c.frame_height))
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(element)
    return () => observer?.disconnect()
  }, [c.frame_width, c.frame_height])

  useEffect(() => {
    onInteraction?.({ pending: !!first, message: hint })
  }, [first, hint, onInteraction])

  useEffect(() => {
    keyboardStart.current = null
  }, [disabled, tool, cancelVersion])

  const report = (point: Point | null, message: string) => {
    setSelection({ point, message, configuration: c, tool, cancelVersion })
  }
  const cancel = () => report(null, 'Pemilihan titik dibatalkan. Klik titik pertama untuk mulai lagi.')

  return (
    <svg
      ref={svg} className="configuration-overlay" aria-label="Garis dan area pada gambar"
      tabIndex={disabled ? -1 : 0}
      viewBox={`0 0 ${c.frame_width} ${c.frame_height}`} preserveAspectRatio="xMidYMid meet"
      onKeyDown={event => {
        if (event.key !== 'Escape') return
        event.preventDefault()
        cancel()
        if (keyboardStart.current && tool === 'line' && !disabled) {
          onChange(keyboardStart.current)
          keyboardStart.current = null
        }
      }}
      onPointerDown={event => {
        if (disabled) return
        const box = event.currentTarget.getBoundingClientRect()
        const point = pointerPoint(event.clientX - box.left, event.clientY - box.top,
          c.frame_width, c.frame_height, box.width, box.height)
        if (!point) {
          report(first, 'Pilih titik pada gambar, bukan bidang kosong.')
          return
        }
        if (!first) {
          report(point, tool === 'zone' ? 'Sudut pertama ditandai. Klik sudut kedua yang berlawanan; Escape atau Batal untuk mengulang.'
            : 'Titik P1 ditandai. Klik P2; Escape atau Batal untuk mengulang.')
          return
        }
        if (tool === 'line') {
          const candidate = { ...c, line: { p1: first, p2: point }, inside: '' as const }
          if (!lineIsValid(candidate, minLineLength)) {
            report(null, 'Garis terlalu pendek. Klik ulang dua titik yang lebih berjauhan.')
            return
          }
          report(null, 'Periksa garis pada form sebelum menyimpan.')
          onChange(candidate)
          return
        }
        if (c.zones.length >= maxZones) {
          report(null, 'Batas area motor tercapai. Hapus area sebelum memilih lagi.')
          return
        }
        const rect: [number, number, number, number] = [
          Math.min(first[0], point[0]), Math.min(first[1], point[1]),
          Math.max(first[0], point[0]), Math.max(first[1], point[1]),
        ]
        const error = zoneGeometryIssues(rect, c.zones.length, minZoneArea)[0]
        if (error) {
          report(null, `${error.message} Klik ulang dua sudut yang lebih berjauhan.`)
          return
        }
        let name = 1
        while (c.zones.some(zone => zone.name === `zona-${name}`)) name++
        report(null, 'Periksa area motor pada form sebelum menyimpan.')
        onChange({ ...c, zones: [...c.zones, { name: `zona-${name}`, rect }] })
      }}
      onPointerCancel={cancel}
    >
      <title>{hint}</title>
      {clip.length > 2 && (
        <polygon points={clip.map(point => `${point[0] * c.frame_width},${point[1] * c.frame_height}`).join(' ')} className="inside-shade" pointerEvents="none" />
      )}
      {c.zones.map((zone, index) => zoneGeometryIssues(zone.rect, index, minZoneArea).length ? null : (
        <g key={zone.name} pointerEvents="none">
          <rect
            x={zone.rect[0] * c.frame_width} y={zone.rect[1] * c.frame_height}
            width={(zone.rect[2] - zone.rect[0]) * c.frame_width} height={(zone.rect[3] - zone.rect[1]) * c.frame_height}
            className="zone-shape"
          />
          <text style={{ fontSize: 14 / scale }} x={zone.rect[0] * c.frame_width + 6} y={zone.rect[1] * c.frame_height + 20}>
            Area {index + 1}
          </text>
        </g>
      ))}
      {validLine && (
        <line
          x1={c.line.p1[0] * c.frame_width} y1={c.line.p1[1] * c.frame_height}
          x2={c.line.p2[0] * c.frame_width} y2={c.line.p2[1] * c.frame_height}
          className="access-line" pointerEvents="none"
        />
      )}
      {validLine && [c.line.p1, c.line.p2].map((point, index) => (
        <g key={index} pointerEvents={handlesActive ? undefined : 'none'}>
          {handlesActive && (
            <circle
              tabIndex={0} role="button" aria-label={`Titik P${index + 1}; panah untuk menggeser`}
              cx={point[0] * c.frame_width} cy={point[1] * c.frame_height} r={22 / scale} className="point-handle"
              onBlur={() => { keyboardStart.current = null }}
              onPointerDown={event => {
                event.currentTarget.focus()
                if (!first) event.stopPropagation()
              }}
              onKeyDown={event => {
                if (!handlesActive) return
                if (event.key === 'Enter') { keyboardStart.current = null; return }
                const delta = event.shiftKey ? 10 : 1
                const dx = event.key === 'ArrowLeft' ? -delta : event.key === 'ArrowRight' ? delta : 0
                const dy = event.key === 'ArrowUp' ? -delta : event.key === 'ArrowDown' ? delta : 0
                if (!dx && !dy) return
                event.preventDefault()
                if (!keyboardStart.current) keyboardStart.current = c
                onChange({ ...c, line: { ...c.line, [index === 0 ? 'p1' : 'p2']: [
                  Math.max(0, Math.min(1, point[0] + dx / c.frame_width)),
                  Math.max(0, Math.min(1, point[1] + dy / c.frame_height)),
                ] }, inside: '' })
              }}
            />
          )}
          <circle className="point-marker" cx={point[0] * c.frame_width} cy={point[1] * c.frame_height} r={6 / scale} pointerEvents="none" />
          <text style={{ fontSize: 14 / scale }} x={point[0] * c.frame_width + 10} y={point[1] * c.frame_height - 10} pointerEvents="none">
            P{index + 1}
          </text>
        </g>
      ))}
      {first && (
        <circle className="point-marker pending-corner" aria-label="Titik pertama yang menunggu titik kedua"
          cx={first[0] * c.frame_width} cy={first[1] * c.frame_height} r={8 / scale} pointerEvents="none" />
      )}
    </svg>
  )
}
