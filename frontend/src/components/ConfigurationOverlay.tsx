import { useEffect, useRef, useState } from 'react'
import { Configuration, Point, insidePolygon, pointerPoint } from '../geometry/privateParkingGeometry'
interface Props {configuration:Configuration;disabled:boolean;onChange:(c:Configuration)=>void;tool:'line'|'zone'}
export function ConfigurationOverlay({configuration:c,disabled,onChange,tool}:Props) {
 const svg=useRef<SVGSVGElement|null>(null),keyboardStart=useRef<Configuration|null>(null);const [scale,setScale]=useState(1)
 useEffect(()=>{const element=svg.current;if(!element)return;const measure=()=>{const b=element.getBoundingClientRect();if(b.width&&b.height)setScale(Math.min(b.width/c.frame_width,b.height/c.frame_height))};measure();const observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(measure);observer?.observe(element);return()=>observer?.disconnect()},[c.frame_width,c.frame_height])
 const first=useRef<Point|null>(null),[hint,setHint]=useState('Pilih dua titik pada gambar. Form angka juga tersedia.')
 const updatePoint=(index:0|1,p:Point)=>onChange({...c,line:{...c.line,[index===0?'p1':'p2']:p},inside:''})
 const clip=insidePolygon(c)
 return <svg ref={svg} className="configuration-overlay" aria-label="Garis dan area pada gambar" viewBox={`0 0 ${c.frame_width} ${c.frame_height}`} preserveAspectRatio="xMidYMid meet" onPointerDown={e=>{
  if(disabled)return
  const box=e.currentTarget.getBoundingClientRect();const p=pointerPoint(e.clientX-box.left,e.clientY-box.top,c.frame_width,c.frame_height,box.width,box.height)
  if(!p){setHint('Pilih titik pada gambar, bukan bidang kosong.');return}
  if(!first.current){first.current=p;setHint('Pilih titik kedua.');return}
  const a=first.current;first.current=null
  if(tool==='line'){onChange({...c,line:{p1:a,p2:p},inside:''})}
  else if(c.zones.length<8){let n=1;while(c.zones.some(z=>z.name===`zona-${n}`))n++;onChange({...c,zones:[...c.zones,{name:`zona-${n}`,rect:[Math.min(a[0],p[0]),Math.min(a[1],p[1]),Math.max(a[0],p[0]),Math.max(a[1],p[1])]}]})}
  setHint('Periksa pengaturan pada form sebelum menyimpan.')
 }} onPointerCancel={()=>{first.current=null;setHint('Pemilihan titik dibatalkan.')}}>
  <title>{hint}</title>
  {clip.length>2&&<polygon points={clip.map(p=>`${p[0]*c.frame_width},${p[1]*c.frame_height}`).join(' ')} className="inside-shade"/>}
  {c.zones.map((z,i)=><g key={z.name}><rect x={z.rect[0]*c.frame_width} y={z.rect[1]*c.frame_height} width={(z.rect[2]-z.rect[0])*c.frame_width} height={(z.rect[3]-z.rect[1])*c.frame_height} className="zone-shape"/><text style={{fontSize:14/scale}} x={z.rect[0]*c.frame_width+6} y={z.rect[1]*c.frame_height+20}>Area {i+1}</text></g>)}
  <line x1={c.line.p1[0]*c.frame_width} y1={c.line.p1[1]*c.frame_height} x2={c.line.p2[0]*c.frame_width} y2={c.line.p2[1]*c.frame_height} className="access-line"/>
  {[c.line.p1,c.line.p2].map((p,index)=><g key={index}><circle tabIndex={disabled?-1:0} role="button" aria-label={`Titik P${index+1}; panah untuk menggeser`} cx={p[0]*c.frame_width} cy={p[1]*c.frame_height} r={22/scale} className="point-handle" onBlur={()=>{keyboardStart.current=null}} onPointerDown={e=>e.stopPropagation()} onKeyDown={e=>{
   if(disabled)return
   if(e.key==='Escape'){first.current=null;if(keyboardStart.current){onChange(keyboardStart.current);keyboardStart.current=null}return}
   if(e.key==='Enter'){keyboardStart.current=null;return}
   const delta=e.shiftKey?10:1
   const dx=e.key==='ArrowLeft'?-delta:e.key==='ArrowRight'?delta:0,dy=e.key==='ArrowUp'?-delta:e.key==='ArrowDown'?delta:0
   if(dx||dy){e.preventDefault();if(!keyboardStart.current)keyboardStart.current=c;updatePoint(index as 0|1,[Math.max(0,Math.min(1,p[0]+dx/c.frame_width)),Math.max(0,Math.min(1,p[1]+dy/c.frame_height))])}
  }}/><circle className="point-marker" cx={p[0]*c.frame_width} cy={p[1]*c.frame_height} r={6/scale} pointerEvents="none"/><text style={{fontSize:14/scale}} x={p[0]*c.frame_width+10} y={p[1]*c.frame_height-10}>P{index+1}</text></g>)}
 </svg>
}
