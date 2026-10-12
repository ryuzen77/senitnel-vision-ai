export type Point = [number, number]
export interface Configuration {
  frame_width: number; frame_height: number
  line: { p1: Point; p2: Point }; inside: 'left' | 'right' | ''
  zones: { name: string; rect: [number, number, number, number] }[]
}
export function emptyConfiguration(width: number, height: number): Configuration {
  return { frame_width: width, frame_height: height, line: { p1: [0, 0], p2: [0, 0] }, inside: '', zones: [] }
}
export function geometryError(c: Configuration, limits = { min_line_length: .05, min_zone_area: .005, max_zones: 8 }): string | null {
  const finite = (n: number) => Number.isFinite(n) && n >= 0 && n <= 1
  if (![c.frame_width, c.frame_height].every(n => Number.isInteger(n) && n > 0 && n <= 16384)) return 'Ukuran gambar belum dapat dipastikan.'
  if (![...c.line.p1, ...c.line.p2].every(finite)) return 'Koordinat garis harus berada pada gambar.'
  if (Math.hypot(c.line.p2[0] - c.line.p1[0], c.line.p2[1] - c.line.p1[1]) < limits.min_line_length) return 'Garis terlalu pendek.'
  if (!['left', 'right'].includes(c.inside)) return 'Pilih bagian dalam terhadap arah garis.'
  if (!c.zones.length || c.zones.length > limits.max_zones) return `Tambahkan 1 sampai ${limits.max_zones} area motor.`
  const names = new Set<string>()
  for (const z of c.zones) {
    if (!/^[a-z0-9_-]{1,32}$/.test(z.name) || names.has(z.name)) return 'Nama area harus unik.'
    names.add(z.name)
    const [x1, y1, x2, y2] = z.rect
    if (!z.rect.every(finite) || x1 >= x2 || y1 >= y2 || (x2-x1)*(y2-y1) < limits.min_zone_area) return 'Area motor terlalu kecil atau koordinatnya tidak valid.'
  }
  return null
}
export function contain(width: number, height: number, boxWidth: number, boxHeight: number) {
  if (![width, height, boxWidth, boxHeight].every(n => Number.isFinite(n) && n > 0)) return null
  const scale = Math.min(boxWidth/width, boxHeight/height)
  return { width: width*scale, height: height*scale, x: (boxWidth-width*scale)/2, y: (boxHeight-height*scale)/2, scale }
}
export function pointerPoint(x: number, y: number, width: number, height: number, boxWidth: number, boxHeight: number): Point | null {
  const c = contain(width, height, boxWidth, boxHeight)
  if (!c || x<c.x || y<c.y || x>c.x+c.width || y>c.y+c.height) return null
  return [(x-c.x)/c.width, (y-c.y)/c.height]
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
