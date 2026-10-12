import {describe,it,expect} from 'vitest'
import {contain,pointerPoint,insidePolygon,geometryError,zoneGeometryIssues,geometryIssues,lineIsValid} from './privateParkingGeometry'
import {validConfiguration as c} from '../testUtils/appFetchStub'
it('accepts browser rounding at a fractional edge but rejects letterbox and nonfinite input', () => {
  const box = contain(640, 480, 830.859375, 466.46875)!
  expect(pointerPoint(box.x - .000002, 0, 640, 480, 830.859375, 466.46875)).toEqual([0, 0])
  expect(pointerPoint(box.x - .01, 0, 640, 480, 830.859375, 466.46875)).toBeNull()
  expect(pointerPoint(NaN, 0, 640, 480, 830.859375, 466.46875)).toBeNull()
})
describe('original image geometry',()=>{
 it.each([[640,480,800,450],[480,640,800,450],[640,640,400,400]])('round trips and ignores letterboxing %i x %i',(w,h,bw,bh)=>{const box=contain(w,h,bw,bh)!;const p=pointerPoint(box.x+.2*box.width,box.y+.8*box.height,w,h,bw,bh)!;expect(p[0]).toBeCloseTo(.2);expect(p[1]).toBeCloseTo(.8);if(box.x>0)expect(pointerPoint(0,bh/2,w,h,bw,bh)).toBeNull();if(box.y>0)expect(pointerPoint(bw/2,0,w,h,bw,bh)).toBeNull()})
 it('rejects zero dimensions and keeps normalized geometry across resize',()=>{expect(contain(0,1,1,1)).toBeNull();expect(c.line.p1).toEqual([.2,.5]);expect(geometryError(c)).toBeNull()})
 it('shades image-coordinate right below the forward horizontal line, reverses left',()=>{expect(insidePolygon(c).every(p=>p[1]>=.5)).toBe(true);expect(insidePolygon({...c,inside:'left'}).every(p=>p[1]<=.5)).toBe(true);expect(insidePolygon({...c,line:{p1:c.line.p2,p2:c.line.p1},inside:'left'})).toEqual(insidePolygon(c))})
 it.each([NaN,Infinity,-.1,1.1])('rejects hostile coordinate %s',n=>expect(geometryError({...c,line:{...c.line,p1:[n,0]}})).not.toBeNull())
 it('requires a side, sufficient area and bounded zone count',()=>{expect(geometryError({...c,inside:''})).not.toBeNull();expect(geometryError({...c,zones:[]})).not.toBeNull();expect(geometryError({...c,zones:Array.from({length:9},(_,i)=>({...c.zones[0],name:`zona-${i}`}))})).not.toBeNull();expect(geometryError({...c,zones:[{name:'z',rect:[.5,.5,.1,.1]}]})).not.toBeNull()})
})

it('structured issues keep raw rectangle order and select the correct invalid axes',()=>{
 const rect=[.8,.9,.2,.3]
 expect(zoneGeometryIssues(rect,0).map(issue=>issue.field)).toEqual(['zones[0].rect[0]','zones[0].rect[2]','zones[0].rect[1]','zones[0].rect[3]'])
 expect(rect).toEqual([.8,.9,.2,.3])
 expect(geometryIssues({...c,zones:[{name:'z',rect:[.1,.8,.5,.2]}]})[0].field).toBe('zones[0].rect[1]')
})
it('the shared area minimum and invalid initial line fail closed',()=>{
 expect(zoneGeometryIssues([.1,.1,.2,.11],0)).not.toHaveLength(0)
 expect(zoneGeometryIssues([0,0,.5,.5],0)).toHaveLength(0)
 expect(lineIsValid({...c,line:{p1:[0,0],p2:[0,0]}})).toBe(false)
})
