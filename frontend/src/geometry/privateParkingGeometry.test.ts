import {describe,it,expect} from 'vitest'
import {contain,pointerPoint,insidePolygon,geometryError} from './privateParkingGeometry'
import {validConfiguration as c} from '../testUtils/appFetchStub'
describe('original image geometry',()=>{
 it.each([[640,480,800,450],[480,640,800,450],[640,640,400,400]])('round trips and ignores letterboxing %i x %i',(w,h,bw,bh)=>{const box=contain(w,h,bw,bh)!;const p=pointerPoint(box.x+.2*box.width,box.y+.8*box.height,w,h,bw,bh)!;expect(p[0]).toBeCloseTo(.2);expect(p[1]).toBeCloseTo(.8);if(box.x>0)expect(pointerPoint(0,bh/2,w,h,bw,bh)).toBeNull();if(box.y>0)expect(pointerPoint(bw/2,0,w,h,bw,bh)).toBeNull()})
 it('rejects zero dimensions and keeps normalized geometry across resize',()=>{expect(contain(0,1,1,1)).toBeNull();expect(c.line.p1).toEqual([.2,.5]);expect(geometryError(c)).toBeNull()})
 it('shades image-coordinate right below the forward horizontal line, reverses left',()=>{expect(insidePolygon(c).every(p=>p[1]>=.5)).toBe(true);expect(insidePolygon({...c,inside:'left'}).every(p=>p[1]<=.5)).toBe(true);expect(insidePolygon({...c,line:{p1:c.line.p2,p2:c.line.p1},inside:'left'})).toEqual(insidePolygon(c))})
 it.each([NaN,Infinity,-.1,1.1])('rejects hostile coordinate %s',n=>expect(geometryError({...c,line:{...c.line,p1:[n,0]}})).not.toBeNull())
 it('requires a side, sufficient area and bounded zone count',()=>{expect(geometryError({...c,inside:''})).not.toBeNull();expect(geometryError({...c,zones:[]})).not.toBeNull();expect(geometryError({...c,zones:Array.from({length:9},(_,i)=>({...c.zones[0],name:`zona-${i}`}))})).not.toBeNull();expect(geometryError({...c,zones:[{name:'z',rect:[.5,.5,.1,.1]}]})).not.toBeNull()})
})
