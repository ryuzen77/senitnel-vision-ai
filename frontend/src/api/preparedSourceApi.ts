import { Configuration, geometryError } from '../geometry/privateParkingGeometry'
export const SOURCE_BASE = '/api/video/sources'
export const SECURE_CONTEXT_MESSAGE = 'Unggah baru, simpan pengaturan, dan Play baru memerlukan HTTPS atau localhost agar identitas operasi dapat dibuat. Stop, hapus sumber, dan pembersihan tetap tersedia.'
export function privateMutationAvailable(): boolean { return typeof globalThis.crypto?.randomUUID === 'function' }
export function operationId(): string {
  if (!privateMutationAvailable()) throw new SourceRequestError('deployment')
  return crypto.randomUUID()
}
export const SOURCE_MESSAGES = {
  unknown_source:'Sumber belum ditemukan. Periksa status sumber.', unknown_attempt:'Status mulai analisis belum ditemukan. Periksa status sumber.',
  source_gone:'Sumber ini sudah tidak tersedia.', outcome_evicted:'Riwayat operasi sudah tidak tersedia. Periksa status sumber dan pembersihan.',
  configuration_too_large:'Pengaturan terlalu besar.', unsupported_type:'Gunakan file MP4 atau WebM.', too_large:'File melebihi batas unggahan.',
  not_decodable:'File belum dapat diputar. Setelah pembersihan, pilih file lain.', invalid_geometry:'Pengaturan area belum valid.',
  invalid_operation_id:'Identitas operasi tidak valid. Periksa status sebelum tindakan baru.', invalid_request:'Permintaan belum valid. Periksa pengaturan.', missing_file:'Pilih file video terlebih dahulu.',
  write_failed:'File belum dapat diproses. Periksa pembersihan sumber.', preview_failed:'Preview belum dapat diproses.', cleanup_pending:'Pembersihan belum selesai.', storage_unavailable:'Penyimpanan belum tersedia.', closed:'Layanan sedang ditutup. Status belum tersedia.',
  generation_mismatch:'Sumber telah berubah. Periksa status sumber.', revision_conflict:'Pengaturan telah berubah. Periksa revisi sebelum menyimpan lagi.', revision_stale:'Revisi pengaturan telah berubah.',
  capture_busy:'Preview sedang digunakan proses lain.', play_in_progress:'Analisis sedang dimulai atau masih berjalan.', session_cleanup_pending:'Pembersihan analisis belum selesai.',
  operation_reused:'Identitas operasi sudah digunakan.', attempt_conflict:'Urutan mulai analisis tidak cocok.', attempt_superseded:'Operasi ini sudah dilewati.', attempt_limit:'Batas operasi sumber tercapai. Hapus sumber setelah pembersihan, lalu pilih file lagi.',
  frame_size_mismatch:'Ukuran gambar tidak cocok. Periksa kembali preview.', source_geometry_unstable:'Ukuran gambar berubah. Hapus sumber setelah pembersihan dan unggah ulang.',
  private_mode_inactive:'Alur persiapan privat tidak tersedia pada mode ini.', private_flow_required:'Mode privat memerlukan persiapan file sebelum analisis.',
  slot_busy:'Masih ada sumber yang digunakan aplikasi.', upload_not_expected:'File tidak dapat diunggah pada tahap ini.', cancelled:'Pengunggahan dibatalkan.', deadline_exceeded:'Batas waktu pengunggahan terlewati.', expired:'Sumber ini sudah kedaluwarsa.', not_prepared:'File belum siap untuk diatur.', play_pending:'Analisis sedang dimulai.', playing:'Analisis masih berjalan.', config_not_ready:'Simpan pengaturan yang valid sebelum mulai analisis.',
} as const
export type SourceCategory = keyof typeof SOURCE_MESSAGES
const GENERAL_FIELDS = ['configuration', 'revision', 'attempt', 'config_revision', 'operation_id', 'file', 'generation', 'expected_revision', 'frame_width', 'frame_height', 'inside', 'line', 'zones']
export function knownErrorField(value: unknown): string | null {
  if (typeof value !== 'string') return null
  return GENERAL_FIELDS.includes(value) || /^line\.p[12]$/.test(value)
    || /^zones\[[0-7]\](?:\.(?:name|rect))?$/.test(value) ? value : null
}
export function errorFieldMessage(field: string | null): string | null {
  if (!field || !knownErrorField(field)) return null
  if (field.startsWith('line')) return 'Periksa titik dan panjang garis masuk.'
  if (field === 'inside') return 'Pilih kembali sisi dalam terhadap arah P1 ke P2.'
  if (field.startsWith('zones')) return 'Periksa area motor yang ditandai.'
  if (['frame_width', 'frame_height'].includes(field)) return 'Periksa ukuran preview sumber ini.'
  if (['revision', 'expected_revision', 'config_revision'].includes(field)) return 'Revisi pengaturan perlu diperiksa sebelum menyimpan atau memulai.'
  if (['attempt', 'operation_id'].includes(field)) return 'Identitas permintaan mulai perlu diperiksa; jangan membuat permintaan baru untuk menebak hasilnya.'
  if (field === 'file') return 'Periksa file yang dipilih.'
  if (field === 'generation') return 'Sumber telah berubah. Periksa status sumber.'
  return 'Periksa pengaturan pada formulir ini.'
}
export class SourceRequestError extends Error {
  constructor(public readonly category: SourceCategory | 'response' | 'connection' | 'deployment', public readonly status=0, public readonly source_id: string|null=null, public readonly generation: number|null=null, public readonly field: string|null=null) {
    super(category==='deployment'?SECURE_CONTEXT_MESSAGE:category==='response'?'Respons aplikasi tidak dapat dibaca. Periksa status sebelum mencoba lagi.':category==='connection'?'Layanan tidak dapat dihubungi. Status belum dapat dipastikan.':SOURCE_MESSAGES[category])
    this.name='SourceRequestError'
  }
}
const uuid = (v: unknown): v is string => typeof v==='string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v)
const obj = (v: unknown): v is Record<string, unknown> => v!==null && typeof v==='object' && !Array.isArray(v)
const integer = (v: unknown, min=0): v is number => typeof v==='number' && Number.isSafeInteger(v) && v>=min
const nullable = (v: unknown, test: (v: unknown)=>boolean) => v===null || test(v)
const finite = (v: unknown): v is number => typeof v==='number' && Number.isFinite(v) && v>=0
function requireValid(ok: boolean): asserts ok { if (!ok) throw new SourceRequestError('response') }
export interface Limits {max_upload_bytes:number; accepted_types:string[];max_zones:number;min_line_length:number;min_zone_area:number;max_configuration_bytes:number}
export interface Capabilities { mode:'legacy'|'private'|'disabled'; preparation_available:boolean;template_available:boolean;evaluation_enabled:boolean;source_kinds:string[];limits:Limits }
export interface Attempt {attempt:number;operation_id:string;outcome:'PENDING'|'STARTED'|'REFUSED'|'FAILED'|'TIMED_OUT'|'CANCELLED';reason:string|null;session_ref:string|null;session_id:string|null;session:{phase:'NONE'|'RUNNING'|'ENDING'|'ENDED';end_reason:string|null;cleanup_ok:boolean|null}}
export interface CurrentSource {
 source_id:string|null;generation:number|null;state:'AWAITING_FILE'|'WRITING'|'PREPARED'|'PLAY_PENDING'|'PLAYING'|'ENDING'|'DELETING'|'CLEANUP_PENDING'|'CLEANUP_STALLED';upload_operation_id:string|null
 frame_width:number|null;frame_height:number|null;preview_revision:number|null;config:{revision:number;ready:boolean}|null;geometry_unstable:boolean;template_available:boolean;next_attempt:number|null;last_attempt:Attempt|null;expires_in_seconds:number|null;deadline_in_seconds:number|null
 cleanup:{pending:boolean;reason:string|null;attempts:number;capture:'CREATING'|'OWNED'|'RELEASE_PENDING'|'RELEASE_STALLED'|null;capture_attempts:number}
 analysis:{stop_required:boolean;state:string;session_ref:string|null;session_id:string|null}|null
}
export interface SourceIdentity {source_id:string;generation:number}
export interface Prepared {state:'PREPARED';frame_width:number;frame_height:number;preview_revision:number}
export interface Saved {revision:number;ready:boolean;canonical:Configuration}
function caps(v: unknown): Capabilities {
 requireValid(obj(v)&&['legacy','private','disabled'].includes(String(v.mode))&&typeof v.preparation_available==='boolean'&&typeof v.template_available==='boolean'&&typeof v.evaluation_enabled==='boolean'&&Array.isArray(v.source_kinds)&&v.source_kinds.every(x=>x==='file')&&obj(v.limits))
 const l=v.limits as Record<string,unknown>
 requireValid(integer(l.max_upload_bytes,1)&&integer(l.max_zones,1)&&finite(l.min_line_length)&&finite(l.min_zone_area)&&integer(l.max_configuration_bytes,1)&&Array.isArray(l.accepted_types)&&l.accepted_types.every(x=>['video/mp4','video/webm'].includes(String(x))))
 return v as unknown as Capabilities
}
export function decodeAttempt(v: unknown): Attempt {
 requireValid(obj(v)&&integer(v.attempt,1)&&uuid(v.operation_id)&&['PENDING','STARTED','REFUSED','FAILED','TIMED_OUT','CANCELLED'].includes(String(v.outcome))&&nullable(v.reason,x=>typeof x==='string')&&nullable(v.session_ref,x=>typeof x==='string')&&nullable(v.session_id,uuid)&&obj(v.session))
 const s=v.session as Record<string,unknown>
 requireValid(['NONE','RUNNING','ENDING','ENDED'].includes(String(s.phase))&&nullable(s.cleanup_ok,x=>typeof x==='boolean')&&nullable(s.end_reason,x=>['stop','eof','error','refused','cancelled','frame_size_changed'].includes(String(x))))
 return v as unknown as Attempt
}
function current(v: unknown): CurrentSource {
 requireValid(obj(v)&&nullable(v.source_id,uuid)&&nullable(v.generation,x=>integer(x,1))&&(v.source_id===null)===(v.generation===null)&&['AWAITING_FILE','WRITING','PREPARED','PLAY_PENDING','PLAYING','ENDING','DELETING','CLEANUP_PENDING','CLEANUP_STALLED'].includes(String(v.state))&&nullable(v.upload_operation_id,uuid)&&typeof v.geometry_unstable==='boolean'&&typeof v.template_available==='boolean')
 for(const k of ['frame_width','frame_height','preview_revision','next_attempt']) requireValid(nullable(v[k],x=>integer(x,1)))
 for(const k of ['expires_in_seconds','deadline_in_seconds']) requireValid(nullable(v[k],finite))
 requireValid(v.config===null||(obj(v.config)&&integer(v.config.revision,1)&&typeof v.config.ready==='boolean'))
 requireValid(obj(v.cleanup)&&typeof v.cleanup.pending==='boolean'&&integer(v.cleanup.attempts)&&integer(v.cleanup.capture_attempts)&&nullable(v.cleanup.reason,x=>typeof x==='string')&&nullable(v.cleanup.capture,x=>['CREATING','OWNED','RELEASE_PENDING','RELEASE_STALLED'].includes(String(x))))
 requireValid(v.analysis===null||(obj(v.analysis)&&typeof v.analysis.stop_required==='boolean'&&['IDLE','STARTING','RUNNING','STOPPING','ERROR'].includes(String(v.analysis.state))&&nullable(v.analysis.session_ref,x=>typeof x==='string')&&nullable(v.analysis.session_id,uuid)))
 if(v.last_attempt!==null)decodeAttempt(v.last_attempt)
 return v as unknown as CurrentSource
}
function configuration(v: unknown): Configuration {
 requireValid(obj(v)&&obj(v.line)&&Array.isArray(v.line.p1)&&v.line.p1.length===2&&Array.isArray(v.line.p2)&&v.line.p2.length===2&&Array.isArray(v.zones)&&v.zones.every(z=>obj(z)&&typeof z.name==='string'&&Array.isArray(z.rect)&&z.rect.length===4))
 const c=v as unknown as Configuration;requireValid(geometryError(c)===null);return c
}
async function request<T>(path:string, init:RequestInit, decode:(v:unknown)=>T, empty=false):Promise<T|null> {
 let r:Response
 try {r=await fetch(path,init)}catch(e){if(e instanceof DOMException&&e.name==='AbortError')throw e;throw new SourceRequestError('connection')}
 if(!r.ok){
   let body:unknown;try{body=await r.json()}catch{throw new SourceRequestError('response',r.status)}
   requireValid(obj(body)&&typeof body.category==='string'&&Object.prototype.hasOwnProperty.call(SOURCE_MESSAGES,body.category))
   const id=nullable(body.source_id,uuid)?body.source_id as string|null:null
   const generation=nullable(body.generation,x=>integer(x,1))?body.generation as number|null:null
   const field=knownErrorField(body.field)
   throw new SourceRequestError(body.category as SourceCategory,r.status,id,generation,field)
 }
 if(empty&&r.status===204)return null
 try{return decode(await r.json())}catch(e){if(e instanceof SourceRequestError)throw e;throw new SourceRequestError('response')}
}
const json=(body:unknown,signal?:AbortSignal):RequestInit=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal})
const target=(id:SourceIdentity)=>`${SOURCE_BASE}/${encodeURIComponent(id.source_id)}`
export const getCapabilities=(signal?:AbortSignal)=>request(SOURCE_BASE+'/capabilities',{signal},caps) as Promise<Capabilities>
export const getCurrentSource=(signal?:AbortSignal)=>request(SOURCE_BASE+'/current',{signal},current,true)
export async function createSource(id:string,signal?:AbortSignal):Promise<SourceIdentity>{return await request(SOURCE_BASE,json({operation_id:id},signal),v=>{requireValid(obj(v)&&uuid(v.source_id)&&integer(v.generation,1)&&v.state==='AWAITING_FILE');return v as unknown as SourceIdentity}) as SourceIdentity}
export async function putSourceFile(id:SourceIdentity,file:File,signal?:AbortSignal):Promise<Prepared>{const body=new FormData();body.append('file',file);return await request(target(id)+`/file?generation=${id.generation}`,{method:'PUT',body,signal},v=>{requireValid(obj(v)&&v.state==='PREPARED'&&integer(v.frame_width,1)&&integer(v.frame_height,1)&&integer(v.preview_revision,1));return v as unknown as Prepared}) as Prepared}
export async function getTemplate(id:SourceIdentity,signal?:AbortSignal):Promise<Configuration|null>{return await request(target(id)+`/template?generation=${id.generation}`,{signal},v=>{requireValid(obj(v)&&v.confirmed===false);return configuration(v)},true)}
export async function saveConfiguration(id:SourceIdentity,expected_revision:number|null,c:Configuration,signal?:AbortSignal):Promise<Saved>{
 const body={generation:id.generation,expected_revision,frame_width:c.frame_width,frame_height:c.frame_height,line:c.line,inside:c.inside,zones:c.zones}
 if(new TextEncoder().encode(JSON.stringify(body)).length>4096)throw new SourceRequestError('configuration_too_large',413)
 return await request(target(id)+'/configuration',{...json(body,signal),method:'PUT'},v=>{requireValid(obj(v)&&integer(v.revision,1)&&typeof v.ready==='boolean');return {revision:v.revision,ready:v.ready,canonical:configuration(v.canonical)}}) as Saved
}
export const playSource=(id:SourceIdentity,revision:number,attempt:number,operation_id:string,signal?:AbortSignal)=>request(target(id)+'/play',json({generation:id.generation,config_revision:revision,attempt,operation_id},signal),decodeAttempt) as Promise<Attempt>
export const getAttempt=(id:SourceIdentity,attempt:number,signal?:AbortSignal)=>request(target(id)+`/play/${attempt}`,{signal},decodeAttempt) as Promise<Attempt>
export const deleteSource=(id:SourceIdentity,signal?:AbortSignal)=>request(target(id)+`?generation=${id.generation}`,{method:'DELETE',signal},v=>{requireValid(obj(v)&&v.state==='DELETING');return v},true)
export const retrySourceCleanup=(signal?:AbortSignal)=>request(SOURCE_BASE+'/cleanup/retry',{method:'POST',signal},v=>{requireValid(obj(v)&&obj(v.cleanup)&&['pending','stalled','attempted'].every(k=>integer((v.cleanup as Record<string,unknown>)[k])));return v},true)
export async function previewSource(id:SourceIdentity,signal?:AbortSignal):Promise<Blob>{
 const r=await fetch(target(id)+`/preview?generation=${id.generation}`,{signal})
 if(!r.ok){await requestError(r);throw new SourceRequestError('response')}
 if(!r.headers.get('content-type')?.startsWith('image/jpeg'))throw new SourceRequestError('response')
 return r.blob()
}
async function requestError(r:Response):Promise<never>{
 let body:unknown;try{body=await r.json()}catch{throw new SourceRequestError('response')}
 requireValid(obj(body)&&typeof body.category==='string'&&Object.prototype.hasOwnProperty.call(SOURCE_MESSAGES,body.category))
 throw new SourceRequestError(body.category as SourceCategory,r.status,uuid(body.source_id)?body.source_id:null,integer(body.generation,1)?body.generation:null)
}
