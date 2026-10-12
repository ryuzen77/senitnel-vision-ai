import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api/preparedSourceApi'
import { Configuration, emptyConfiguration, geometryError } from '../geometry/privateParkingGeometry'
import { stopVideo } from '../api/videoApi'
export const CANCEL_RECONCILIATION_MS = 30000
export const CAPABILITIES_MAX_BACKOFF_MS = 15000
interface View {
  capabilities: api.Capabilities | null
  current: api.CurrentSource | null
  fresh: boolean
  pending: string | null
  message: string | null
  errorField: string | null
  identity: api.SourceIdentity | null
  preview: string | null
  imageVerified: boolean
  draft: Configuration | null
  acceptedRevision: number | null
  unknown: 'create' | 'save' | 'play' | null
  cancelling: boolean
  reconciliationPaused: boolean
}
interface UploadIntent {
  op: string
  cancelled: boolean
  settled: boolean
  id: api.SourceIdentity | null
  deleteSent: boolean
  deleting: boolean
}
interface PlayIntent {
  id: api.SourceIdentity
  attempt: number
  op: string
  revision: number
  paused: boolean
}
const initial: View = {
  capabilities: null, current: null, fresh: false, pending: null,
  message: null, errorField: null, identity: null, preview: null,
  imageVerified: false, draft: null, acceptedRevision: null,
  unknown: null, cancelling: false, reconciliationPaused: false,
}
const same = (a: api.SourceIdentity | null, b: {
  source_id: string | null
  generation: number | null
} | null) => !!a && !!b && a.source_id === b.source_id && a.generation === b.generation
function safeMessage(error: unknown): string {
  return error instanceof api.SourceRequestError
    ? error.message
    : 'Status belum dapat dipastikan. Periksa status sebelum mencoba lagi.'
}
function outcomeMessage(attempt: api.Attempt): string {
  switch (attempt.outcome) {
    case 'STARTED': return 'Permintaan mulai diterima. Memeriksa status analisis.'
    case 'REFUSED': return 'Analisis belum dapat dimulai. Periksa status; hentikan proses secara manual bila diperlukan.'
    case 'FAILED': return 'Analisis gagal dimulai. Periksa status dan pembersihan.'
    case 'TIMED_OUT': return 'Batas waktu mulai analisis terlewati. Memeriksa pembersihan.'
    case 'CANCELLED': return 'Permintaan mulai dibatalkan. Memeriksa pembersihan.'
    default: return 'Status mulai analisis belum dapat dipastikan.'
  }
}
export function useSourcePreparation(onLifecycle: () => void) {
  const [view, setView] = useState<View>(initial)
  const live = useRef(view)
  live.current = view
  const mounted = useRef(true)
  const generation = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const url = useRef<string | null>(null)
  const uploadIntent = useRef<UploadIntent | null>(null)
  const releasedUploadOperation = useRef<string | null>(null)
  const playIntent = useRef<PlayIntent | null>(null)
  // The executor token prevents a late finally from releasing a newer action.
  const executor = useRef<object | null>(null)
  const capsGeneration = useRef(0)
  const previousMode = useRef<api.Capabilities['mode'] | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [capsRefresh, setCapsRefresh] = useState(0)
  const [cancelRefresh, setCancelRefresh] = useState(0)
  const update = useCallback((part: Partial<View>) => {
    if (!mounted.current)
      return
    live.current = { ...live.current, ...part }
    setView(live.current)
  }, [])
  const clearImage = useCallback(() => {
    if (url.current)
      URL.revokeObjectURL(url.current)
    url.current = null
    update({ preview: null, imageVerified: false })
  }, [update])
  const poke = useCallback(() => {
    generation.current += 1
    update({ fresh: false })
    setRefresh(n => n + 1)
  }, [update])
  const isCurrent = (epoch: number) => mounted.current && generation.current === epoch
  const privateAvailable = () => live.current.capabilities?.mode === 'private'
    && live.current.capabilities.preparation_available
  const uuidAvailable = () => {
    if (api.privateMutationAvailable())
      return true
    update({ message: api.SECURE_CONTEXT_MESSAGE })
    return false
  }
  const refreshCapabilities = useCallback(() => setCapsRefresh(n => n + 1), [])
  // Reads are serial. A failure never invents a mode; the bounded backoff recovers.
  useEffect(() => {
    mounted.current = true
    const serial = ++capsGeneration.current
    let timer: number | undefined
    let readController: AbortController | undefined
    let failures = 0
    const valid = () => mounted.current && capsGeneration.current === serial
    const poll = async () => {
      readController = new AbortController()
      let delay = CAPABILITIES_MAX_BACKOFF_MS
      try {
        const capabilities = await api.getCapabilities(readController.signal)
        if (!valid())
          return
        failures = 0
        if (previousMode.current !== null && previousMode.current !== capabilities.mode) {
          controller.current?.abort()
          executor.current = null
          uploadIntent.current = null
          playIntent.current = null
          poke()
          clearImage()
          update({
            identity: null, current: null, draft: null, acceptedRevision: null,
            pending: null, unknown: null, cancelling: false, reconciliationPaused: false
          })
        }
        previousMode.current = capabilities.mode
        update({ capabilities })
      }
      catch (error) {
        if (!valid())
          return
        delay = Math.min(1000 * 2 ** Math.min(failures++, 4), CAPABILITIES_MAX_BACKOFF_MS)
        update({ capabilities: null, message: safeMessage(error), fresh: false })
      }
      finally {
        if (valid())
          timer = window.setTimeout(poll, delay)
      }
    }
    void poll()
    return () => {
      capsGeneration.current += 1
      readController?.abort()
      if (timer !== undefined)
        window.clearTimeout(timer)
    }
  }, [capsRefresh, clearImage, poke, update])
  useEffect(() => () => {
    mounted.current = false
    generation.current += 1
    executor.current = null
    uploadIntent.current = null
    playIntent.current = null
    controller.current?.abort()
    if (url.current)
      URL.revokeObjectURL(url.current)
  }, [])
  // This bounds frontend waiting only; it does not prove server cancellation/cleanup.
  useEffect(() => {
    if ((!view.cancelling && view.unknown !== 'create') || uploadIntent.current?.id || view.reconciliationPaused)
      return
    const intent = uploadIntent.current
    const timer = window.setTimeout(() => {
      if (!mounted.current || uploadIntent.current !== intent || intent?.id)
        return
      update({ reconciliationPaused: true, message: 'Hasil permintaan sumber belum diketahui. Lepaskan penantian lokal hanya untuk mereset UI; tindakan ini tidak membatalkan pekerjaan backend, dan operasi server mungkin masih berjalan.' })
    }, CANCEL_RECONCILIATION_MS)
    return () => window.clearTimeout(timer)
  }, [view.cancelling, view.unknown, view.reconciliationPaused, cancelRefresh, update])
  function retireIntent(message: string, current: api.CurrentSource | null) {
    controller.current?.abort()
    executor.current = null
    uploadIntent.current = null
    playIntent.current = null
    clearImage()
    generation.current += 1
    setRefresh(n => n + 1)
    update({
      current, identity: current?.source_id && current.generation
        ? { source_id: current.source_id, generation: current.generation } : null,
      fresh: true, pending: null, unknown: null, cancelling: false,
      reconciliationPaused: false, draft: null, acceptedRevision: null, errorField: null, message
    })
  }
  function acceptAttempt(result: api.Attempt, intent: PlayIntent) {
    if (result.attempt !== intent.attempt || result.operation_id !== intent.op) {
      throw new api.SourceRequestError('response')
    }
    if (result.outcome !== 'PENDING') {
      playIntent.current = null
      update({ unknown: null, reconciliationPaused: false, message: outcomeMessage(result) })
    }
  }
  function lookupError(error: unknown, intent: PlayIntent) {
    if (error instanceof api.SourceRequestError) {
      if (['outcome_evicted', 'source_gone', 'expired', 'attempt_limit', 'attempt_superseded', 'operation_reused', 'attempt_conflict'].includes(error.category)) {
        playIntent.current = null
        update({ unknown: null, reconciliationPaused: false, acceptedRevision: null, message: error.message })
        return
      }
      if (['unknown_source', 'unknown_attempt'].includes(error.category)) {
        intent.paused = true
        update({ reconciliationPaused: true, message: 'Status operasi belum ditemukan. Periksa lagi atau kirim ulang permintaan yang sama secara manual; hasil sebelumnya belum diketahui.' })
        return
      }
    }
    update({ message: safeMessage(error), errorField: error instanceof api.SourceRequestError ? error.field : null })
  }
  useEffect(() => {
    if (view.capabilities?.mode !== 'private')
      return
    let active = true
    let timer: number | undefined
    let readController: AbortController | undefined
    const epoch = generation.current
    const valid = () => active && isCurrent(epoch)
    const poll = async () => {
      readController = new AbortController()
      try {
        const current = await api.getCurrentSource(readController.signal)
        if (!valid())
          return
        const old = live.current
        const upload = uploadIntent.current
        const play = playIntent.current
        const waitingForCreate = !!upload && !upload.id
          && (old.cancelling || old.unknown === 'create' || old.pending === 'upload')
        const rediscovered = releasedUploadOperation.current !== null
          && current?.upload_operation_id === releasedUploadOperation.current && !!current?.source_id
        if (rediscovered) {
          releasedUploadOperation.current = null
          update({ message: 'Sumber dari permintaan sebelumnya ditemukan kembali pada backend. Ini bukan konfirmasi unggahan baru; periksa sumber ini sebelum tindakan berikutnya.' })
        }
        const changed = !!old.identity && !!current && !same(old.identity, current)
        const gone = !!old.identity && !current
        // Identity changes invalidate active actions too, not just settled replies.
        if (changed || gone) {
          retireIntent(rediscovered
            ? 'Sumber dari permintaan sebelumnya ditemukan kembali pada backend. Ini bukan konfirmasi unggahan baru; periksa sumber ini sebelum tindakan berikutnya.'
            : 'Status sumber berubah. Sumber yang ditemukan pada backend menggunakan identitasnya sendiri; periksa sumber yang sekarang.', current)
          return
        }
        // A rejected/aborted fetch or current 204 cannot prove that create did not run.
        // Keep its intent until an identity matches or the operator releases waiting.
        // A same-source snapshot sampled during a mutation cannot order its ACK.
        if (old.pending && !old.cancelling)
          return
        let identity = old.identity
        if (current?.source_id && current.generation && !identity && !old.cancelling && !waitingForCreate) {
          identity = { source_id: current.source_id, generation: current.generation }
        }
        if (upload && !upload.id && current?.upload_operation_id === upload.op
          && current.source_id && current.generation) {
          upload.id = { source_id: current.source_id, generation: current.generation }
          if (old.cancelling && !old.reconciliationPaused)
            void removeCancelled(upload)
          if (!old.cancelling) {
            identity = upload.id
            update({ unknown: null, reconciliationPaused: false,
              message: 'Sumber ditemukan kembali pada backend. Periksa preview dan pengaturan sumber ini; hasil unggahan belum dikonfirmasi.' })
          }
        }
        if (old.cancelling && upload?.id && upload.deleteSent && upload.settled
          && !same(upload.id, current)) {
          retireIntent('Sumber lama tidak lagi tersedia. Penghapusan mengikuti status backend.', current)
          return
        }
        const revoked = old.acceptedRevision !== null && same(old.identity, current)
          && current?.config?.revision !== old.acceptedRevision
        if (current?.geometry_unstable) {
          clearImage()
          update({ draft: null, acceptedRevision: null })
        }
        update({ current, identity, fresh: true, ...(revoked ? { acceptedRevision: null } : {}) })
        if (play && old.unknown === 'play' && !play.paused) {
          try {
            const result = await api.getAttempt(play.id, play.attempt, readController.signal)
            if (!valid() || playIntent.current !== play)
              return
            acceptAttempt(result, play)
          }
          catch (error) {
            if (valid() && playIntent.current === play)
              lookupError(error, play)
          }
        }
      }
      catch (error) {
        if (!valid())
          return
        update({ fresh: false, message: safeMessage(error) })
        if (error instanceof api.SourceRequestError && error.category === 'private_mode_inactive')
          refreshCapabilities()
      }
      finally {
        if (valid())
          timer = window.setTimeout(poll, 1000)
      }
    }
    void poll()
    return () => {
      active = false
      readController?.abort()
      if (timer !== undefined)
        window.clearTimeout(timer)
    }
    // Responses use the captured generation; action closures are invalidated by poke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.capabilities?.mode, refresh, update, clearImage, refreshCapabilities])
  async function removeCancelled(intent: UploadIntent) {
    if (!intent.id || intent.deleteSent || intent.deleting || !mounted.current
      || uploadIntent.current !== intent)
      return
    intent.deleteSent = true
    intent.deleting = true
    try {
      await api.deleteSource(intent.id)
      if (mounted.current && uploadIntent.current === intent) {
        poke()
        update({ message: 'Memeriksa penyelesaian penghapusan.' })
      }
    }
    catch (error) {
      if (mounted.current && uploadIntent.current === intent) {
        update({ message: safeMessage(error) })
        poke()
      }
    }
    finally {
      intent.deleting = false
    }
  }
  async function prepare(file: File) {
    if (executor.current || live.current.identity || live.current.current
      || live.current.cancelling || live.current.unknown || !live.current.fresh
      || !privateAvailable() || !uuidAvailable())
      return
    const caps = live.current.capabilities!
    if (!caps.limits.accepted_types.includes(file.type) || !/\.(mp4|webm)$/i.test(file.name)
      || file.size > caps.limits.max_upload_bytes) {
      update({ message: 'Gunakan MP4 atau WebM sesuai batas ukuran unggahan.' })
      return
    }
    const token = {}
    executor.current = token
    poke()
    const epoch = generation.current
    const ctrl = new AbortController()
    controller.current = ctrl
    const intent: UploadIntent = {
      op: api.operationId(), cancelled: false, settled: false,
      id: null, deleteSent: false, deleting: false
    }
    uploadIntent.current = intent
    update({ pending: 'upload', message: null, errorField: null, unknown: null })
    try {
      const id = await api.createSource(intent.op, ctrl.signal)
      // Reset/unmount may retire this intent even if fetch ignored abort.
      if (!mounted.current || uploadIntent.current !== intent)
        return
      intent.id = id
      if (intent.cancelled) {
        if (!live.current.reconciliationPaused)
          await removeCancelled(intent)
        return
      }
      if (!isCurrent(epoch))
        return
      update({ identity: id })
      const prepared = await api.putSourceFile(id, file, ctrl.signal)
      if (!mounted.current || uploadIntent.current !== intent)
        return
      if (intent.cancelled) {
        if (!live.current.reconciliationPaused)
          await removeCancelled(intent)
        return
      }
      if (!isCurrent(epoch))
        return
      update({
        draft: emptyConfiguration(prepared.frame_width, prepared.frame_height),
        message: 'File siap. Muat preview sebelum mengatur area.'
      })
    }
    catch (error) {
      if (!mounted.current || uploadIntent.current !== intent)
        return
      if (intent.cancelled) {
        if (intent.id && !live.current.reconciliationPaused)
          await removeCancelled(intent)
      }
      else if (isCurrent(epoch)) {
        const known = error instanceof api.SourceRequestError && !['connection', 'response'].includes(error.category)
        update({
          message: error instanceof api.SourceRequestError && error.category === 'cleanup_pending'
            ? 'Pembersihan unggahan belum selesai. Unggah ulang setelah pembersihan.' : safeMessage(error),
          unknown: !intent.id && !known ? 'create' : null
        })
      }
    }
    finally {
      intent.settled = true
      if (executor.current === token)
        executor.current = null
      if (mounted.current && uploadIntent.current === intent) {
        update({ pending: null })
        poke()
      }
    }
  }
  async function cancel() {
    if (!privateAvailable())
      return
    let intent = uploadIntent.current
    if (!intent) {
      const id = live.current.identity
      if (!id)
        return
      intent = {
        op: live.current.current?.upload_operation_id ?? '', id,
        cancelled: true, settled: true, deleteSent: false, deleting: false
      }
      uploadIntent.current = intent
    }
    else if (intent.cancelled && !intent.deleting) {
      // Only an operator click retries DELETE, never a poll or late ACK.
      intent.deleteSent = false
    }
    intent.cancelled = true
    controller.current?.abort()
    clearImage()
    update({
      cancelling: true, draft: null, acceptedRevision: null, unknown: null,
      reconciliationPaused: false, message: 'Memeriksa pembatalan dan pembersihan.'
    })
    setCancelRefresh(n => n + 1)
    poke()
    await removeCancelled(intent)
  }
  async function action(kind: string, run: (id: api.SourceIdentity, signal: AbortSignal) => Promise<void>, needsUuid = false) {
    const id = live.current.identity
    if (executor.current || !id || !privateAvailable() || (needsUuid && !uuidAvailable()))
      return
    const token = {}
    executor.current = token
    poke()
    const epoch = generation.current
    controller.current = new AbortController()
    update({ pending: kind, message: null, errorField: null })
    try {
      await run(id, controller.current.signal)
    }
    catch (error) {
      if (!isCurrent(epoch))
        return
      const safeError = error instanceof api.SourceRequestError && error.category === 'cleanup_pending'
        && !same(id, error) ? new api.SourceRequestError('response') : error
      const unknown = safeError instanceof api.SourceRequestError && ['response', 'connection'].includes(safeError.category)
      if (kind === 'play' && !unknown) {
        playIntent.current = null
        update({ unknown: null, reconciliationPaused: false })
      }
      update({
        message: safeMessage(safeError), errorField: safeError instanceof api.SourceRequestError ? safeError.field : null,
        ...(unknown && (kind === 'save' || kind === 'play') ? { unknown: kind } : {})
      })
    }
    finally {
      if (executor.current === token)
        executor.current = null
      if (isCurrent(epoch)) {
        update({ pending: null })
        poke()
      }
    }
  }
  async function loadPreview() {
    await action('preview', async (id, signal) => {
      const epoch = generation.current
      const current = live.current.current
      if (!same(id, current) || !current?.frame_width || !current.frame_height || current.geometry_unstable)
        return
      const blob = await api.previewSource(id, signal)
      if (!isCurrent(epoch))
        return
      clearImage()
      url.current = URL.createObjectURL(blob)
      update({
        preview: url.current, imageVerified: false,
        draft: live.current.draft ?? emptyConfiguration(current.frame_width, current.frame_height), message: null
      })
    })
  }
  async function template() {
    await action('template', async (id, signal) => {
      const epoch = generation.current
      const config = await api.getTemplate(id, signal)
      if (!isCurrent(epoch))
        return
      if (config && config.frame_width === live.current.draft?.frame_width
        && config.frame_height === live.current.draft?.frame_height) {
        update({ draft: config, acceptedRevision: null, message: 'Contoh pengaturan. Periksa dan simpan untuk file ini.' })
      }
      else
        update({ message: 'Contoh pengaturan tidak tersedia untuk gambar ini.' })
    })
  }
  async function save() {
    const draft = live.current.draft
    const current = live.current.current
    if (live.current.unknown === 'play' || !draft || !live.current.imageVerified || !same(live.current.identity, current)
      || !current || current.state !== 'PREPARED' || current.cleanup.pending || current.geometry_unstable)
      return
    const error = geometryError(draft, live.current.capabilities!.limits)
    if (error) {
      update({ message: error })
      return
    }
    await action('save', async (id, signal) => {
      const epoch = generation.current
      const saved = await api.saveConfiguration(id, current.config?.revision ?? null, draft, signal)
      if (isCurrent(epoch))
        update({
          draft: saved.canonical, acceptedRevision: saved.ready ? saved.revision : null,
          unknown: null, message: 'Pengaturan diterima. Memeriksa kesiapan sumber.'
        })
    }, true)
  }
  const canPlay = !!view.identity && same(view.identity, view.current) && view.current?.state === 'PREPARED'
    && !view.current.analysis?.stop_required && !!view.acceptedRevision
    && view.current.config?.revision === view.acceptedRevision && view.current.config.ready
    && !view.current.geometry_unstable && !view.current.cleanup.pending && view.imageVerified
    && view.fresh && !view.pending && !view.unknown && !view.cancelling && api.privateMutationAvailable()
  async function play() {
    if (!canPlay || !live.current.current?.next_attempt)
      return
    const intent: PlayIntent = {
      id: live.current.identity!, op: api.operationId(),
      attempt: live.current.current.next_attempt, revision: live.current.acceptedRevision!, paused: false
    }
    await action('play', async (_id, signal) => {
      const epoch = generation.current
      playIntent.current = intent
      onLifecycle()
      update({ unknown: 'play' })
      const result = await api.playSource(intent.id, intent.revision, intent.attempt, intent.op, signal)
      if (!isCurrent(epoch) || playIntent.current !== intent)
        return
      acceptAttempt(result, intent)
      onLifecycle()
    }, true)
  }
  async function checkOperation() {
    const intent = playIntent.current
    if (!intent || !same(intent.id, live.current.identity)) {
      if (live.current.cancelling || live.current.unknown === 'create') {
        update({ reconciliationPaused: false })
        setCancelRefresh(n => n + 1)
      }
      poke()
      return
    }
    await action('lookup', async (_id, signal) => {
      const epoch = generation.current
      try {
        const result = await api.getAttempt(intent.id, intent.attempt, signal)
        if (isCurrent(epoch) && playIntent.current === intent)
          acceptAttempt(result, intent)
      }
      catch (error) {
        if (isCurrent(epoch) && playIntent.current === intent)
          lookupError(error, intent)
      }
    })
  }
  async function replayPlay() {
    const intent = playIntent.current
    if (!intent || !intent.paused || !same(intent.id, live.current.identity) || !live.current.fresh)
      return
    await action('replay', async (_id, signal) => {
      const epoch = generation.current
      onLifecycle()
      try {
        const result = await api.playSource(intent.id, intent.revision, intent.attempt, intent.op, signal)
        if (isCurrent(epoch) && playIntent.current === intent)
          acceptAttempt(result, intent)
      }
      catch (error) {
        if (isCurrent(epoch) && playIntent.current === intent)
          lookupError(error, intent)
      }
      finally {
        if (isCurrent(epoch))
          onLifecycle()
      }
    })
  }
  function resetWaiting() {
    if (!live.current.reconciliationPaused || playIntent.current)
      return
    releasedUploadOperation.current = uploadIntent.current?.op ?? null
    retireIntent('Penantian lokal dilepas. Operasi server mungkin masih selesai kemudian; periksa status sebelum unggahan baru.', live.current.current)
    poke()
  }
  async function stop() {
    if (executor.current)
      return
    const token = {}
    executor.current = token
    poke()
    const epoch = generation.current
    onLifecycle()
    update({ pending: 'stop', message: null })
    try {
      await stopVideo()
    }
    catch (error) {
      if (isCurrent(epoch))
        update({ message: safeMessage(error) })
    }
    finally {
      if (executor.current === token)
        executor.current = null
      if (isCurrent(epoch)) {
        update({ pending: null })
        poke()
        onLifecycle()
      }
    }
  }
  async function recover() {
    if (executor.current || !privateAvailable())
      return
    const token = {}
    executor.current = token
    poke()
    const epoch = generation.current
    update({ pending: 'recover' })
    try {
      await api.retrySourceCleanup()
      if (isCurrent(epoch))
        update({ message: 'Memeriksa penyelesaian pembersihan.' })
    }
    catch (error) {
      if (isCurrent(epoch))
        update({ message: safeMessage(error) })
    }
    finally {
      if (executor.current === token)
        executor.current = null
      if (isCurrent(epoch)) {
        update({ pending: null })
        poke()
      }
    }
  }
  return {
    ...view, canPlay, prepare, cancel, loadPreview, template, save, play, stop, recover,
    refreshCapabilities, checkOperation, replayPlay, resetWaiting,
    canReplay: !!playIntent.current?.paused && view.unknown === 'play' && view.fresh && !view.pending,
    changeDraft: (draft: Configuration) => update({ draft, acceptedRevision: null, errorField: null }),
    verifyImage: (width: number, height: number) => {
      if (!view.preview || live.current.preview !== view.preview)
        return
      const draft = live.current.draft
      const matches = !!draft && draft.frame_width === width && draft.frame_height === height
      update({ imageVerified: matches, ...(!matches ? { message: 'Ukuran preview tidak cocok. Periksa sumber.', acceptedRevision: null } : {}) })
    },
    imageError: () => {
      if (!view.preview || live.current.preview !== view.preview)
        return
      clearImage()
      update({ message: 'Preview tidak tersedia. Muat ulang gambar secara manual.' })
    },
  }
}
