import { useEffect, useRef, useState } from 'react'
import { useSourcePreparation } from '../hooks/useSourcePreparation'
import { ConfigurationOverlay } from './ConfigurationOverlay'
import { Configuration, geometryError } from '../geometry/privateParkingGeometry'
import { errorFieldMessage, privateMutationAvailable, SECURE_CONTEXT_MESSAGE } from '../api/preparedSourceApi'

type Preparation = ReturnType<typeof useSourcePreparation>

// Avoid binary floating point tails in the form; retain original payload precision.
const percent = (value: number) => Number.isFinite(value) ? Number((value * 100).toFixed(8)) : ''

export function PrivateParkingConfiguration({ preparation: p }: { preparation: Preparation }) {
  const [tool, setTool] = useState<'line' | 'zone'>('line')
  const [validation, setValidation] = useState<string | null>(null)
  const form = useRef<HTMLFormElement>(null)
  const c = p.current
  const d = p.draft
  const fieldMessage = errorFieldMessage(p.errorField)
  const fieldInvalid = (name: string) => !!p.errorField && (
    name === p.errorField || name.startsWith(p.errorField + '.') || name.startsWith(p.errorField + '[')
  )

  useEffect(() => {
    if (!p.errorField) return
    const input = [...form.current?.querySelectorAll<HTMLElement>('[data-contract-field]') ?? []]
      .find(el => fieldInvalid(el.dataset.contractField ?? '') && !el.matches(':disabled'))
    ;(input ?? form.current)?.focus()
  }, [p.errorField, p.pending])

  const cryptoAvailable = privateMutationAvailable()
  const locked = !!p.pending || p.unknown === 'play' || p.cancelling || !!c?.cleanup.pending
    || !!c?.geometry_unstable || c?.state !== 'PREPARED' || !p.imageVerified || !cryptoAvailable
  const editorChange = (draft: Configuration) => {
    setValidation(null)
    p.changeDraft(draft)
  }
  const pointInput = (name: 'p1' | 'p2', axis: 0 | 1) => (
    <label key={`${name}-${axis}`}>
      {name.toUpperCase()} {axis === 0 ? 'horizontal' : 'vertikal'} (%)
      <input
        type="number" min="0" max="100" step="any"
        data-contract-field={`line.${name}`}
        aria-invalid={fieldInvalid(`line.${name}`)}
        value={d ? percent(d.line[name][axis]) : ''}
        onChange={e => {
          if (!d) return
          const point = [...d.line[name]] as [number, number]
          point[axis] = e.target.value === '' ? NaN : Number(e.target.value) / 100
          editorChange({ ...d, line: { ...d.line, [name]: point }, inside: '' })
        }}
      />
    </label>
  )

  return (
    <section className="private-preparation" aria-label="Persiapan area parkir">
      <div className="preparation-image-column">
        <h2>Preview persiapan</h2>
        <p className="preview-only">
          {p.unknown === 'play' || p.pending === 'play' || c?.state === 'PLAY_PENDING' || c?.state === 'PLAYING'
            ? 'Status analisis sedang diperiksa.' : 'Preview saja — analisis belum dimulai'}
        </p>
        <div className="preparation-image">
          {p.preview && d ? (
            <>
              <img
                src={p.preview} alt="Preview file untuk pengaturan area"
                onLoad={e => p.verifyImage(e.currentTarget.naturalWidth, e.currentTarget.naturalHeight)}
                onError={p.imageError}
              />
              <ConfigurationOverlay
                key={`${p.preview}-${tool}`} configuration={d}
                disabled={locked} onChange={editorChange} tool={tool}
              />
            </>
          ) : <p>Gambar sumber akan tersedia setelah file disiapkan.</p>}
        </div>
        {!cryptoAvailable && <p className="inline-error" role="alert">{SECURE_CONTEXT_MESSAGE}</p>}
        <div className="preparation-actions">
          <label className="file-input-label">
            Pilih file untuk preview
            <input
              type="file" aria-label="Pilih file untuk preview" accept="video/mp4,video/webm"
              disabled={!cryptoAvailable || !p.capabilities?.preparation_available || !!c || !!p.identity
                || !!p.pending || p.cancelling || !!p.unknown || !p.fresh}
              onChange={e => {
                const file = e.target.files?.[0]
                e.target.value = ''
                if (file) void p.prepare(file)
              }}
            />
            <small>File belum dianalisis sampai Anda memilih Mulai analisis.</small>
          </label>
          {p.identity && (
            <button
              type="button"
              disabled={!!p.pending || p.cancelling || !!c?.geometry_unstable
                || !!c?.cleanup.pending || c?.state !== 'PREPARED'}
              onClick={() => void p.loadPreview()}
            >Muat preview</button>
          )}
          {(p.identity || p.pending === 'upload' || p.unknown === 'create') && (
            <button
              type="button" disabled={c?.state === 'PLAYING' || c?.state === 'ENDING'}
              onClick={() => void p.cancel()}
            >{p.pending === 'upload' ? 'Batalkan unggahan' : 'Hapus sumber'}</button>
          )}
          {(c?.state === 'CLEANUP_STALLED' || c?.cleanup.capture === 'RELEASE_STALLED') && (
            <button type="button" disabled={!!p.pending} onClick={() => void p.recover()}>
              Coba bersihkan lagi
            </button>
          )}
          {(c?.analysis?.stop_required || c?.state === 'PLAYING' || c?.state === 'ENDING') && (
            <button type="button" className="stop-button" disabled={!!p.pending} onClick={() => void p.stop()}>
              Hentikan analisis
            </button>
          )}
          {p.reconciliationPaused && (
            <button
              type="button" disabled={!!p.pending && p.pending !== 'upload'}
              onClick={() => void p.checkOperation()}
            >Periksa operasi lagi</button>
          )}
          {p.canReplay && (
            <button type="button" onClick={() => void p.replayPlay()}>
              Kirim ulang permintaan yang sama
            </button>
          )}
          {p.reconciliationPaused && (p.cancelling || p.unknown === 'create') && (
            <button type="button" onClick={p.resetWaiting}>Lepaskan penantian lokal</button>
          )}
        </div>
        {(p.pending || p.cancelling || p.unknown || c?.cleanup.pending) && (
          <p role="status">
            {p.reconciliationPaused ? 'Pemeriksaan otomatis operasi dihentikan. Pilih tindakan pemulihan manual.'
              : p.cancelling ? 'Memeriksa pembatalan dan pembersihan.'
              : p.unknown ? 'Status belum dapat dipastikan. Sedang memeriksa status.'
              : c?.cleanup.pending ? 'Pembersihan belum selesai.' : 'Permintaan sedang diproses.'}
          </p>
        )}
        {p.message && <p className="inline-error" role="status">{p.message}</p>}
        {c?.geometry_unstable && (
          <p className="inline-error">
            Ukuran gambar berubah. Hapus sumber setelah pembersihan, lalu unggah dan atur kembali.
          </p>
        )}
      </div>
      <form
        ref={form} tabIndex={-1} className="configuration-form"
        aria-describedby={fieldMessage ? 'server-field-error' : undefined}
        onSubmit={e => {
          e.preventDefault()
          const error = d ? geometryError(d, p.capabilities?.limits) : 'Muat preview terlebih dahulu.'
          setValidation(error)
          if (error) {
            form.current?.querySelector<HTMLInputElement>('input:not(:disabled)')?.focus()
            return
          }
          void p.save()
        }}
      >
        <h2>Atur area parkir</h2>
        <p>Garis yang dilalui saat masuk dan area tempat motor diparkir.</p>
        {p.capabilities?.template_available && (
          <button type="button" disabled={locked} onClick={() => void p.template()}>
            Gunakan contoh pengaturan
          </button>
        )}
        <fieldset disabled={locked} aria-describedby={validation ? 'configuration-error' : undefined}>
          <legend>Garis masuk — arah P1 ke P2</legend>
          <div className="coordinate-grid">
            {pointInput('p1', 0)}{pointInput('p1', 1)}{pointInput('p2', 0)}{pointInput('p2', 1)}
          </div>
          <div className="editor-tools">
            <button type="button" aria-pressed={tool === 'line'} onClick={() => setTool('line')}>
              Pilih titik garis pada gambar
            </button>
            <button
              type="button" disabled={(d?.zones.length ?? 0) >= 8}
              aria-pressed={tool === 'zone'} onClick={() => setTool('zone')}
            >Pilih sudut area pada gambar</button>
          </div>
          <fieldset>
            <legend>Bagian dalam terhadap arah P1 ke P2</legend>
            {(['left', 'right'] as const).map(side => (
              <label className="side-choice" key={side}>
                <input
                  type="radio" name="inside" data-contract-field="inside"
                  aria-invalid={fieldInvalid('inside')} checked={d?.inside === side}
                  onChange={() => { if (d) editorChange({ ...d, inside: side }) }}
                />
                {side === 'left' ? 'Kiri' : 'Kanan'} terhadap P1 ke P2
              </label>
            ))}
          </fieldset>
          <p className="editor-help">
            Arsiran menunjukkan sisi yang dipilih. Angka adalah persen gambar asli; garis dapat diatur tanpa drag.
          </p>
          {d?.zones.map((zone, index) => (
            <fieldset key={zone.name}>
              <legend>Area motor {index + 1}</legend>
              <div className="coordinate-grid">
                {zone.rect.map((value, axis) => (
                  <label key={axis}>
                    {['Kiri', 'Atas', 'Kanan', 'Bawah'][axis]} (%)
                    <input
                      type="number" min="0" max="100" step="any"
                      data-contract-field={`zones[${index}].rect`}
                      aria-invalid={fieldInvalid(`zones[${index}].rect`)} value={percent(value)}
                      onChange={e => {
                        const rect = [...zone.rect] as [number, number, number, number]
                        rect[axis] = e.target.value === '' ? NaN : Number(e.target.value) / 100
                        editorChange({ ...d, zones: d.zones.map((existing, i) => i === index ? { ...zone, rect } : existing) })
                      }}
                    />
                  </label>
                ))}
              </div>
              <button type="button" onClick={() => editorChange({ ...d, zones: d.zones.filter((_, i) => i !== index) })}>
                Hapus area {index + 1}
              </button>
            </fieldset>
          ))}
          <button
            type="button" disabled={(d?.zones.length ?? 0) >= 8}
            onClick={() => {
              if (!d) return
              let n = 1
              while (d.zones.some(zone => zone.name === `zona-${n}`)) n++
              editorChange({ ...d, zones: [...d.zones, { name: `zona-${n}`, rect: [0, 0, 0, 0] }] })
            }}
          >Tambah area motor</button>
        </fieldset>
        {fieldMessage && <p id="server-field-error" className="inline-error" role="alert">{fieldMessage}</p>}
        {validation && <p id="configuration-error" className="inline-error" role="alert">{validation}</p>}
        <button className="primary-button" type="submit" disabled={locked}>Simpan pengaturan</button>
        <p role="status">
          {p.acceptedRevision ? 'Pengaturan diterima untuk sumber ini.' : 'Pengaturan belum disimpan dan dikonfirmasi untuk sumber ini.'}
        </p>
        <button className="primary-button" type="button" disabled={!p.canPlay} onClick={() => void p.play()}>
          Mulai analisis
        </button>
      </form>
    </section>
  )
}
