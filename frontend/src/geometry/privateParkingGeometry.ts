export type Point = [number, number]
export interface Configuration {
  frame_width: number; frame_height: number
  line: { p1: Point; p2: Point }; inside: 'left' | 'right' | ''
  zones: { name: string; rect: [number, number, number, number] }[]
}
export function emptyConfiguration(width: number, height: number): Configuration {
  return { frame_width: width, frame_height: height, line: { p1: [0, 0], p2: [0, 0] }, inside: '', zones: [] }
}
export interface GeometryIssue { field: string; message: string }
export interface GeometryLimits { min_line_length: number; min_zone_area: number; max_zones: number }
const defaultLimits: GeometryLimits = { min_line_length: .05, min_zone_area: .005, max_zones: 8 }
const normalized = (value: number) => Number.isFinite(value) && value >= 0 && value <= 1

export function lineIsValid(c: Configuration, minimum = defaultLimits.min_line_length): boolean {
  return [...c.line.p1, ...c.line.p2].every(normalized)
    && Math.hypot(c.line.p2[0] - c.line.p1[0], c.line.p2[1] - c.line.p1[1]) >= minimum
}

// Form values stay in their entered order. Only pointer selection normalizes corners.
export function zoneGeometryIssues(rect: readonly number[], index: number, minimum = defaultLimits.min_zone_area): GeometryIssue[] {
  const fields = rect.map((_, axis) => `zones[${index}].rect[${axis}]`)
  const labels = ['Kiri', 'Atas', 'Kanan', 'Bawah']
  const issues: GeometryIssue[] = []
  rect.forEach((value, axis) => {
    if (!normalized(value)) issues.push({ field: fields[axis], message: `Isi ${labels[axis]} antara 0 dan 100%.` })
  })
  if (issues.length) return issues
  const [left, top, right, bottom] = rect
  if (left >= right) {
    for (const axis of [0, 2]) issues.push({ field: fields[axis], message: 'Kiri harus lebih kecil dari Kanan. Ubah nilai batas area.' })
  }
  if (top >= bottom) {
    for (const axis of [1, 3]) issues.push({ field: fields[axis], message: 'Atas harus lebih kecil dari Bawah. Ubah nilai batas area.' })
  }
  if (!issues.length && (right - left) * (bottom - top) < minimum) {
    for (const field of fields) issues.push({ field, message: `Area motor terlalu kecil. Luas minimum ${minimum * 100}% gambar; perbesar batas area.` })
  }
  return issues
}

export function geometryIssues(c: Configuration, limits: GeometryLimits = defaultLimits): GeometryIssue[] {
  const issues: GeometryIssue[] = []
  for (const field of ['frame_width', 'frame_height'] as const) {
    if (!Number.isInteger(c[field]) || c[field] <= 0 || c[field] > 16384) {
      issues.push({ field, message: 'Ukuran gambar belum dapat dipastikan.' })
    }
  }
  for (const name of ['p1', 'p2'] as const) {
    c.line[name].forEach((value, axis) => {
      if (!normalized(value)) issues.push({ field: `line.${name}[${axis}]`, message: 'Koordinat garis harus berada antara 0 dan 100% gambar.' })
    })
  }
  if ([...c.line.p1, ...c.line.p2].every(normalized) && !lineIsValid(c, limits.min_line_length)) {
    issues.push({ field: 'line', message: 'Garis terlalu pendek. Jauhkan kedua titik masuk.' })
  }
  if (!['left', 'right'].includes(c.inside)) issues.push({ field: 'inside', message: 'Pilih bagian dalam terhadap arah garis.' })
  if (!c.zones.length || c.zones.length > limits.max_zones) {
    issues.push({ field: 'zones', message: `Tambahkan 1 sampai ${limits.max_zones} area motor.` })
  }
  const names = new Set<string>()
  c.zones.forEach((zone, index) => {
    if (!/^[a-z0-9_-]{1,32}$/.test(zone.name) || names.has(zone.name)) {
      issues.push({ field: `zones[${index}].name`, message: 'Nama area harus unik.' })
    }
    names.add(zone.name)
    issues.push(...zoneGeometryIssues(zone.rect, index, limits.min_zone_area))
  })
  return issues
}

export function geometryError(c: Configuration, limits: GeometryLimits = defaultLimits): string | null {
  return geometryIssues(c, limits)[0]?.message ?? null
}
export function contain(width: number, height: number, boxWidth: number, boxHeight: number) {
  if (![width, height, boxWidth, boxHeight].every(n => Number.isFinite(n) && n > 0)) return null
  const scale = Math.min(boxWidth/width, boxHeight/height)
  return { width: width*scale, height: height*scale, x: (boxWidth-width*scale)/2, y: (boxHeight-height*scale)/2, scale }
}
export function pointerPoint(x: number, y: number, width: number, height: number, boxWidth: number, boxHeight: number): Point | null {
  const c = contain(width, height, boxWidth, boxHeight)
  // Browser pointer coordinates can round fractional CSS pixels at the image edge.
  const epsilon = .00001
  if (!c || !Number.isFinite(x) || !Number.isFinite(y)
    || x < c.x - epsilon || y < c.y - epsilon
    || x > c.x + c.width + epsilon || y > c.y + c.height + epsilon) return null
  return [Math.max(0, Math.min(1, (x - c.x) / c.width)),
    Math.max(0, Math.min(1, (y - c.y) / c.height))]
}
export function insidePolygon(c: Configuration): Point[] {
  let polygon: Point[] = [[0,0],[1,0],[1,1],[0,1]]
  if (!c.inside) return []
  const side = (p: Point) => ((c.line.p2[0]-c.line.p1[0])*(p[1]-c.line.p1[1])-(c.line.p2[1]-c.line.p1[1])*(p[0]-c.line.p1[0]))*(c.inside==='right'?1:-1)
  const output: Point[] = []
  for (let i=0;i<polygon.length;i++) {
    const a=polygon[i], b=polygon[(i+1)%polygon.length], sa=side(a), sb=side(b)
    if (sa>=0) output.push(a)
    if ((sa>=0)!==(sb>=0)) { const t=sa/(sa-sb); output.push([a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])]) }
  }
  polygon=output; return polygon
}
