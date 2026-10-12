# Preview, garis/zona, lalu Play — rencana frontend P1d

## 1. Status, baseline, dan otorisasi

**COMPLETED — frontend verified with API fakes. PASS WITH LIMITATIONS, Claude melalui owner/PM.**
Penulis: Codex. Penyelarasan kontrak: 2026-10-12. Owner mengotorisasi implementasi P1d pada blob
`36022c15460b0167590c4c6ce301ced573ce56d7`, 2026-10-12, dan meminta berhenti
pada awaiting independent review Claude saat handoff implementasi. Penutupan dan
stage/commit/fetch/push tepat scope P1d kini diotorisasi owner pada §23.

Baseline implementasi P1d:
`eb6a3ac9b093a99fd9e151261f984383b5b95c01`
(`feat: add private parking preparation API and runtime`). Staging kosong saat
preflight; `frontend/` dan `backend/` bersih terhadap HEAD. Rencana backend committed:
[private-parking-preview-configuration.md](private-parking-preview-configuration.md),
blob `f294ccb5255f6b368d2bf0bbb4a156d396a18818`, khususnya §19–§19.3.
Baseline `a45c986` dan verdict BLOCKED pada §11–§19 adalah catatan historis;
**§1–§10 merupakan kontrak/rancangan frontend aktif**; bukti implementer P1d dan
batas cakupannya berada pada §20–§22. §22 adalah manifest source/test final; §23 adalah penutupan dan atribusi review. Riwayat §11–§19 bukan status implementasi terkini.

Tujuan: file → preview sebelum analisis → pengaturan garis/sisi/zona → simpan
operator → Mulai analisis. Keputusan owner tetap:

- File video dahulu, hanya pada mode privat; preview webcam/CCTV ditunda.
- Legacy/retail mempertahankan alur immediate-start yang tersedia.
- Template startup harus dikonfirmasi untuk sumber ini; bukan binding otomatis.
- Editor tersedia tanpa template valid; Play memerlukan konfigurasi operator.
- Cancel/hapus lama selesai sebelum penggantian sumber.
- Refusal Play mempertahankan sumber/config; Play baru selalu intent operator.
- Stop mengakhiri analisis; file sumber tetap sampai DELETE atau expiry.
- Sisi dalam left/right terhadap arah P1→P2, tanpa inside_point operator.

Konteks: [AGENTS.md](../../../AGENTS.md), [aturan rencana](../../PLANS.md),
[arsitektur](../../../ARCHITECTURE.md), [frontend](../../FRONTEND.md),
[design](../../DESIGN.md), [reliability](../../RELIABILITY.md),
[security](../../SECURITY.md), [product specs](../../product-specs/index.md),
[F1](../completed/f1-frontend-ui-ux.md), [T3](t3-private-parking-integration.md).
Dokumen bersama dapat memiliki delta penulis lain; kontrak implementasi P1c dibaca
pada commit di atas. Skill frontend-design dipakai untuk arah editor dan acceptance
visual. Tidak menonton media nyata baru. Bukti visual P1d pada §20 memakai API fake,
gambar pola sintetis, dan bundle produksi; bukan hasil review independen.

### Batas deployment

Mutation privat memerlukan **HTTPS atau localhost**, tempat crypto.randomUUID()
tersedia untuk unggah baru, simpan konfigurasi, dan Play baru. Jika API tersebut
tidak tersedia, UI memberi penjelasan dan menahan tindakan tersebut tanpa fallback
operation ID. Stop, DELETE, dan cleanup tidak memerlukan pembuatan UUID dan tetap
tersedia. Replay manual memakai tuple yang sudah dipin, tanpa UUID baru.
Observasi status tetap boleh berjalan. Alur legacy tidak membutuhkan operation ID
privat. Browser fake menggunakan bundle file lokal dalam secure context Chrome,
bukan bukti deployment HTTP jarak jauh.

## 2. Fakta implementasi dan dependensi yang tersisa

- Backend P1a/P1b/P1c committed: prepared store, lease binding, preview, simpan,
  Play, recovery dan status identitas tersedia. Frozen wire contract ada di backend
  §19.3; ini bukan lagi proposal endpoint. Verifikasi backend memakai fake.
- Frontend F1 tetap memakai `videoApi.ts` untuk legacy upload/webcam/Stop; P1d
  menambahkan tipe optional `session_ref`/`session_id` dari P1c untuk join hasil.
  Decoder status legacy tetap seperti baseline; API sources baru memvalidasi model.
- Preview F1 berupa MJPEG ketika RUNNING. Persiapan P1d memakai JPEG endpoint
  sources; tidak memakai `/api/video/stream` sebelum analisis.
- Polling status/evaluasi serial dengan abort, epoch, generation dan render guard
  dipertahankan. Detection/domain hook kini menerima identitas/epoch eksplisit,
  termasuk pergantian RUNNING A→RUNNING B tanpa render IDLE perantara.
- `/current` menyajikan snapshot store; `analysis` dibaca terpisah dan sementara.
  Tidak ada snapshot lintas endpoint atomik atau nomor urut observasi server umum.
- `/current.config` hanya revision/ready. **Tidak ada GET konfigurasi canonical**.
  Setelah reload atau ACK simpan hilang, UI tidak dapat mengambil garis/zona yang
  sudah diterima dari endpoint status. Template tidak menggantikannya.
- Hasil domain/detection/evaluation membawa source_session_id, bukan lease atau
  session_ref. Join hasil bergantung UUID sesi unik produksi; tidak mengklaim aman
  terhadap session_id reusable pada payload lintas endpoint.
- Tidak ada API status peringatan akses lokal, gesture, atau kesiapan speaker.
  Pengaturan area bukan izin akses, identitas, kepemilikan atau bukti pencurian.

## 3. Scope implementasi yang diotorisasi

Daftar berikut adalah batas scope desain yang diotorisasi owner. Subset kandidat
yang benar-benar berubah dicatat beserta blob pada §20. Tidak menambah package.

| Path lengkap relatif root | Status / tujuan |
|---|---|
| `frontend/src/App.tsx` | existing; routing capability, editor dan monitoring, pending intent/epoch |
| `frontend/src/api/videoApi.ts` | existing; additive identity status, kontrak legacy tetap |
| `frontend/src/api/videoApi.test.ts` | existing; parsing status/legacy/Stop |
| `frontend/src/api/preparedSourceApi.ts` | new; adapter endpoint P1c, decoder respons/error aman |
| `frontend/src/api/preparedSourceApi.test.ts` | new; wire models, identitas request, status/category |
| `frontend/src/components/VideoSourceSelector.tsx` | existing; kontrol sumber privat/legacy dan cancel/Stop |
| `frontend/src/components/VideoSourceSelector.test.tsx` | existing; route/mode dan pending |
| `frontend/src/components/VideoPreview.tsx` | existing; JPEG persiapan versus MJPEG monitoring, cleanup URL |
| `frontend/src/components/VideoPreview.test.tsx` | new; image identity, error, URL cleanup |
| `frontend/src/components/PrivateParkingConfiguration.tsx` | new; form garis/sisi/zona, simpan dan dirty |
| `frontend/src/components/PrivateParkingConfiguration.test.tsx` | new; operator ACK, field errors, keyboard |
| `frontend/src/components/ConfigurationOverlay.tsx` | new; SVG contain, dua titik/sudut, arah/arsiran |
| `frontend/src/components/ConfigurationOverlay.test.tsx` | new; pointer/keyboard/resize |
| `frontend/src/hooks/useSourcePreparation.ts` | new; serial current/lookup, intents, recovery dan stale guard |
| `frontend/src/hooks/useSourcePreparation.test.tsx` | new; deferred response, unknown/cancel/cleanup |
| `frontend/src/geometry/privateParkingGeometry.ts` | new; pemetaan koordinat dan validasi |
| `frontend/src/geometry/privateParkingGeometry.test.ts` | new; bounds, round-trip, left/right |
| `frontend/src/hooks/useVideoStatus.ts` | existing; identitas additive dalam polling serial |
| `frontend/src/hooks/useVideoStatus.test.tsx` | new; identity/epoch dan stale status |
| `frontend/src/hooks/useCurrentEvaluation.ts` | existing; ikat ke sesi yang dikonfirmasi status |
| `frontend/src/hooks/useCurrentEvaluation.test.tsx` | existing; guard dan regresi evaluasi |
| `frontend/src/hooks/useLatestDetection.ts` | existing; epoch/session pin, reset render |
| `frontend/src/hooks/useLatestDetection.test.tsx` | new; hasil A tidak tampil pada B |
| `frontend/src/hooks/useCurrentDomain.ts` | existing; epoch/session pin, reset render |
| `frontend/src/hooks/useCurrentDomain.test.tsx` | new; stale domain dan UNKNOWN |
| `frontend/src/App.test.tsx` | existing; komposisi fake alur privat/legacy |
| `frontend/src/App.status.test.tsx` | existing; capability/status/refusal |
| `frontend/src/App.lifecycle.test.tsx` | existing; cancel, Play unknown, EOF/Stop/switch |
| `frontend/src/App.layout.test.tsx` | existing; urutan DOM, form dan responsif |
| `frontend/src/testUtils/controlledFetch.ts` | existing; gate/deferred fetch dan abort diabaikan |
| `frontend/src/testUtils/appFetchStub.ts` | existing; fake P1c typed, tanpa hasil AI sintetis |
| `frontend/src/index.css` | existing; editor, overlay, focus dan responsive |
| `docs/exec-plans/active/private-parking-configuration-ui.md` | rencana dan bukti/atribusi |

Backend, AGENTS.md, PLAN.md, dokumen bersama, referensi root, package/lockfile,
gesture/audio/prompt/inference, persistensi lintas restart, multi-kamera, ROI retail,
preview seek/pause dan webcam/CCTV preview berada di luar scope.

## 4. Layout dan pemeriksaan terhadap brief

Pertahankan arah F1: canvas `#07111f`, surface `#101f34`, ink `#edf5ff`, action
`#4c91f2`, attention `#f3c969`, error `#ff7f8b`; font Segoe UI/system lokal.
Preview adalah ruang kerja utama, bukan kumpulan kartu metrik. Desktop ≥1024 px:
sekitar dua pertiga lebar untuk gambar, form ringkas di kanan. Mobile: gambar →
kontrol file → editor → Mulai analisis. Form delapan zona boleh panjang tanpa
menghilangkan akses numeric atau tombol utama. Teks rata kiri, sentence case.

```text
Pilih file → Atur area → Mulai analisis
┌──────────────────────────────┬─────────────────────────┐
│ Preview saja                 │ Garis masuk: P1 dan P2  │
│ gambar + garis/zona draft    │ Bagian dalam kiri/kanan │
│ arah P1→P2 dan arsiran       │ Area motor 1 ...        │
├──────────────────────────────┤ Atur dengan angka       │
│ File          Hapus sumber   │ Simpan pengaturan       │
└──────────────────────────────┴─────────────────────────┘
                                  Mulai analisis
```

Template: “Contoh pengaturan — periksa untuk file ini”; pengguna mengedit atau
menggunakan lalu menyimpan untuk sumber current. Startup missing/invalid menampilkan
editor kosong. Saat monitoring, pakai layout F1 dan panel “Penilaian AI”; teks statis
membedakannya dari peringatan akses lokal. Tidak ada indikator lokal/gesture aktif.

## 5. Kontrak API yang dibekukan pada P1c

Base sources: `/api/video/sources`. Tabel ini adalah kebutuhan adapter frontend;
model/status/category otoritatif tetap backend §19–§19.3 dan source committed.

| Operasi | Request dan hasil yang dipakai UI |
|---|---|
| `GET /capabilities` | mode legacy/private/disabled, preparation_available, template_available, evaluation_enabled, source_kinds, limits |
| `POST /api/video/sources` | UUID operation_id untuk intent upload; 201 source_id/generation/state; replay ID yang sama hanya untuk intent yang sama, tidak otomatis |
| `PUT /{id}/file?generation=g` | multipart file; 200 PREPARED, frame_width/height, preview_revision |
| `GET /current` | 200 snapshot store termasuk upload_operation_id/config/cleanup/last_attempt; 204 jika tidak ada slot/obligation |
| `GET /{id}/preview?generation=g` | JPEG ukuran decode asli; **tanpa parameter preview_revision**; revisi tetap bagian identitas lokal gambar |
| `GET /{id}/template?generation=g` | 200 line/inside/zones/dimensi/confirmed:false atau 204; 204 tidak reset TTL |
| `PUT /{id}/configuration` | generation, expected_revision (null pada konfigurasi pertama), frame_width/height, line, inside, zones; 200 revision/ready/canonical |
| `DELETE /{id}?generation=g` | 202 DELETING atau 204; 202 menahan penggantian sumber |
| `POST /{id}/play` | generation, config_revision, attempt=current.next_attempt, operation_id UUID; 200 attempt record sesudah CAS, termasuk REFUSED/FAILED |
| `GET /{id}/play/{attempt}` | lookup record intent yang sama; path ini **tidak memuat generation query**; respons harus cocok dengan operation_id/attempt yang dipin |
| `POST /cleanup/retry` | tanpa source ID/body; 202 counts pending/stalled/attempted atau 204; recovery operator satu percobaan per obligation stalled |
| `GET /api/video/status` | state/source/error serta session_ref/session_id additive, satu snapshot lock |
| `POST /api/video/stop` | aksi Stop manual, termasuk pemulihan ERROR; ACK tidak membuktikan cleanup selesai |

ACK upload/config/template tidak membawa source_id/generation. Penerimaan memakai
closure request `(epoch, source_id, generation, draft revision)`; jangan mewajibkan
field yang tidak ada, atau mengarang identitas dari nama file. Record Play memuat
attempt, operation_id, outcome, reason, session_ref/session_id dan
session `{phase, end_reason, cleanup_ok}`. Outcome final berbeda dari phase cleanup.

Operation ID upload dan Play dibuat dengan **`crypto.randomUUID()`**, memakai
string UUID kanonik lowercase dengan tanda hubung yang dipin sejak intent dibuat.
Pencocokan `/current.upload_operation_id` dan record Play memakai ID kanonik itu
secara tepat, bersama epoch/tuple intent; tidak memakai nama file, Date.now(), UUID
tanpa tanda hubung, atau bentuk uppercase yang berbeda dari normalisasi backend.
Jika crypto.randomUUID tidak tersedia, tahan mutation dan tampilkan ketidaktersediaan
fitur; jangan membuat fallback ID. Intent baru hanya dari aksi operator yang baru;
rekonsiliasi tidak mengganti ID.

Typed decoder memvalidasi struktur, enum, angka finite, ID/tuple yang memang tersedia.
Error publik hanya allowlist category/field; tidak merender body, exception, path,
nama upload atau nilai konfigurasi mentah. Category tak dikenal/2xx rusak menjadi
pesan respons tidak dapat dibaca, terpisah dari kegagalan koneksi.

| HTTP / category | Respons operator dan lifecycle |
|---|---|
| 404 unknown_source/unknown_attempt | Periksa current; bukan bukti intent tidak pernah berjalan |
| 409 generation_mismatch/revision_conflict/revision_stale | Pertahankan draft bertanda belum diterima; rekonsiliasi identitas/revisi sebelum aksi manual |
| 409 capture_busy | Preview dipakai proses lain; tombol muat ulang gambar eksplisit, tanpa loop retry |
| 409 play_in_progress/session_cleanup_pending | Rekonsiliasi; tahan intent Play/penggantian baru |
| 409 operation_reused/attempt_conflict/attempt_superseded/attempt_limit | Tidak membuat UUID atau retry Play otomatis; jelaskan konflik/batas |
| 409 frame_size_mismatch | Verifikasi kembali metadata gambar; jangan rescale diam-diam atau menyimpulkan generation sudah sticky tanpa current.geometry_unstable |
| 409 source_geometry_unstable | Generation tidak stabil harus dihapus/upload baru setelah cleanup; resave/Stop tidak memulihkannya |
| 409 private_mode_inactive/private_flow_required | Muat capability fresh; tidak fallback legacy |
| 410 source_gone/outcome_evicted | Akhiri lookup yang sama; jangan menyatakan cleanup sukses atau membuat Play baru dari hilangnya record |
| 413 configuration_too_large | “Pengaturan terlalu besar”; draft tetap, tidak dianggap diterima |
| 422 invalid_request/invalid_operation_id/invalid_geometry/missing_file/unsupported_type/too_large/not_decodable | Pesan kategori/field aman; koreksi draft atau unggah ulang setelah cleanup, sesuai state server |
| 500 write_failed/preview_failed | “File/preview belum dapat diproses”; rekonsiliasi tanpa auto retry mutation |
| 503 cleanup_pending (+source_id/generation) | Hanya terapkan identitas error untuk intent yang cocok; tahan aksi baru sampai cleanup selesai |
| 503 storage_unavailable/closed | Tidak fallback; status unavailable, aksi start baru ditahan |

Sembilan kategori HTTP **409** berikut juga bagian kontrak beku, bukan respons
rusak. Pesan, aksi operator, dan efeknya eksplisit; tidak ada retry mutation atau
Stop otomatis. Pembacaan current/lookup serial adalah rekonsiliasi, bukan retry Play.

| Category | Pesan operator | Tindakan yang tersedia | Efek lifecycle |
|---|---|---|---|
| slot_busy | “Masih ada sumber yang digunakan aplikasi.” | Periksa current; Hapus sumber lama hanya setelah identitasnya terkonfirmasi dan state mengizinkan. Jika analisis berjalan, Stop tersedia sebagai aksi manual. | Create ditolak; jangan membuat intent upload baru otomatis atau menarget sumber B dari cancel A. Sumber lama tetap sampai cleanup terbukti selesai. |
| upload_not_expected | “File tidak dapat diunggah pada tahap ini.” | Periksa current; gunakan preview/editor hanya bila sumber yang cocok sudah PREPARED. Untuk mengganti, Hapus sumber secara manual lalu tunggu cleanup. | Jangan mengulang PUT atau menimpa sumber; ikuti state yang sudah ada, bukan kembali ke AWAITING_FILE secara optimistis. |
| cancelled | “Pengunggahan dibatalkan.” | Pantau cleanup; Hapus/Coba bersihkan lagi hanya jika obligation/state mengizinkan. Pilih file lagi secara manual setelah slot kosong terkonfirmasi. | Operasi lama tidak menjadi PREPARED; buang ACK/draft aktifnya, pertahankan identitas cleanup. Cancel bukan bukti file sudah dihapus. |
| deadline_exceeded | “Batas waktu pengunggahan terlewati.” | Periksa cleanup; pemulihan eksplisit bila stalled, lalu unggah ulang manual setelah admission terbuka. | Writer lama tidak dipromosikan menjadi PREPARED. Deadline bukan cleanup sukses; Play/penggantian tetap tertahan selama resource lama dipakai. |
| expired | “Sumber ini sudah kedaluwarsa.” | Periksa cleanup; pilih file lagi setelah penghapusan terkonfirmasi, dengan konfigurasi operator baru. | ACK/config/preview generation lama tidak lagi berlaku; jangan refresh atau Play generation lama sebagai pemulihan. Expiry tidak langsung membuka admission. |
| not_prepared | “File belum siap untuk diatur.” | Periksa status intent unggahan; Cancel/Hapus sumber secara manual jika ingin membatalkan dan state mengizinkan. | Preview/editor/simpan/Play menunggu PREPARED yang cocok. Jangan menganggap refusal berarti EMPTY atau memulai upload kedua. |
| play_pending | “Analisis sedang dimulai.” | Baca outcome intent yang sama/current; Cancel lewat Hapus sumber secara manual bila masih PLAY_PENDING dan diizinkan backend. | Simpan/edit dibekukan; jangan membuat Play baru, mengulang POST, atau menganggap start sudah RUNNING. Cleanup tetap harus selesai setelah pembatalan. |
| playing | “Analisis masih berjalan.” | Hentikan analisis secara manual; setelah cleanup/current fresh, atur atau hapus sumber sesuai tujuan operator. | Save/DELETE yang ditolak tidak mengubah konfigurasi atau menghapus sumber. Editor tetap terkunci; tidak menjalankan Stop sebagai respons otomatis terhadap error. |
| config_not_ready | “Simpan pengaturan yang valid sebelum mulai analisis.” | Periksa current dan geometri; edit/simpan manual bila sumber PREPARED dan tidak cleanup/unstable. | Play ditolak sebelum CAS, attempt tidak dipakai. Template/dirty/ACK unknown tidak menjadi konfigurasi diterima; jangan menjalankan Play lagi otomatis. |

`write_finished` tidak masuk pemetaan kategori HTTP: itu penjaga internal akhir
writer pada store, bukan kategori kontrak publik P1c. Semua kategori publik pada
tabel §5 dipetakan ke pesan/action/lifecycle; hanya kategori **di luar kontrak**
mendapat pesan respons tidak dapat dibaca. Struktur 2xx/body yang malformed tetap
kesalahan decoding tersendiri, bukan alasan menggolongkan kategori publik sebagai rusak.

`cleanup_pending` pada create adalah 409. Refusal Play sebelum CAS 409/410/503
tidak memakai attempt; Play sesudah CAS 200 REFUSED/FAILED memakai attempt.
Envelope configuration **seluruh JSON UTF-8 ≤4096 bytes**, diukur TextEncoder,
bukan string.length; input dalam batas tetapi invalid menghasilkan 422.
Envelope harus memiliki **tepat tujuh key**, sesuai `_CONFIG_KEYS` backend:
`generation`, `expected_revision`, `frame_width`, `frame_height`, `line`, `inside`,
`zones`. Tidak mengirim operation_id, source_id, preview_revision, confirmed,
inside_point, thresholds, atau field tambahan di envelope. Key hilang/tambahan
adalah `422 invalid_request`; expected_revision tetap dikirim meski bernilai null.
Limits upload/bentuk geometri berasal capabilities; default PM/retensi bukan SLA.

**503 cleanup_pending dibedakan menurut operasi asal**, bukan pesan generik atau
menebak dari state sesaat setelah error:

- Pada PUT file/upload-probe: release gagal membuat upload lama dibatalkan/voided.
  Pesan “Pembersihan unggahan belum selesai. Unggah ulang setelah pembersihan.”
  Tunggu release dan penghapusan file, gunakan recovery manual bila stalled;
  setelah admission terbuka operator membuat sumber/unggahan baru dan mengatur
  ulang. **Jangan menunggu upload gagal menjadi PREPARED** atau meneruskan preview/
  save/Play pada generation itu; pertahankan tuple cleanup hingga selesai.
- Pada GET preview: release gagal masih mempertahankan sumber, kecuali ada
  DELETE/expiry/shutdown lain. Pesan “Pembersihan preview belum selesai.” Tahan
  preview baru/config/Play; setelah release berhasil dan current fresh menyatakan
  sumber yang sama masih valid, operator dapat melanjutkan atau memuat gambar
  secara eksplisit. Tidak mewajibkan unggah ulang hanya karena cleanup preview.
- Error 503 pada config/Play dapat merefleksikan capture cleanup yang sudah pending.
  Pertahankan konteks asal yang diketahui dan identitas error; jika asal tidak
  diketahui, rekonsiliasi current tanpa mengarang status retained/voided. Tidak ada
  Stop otomatis, PUT ulang, atau Play baru otomatis pada ketiga jalur ini.

## 6. Geometri dan akses operator

Gambar pertama decodable adalah acuan persiapan; analisis file dimulai dari awal,
tanpa seek/crop/rotate editor. Payload normal [0,1] pada ukuran frame backend W,H.
JPEG naturalWidth/Height harus cocok; mismatch menahan editor/simpan/Play dan meminta
pemeriksaan sumber. Tidak menggambar sebelum image load dan metadata yang cocok.

Untuk content box Cw×Ch, origin viewport L,T, object-position center:
`s=min(Cw/W,Ch/H)`, `Dw=sW`, `Dh=sH`, `ox=(Cw-Dw)/2`, `oy=(Ch-Dh)/2`.
Pointer → `u=(clientX-L-ox)/Dw`, `v=(clientY-T-oy)/Dh`.
Overlay → `x=ox+uDw`, `y=oy+vDh`. Border/padding tidak termasuk content image;
CSS pixels, bukan DPR. ResizeObserver/scroll memperbarui mapping, tidak payload.

- Klik letterbox diabaikan, bukan dijadikan titik tepi. Drag pegangan dapat dibatasi
  gambar; pointercancel/Escape mengembalikan transaksi edit sebelumnya.
- Garis P1→P2, side radio left/right, arah dan arsiran dipotong ke gambar. Pada x
  kanan/y bawah, cross product raw_side <0 left, >0 right. Membalik endpoint atau
  mengubah garis meminta konfirmasi sisi lagi. Tidak mengirim inside_point.
- Rectangle sejajar sumbu; dua sudut dinormalisasi; nama internal zona-1 dst., unik.
  Numeric persen/piksel gambar asli menyediakan alur lengkap tanpa drag; pertahankan
  presisi payload sampai operator mengedit. Tidak menambah ROI polygon/threshold.
- Validasi sesuai committed model/capabilities: finite [0,1], line ≥0.05, 1–8 zona,
  luas ≥0.005, x1<x2/y1<y2, nama `[a-z0-9_-]{1,32}`, dimensi integer 1–16384.
  Overlap zona tidak dilarang validator; bukan izin motor dan tidak disatukan otomatis.

Keyboard: Tab melalui field/radio/tombol, panah satu piksel asli dan Shift+panah
sepuluh pada pegangan; label P1/P2 dan sudut zona, focus-visible, fieldset/legend,
aria-describedby error, fokus ringkasan ke field invalid pertama. Status simpan
menggunakan live region tanpa membacakan setiap pointer move/poll. Target kontrol
≥44 px, reduced motion, zoom 200%, kontras dan penanda selain warna.

## 7. Lifecycle, identitas, dan rekonsiliasi

### Routing dan polling

Capability fresh menentukan routing; bukan domain atau evaluation_enabled. Private
memakai persiapan saja, tidak menawarkan upload legacy/webcam. Legacy mempertahankan
F1/retail. Disabled, capability malformed/unavailable, private store unavailable
menahan start baru tanpa fallback; observasi status/Stop untuk cleanup tetap tersedia.
Polling current/status/lookup serial, setTimeout setelah request selesai, abort dan
generation/render guard. Interval current usulan frontend 1000 ms; status/evaluasi
1000, detection 500, domain 750 dipertahankan. Tidak polling preview/template sebagai
keepalive. Error status bukan EMPTY/IDLE. Mode berubah/restart menginvalidasi epoch.

### Cancel upload dan penggantian

Buat operation_id kanonik dengan crypto.randomUUID() sebelum POST dan pin
intent/epoch. Cancel segera menutup preview,
draft aktif dan hasil, tetapi menyimpan tuple cleanup. Abort fetch bukan rollback:
backend unit upload yang sudah berjalan tetap dapat selesai. Jika id belum diterima,
rekonsiliasi current.upload_operation_id; hanya ID intent yang sama boleh menjadi
sasaran DELETE. Late ACK menuju cleanup, tidak melanjutkan PUT. Current B bukan A.
204 saat POST A belum pasti bukan bukti cancel atau bukti create tidak pernah
berjalan. Fetch rejected/settled, AbortError, dan abort yang selesai **tidak**
melepas intent tanpa source ID, baik cancel maupun ACK create hilang tanpa cancel.
Upload B tetap ditahan selama jendela rekonsiliasi **30 detik**, dan tetap ditahan
sesudah jendela itu sampai operator memilih tindakan manual. Tidak mengulang
create/PUT otomatis. Setelah 30 detik tampil Periksa operasi lagi dan **Lepaskan
penantian lokal**, bukan upload B otomatis. Pelepasan hanya mereset penantian UI;
**tidak membatalkan pekerjaan backend** atau membuktikan cleanup sudah berhasil.
Pelepasan menginvalidasi executor/epoch/intent A. ACK A terlambat sesudah reset atau
unmount tidak PUT, tidak melepas executor B, dan tidak memperbarui state B.
Jika A kemudian terlihat pada current, UI memakai source_id/generation/operation_id
asli dari backend dan menyebut sumber ditemukan kembali, tanpa menganggapnya hasil
upload B, mengarang konfigurasi, atau mengirim DELETE otomatis. Setelah id diketahui, satu DELETE dan serial current hingga
obligation A hilang. 202/timeout/expiry bukan bukti file terhapus. Sumber lain tidak
menjadi target DELETE dari cancel A. Unmount membatalkan polling lokal, bukan klaim
server bersih; obligation/TTL tetap milik backend.

### Simpan dan template

Snapshot draft dan expected_revision; edit dibekukan selama save. Hanya ACK valid
milik closure current dengan canonical/dimensi valid membentuk accepted configuration.
Sesudahnya current.config revision/ready harus cocok, draft tidak dirty, tidak ada
geometry instability/cleanup pending atau mutation unknown sebelum UI menawarkan
Play. Template 200 belum confirmed; 204 tidak keepalive dan tidak mematikan editor.
Setiap edit menggugurkan kelayakan Play hingga save berikutnya.

ACK save hilang/2xx rusak: current.ready atau revision yang naik **tidak membuktikan
geometri draft kita diterima** (tab lain dapat menulis). Tahan Play, baca current,
pertahankan draft bertanda belum diterima; operator boleh menyimpan ulang secara
manual dengan expected_revision fresh. Tidak melakukan PUT otomatis. Setelah reload,
config canonical tidak tersedia: preview sumber retained boleh diperiksa, tetapi
accepted geometry tidak direkonstruksi dari template atau angka status. Operator
mengatur/menyimpan lagi sebelum Play dari editor baru. Kebijakan ini diajukan untuk
review Claude; tidak memerlukan endpoint baru atau mengubah config backend diam-diam.

### Play unknown, outcome, EOF dan Stop

Pin `(source_id, generation, config_revision, attempt, operation_id)` sekali untuk
intent. Tidak ada RUNNING optimistis. ACK hilang/malformed/timeout → status unknown,
serial lookup **intent yang sama** dan current/status; tidak mengirim POST Play ulang
secara otomatis, termasuk dengan ID sama. Replay manual ID/attempt sama dibolehkan
API tetapi tidak menjadi langkah otomatis UI. Outcome PENDING tetap unknown.
REFUSED/FAILED/TIMED_OUT/CANCELLED adalah final, bukan request ulang; STARTED adalah
riwayat start, bukan bukti masih RUNNING. Retention 8 dan bindings cap 256 sesuai PM;
outcome_evicted/source_gone menghentikan lookup itu, tetap rekonsiliasi ownership/
cleanup melalui current/status. Tidak memakai UUID baru untuk melewati eviction.
Jika keadaan masih unknown, aksi baru tetap ditahan; intent berikutnya manual
setelah readiness dan completion intent lama cukup dibuktikan oleh kontrak.
Refusal tanpa reservasi (phase NONE dan identitas sesi null) tidak menunggu hook
cleanup sesi yang tidak pernah ada; current tetap harus fresh. Phase ENDING atau
cleanup_ok false/null pada sesi yang sudah terbentuk tetap menahan aksi terkait.

`analysis` pada current adalah observasi sementara, **bukan penentu config.ready**.
stop_required menampilkan aksi Stop manual. ERROR sampai Stop tidak dipulihkan
otomatis; manual Play yang ditolak video_busy dapat menghasilkan 200 REFUSED dan
memakai attempt. Stop ACK/IDLE saja tidak membuka Play: current/source/revisi/cleanup
serta ACK geometri lokal tetap perlu cocok. EOF/error segera melepas stream dan
menahan respons STARTED lama agar tidak menghidupkan RUNNING lagi. Stop tidak DELETE
sumber; operasi penghapusan terpisah dan jelas bagi operator.

### Cleanup, expiry dan geometri tidak stabil

CLEANUP_PENDING/STALLED dapat tanpa source_id (leftover startup). Tidak membuat ID
DELETE palsu: sediakan “Coba bersihkan lagi” melalui POST cleanup/retry yang nyata,
satu aksi operator, kemudian current. Counts/204 recovery tidak memulihkan readiness
sendiri; periksa current/capability. Dengan source ID, DELETE mencoba sekali untuk
obligation file stalled; release capture stalled dipulihkan endpoint recovery. Tidak
loop retry, tidak memulai B sebelum seluruh obligation selesai. Deadline server
menginvalidasi operasi, tidak membuktikan cleanup; countdown hanya informasi.

Idle TTL tidak reset oleh polling; preview/template tersedia dan accepted mutations
mengikuti kontrak reset. Template 204 tidak reset. Geometry_unstable akibat frame
size mismatch sticky untuk generation, sekalipun Stop membuat video IDLE. Resave/
rescale tidak memperbaikinya; tunggu cleanup lalu delete→upload baru→save operator.
Same-size pan/crop tidak terdeteksi backend; bukan alasan mengklaim konfigurasi aman.

### Batas snapshot dan hasil monitoring

Pin session_ref/session_id dari status yang sah; hasil hanya tampil jika source_session_id
cocok dan epoch/request tetap current. Status A terlambat, mode berbeda, non-RUNNING,
atau identitas kosong membuang hasil. Satu poll in-flight per endpoint; invalidasi
sebelum abort. Current/analysis/status/outcome tidak atomik bersama: observasi konflik
menahan aksi, diikuti pembacaan fresh, tanpa menyusun tuple dari A dan B. Lease tidak
masuk API/UI. Session_id unik produksi bukan dukungan payload reusable. Identitas
menjamin batas itu saja; tidak mengklaim global ordering server yang belum ada.

## 8. Bahasa dan kategori error operator

Alur tetap dapat diselesaikan tanpa drag. Gunakan “Preview saja — analisis belum
dimulai”, “Simpan pengaturan”, “Mulai analisis”, “Hentikan analisis”, “Hapus sumber”.
Unknown: “Status belum dapat dipastikan. Sedang memeriksa status.” Cleanup stalled:
“Pembersihan tertahan” dengan aksi recovery eksplisit yang sesuai identitas.
Field error: “Garis terlalu pendek”, “Pilih bagian dalam terhadap arah garis”,
“Area motor terlalu kecil”, “Tambahkan setidaknya satu area motor”. Pertahankan draft
valid ketika save ditolak; jangan menyebut NORMAL sebagai aman/pelaku/izin sah.

Fetch failure, malformed 2xx, category/refusal dan proses cleanup dibedakan. Tidak
menampilkan token/lease/UUID/path/body mentah atau mengirim JPEG/konfigurasi ke log,
localStorage, telemetry atau prompt. Blob JPEG hanya di memori; revoke object URL
pada replace/error/unmount, pointer listener/observer/timer dibersihkan. Error JPEG
bukan retry loop; URL baru hanya aksi muat ulang eksplisit atau identitas sumber baru.

## 9. Acceptance dan verifikasi setelah implementasi diotorisasi

Tabel ini adalah **target acceptance**, bukan klaim bahwa seluruh variasinya diuji.
Hasil dan batas yang benar-benar dijalankan dicatat pada §20. Fake fetch/deferred gates,
clock/timer; tidak ada server/provider/inference/perangkat atau hasil AI simulasi.

| ID | Bukti yang diwajibkan |
|---|---|
| P1d-01 | capabilities private/legacy/disabled/unavailable/malformed; hanya route sesuai mode; private tanpa template tetap editor; tidak ada fallback atau start webcam privat |
| P1d-02 | App nyata + adapter/hook/editor: create→PUT file→preview→save valid ACK→Play; sebelum Play nol legacy start dan nol klaim evaluasi/gesture/audio aktif |
| P1d-03 | response validation semua model/status/category; parameterized test seluruh kategori HTTP publik pada §5, termasuk sembilan kategori 409, menghasilkan pesan/aksi/lifecycle yang ditetapkan, bukan respons rusak; hanya kategori di luar kontrak mendapat pesan respons tidak dapat dibaca; write_finished tidak masuk allowlist publik. Upload/config/template ACK memakai closure; 503 identity A tidak diterapkan ke B; raw body/marker tidak tampil; tidak ada mutation retry/Stop otomatis |
| P1d-04 | contain landscape/portrait/square, letterbox kedua sumbu, border/padding, scroll/resize/zoom/DPR, round-trip, zero/mismatched dimension; klik letterbox tidak mengubah draft |
| P1d-05 | arah P1→P2 left/right horizontal/vertikal/diagonal/reverse; konfirmasi ulang; reverse corners, min length/area, 1/8/9 zona, finite/bounds/names; envelope UTF-8 4096/4097 bytes; payload tepat tujuh key _CONFIG_KEYS, expected_revision null tetap hadir; key hilang/tambahan ditolak, tanpa operation_id/confirmed/inside_point/thresholds tambahan |
| P1d-06 | keyboard/numeric lengkap tanpa drag; panah/Shift/Escape/pointercancel, radio, add/edit/delete zona, fokus error, aria labels dan reduced motion |
| P1d-07 | template belum ACK, dirty membatalkan Play; lost save ACK + current ready dari tab lain tidak membuka Play; manual save fresh + canonical ACK baru; reload tidak mengarang saved geometry |
| P1d-08 | create ACK di-gate; cancel sebelum source ID termasuk fetch langsung reject saat abort; current204/fetch settled/AbortError tidak melepas intent; ACK hilang tanpa cancel tetap unknown; 30s hanya membuka aksi Lepaskan penantian lokal (bukan POST B); reset manual menginvalidasi executor/epoch A; B→ACK A terlambat tidak PUT A/ubah B; A pada current ditemukan kembali dengan identitas asli, bukan hasil B dan tidak DELETE otomatis; matching operation saja boleh menjadi target cancel; zero auto POST ulang |
| P1d-09 | cancel saat PUT masih berjalan lalu late PREPARED; DELETE202/cleanup pending tetap menahan B; backend dapat selesai setelah fetch abort, UI tidak menampilkan sumber cancelled kembali |
| P1d-10 | save/Play/result/error/finally A digate sampai B; request epoch/render guard mencegah perubahan B dan hasil A, termasuk RUNNING→RUNNING dan Stop→Start satu batch |
| P1d-11 | Play ACK hilang, malformed atau timeout: lookup intent sama; no POST retry/new UUID; immediate EOF/ERROR/ENDED tidak ditimpa STARTED late |
| P1d-12 | pre-CAS 409/410/503 tidak diasumsikan memakai attempt; post-CAS 200 REFUSED/FAILED final dan memakai attempt; eviction/cap256/conflict tidak memicu Play baru otomatis |
| P1d-13 | Stop tidak DELETE source/config; ERROR sampai Stop menawarkan pemulihan manual; Stop ACK belum membuka Play; current.analysis bukan config readiness/tuple atomik |
| P1d-14 | expiry/deadline dan TTL reset: polling bukan keepalive, template204 tanpa reset; unknown/pending/stalled menahan Play/B, tidak menjadikan timeout cleanup success |
| P1d-15 | leftover tanpa ID→cleanup/retry; source stalled→DELETE/recovery sesuai obligation; counts/204 diverifikasi current; tidak ada empty-ID DELETE atau retry loop |
| P1d-16 | frame_size_changed sticky walau video IDLE setelah Stop; no rescale/resave/Play generation lama; manual cleanup/delete/new upload/confirm; same-size scene change batas, bukan fake deteksi |
| P1d-17 | result join session_ref/status/session_id + source_session_id; wrong/missing/stale ID dibuang; snapshot lintas endpoint tidak diperlakukan atomik; reusable result ID tidak diklaim aman |
| P1d-18 | preview decode/imageerror/503capturebusy tanpa auto retry atau start; EOF/Stop/unmount/source change membersihkan URL/listener/observer/fetch/timers; afterEach tidak meninggalkan pekerjaan |
| P1d-19 | F1 legacy upload/webcam/409/503/streamerror, domain UNKNOWN/retail, polling 1000/1000/500/750 dan disabled cloud tetap; tidak mengubah panel AI menjadi indikator lokal |
| P1d-20 | browser fake dan review visual pada matriks di bawah; catat siapa menjalankan, viewport, batas audit, tanpa klaim video/perangkat nyata |
| P1d-21 | mock crypto.randomUUID memberi UUID kanonik; ID yang sama dipin pada POST/cancel-current/lookup intent, tidak diubah format atau diganti setelah ACK hilang; unavailable crypto menahan mutation. Operation ID B tidak cocok dengan intent A; intent manual berikutnya mendapat UUID baru |
| P1d-22 | PUT file mendapat 503 cleanup_pending karena probe release gagal: nol preview/config/Play generation lama, tidak menunggu PREPARED; release/file delete selesai lalu create+upload baru hanya setelah intent operator; fixture GET preview 503 mempertahankan source/config sampai release sukses, tanpa memaksa upload baru atau retry otomatis; expiry/cancel bersamaan tetap menginvalidasi source lama |

### Pemeriksaan visual yang direncanakan

Enam viewport: **1440/1280/1024/768/390/320 ×900**; tambahan zoom200% dan form8zona.
Preview/editor menggunakan gambar buatan sederhana non-CCTV hanya di harness fake,
tanpa hasil AI yang menyerupai runtime, tanpa aset media disimpan ke repo/report Git.
Periksa landscape/portrait/square contain, overlay tepi/direction/arsiran, long errors,
unknown/cleanup/geometry mismatch dan retained source; bukan hanya empty state.

- Desktop preview tetap dominan; mobile urutan DOM/fokus jelas tanpa horizontal
  overflow/crop/tombol terpotong. Target≥44px, focus terlihat, keyboard-only flow.
- Sampel kontras teks/fokus/garis terhadap surface dan latar gambar terang/gelap;
  halo/label/arsiran memastikan arti tidak hanya warna. Reduced motion diperiksa.
- Bukti screenshot/kontras/fokus implementer dilabeli terpisah dari review Claude.
  Audit screen reader tidak diklaim kecuali benar-benar dijalankan. Browser fake
  bukan bukti input video nyata, akurasi deteksi, audio, izin akses atau latensi.

Verifikasi serial dari `frontend/` setelah implementasi diotorisasi: unit geometry
+ adapter, hooks/integration deferred-fetch, `npm test`, `npm run build`; lalu
`git diff --check` dan newline/whitespace file baru. Harness browser fake F1 boleh
dipakai bila tetap file/build lokal dan fetch fake, tanpa server atau akses network.
Tidak ada test/build/browser aplikasi pada delta rencana ini. Backend suite tidak
perlu diulang untuk dokumentasi frontend; hasil P1c tetap bukti fase backend.

## 10. Delta untuk review Claude, keputusan, dan batas

Delta terhadap rencana lama blob `2a0f2ac4167821c1f31b7ae3603059ef82be5885`:

1. Catat commit P1c lengkap dan frozen plan blob; ubah kemampuan API dari proposal
   menjadi implemented dengan fake, sementara frontend P1d tetap belum implemented.
2. Pisahkan adapter sources baru dari videoApi legacy, tambahkan scope exact path
   untuk test preview/status/hooks serta preserve F1. Tidak mengubah backend/docs bersama.
3. Selaraskan wire identity/body/status termasuk post-CAS200, cleanup/retry tanpa ID,
   preview tanpa revision query, template204 tanpa TTL reset, geometry sticky.
4. Tambahkan batas lost-save/reload: tidak ada canonical GET, jadi ready/revision
   saja tidak menjadi ACK geometri lokal; manual resave harus menerima ACK baru.
5. Tambahkan acceptance lifecycle/error dan matriks visual lengkap dengan atribusi.

**Handoff:** delta siap diserahkan melalui owner untuk review independen Claude.
Belum ada verdict Claude atas versi ini. Tiga temuan desain UI lama di §18 tetap
catatan historis, bukan pengganti review delta sekarang. Implementasi UI menunggu
review tersebut dan penugasan owner; tidak meminta ulang keputusan produk yang
sudah diambil. Tidak ada stage/commit/push dalam penugasan ini.

### Delta setelah review Claude terhadap blob c7325f8

Owner meneruskan temuan Claude; revisi ini menutup kelengkapan sembilan kategori
HTTP 409, UUID kanonik dari crypto.randomUUID, tepat tujuh key envelope config,
serta perbedaan cleanup upload-probe versus preview. §5 dan acceptance P1d-03/05/
21/22 diselaraskan dengan source committed `eb6a3ac`. Tidak memasukkan write_finished
sebagai kategori publik. Hash penuh revisi disampaikan pada handoff; review ulang
Claude terhadap delta ini masih menunggu, implementasi UI belum dimulai.

Hal yang perlu Claude periksa: kesesuaian manual save ulang/reload dengan endpoint
canonical yang tersedia, scope hook status/result tanpa reusable-ID overclaim,
serta gate cleanup/unknown sebelum intent baru. Bila membutuhkan endpoint baru,
catat gap untuk owner dan jangan memperluas source/backend secara otomatis.

Batas tetap: geometri bukan kalibrasi dunia nyata; same-size pan/crop tidak
terdeteksi; gesture/kesiapan audio tidak tersedia di API; kuota, latency dan kualitas
model/video nyata tidak diverifikasi. Timeout/retensi mengikuti keputusan PM pada
backend §0.1, bukan SLA. Tidak menyatakan seluruh UI/UX atau T3 selesai.

## Riwayat desain/review sebelum kontrak P1c committed

§11–§19 di bawah dipertahankan sebagai riwayat bertanggal. Status BLOCKED/proposal
atau otorisasi fase lama tidak menjadi status aktif P1d; rancangan sekarang §1–§10.

## 11. Review historis draft awal P1 backend — Codex, 2026-10-11

Blob rencana Claude yang diperiksa:
`2fc0a08ed41756d5558e8a4003d2f0286e49562f`, baseline source `a45c986`.
**Verdict versi awal: BLOCKED untuk implementasi/alur frontend penuh.**
Keputusan terbuka dan usulan frontend di bagian historis ini tidak menggantikan
keputusan PM terbaru pada §1. Status review revisi ada di §12.
Ini review draft, bukan temuan bug implementasi P1 yang sudah berjalan. Tidak
mengedit source backend, P1 atau dokumen bersama. Invariant dan acceptance yang
perlu dilengkapi berikut tidak mensyaratkan satu detail implementasi tertentu.

Yang sudah sesuai: preview file dipisahkan dari analisis, identity generation dan
config revision eksplisit, expected_revision untuk konflik, fail-closed config,
token-bound binding, legacy routes tetap, retail tidak diklaim runtime, error
tidak mengandung nilai/path. Stop/EOF tidak menjanjikan prepared source hidup lagi.

### Gap yang menahan kesiapan kontrak

1. **Cancel sebelum upload selesai dan hasil mutation yang hilang.** P1 §4.2
   (baris 73–78) baru memberikan source_id setelah upload sukses; DELETE memerlukan
   ID tersebut, current tidak menyebut operasi PREPARING/cleanup/handoff, dan Play
   mengembalikan VideoStartResponse tanpa identitas sesi/operasi. Abort browser tidak
   membuktikan rollback server. Tambahkan kontrak untuk menemukan/membatalkan operasi
   pending miliknya dan merekonsiliasi lost ACK upload/PUT/Play tanpa POST ulang atau
   menyentuh B. Identitas operasi harus non-reusable lintas restart/reuse session_id;
   tidak harus memakai endpoint tertentu. Acceptance: gate sebelum respons upload,
   cancel, switch B, late A; ACK Play hilang lalu status membuktikan hasil; current
   tidak memberi A label B. Sertakan geometri/config yang dapat dibaca kembali atau
   nyatakan preview ditampilkan tetapi Play tetap terkunci sampai konfigurasi ulang.
2. **Lifetime tanpa observasi belum dibatasi.** P1 §4.1 baris 63–65 hanya menulis
   satu slot dan cleanup pada panggilan berikut; tidak ada deadline atau pemilik
   expiry ketika tab ditutup tanpa call berikutnya. Bounded count tidak membatasi
   umur file. Tetapkan kebijakan lifetime/control clock dan pemilik cleanup tanpa
   observasi, atau minta PM menyetujui file ditahan sampai call/restart sebagai
   keterbatasan produk. Catatan delete gagal tetap menjadi kewajiban, admission B
   tertutup sampai cleanup sukses. Acceptance: tab hilang, tanpa polling/frame,
   expiry; delete gagal sekali/berulang; tidak membuka slot secara palsu.
3. **Handoff menahan store lock saat memanggil dependency.** P1 §4.3 baris 108–114
   menyebut memanggil start VideoService di bawah lock store. Source sekarang
   `_atomic_reserve` juga memulai sinks; start/capture/cleanup dapat tertahan. Ini
   tidak sesuai konvensi lock di DESIGN.md dan dapat menahan status/cancel/shutdown.
   Tidak ada siklus deadlock yang dibuktikan hanya dari draft. Kontrak perlu
   menyatakan linearization pin identity/config/handoff, operasi dependency di
   luar state lock, urutan lock dan single-owner rollback; invalidation harus
   tetap dapat berjalan saat dependency tertahan. Admission prepare dan legacy
   start harus berkoordinasi, bukan check IDLE lalu write/probe yang masih race.
   Acceptance: legacy start versus prepare setelah admission check, gate sink
   start/read/release, Stop/close versus handoff, immediate EOF/error; tepat satu
   pemilik file, cleanup gagal tidak dilewati, B tidak memulai sebelum cleanup.
4. **Mode dan status kesiapan belum bisa dirender jujur.** P1 current hanya berisi
   metadata prepared/config_valid; API lama tidak melaporkan mode/capability.
   Frontend tidak tahu kapan perlu editor privat atau bisa Play legacy; mencoba
   PUT lalu menafsirkan semua 409 sebagai mode legacy tidak aman. Tetapkan cara
   membaca alur yang diizinkan, fase cleanup dan ikatan sesi analisis. Kontrak PUT
   ACK hanya config_revision: harus jelas apakah backend mempertahankan geometri
   persis atau mengembalikan canonical geometry; respons invalid tidak membuka
   Play. Acceptance: mode disabled/invalid tanpa fallback, legacy dan private
   cloud-disabled, malformed 2xx, expired source, foreign/stale session status.
5. **Geometri preview/analisis dan sisi dalam belum final.** P1 §4.2 baris 80–89
   mengusulkan left/right dan offset midpoint (open). W/H saja belum menjamin
   orientasi/crop preview sama dengan analisis. Definisikan sumbu x kanan/y bawah,
   makna left/right dengan konvensi tanda yang pasti, kasus batas gambar dan
   thresholds efektif; backend wajib memvalidasi sisi yang dipilih dan binding
   geometri ke input analisis. Acceptance: garis horizontal/vertikal/diagonal,
   endpoint dibalik, sudut/tepi, hysteresis non-default, resolusi/orientasi berubah
   dan angka ekstrem. Frontend dapat memakai titik klik untuk memilih setengah
   bidang lalu mengirim enum sisi jika disepakati; titik klik itu tidak boleh
   diklaim sebagai inside_point persis yang disimpan backend.

### Keputusan PM dan penyelarasan frontend

- P1 §7 keputusan startup config/default versus config operator, replace/DELETE,
  refused Play retain/discard, replay Stop dan flow legacy masih terbuka. Di source
  saat ini config startup invalid menghasilkan DisabledParkingTrigger; UI config
  belum bisa menghidupkan pipeline itu. Keputusan ini harus ada sebelum acceptance
  binding/aktivasi dapat dinyatakan lengkap. Binding harus siap sebelum sink start
  pertama dan stale binding tidak mengubah konfigurasi/thresholds sesi baru.
- Preview kamera ditunda P1 (§6 L1/§7.4). Untuk fase file saja, frontend mempertahankan
  kamera immediate-start legacy yang diberi copy jelas; jangan menampilkan editor
  privat kamera palsu. PM perlu memilih fase file-first atau memperluas kontrak
  kamera; seluruh kebutuhan preview sumber pada penugasan belum tertutup P1 ini.
- Proposal endpoint P1 boleh dipakai sebagai input review, belum dimasukkan sebagai
  API tersedia ke source frontend. Setelah revisi menutup gap, sesuaikan §6 inside
  representation, §7 cancellation/reconciliation dan exact scope file; tetap DRAFT
  sampai review silang dan keputusan PM.

Bukti review: pembacaan rencana P1, source API/models/runtime/VideoService/trigger
dan frontend pada baseline, hash blob dan pemeriksaan path/whitespace. Tidak ada
test runtime, build, API call, media atau perangkat dijalankan. Ini tidak membuktikan
ketepatan deteksi, keamanan akses, gesture, delivery audio atau latensi produksi.

## 12. Re-review P1 rev 2 setelah keputusan PM — Codex, 2026-10-11

Pada awal turn P1 masih versi lama; rev 2 kemudian tersedia dan dibaca seluruhnya
secara read-only. Blob yang diperiksa:
`53e27c11e31fd7bf4bb146f5391c26a961c43a2e`, source baseline `a45c986`.
**Verdict desain rev 2: BLOCKED**, karena empat invariant tersisa di bawah.
Ini bukan PASS versi lama dan bukan penilaian implementasi yang belum ada.
Keputusan PM §1 sudah sesuai rev 2; frontend tetap DRAFT, belum implementasi
atau review independen. Tidak ada perubahan rencana backend oleh Codex.

Hasil keenam pemeriksaan blocker awal:

| Kontrak | Hasil terhadap rev 2 |
|---|---|
| Cancel upload pending | Gap utama tertutup: reserve sumber sebelum file PUT; DELETE menginvalidasi WRITING; writer memeriksa cancel sebelum publish. Identity create ACK hilang dapat ditemukan lewat upload_operation_id di current. Acceptance 2 memakai gate writer |
| Rekonsiliasi Play | Ada operation_id dan GET outcome, tetapi bentuk pending/akhir sesi serta replay operasi lama setelah operasi baru belum lengkap (R1) |
| TTL/cleanup idle | Ada control-clock sweeper idle, cleanup_pending menolak B dan retry tercatat. Shutdown/Stop belum mensyaratkan cleanup analisis sukses sebelum source siap/dihapus (R2) |
| Handoff tanpa lock store | Gap lock store tertutup: pin di lock, dependency di luar lock, hook di luar lock VideoService. Admission/closed-start tetap perlu gate sebelum reserve dan saat start tertahan (R2) |
| Capability/identity/readiness | Capability, template false, operator config dan canonical echo tersedia dalam proposal. Registry/session_ref masih session_id-only, belum melindungi reuse identitas sesi (R3) |
| Geometri preview–analisis | Native size/same decoder dan sign left/right eksplisit. Check first frame belum menjamin seluruh frame analisis cocok (R4) |

### Invariant yang masih perlu ditutup

**R1 — rekaman operasi Play dan outcome pending/terminal** (P1 §5 baris 93–94,
§8 baris 181–194). Dedup hanya terhadap *last Play*. Urutan op-A selesai → op-B
selesai → replay op-A pada sumber/config sama tidak lagi cocok last op dan dapat
memulai sesi ketiga, bertentangan dengan “operation_id tidak diterima dua kali
untuk pekerjaan berbeda”. Outcome GET hanya STARTED/REFUSED/FAILED; saat start
tertahan belum ada body pending yang dapat dirender, dan immediate EOF membutuhkan
status yang membedakan start pernah berhasil dari analisis masih berjalan.

Kontrak perlu menjamin op lama tidak kembali menjadi kerja baru, serta pembacaan
outcome pending/terminal dan status sesi yang konsisten. Boleh memakai penolakan
operasi lama/tombstone/retensi bounded sesuai keputusan desain; tidak wajib
menyimpan riwayat tak terbatas. Tambahkan acceptance A→B→replay A, GET saat gated
pending, lost ACK dengan immediate EOF/error, dan lookup setelah expiry/delete.
UI tidak akan mengirim Play baru ketika outcome belum diketahui.

**R2 — selesai analisis tidak sama dengan cleanup berhasil** (P1 §8 baris 193–197,
212–213; source `VideoService.stop` dan `_release_session`). Stop source sekarang
dapat selesai dengan ERROR setelah waiter/join timeout, release gagal, sink cleanup
gagal atau read masih aktif. Draft mengembalikan PREPARED pada refused/failed/end,
dan shutdown `video.stop()` lalu store.close yang menghapus file; belum ada syarat
capture opener/read/release serta sink cleanup selesai sebelum membuka readiness
atau delete source. Hook sink-end sendiri tidak membuktikan semua cleanup selesai.

Kontrak perlu mempertahankan ownership source dan admission tertutup sampai
cleanup analisis benar-benar sukses; cancel/expiry/close tidak menghapus file yang
masih dipakai. Close saat Play baru pinned tetapi belum reserve tidak boleh
memungkinkan start terlambat menghidupkan analisis. Tambahkan gate sebelum reserve,
di sink-start/read/release, Stop waiter timeout, refusal dengan rollback gagal,
immediate EOF dan shutdown; verify sumber/config retained, B/Play ditolak, retry
hanya kewajiban tertunda, tepat satu pemilik dan tidak deadlock. Ini invariant,
bukan permintaan desain ulang detail internal VideoService yang sudah direview.

**R3 — registry dan end hook harus terikat instance sesi** (P1 §4 baris 73,
§7 baris 148–160). Registry bind/get/unbind hanya memakai session_id; end hook/status
juga memakai session_ref = session_id. Source VideoService memiliki reservation
token dan session factory yang dapat mengulang ID pada tests. Late bind/unbind/end
A dengan ID sama dapat memengaruhi B meski config generation store berbeda.

Kontrak minimal perlu authority instance/non-reusable reference yang menyertai
binding, lookup dan cleanup, diverifikasi atomik; token internal boleh tetap
privat. Tidak cukup memeriksa string ID sebelum dependency lalu menggunakannya
kemudian. Acceptance reuse sid A/B dengan gate bind, start refusal, cleanup/end
terlambat: config/thresholds B tidak diganti/dihapus, status A tidak membuka B.

**R4 — geometri setiap frame yang ditawarkan** (P1 §6 baris 127–130, §7.5,
acceptance 7). Same decoder dan first-frame check menutup perbedaan awal, tetapi
file dapat menghasilkan ukuran/orientasi berubah setelah frame pertama. Draft
belum menolak perubahan sebelum frame tersebut masuk detector/trigger/sampler.

Nyatakan invariant geometri analisis tetap sama dengan revisi preview untuk
seluruh frame yang digunakan, atau sumber yang berubah ditolak sebelum offer.
Acceptance fake capture: frame awal cocok, frame berikutnya mismatch, tidak ada
offer/alert/evaluasi dari frame mismatch; source config menjadi tidak-ready sampai
revalidasi yang sah, tanpa diam-diam mengubah koordinat. Tidak mensyaratkan seek,
kalibrasi dunia nyata atau deteksi kamera bergeser pada fase ini.

### Scope dan keterbatasan rev 2

Scope source/API/models/runtime dan empat test baru sudah disebut lebih konkret,
termasuk handler video, optional startup template dan perubahan model sisi dalam.
Namun shared decoder helper belum punya path/owner eksplisit; regresi lease,
VideoService/upload, activation dan inside_point-template perlu menyebut test
existing yang akan diadaptasi, bukan hanya menjalankan full suite setelahnya.
Pembawa kontrak session instance, registry/end hook dan schema pending/outcome
harus tercakup daftar path final sebelum implementasi.

TTL 30 menit/retry 60 detik adalah usulan nilai, bukan hasil pengukuran atau keputusan
baru frontend. Tidak expiry saat WRITING/PLAY_PENDING/PLAYING dinyatakan eksplisit:
satu slot bounded, tetapi operasi tertahan dapat menahan slot/file tanpa batas waktu;
catat sebagai keterbatasan dan tentukan recovery/invalidation saat cancel/close.
Tidak meminta timeout perangkat nyata atau persetujuan ulang keputusan PM §1.
Preview pertama decodable dan Play dari awal mengikuti proposal rev 2; tanpa seek.

Checklist acceptance untuk revisi berikutnya:

| Kontrak | Bukti desain dan acceptance yang diperlukan |
|---|---|
| Cancel upload pending | Identitas operasi diketahui sebelum ACK file; cancel dapat invalidasi operasi sendiri; late probe/write/response tidak menerbitkan A; cleanup tertunda tetap menahan B |
| Rekonsiliasi Play | Outcome pending/terminal operasi yang sama dapat dibaca setelah ACK hilang, termasuk immediate EOF/error; sumber/config dipertahankan saat refusal; tidak membutuhkan Play baru untuk mengetahui hasil |
| TTL dan cleanup idle | Control-clock deadline, pemilik expiry tanpa polling/frame, kewajiban delete/release tersimpan, admission tetap tertutup ketika cleanup gagal; Stop tidak menghapus sumber, expiry tidak release capture saat read aktif |
| Handoff tanpa lock store | Pin identity/revision/binding atomik; dependency di luar state lock; invalidation Stop/close tidak menunggu dependency; tepat satu pemilik cleanup, operasi A tidak mengubah B |
| Capability, identity, readiness | Alur P1 privat/file saja, template belum konfirmasi, editor tanpa startup valid, identitas non-reusable dan revisi otoritatif; Play hanya operator config valid dan cleanup selesai; legacy/retail tetap |
| Geometri preview–analisis | Gambar asli/orientasi/crop terikat sumber, sisi left/right P1→P2 dengan tanda pasti, transform contain frontend sesuai, perubahan geometri membatalkan konfigurasi sebelum analisis |

Acceptance race tambahan wajib memakai deferred response/gate dan fake clock,
bukan sleep untuk mengharapkan urutan:

- **Upload/cancel:** tahan write/probe sebelum ACK; mulai cancel A; buktikan B
  ditolak sampai cleanup sukses; lepaskan A dan pastikan response/error/finally
  stale tidak membuka pending B. Ulangi gate sebelum/sesudah publish identitas.
- **Lost Play ACK:** gate dispatch start, hilangkan respons; polling operation
  tetap pending, lalu terminal started/refused/ended. Uji EOF/error sebelum
  respons Play, Stop sebelum/dalam handoff, dan source retained dengan lease
  analisis yang baru pada Play manual berikutnya. Jumlah Play mutation tetap satu
  selama outcome belum diketahui, tanpa optimisme RUNNING/ready.
- **TTL/cleanup:** fake clock maju tanpa call/frame UI; pemilik sweep backend
  menginvalidasi; release/delete gagal sekali/berulang mempertahankan kewajiban.
  Gate read aktif, Stop, delete/expiry dan restart; tidak menghapus file/read
  resource yang masih dipakai dan tidak menerima B sebelum cleanup.
- **Stale config:** gate PUT A; delete A lalu sumber baru dengan nama file/ID sesi
  sama; late ACK/revision/preview A tidak mengizinkan Play B. Template valid juga
  tetap membutuhkan konfirmasi operator B, dengan left/right tervalidasi.

Scope implementasi backend harus memberi daftar exact source/test yang mengubah
admission, per-session config/thresholds, retensi file setelah Stop, routing
capability, pemilik TTL, outcome Play, shutdown dan geometri. Bila legacy handler
atau model/protocol tambahan diperlukan untuk invariant itu, daftar scope P1
harus menyebutnya; test acceptance tidak cukup bila file pembawa kontraknya
tidak tercakup. Revisi yang menutup R1–R4 perlu dibaca ulang dengan blob baru.

Verifikasi delta rencana ini: HEAD tetap `a45c986`, staging kosong; source
frontend/backend tidak berubah. Periksa tautan lokal, newline/whitespace dan
hash akhir UI serta P1. Tidak menjalankan test/build aplikasi pada delta desain.
Seluruh perubahan Claude dipertahankan; hanya file rencana UI ini diedit Codex.

## 13. Catatan historis menunggu R1–R4 dan deadline operasi

Owner/PM menerima verdict rev 2 BLOCKED dan menugaskan Claude merevisi R1–R4,
ditambah deadline WRITING/PLAY_PENDING. Pada preflight penugasan ini blob backend
masih `53e27c11e31fd7bf4bb146f5391c26a961c43a2e` (rev 2). **Revisi berikutnya belum
tersedia pada preflight; belum direview, bukan PASS atau pengulangan verdict baru
atas rev 2.** Setelah blob baru tersedia, hasil read-only dicatat di bagian ini.

Desain UI sekarang sudah menyatakan pending/unknown, outcome eviction, cleanup
pending, deadline bukan cleanup success, serta mismatch setiap frame; tidak
mengasumsikan nama enum/endpoint baru selain proposal yang telah dibaca.

Kriteria re-review backend: idempotensi operasi lama bahkan setelah eviction;
unknown/pending/start/end dapat direkonsiliasi tanpa kerja baru; source siap/delete
hanya setelah capture/read dan cleanup selesai; binding dan callback cleanup
terikat instance/lease/generation; validasi geometri sebelum setiap frame offer;
deadline menginvalidasi operasi tanpa menghapus kewajiban cleanup. Gate sebelum/
sesudah reserve, writer/probe, capture read, release, sink cleanup dan publish
deadline harus membuktikan ordering, tanpa sleep, serta membersihkan semua thread.

Review UI Claude belum tersedia dalam rencana/laporan yang dibaca pada preflight.
Tidak mengatribusi review mandiri ini sebagai verdict Claude. Jika review tersebut
disampaikan owner atau dicatat Claude, temuan desain ditangani hanya di rencana UI.
P1 tetap DRAFT, tanpa otorisasi implementasi source.

## 14. Review historis backend rev 3 — Codex, 2026-10-11

Blob backend yang dibaca seluruhnya:
`413c5ae072760268223e1099d8bb85bf729478bf`; HEAD source tetap `a45c986`.
**Verdict desain: BLOCKED** — perbaikan R1–R4 sudah menutup sebagian besar gap,
tetapi tiga kontrak berikut belum konsisten. Tidak memberi verdict implementasi
atau menjalankan tests. Review UI Claude belum tersedia pada dokumen/laporan yang
dapat dibaca; pesan owner bahwa Claude selesai bukan atribusi verdict review UI.

### Perbaikan terverifikasi dari teks rev 3

- CAS next_attempt monoton melarang eksekusi attempt lama setelah eviction;
  PENDING/TIMED_OUT dan session.phase/cleanup_ok tersedia untuk rekonsiliasi.
- ENDING menahan readiness/delete/expiry sampai hook cleanup_ok true untuk lease
  yang cocok; file-user accounting mencakup writer, preview reader dan analisis.
- Registry bind/unbind memakai lease; get id-only diserialkan dengan lifecycle
  start/end VideoService. Session_ref UUID berbeda dari session_id yang reusable.
- Setiap frame diperiksa ukurannya sebelum packet/offer; mismatch mematikan
  analisis dan generation geometry_unstable tidak dapat dibuka dengan resave.
- WRITING/PLAY_PENDING punya deadline/sweeper tanpa API call; deadline hanya
  invalidasi, pemilik resource tetap sampai cleanup. Thread yang tidak kembali
  tetap memblokir slot; ini keterbatasan tercatat, bukan cleanup sukses.
- Scope menyebut registry, decoder helper dan test/regresi konkret. Nilai deadline,
  retry, retensi dan first-decodable preview tetap proposal, bukan keputusan PM baru.

### Sisa temuan

**R5 — admission predicate masih memiliki celah check-to-use** (P1 §7.5 baris
176–178, §8.5 baris 263–266). Draft menyebut callback store mengambil lock singkat
di bawah V._lock lalu reservasi “atomik”. Setelah callback melepas store lock dan
sebelum reservasi V dibuat, sweeper/close dapat menjatuhkan pin. V._lock saja tidak
menserialkan invalidasi store itu. Deadline/close dapat menang sebelum reservasi
tetapi sesi tetap dibuat dari jawaban true lama; ini bertentangan dengan acceptance
4 “deadline before reservation → no session”. Men-stop setelah start mengurangi
dampak, tetapi belum membuktikan invariant no late session.

Tetapkan satu linearization antara admission/reservasi dan invalidasi pin; boleh
claim/handoff authority lain atau urutan lock yang konsisten, tanpa dependency I/O
di state lock. Acceptance gate tepat **setelah predicate true sebelum reservasi**,
sweeper/close menginvalidasi, lalu lanjutkan: tidak ada sink/capture/dispatch stale,
atau jelaskan bahwa reservation telah linearized lebih dahulu dan invalidation
mengakhiri authority yang sama. Tidak cukup gate sebelum predicate atau setelah
seluruh start. Semua cleanup tetap satu pemilik, tidak menghidupkan B.

**R6 — hook dapat mendahului return start** (P1 §7.5 baris 175–187, §8.1 dan
§8.3 baris 224–238). start_prepared_file baru mengembalikan lease/session_ref setelah
start selesai, tetapi EOF, Stop atau refusal cleanup dapat memanggil on_finished
lebih dahulu. Store harus sudah mengetahui file-user/lease/attempt tepat sebelum
hook pertama, serta tidak mengubah ENDED/ENDING/PREPARED menjadi PLAYING dari
return STARTED yang terlambat. Langkah 3 secara eksplisit menulis PLAYING saat
masih pinned; belum menentukan merge dengan hook awal atau registrasi user sebelum
return. Hook cleanup_ok true yang diabaikan karena lease belum dikenal dapat
meninggalkan obligation selamanya; close juga tidak boleh menghapus file antara
reservation dan registrasi user.

Kontrak perlu registration authority sebelum pemakaian file/hook dan monotonic
merge outcome/session cleanup. Acceptance tahan **return** start setelah immediate
EOF/Stop/refusal hook sudah selesai: record tetap STARTED+ENDED atau REFUSED/FAILED
dengan cleanup yang benar; source tidak PLAYING lagi, user count benar. Uji false/
null hook lalu true retry hook, close di gap registration, dan hook stale A setelah
B. Tidak mensyaratkan nama method/struktur kode tertentu.

**R7 — operation_id sama pada attempt baru** (P1 §4 baris 77, §8.2 baris 207–218).
CAS menolak attempt lama, tetapi aturan replay hanya memeriksa attempt sama.
Request dengan next_attempt yang sah dan operation_id milik attempt sebelumnya
masih dapat lolos, bertentangan dengan invariant “operation_id tidak terikat dua
attempt”. Setelah eviction, record yang diperlukan untuk cek ID mungkin hilang.
Tegaskan lingkup idempotency dan refusal untuk ID yang sudah pernah terikat,
termasuk retensi bounded/eviction; atau ubah kontrak identitas sehingga pasangan
attempt/op adalah identitas dan tidak menjanjikan dedup UUID saja. Acceptance:
op-A pada attempt berikutnya, sebelum dan setelah eviction; tidak ada kerja yang
tidak sesuai kontrak. UI selalu memakai UUID baru hanya untuk Play manual baru
yang readiness-nya sudah terbukti, tidak untuk mengatasi unknown outcome.

### Keterbatasan integrasi frontend yang masih terbuka

P1 §11.5 hanya menambahkan session_ref ke status, sementara evaluation/detection/
domain masih membawa source_session_id. UI tidak dapat menyamakan dua identitas
itu; binding lintas endpoint untuk reuse ID/aksi dari tab lain tetap perlu kontrak
atau batas jaminan yang eksplisit sebelum implementasi UI. Epoch/abort lokal
dipertahankan; tidak diklaim menggantikan identity backend. Private webcam analysis
tidak tersedia pada fase file-only; legacy webcam tetap. Preview/reencode recovery
geometri mengikuti proposal rev 3, bukan fitur yang sudah ada.

Verifikasi perencanaan: hash versi, source terkait VideoService/detector/API dan
isi rencana dibaca; link/whitespace/newline diperiksa. Tidak ada source/backend/
dokumen bersama diedit, test/build, server, provider, media/perangkat, stage atau
commit dijalankan. P1 tetap DRAFT sampai temuan desain ditutup dan PM memutuskan.

## 15. Review historis backend rev 4 — Codex, 2026-10-11

Blob yang dibaca secara read-only:
`c6e9ee450d959aac88b78497010027d77025f8de`, baseline source HEAD `a45c986`.
**Verdict desain: PASS WITH LIMITATIONS; R5–R7 tertutup pada kontrak desain.**
Tidak ada blocker baru pada scope review ini. P1 tetap DRAFT dan implementasi
belum diotorisasi; hasil ini bukan verifikasi implementasi/runtime atau keputusan
atas opsi teknis/nilai yang masih proposal.

| Temuan sebelumnya | Bukti penutupan rev 4 |
|---|---|
| R5 check-to-use admission | Ticket leaf lock memastikan tepat satu OPEN→CONSUMED/CANCELLED; consume adalah titik linearization di reservation section V._lock; cancel menang berarti tidak ada reservation/bind/capture/sink, consume menang berarti cancel memperoleh lease tepat |
| R6 hook sebelum return | File user didaftarkan saat pin, lease/ref tercatat saat consume; hook membawa ticket_id+lease dan tetap diproses setelah generation diinvalidasi; return hanya mengisi outcome/fase secara monoton, tidak mengembalikan ENDED menjadi RUNNING/PLAYING |
| R7 operation_id lintas attempt/eviction | Map binding ID→attempt tidak ikut eviction outcome; request dengan ID lama pada attempt baru ditolak operation_reused; watermark CAS menolak attempt lama; cap membatasi map sepanjang umur generation |

Cleanup tetap terpisah dari invalidation/deadline: ticket open yang dibatalkan
boleh melepaskan file use karena reservation tidak mungkin terjadi; ticket consumed
tetap memilikinya sampai cleanup_ok true. False/null menahan ENDING/admission.
Penghapusan intent dipertahankan saat menunggu hook, bukan mengembalikan sumber
yang sudah dihapus menjadi siap. Registry tetap lease-bound, serialized start/end;
per-frame size mismatch tetap ditolak sebelum packet/offer dan generation menjadi
geometry_unstable. Scope mencakup decoder, registry, hook dan test terkait.

Acceptance §9.3–§9.4 secara eksplisit mencakup eviction/reuse/cap, gate sebelum
consume, sesudah consume sebelum sink start, hook sebelum return, cleanup false
lalu true, stale hook dengan reuse session_id, serta close/delete di semua gate.
§9.5–§9.9 mencakup cleanup timeout/read aktif, binding, mismatch frame lanjutan,
deadline tanpa call dan config basi. Semua gate/thread dibersihkan di teardown.
Ini pemeriksaan atas rancangan acceptance; tests tersebut belum diimplementasikan
atau dijalankan oleh reviewer. Saat implementasi, merge harus menjaga intent
delete/close serta outcome terminal meski callback datang lebih awal.

### Keterbatasan dan tindak lanjut desain

- Kontrak identitas session_ref pada evaluation/detection/domain masih keputusan
  teknis terbuka di P1 §11.5. UI tidak membandingkan session_ref UUID dengan
  source_session_id; jaminan binding lintas endpoint harus final sebelum source
  UI diimplementasikan. Epoch/abort lokal tetap perlu dan tidak membuktikan
  identitas hasil backend sendiri.
- Durasi deadline/TTL/retry, delapan outcome dan cap 256 belum diputuskan PM atau
  diukur. Estimasi ≈6 KiB binding adalah ukuran data mentah, bukan pengukuran
  overhead Python atau keseluruhan RAM. Append-only hook log pada ticket harus
  tetap dibatasi/diringkas saat implementasi agar retry cleanup berulang tidak
  menambah state tanpa batas; tidak boleh menghapus kewajiban cleanup.
- §8.4 masih menyatakan semua start yang tidak kembali memblokir slot. §8.3.7
  lebih spesifik: OPEN_CANCELLED tanpa reservation boleh melepaskan slot; hanya
  writer/consumed ticket yang resource-nya belum bersih perlu memblokir. Gunakan
  aturan ownership tersebut pada copy UI dan implementasi; ini klarifikasi
  editorial, bukan blocker ticket contract.
- Geometri berukuran sama dengan crop/pan berbeda tidak terdeteksi; konfigurasi
  valid bukan bukti posisi fisik benar atau izin/identitas. Private webcam tetap
  tidak tersedia pada fase file-only; legacy/retail tidak diperluas.
- Dedup upload hanya current slot. Replay create setelah delete dapat membuat
  slot kosong baru yang expired; frontend tidak auto retry create/Play sebagai
  operasi baru. Lookup source_gone/evicted tidak membuktikan tidak pernah start.

Review UI Claude belum ada pada dokumen/laporan yang dibaca; tidak dicatat sebagai
PASS Claude. Rencana UI diselaraskan hanya dengan proposal kontrak rev 4. Tidak
menambah scope, approval ulang keputusan owner, atau source implementation.

Verifikasi sendiri: Git preflight, pembacaan plan/harness/source yang relevan,
hash blob, tautan dan whitespace/final newline; source frontend/backend tetap
tidak berubah, staging kosong. Hanya rencana UI ini diedit Codex; seluruh perubahan
Claude dipertahankan. Tidak ada tests/build aplikasi, server, provider, media/
perangkat nyata, stage, commit atau push dijalankan.

## 16. Review historis delta backend rev 4.1 — Codex, 2026-10-11

Blob backend:
`087c771e0ecd76f3890bb67849fcb36d953ba7d0`, source HEAD `a45c986`.
**Verdict desain: PASS WITH LIMITATIONS; tanpa blocker baru pada delta D1–D6.**
P1 masih DRAFT; hasil review tidak mengotorisasi implementasi atau menetapkan nilai
teknis yang masih proposal. R5–R7 tetap tertutup oleh ticket dan binding generation.

| Delta Claude | Hasil verifikasi read-only |
|---|---|
| D1 bounded hook summary | Latest hook + count, tidak lagi append-only log; kewajiban cleanup tetap disimpan. Cleanup true tidak membenarkan release ulang dari hook stale |
| D2 ownership rule | OPEN_CANCELLED tanpa reservation melepas file use; writer/consumed ticket belum bersih menahan slot. Deadline bukan cleanup success; konsisten dengan ticket linearization |
| D3 same-size detection limit | Crop/pan/zoom/rotation berukuran sama tidak terdeteksi; risiko salah/tidak ada warning disebut eksplisit, C1H masih dependensi draft sebelum CCTV nyata |
| D4 proposal table | Start/reset/expiry/capacity/reason untuk deadline, idle TTL, retention dan retry dijelaskan; estimasi map adalah data mentah sebelum overhead Python, bukan hasil pengukuran |
| D5 idle TTL | PREPARED idle reset oleh mutation/preview/template read, bukan current polling; deadline WRITING/PLAY_PENDING tidak berubah menjadi sliding polling timeout |
| D6 additive join identity | Status dan attempt record menambah session_id di samping session_ref; status dibaca atomik bersama state. Join result memakai source_session_id, bukan menyamakan UUID ref dengan ID result |

D6 cocok dengan source default `VideoService._session_id_factory` yang memakai
uuid4 per reservation. `session_ref` dan join fields masih rancangan, belum ada
di source saat ini. Join bukan snapshot atomik semua endpoint dan bergantung ID
produksi tidak reusable; tidak membuktikan reuse ID hasil arbitrary. Epoch/abort
dan render guard tetap wajib. UI acceptance 17 mencakup join/mismatch dan mencatat
batas reuse; backend perlu acceptance wire model untuk kedua field status/record
dan atomic snapshot bersama state, karena acceptance §9 saat ini terutama menguji
ticket/core, bukan seluruh join frontend. Ini kebutuhan acceptance tambahan,
tidak ditemukan kontrak yang saling bertentangan pada D6.

D5 perlu test fake clock explicit reset pada preview/template/mutation versus
current polling serta rejected/stale request tidak memperbarui sumber generation
lain. Tentukan tepat operasi mutation sukses yang mengatur ulang TTL saat
implementasi; copy UI tidak menganggap polling sebagai keepalive. Test D1 perlu
cleanup retries berulang dan state summary tetap bounded tanpa kehilangan
obligation. Acceptance ini belum dijalankan; test/build aplikasi tidak diulang
untuk perubahan rencana saja.

Open proposals tetap: nilai timeout/TTL/retry, retention/cap, pilihan frame acuan,
async delete dan rename zona; tidak meminta persetujuan ulang keputusan PM §1.
No same-size scene-health guarantee, no gesture/audio readiness, no private webcam
atau pengujian perangkat nyata. Review UI Claude belum tersedia dalam laporan
yang dibaca; tidak mengatribusi verdict sendiri kepada Claude.

Hanya rencana UI diselaraskan; backend dan dokumen bersama tetap milik Claude.
Verifikasi sendiri: Git/HEAD/staging, pembacaan delta terhadap rev 4 dan source
factory/API/hooks, hash blob, link/whitespace/newline. Source frontend/backend
tidak berubah. Tidak ada stage, commit, push atau eksekusi nyata.

## 17. Catatan historis review delta backend rev 4.2

Codex, 2026-10-11, source baseline `a45c986`; blob backend yang diperiksa:
`2ade92735903b64c0f76cf992992071eac681d62`.
**Verdict delta D7–D9 dan acceptance 11–13: PASS WITH LIMITATIONS.** Tidak
mengulang review keseluruhan rev 4.1. Tidak ada blocker baru pada lima invariant
yang diminta owner. Ada satu klarifikasi acceptance shutdown di bawah. UI dan
P1 tetap **DRAFT — menunggu review UI Claude dan keputusan PM**, bukan completed
atau izin implementasi. Delta pencatatan ini hanya file rencana UI.

### Pemeriksaan terarah

| Kebutuhan owner | Bukti desain yang diperiksa |
|---|---|
| Polling tidak memperpanjang TTL | Acceptance 11 menguji current/play lookup/capabilities/video status dipoll tiap detik tetapi expiry tetap; rejected/stale requests tidak reset; successful preview/template/config reset, operasi WRITING/PLAY_PENDING tidak reset |
| Cleanup pending menahan aksi baru | D7 single-flight delete, retry count dan CLEANUP_STALLED tetap punya obligation; POST baru ditolak; source deleting/stalled bukan ready untuk Play; session cleanup tetap lewat Stop manual |
| Respons lintas endpoint tidak atomik | D8 membatasi atomicity per status/store snapshot saja; identity join perlu status ref/id cocok dengan attempt serta result source_session_id, epoch/abort/render guard tetap dipakai |
| UUID produksi bukan dukungan ID reusable | D8 menyatakan join tidak aman untuk payload dengan ID reused; lease/ticket/generation internal tetap aman dalam acceptance 6, dipisahkan dari join UI |
| Rekonsiliasi bukan Play baru | Outcome lookup/last_attempt dan tuple identity dipertahankan; eviction/unknown tidak membuat UUID baru; seluruh mutation Play tetap intent manual setelah readiness dan cleanup terkonfirmasi |

Acceptance 12 menguji gate filesystem saat sweeper/DELETE/shutdown bersamaan,
tepat satu attempt in-flight, count/cap dan stalled tetap menahan admission;
explicit DELETE hanya satu attempt tambahan. Seratus false cleanup hooks tetap
summary bounded; true hook baru mengakhiri obligation. Acceptance 13 menguji
wire field status/attempt, snapshot state+ref+id dan tuple store konsisten,
interleaving A→B serta default uuid4 factory tanpa env/API override. Ini desain
test, bukan hasil test implementasi reviewer.

**Klarifikasi rendah — shutdown saat stalled.** D7/§8.5 menulis maksimal satu
final attempt untuk file tanpa users/in-flight; acceptance 12 justru menulis
shutdown dengan stalled **atau** in-flight tidak membuat extra attempt. In-flight
memang tidak boleh overlap. Untuk stalled tanpa in-flight, tentukan expectation
0 atau 1 yang sama pada aturan dan test; keduanya dapat menjaga bounded retries
dan obligation. Ini tidak membuka admission/Play dan bukan blocker invariant
utama; perlu diselaraskan sebelum test tersebut diimplementasikan. Jangan
menganggap shutdown attempt yang gagal sebagai resource sudah bersih.

### Penyelarasan UI untuk kontrak delta

- CLEANUP_STALLED menampilkan “Penghapusan tertahan. Coba hapus lagi” dengan
  satu aksi eksplisit terhadap source/generation obligation yang sama. Selama
  delete in-flight, tombol dikunci; selesai request → baca status current lagi.
  Tidak ada loop retry browser, upload/Play baru, penghapusan otomatis source
  lain, atau ready optimistis setelah batas retry tercapai. Retry session cleanup
  tetap “Coba hentikan lagi”, terpisah dari retry penghapusan file.
- Pembacaan preview/template berasal dari intent operator, bukan background
  keepalive. Polling status/attempt/capability tidak mengulang pembacaan gambar
  untuk menahan TTL. Client countdown tidak mengubah state server, dan clock
  browser bukan clock otoritatif expiry/cleanup.
- Untuk hasil AI/domain/detection, cocokkan identity pada status current epoch
  dan attempt, lalu source_session_id hasil; empty/mismatch/old tuple tidak
  diterima. Tidak mengklaim status dan hasil merupakan satu transaksi. Aksi dari
  tab lain tetap punya delay pengamatan polling; bila hasil tidak dapat diikat,
  tampilkan belum tersedia, bukan indikator mode/audio/gesture simulasi.
- UI acceptance 17–18 tetap berlaku; tambahkan stalled cleanup gate/cap dan
  explicit retry satu kali, serta field status hilang/invalid/mixed snapshot,
  current tuple lama, response A terlambat setelah B. Semua fakes/gates/timers
  dibersihkan, tanpa assertion yang mengklaim result join aman pada ID reusable.

Tabel timeout/retensi rujukan tetap satu sumber di [P1 §8.4](private-parking-preview-configuration.md).
Nilai 10 min AWAITING_FILE, 5 min WRITING, 60 s PLAY_PENDING, idle 30 min,
retry 60 s/cap 10, retention 8/cap attempt 256 adalah **proposal Claude**, bukan
keputusan PM, SLA atau hasil ukur. Tidak menggandakan tabel nilai otoritatif di
UI; setelah PM memutuskan, frontend mengikuti kontrak/API yang telah dibekukan.
Fase backend P1a→P1b→P1c pada §14 adalah proposal urutan kerja; UI P1d menunggu
kontrak P1c reviewed/frozen dan otorisasi PM, tidak mulai source dari verdict ini.

Review UI Claude belum tersedia pada dokumen/laporan yang dibaca; hasilnya sudah
diminta melalui owner dan tidak diada-adakan. Jika tersedia, catat atribusi/blob
yang direview dan tangani temuan di file ini. Angka proposal, same-size scene
change, perangkat/gesture/audio dan batas join produksi tetap keterbatasan.

Verifikasi sendiri hanya preflight Git, pembacaan D7–D9/acceptance baru/tabel dan
cross-check desain UI, hash blob, link/whitespace/newline. Source tidak berubah;
tidak menjalankan tests/build aplikasi, API/server/provider/perangkat, stage,
commit atau push. Seluruh perubahan Claude dipertahankan.

## 18. Penutupan temuan UI Claude dan review delta P1 rev 4.3

Codex, 2026-10-11; HEAD source `a45c986`. Review UI Claude **disampaikan melalui
owner** dalam penugasan penutupan tiga temuan; tidak ada verdict/angka test review
UI yang diberikan, sehingga tidak mengatribusi PASS atau test implementasi kepada
Claude. Tiga koreksi berikut ditutup dalam rancangan, belum diuji lewat source:

| Temuan Claude | Koreksi dan acceptance |
|---|---|
| Start legacy/webcam masih ambigu di private | §8 memakai mode/preparation_available dari capabilities; private hanya file preparation, tidak ada start legacy/webcam/fallback; acceptance 19 |
| Cancel sebelum source ID belum konkret | §7 menyimpan operation_id sebelum create POST, current.upload_operation_id yang cocok saja menjadi target DELETE; unknown/204 awal tidak dianggap cancel sukses; acceptance 20 |
| Referensi kontrak/cleanup belum mengikuti P1 | Referensi P1 terbaru dan §7 state CLEANUP_STALLED; DELETE manual satu attempt, single-flight, tunggu current cleanup sukses sebelum B/Play baru; §17 menjadi riwayat |

Blob backend yang direview read-only untuk delta D7–D12 dan keputusan parameter:
`000693880dc30eeb974c13fcb5a77d2b1f518e03` (rev 4.3, pembacaan final).
File backend diperbarui Claude selama pemeriksaan; hash awal rev 4.3 yang terlihat
`af39cfb67e9a2660b8a46e5d83bc4aa2cd67286e` tidak dipakai sebagai hash penutupan.
Bagian delta, parameter, shutdown/startup cleanup dan acceptance dibaca ulang
pada versi final; tidak mengasumsikan dua blob itu identik.
**Verdict delta: PASS WITH LIMITATIONS, tanpa blocker baru.** Tidak mengulang
review keseluruhan rev 4.1. Hasil desain tidak membuktikan tests atau implementasi.

- D7 tetap single-flight: in_flight disetel di store lock, filesystem di luar lock;
  sweeper/DELETE/shutdown tidak menumpuk attempt. Setelah limit, obligation tetap
  CLEANUP_STALLED, tidak membuka POST atau Play. Explicit DELETE mencoba sekali,
  lalu rekonsiliasi; session cleanup bukan retry store, tetap Stop manual.
- D11 menutup klarifikasi rendah §17: shutdown pending **atau stalled**, tanpa
  file users/in-flight → satu final attempt pada acceptance 12, tanpa loop/reset
  count. File masih dipakai atau delete in-flight → tidak ada attempt baru.
  Budget habis/final attempt gagal bukan cleanup sukses; obligation tetap,
  admission closed permanen. Receipt request atau state closed bukan bukti delete.
- D12 startup failure kini konkret: direktori prepared yang ditentukan saja,
  failed delete menjadi CLEANUP_PENDING count=1, masuk retry/limit yang sama,
  POST tertolak sampai **seluruh** obligation selesai. Tidak keluar dari konstruktor
  dengan exception delete. Acceptance 12 mencakup gagal sekali/berulang, stalled,
  gate in-flight dan startup leftover; tidak mengklaim restart selalu menyelesaikan
  file yang tidak dapat dihapus.
- D9 acceptance 11 masih membuktikan polling current/play/capabilities/status tidak
  reset TTL; hanya operasi successful source current yang sesuai rule reset.
  Rejected/stale requests tidak memperpanjang generation B; reset versus expiry
  punya satu winner di lock, decode user tidak dihapus selama masih dipakai.
- D8/acceptance 13 tetap atomic per snapshot saja; join hasil hanya dengan default
  UUID produksi. Backend lease/ticket aman terhadap reuse ID bukan bukti join
  payload reusable aman. UI identity/epoch/abort/render guard dan delay observasi
  lintas endpoint tetap disebut, tidak menghasilkan Play baru otomatis.

### Status parameter dan otorisasi

P1 §0.1 mencatat keputusan owner/PM: deadline AWAITING_FILE 10 min, WRITING 5 min,
PLAY_PENDING 60 s, idle TTL 30 min, retention 8, attempt cap 256, cleanup interval
60 s dan limit 10 sebagai **default awal**, bukan hasil kalibrasi/SLA. Angka
tidak berubah dari proposal sebelumnya; statusnya sekarang keputusan PM yang
dicatat Claude. Source/test belum mengimplementasikan parameter ini. Rujukan
otoritatif tetap tabel P1, bukan duplikasi keputusan di banyak dokumen.

P1 rev 4.3 mencatat otorisasi bersyarat owner untuk **P1a saja** setelah review
delta tanpa blocker; P1b–P1d belum diotorisasi. Penugasan Codex ini tetap desain
UI/review, tidak menjalankan implementasi atau meminta approval ulang keputusan
yang sudah diambil. UI masih DRAFT menunggu penutupan PM/kontrak P1c frozen,
tidak menandai seluruh P1/UI selesai. Frame pertama decodable/Play dari awal dan
hal teknis yang masih open tetap proposal terpisah dari default PM.

Keterbatasan tersisa: same-size scene changes tidak terdeteksi, gesture/audio/
device tidak terbukti, result join tidak mendukung ID reusable, tab lain/restart
diketahui lewat observasi polling. Jika create-source response tetap unknown
dan current belum menemukan operasi, UI menahan aksi baru; kontrak tidak dianggap
memberi cancellation proof dari 204 saja. Full verdict re-review UI belum tersedia.

Verifikasi sendiri: Git aktual/staging kosong, pembacaan delta D7–D12/acceptance
11–13 dan §0.1 (tanpa seluruh core review), hash blob, isi rencana UI/tautan/
whitespace/newline. Source frontend/backend tidak berubah, dokumen Claude tetap
dipertahankan. Hanya rencana UI ini diedit; tidak ada tests/build, runtime/API/
perangkat, stage, commit atau push dalam task ini.

## 19. Review independen implementasi P1a dan penyelarasan UI

Codex, 2026-10-11. HEAD `a45c986f7c601884edb6d7ec8d93afe974634d05`, staging
kosong. Rencana backend rev 4.3 dengan catatan implementasi §15 dibaca dari disk:
`294e0787720eb8eb7e25e9aff24e6bf700123ab7`. **Verdict P1a: BLOCKED.** Kelulusan
desain sebelumnya tidak diganti menjadi bukti kelulusan implementasi. P1/UI tetap
DRAFT, frontend belum diimplementasikan, P1b–P1d belum diotorisasi oleh review ini.

### Temuan implementasi yang direproduksi sendiri

1. **Deadline dapat dilewati sebelum sweeper memproses expiry.** `_live()` hanya
   memeriksa voided; request tidak membandingkan control clock dengan deadline.
   Fake clock +1801 s setelah PREPARED: save mengembalikan revision 2 dan TTL
   1800 s baru; Play menghasilkan STARTED dan satu reservasi. WRITING +301 s
   kemudian finish_write menghasilkan PREPARED. Ini berlawanan dengan expiry
   yang menginvalidasi operasi, dan belum dicakup tests yang memanggil tick lebih
   dahulu. Perlu acceptance request/return terlambat ketika sweeper tertunda.
2. **Sweeper berulang tanpa menunggu ketika obligation masih memiliki file user.**
   `_next_due()` memasukkan cleanup yang sudah due, sedangkan `_claim()` menolak
   karena reader/writer/session masih memakai file. Fake preview reader yang
   menahan file expired mencapai 100 pass tanpa clock maju atau delete dipanggil.
   Gate dilepas dan thread joined; perlu acceptance scheduling tanpa busy loop
   selama user masih aktif, termasuk wake sesudah user terakhir selesai.
3. **Koordinat integer sangat besar melewati jalur error validasi terkontrol.**
   `line.p1=[10**400, 0.5]` menghasilkan OverflowError dari math.isfinite,
   bukan InvalidGeometry dengan field. Helper lama juga mempunyai kelemahan ini;
   builder operator baru belum menutupnya. Tidak ada konfigurasi diterima, namun
   P1c tidak boleh mengandalkan semua input invalid menghasilkan error bertipe.
   Perlu test integer ekstrem pada titik dan rect, tanpa log payload mentah.

Source backend tidak diperbaiki reviewer. Scope review: prepared_source.py,
session_config_registry.py, perubahan private_parking.py dan empat test P1a.
Seluruh perubahan penulis lain dipertahankan.

### Delapan keputusan implementer §15

| Keputusan | Penilaian review |
|---|---|
| 1: ticket juga menyimpan session_id | Ekstensi correlation yang sesuai D6; lease tetap authority, bukan sid saja |
| 2: report_geometry_unstable sebelum hook | Sesuai fail-closed; pengecekan setiap frame masih pekerjaan P1b |
| 3: refusal sebelum reservasi menjadi REFUSED setelah CAS | Menyimpang dari 409 video_busy sebelum CAS; attempt terpakai. Perlu keputusan PM/pemetaan P1c, tidak dianggap otomatis disetujui |
| 4: write/probe gagal membatalkan sumber | Ownership tetap ada sampai delete sukses; UX harus memulai create baru setelah cleanup, bukan memakai sumber voided |
| 5: clean hook hanya menjadwalkan delete | Sesuai larangan file I/O di hook; scheduler bermasalah seperti temuan 2; setelah close, cleanup yang datang terlambat dapat menyisakan file untuk startup berikutnya |
| 6: leftover startup tanpa source_id | Kewajiban/admission dipertahankan, tetapi explicit DELETE tidak dapat memulihkan leftover stalled. Batas recovery menyimpang dari janji retry operator umum; rute/policy P1c masih terbuka |
| 7: budget shutdown memakai monotonic nyata | Sesuai batas waktu menunggu cleanup; timeout tidak berarti cleanup sukses. Panggilan dependency Stop sendiri belum dibuktikan bounded oleh budget ini |
| 8: nama file uuid hex, bukan nama upload/source ID | Sesuai privasi dan kepemilikan direktori dedicated |

### Penyelarasan temuan UI Claude melalui owner

- Leftover startup dapat memiliki cleanup state dengan source_id/generation kosong.
  Itu bukan “Belum ada sumber” atau admission terbuka. UI tidak membuat target
  DELETE palsu; stalled tanpa ID tidak menawarkan retry sumber yang tidak tersedia.
  Kewajiban tetap terlihat, upload/Play ditahan sampai backend membuktikan cleanup.
- REFUSED final tidak direkonsiliasi dengan otomatis mengirim Play baru. Manual
  intent berikutnya menunggu outcome, cleanup, konfigurasi dan source identity
  fresh; operation ID lama tidak dipakai pada attempt baru.
- Referensi aktif mengarah ke P1 terbaru §8.2/§8.4/D6 dan §15; rev lama tetap hanya
  riwayat. Kontrak HTTP P1c belum tersedia sebagai kemampuan runtime.

Acceptance UI tambahan: snapshot cleanup tanpa source ID (pending/stalled) menahan
admission dan tidak mengirim DELETE dengan ID kosong; REFUSED sebelum reservation
tidak membuat request Play kedua; respons lintas endpoint lama/mixed tidak membuka
aksi; deadline habis tidak dijadikan bukti cleanup. Frontend mengikuti status
otoritatif setelah temuan backend ditutup, bukan menutupi bug expiry dengan timer UI.

### Bukti independen dan batasnya

- Target empat file P1a: **98 passed**, 0.37 s; full backend: **1307 passed**,
  30.66 s, satu warning deprecation Starlette/AnyIO. Dijalankan serial, masing-masing
  basetemp unik; semua fake, tanpa provider/device.
- pip check: No broken requirements found. Diff/cached whitespace diperiksa;
  tujuh source/test dan rencana backend memiliki final newline, tanpa trailing
  whitespace atau byte kontrol selain newline/tab.
- Reproduksi tambahan memakai fake existing: deadline terlewati, huge integer,
  sweeper 100 pass, cap **256** reservasi (attempt 257 ditolak), serta operation ID
  pertama sesudah outcome eviction tetap ditolak pada attempt baru.
- Klaim pengulangan **30 kali** tetap laporan Claude, tidak direproduksi 30 kali
  oleh Codex. Fake tests bukan bukti VideoService/API wiring, decoder/per-frame
  geometry nyata, audio/gesture, atau keamanan join hasil dengan reusable sid.

Blob source/test yang diperiksa:

| Path (relatif root) | Blob |
|---|---|
| backend/app/models/private_parking.py | 5a31092ac78fbdb06761e4dcfa91c5ac305ec84a |
| backend/app/services/prepared_source.py | bf4cf7bb49d06936f900a772c6b8554fd05231a8 |
| backend/app/services/session_config_registry.py | 8911852a9cf92147d1d9eb2df79fc0b1d78cf77b |
| backend/tests/test_inside_side_geometry.py | 92f786bf828cc068ebc0f1bbcdb2e4160dc6f1c9 |
| backend/tests/test_prepared_source.py | e70145b672caefc672d89095ee62d1fd78f72221 |
| backend/tests/test_prepared_source_races.py | 5fcd102b22c2dcac6ceac3f62eaab18b3c774862 |
| backend/tests/test_session_config_registry.py | 95e190966921ffcca3f050c393b49c5752ecde08 |


## 20. Implementasi P1d awal — bukti historis implementer Codex, 2026-10-12

**Implemented; awaiting independent review Claude.** Tidak ada verdict review
independen implementasi P1d saat catatan ini dibuat. Otorisasi implementasi owner
memakai blob rencana `36022c15460b0167590c4c6ce301ced573ce56d7`. HEAD masih
`eb6a3ac9b093a99fd9e151261f984383b5b95c01`; staging kosong. Backend dan seluruh
dokumen bersama tidak diedit Codex; delta Claude yang sudah ada tetap dipertahankan.

### Perilaku yang diimplementasikan

- Capability menentukan route private/legacy/disabled, tanpa fallback private ke
  start legacy/webcam. File privat di-create dengan UUID kanonik, diunggah, lalu
  operator memilih Muat preview. GET JPEG ini tidak memulai analisis.
- Overlay contain memetakan titik/sudut pada ukuran asli. Line P1/P2 dan sisi
  left/right, arsiran, maksimal delapan rectangle tersedia lewat pointer maupun
  form angka. Line dapat digerakkan keyboard satu pixel (Shift sepuluh), Escape
  membatalkan transaksi keyboard; perubahan garis membatalkan konfirmasi sisi.
  Angka tampilan menghilangkan ekor floating point tanpa mengubah draft payload.
- Save mengirim tepat tujuh key dan expected_revision. Play memerlukan ACK canonical
  milik request, image dimensions cocok, serta current fresh dengan identity/revision
  yang sama, PREPARED, ready, geometry stabil, tanpa cleanup/stop_required/pending.
  Template bukan konfirmasi; perubahan draft membatalkan ACK lokal.
- Generation/abort dan render guard membuang reply lama, termasuk saat transport
  mengabaikan abort. Snapshot yang dibaca selama mutation aktif dibuang: sesudah
  settlement dilakukan GET baru, bukan mengurutkan snapshot lama terhadap ACK.
- Cancel sebelum create ACK memakai operation_id yang dipin dan current.upload_operation_id.
  Late ACK tidak meneruskan PUT. DELETE202/cleanup gagal menahan admission; tidak
  membuka sumber B. DELETE tidak overlap. Klik operator berikutnya boleh mencoba
  DELETE sekali; poll dan late ACK tidak mencoba ulang mutation.
- Play response hilang direkonsiliasi melalui GET attempt dengan identity/attempt/op
  yang sama. Tidak ada POST ulang, UUID pengganti otomatis, atau Stop otomatis.
  Pre-CAS refusal tidak dianggap outcome attempt; REFUSED final tidak memicu retry.
  Outcome evicted tetap dapat menghasilkan status unknown, memerlukan pemeriksaan
  operator; UI tidak menyatakan keberhasilan atau membuat operasi baru.
- Upload/probe cleanup meminta upload baru setelah cleanup; preview cleanup menahan
  source/config. Leftover tanpa source ID memakai explicit cleanup/retry, bukan
  DELETE kosong. Geometry unstable tetap menahan Play walaupun analisis IDLE.
- Stop memakai route existing dan source dipertahankan. Dashboard F1/retail, MJPEG,
  serial polling 1000/1000/500/750 ms, abort/epoch dan error aman dipertahankan.
  Detection/domain/evaluation dijoin pada source_session_id status; latest_result
  yang berbeda dari envelope/session ditolak. MJPEG direfresh ketika session_ref
  berubah, bukan memakai query sebagai lease backend.
- HTTPS/localhost dan crypto.randomUUID() wajib untuk mutation privat; unavailable
  menampilkan penjelasan dan menahan mutation tanpa fallback ID. UI tidak menambah
  readiness, gesture, status audio/peringatan lokal, maupun hasil AI sintetis.

### Verifikasi implementer yang dijalankan

Dijalankan serial dari `frontend/`, tanpa server/provider/perangkat:

- `npm test`: **141 passed, 16 file**, 2.09 s pada run final.
- `npm run build`: TypeScript dan Vite **PASS**, 43 module; JS 195.65 kB
  (61.96 kB gzip), CSS 9.06 kB (2.50 kB gzip). Backend suite/pip check tidak diulang
  untuk delta frontend ini; kelulusan backend bukan bukti implementasi frontend.
- `git diff --check`, staging kosong, final newline/trailing whitespace/control
  bytes kandidat, scope path, dan tautan lokal rencana diperiksa saat penutupan.
  Git memberi peringatan normalisasi LF→CRLF; bukan error whitespace.
- Browser Chrome headless membuka **bundle produksi via file://**, fetch API fake,
  HTTP/HTTPS diblokir, resolver eksternal diblokir. Tidak ada app server, video nyata,
  inferensi, provider atau speaker. Alur App nyata create→PUT→JPEG→edit→save→Play→Stop
  lulus; tepat satu POST Play, nol route start/upload legacy, sumber/config tetap
  sesudah Stop, nol exception JS, nol fixture hasil penilaian AI.

| Acceptance visual implementer | Hasil |
|---|---|
| 1440, 1280, 1024, 768, 390, 320 px; height 900 | Lulus; delapan zona, tidak overflow horizontal; form di bawah gambar pada mobile |
| Preview dominan desktop | Rasio lebar gambar terhadap grid >=66% pada 1280/1440 |
| Target kontrol | Tombol/handle yang terlihat >=44 px; form menyediakan alternatif numeric |
| Zoom 200% pada 320 px | Lulus CSS reflow; bukan hanya screenshot diperbesar |
| Keyboard | Fokus/Tab, ArrowRight satu pixel, Escape restore, radio/form numeric |
| Contain / letterbox | Landscape 640×480, portrait 480×640, square 640×640; klik bidang kosong ditolak |
| Kontras teks sampled | Form 15.07:1, input 17.23:1, tombol primary 5.99:1, bantuan 9.33:1 |
| Pemeriksaan visual screenshot | Codex membaca screenshot final 1280 dan 390; seluruh enam tersimpan lokal ignored |

Harness/screenshot sementara berada di `frontend/node_modules/.cache/` (ignored):
`p1d-browser-check.cjs`, `p1d-visual/editor-{width}.png`. Pola canvas sintetis bukan
CCTV dan tidak di-commit. Harness dibangun dari transport CDP F1, memakai profil dan
fixture P1d tersendiri. Fakta visual ini tetap **bukti implementer**, bukan review Claude.

### Cakupan unit/gate dan acceptance yang belum lengkap

- API sources: parameterized seluruh 40 kategori publik, internal write_finished/
  unknown ditolak; bentuk capabilities/current invalid; exact tujuh key; envelope
  UTF-8 terlalu besar ditolak sebelum fetch; UUID tanpa fallback; malformed success
  dibedakan dari network error. Kategori dibuktikan pada adapter; belum setiap
  kombinasi HTTP/category diuji di komposisi browser.
- Geometry: landscape/portrait/square round-trip, letterbox, zero dimensions,
  horizontal reverse left/right, hostile finite/bounds, area/side/count invalid.
- Hook fake/deferred: create ACK tertahan→cancel tanpa PUT; PUT tertahan→DELETE202
  dan late PREPARED tidak memulihkan draft; DELETE gagal→admission tertahan→klik
  retry; dua klik tidak overlap; no-ID stalled→explicit recovery; lost Play ACK
  lookup tanpa POST baru; known pre-CAS refusal; lost save ACK tidak memakai ready
  writer lain; cleanup probe/preview berbeda; geometry sticky; UUID unavailable.
- Existing F1 suite tetap lulus; tambahan session/result A→B, epoch/render guard,
  video image identity/error serta evaluation 503 dengan status identity tersedia.
- Variasi target §9 yang **belum dibuktikan sendiri secara exhaustive**: semua
  pasangan status/category/aksi UI; seluruh gate save/Play/error/finally A→B pada
  App; current berbeda operation saat cancel before ACK; lookup eviction/cap256
  di browser; semua endpoint expiry/TTL/template204; 4096/4097 exact boundary;
  diagonal/vertical/reverse corners melalui pointer; tiap edge border/padding,
  DPR/resize/scroll kombinasi; screen reader dan audit kontras semua state/error/
  overlay; simulasi reduced-motion preference. Tidak diklaim seluruh §9 lulus.

### Batas dan kegagalan selama implementasi

- Snapshot lintas endpoint tidak atomik; hasil hanya membawa session_id. Unique
  UUID produksi diperlukan, bukan dukungan reuse ID pada join hasil. Tidak ada
  endpoint canonical GET; reload/lost save ACK memerlukan edit/confirm/save manual.
- Browser fake tidak membuktikan decoder, video, gesture, audio, provider, akurasi
  model, deployment HTTPS, atau seluruh pekerjaan UI/UX. Audit visual independen
  dan screen reader belum dilakukan.
- Build pertama salah cwd root reference gagal menemukan vite; dikoreksi ke
  frontend tanpa edit root/package. Sandbox esbuild gagal membaca parent directory;
  command diulang dengan izin eksekusi, tanpa instalasi dependency.
- Encoding ellipsis akibat pembacaan Windows default dikoreksi sekali ke UTF-8;
  dua assertion editor (fieldset inherited disabled/label), overflow zoom200, dan
  gate harness sesudah Stop diperbaiki sekali lalu diverifikasi lulus. Gate Stop
  kini menunggu tombol/snapshot siap, bukan sekadar editor sudah dirender.
- Tidak ada stage, commit, push, backend edit atau perubahan dokumen bersama.

### Kandidat source/test final untuk review independen

Daftar ini subset scope §3 yang berubah. Hash memakai Git blob/filter path, tidak
memasukkan dist, node_modules, screenshots, runtime report, atau referensi root.
Rencana UI sendiri adalah satu kandidat tambahan; hash rencana dilaporkan terpisah
agar tidak membuat hash rekursif.

| Path lengkap relatif root | Blob |
|---|---|
| frontend/src/App.tsx | 31b019c73a24d83b3eb4af69eb24fb595f05a941 |
| frontend/src/api/preparedSourceApi.test.ts | a972b41e49ba91a16355acfea111b04fcaf66bbe |
| frontend/src/api/preparedSourceApi.ts | 723d1683ac6066601638b861f5e0c405f9456a8b |
| frontend/src/api/videoApi.ts | 837ea9e6a4b05fab1138e3e26c21150dc6dc2ea2 |
| frontend/src/components/ConfigurationOverlay.test.tsx | fe21aabeef63b64929d768254c583cb6fa431e83 |
| frontend/src/components/ConfigurationOverlay.tsx | 8067b7a6cb054d78c030f519fa147eac22ccf8fe |
| frontend/src/components/PrivateParkingConfiguration.test.tsx | 97e4c6053da62e3913e460aa9421e7ef1b6ca2b4 |
| frontend/src/components/PrivateParkingConfiguration.tsx | a622065f1fae2c8641abb95bb4aa7c122a4bbbf0 |
| frontend/src/components/VideoPreview.test.tsx | c9ffbf3ad631603f2fce7b8e06140a375abf7811 |
| frontend/src/components/VideoPreview.tsx | daac3e8ce4db9ef1841c2b8f92a9774d90cc6e7d |
| frontend/src/components/VideoSourceSelector.tsx | 1cb1c259e49586dbdacbee4a94321ae7e6e26472 |
| frontend/src/geometry/privateParkingGeometry.test.ts | 59ef0d1c81eb4fb080361f2acf93de84ecd7d03d |
| frontend/src/geometry/privateParkingGeometry.ts | 30d16484fb6da1f222f127306127dba696991837 |
| frontend/src/hooks/useCurrentDomain.test.tsx | 962da78d9cade339497899e5b1420748c43ec503 |
| frontend/src/hooks/useCurrentDomain.ts | f96dab78f9e735d9618255e4322bb3d735827fbb |
| frontend/src/hooks/useCurrentEvaluation.test.tsx | 498f98b8a2e076726fefd3cd7cb2c9860afbb1fd |
| frontend/src/hooks/useCurrentEvaluation.ts | dc1807e09fad1a81eb05f40a91481b9a07a874db |
| frontend/src/hooks/useLatestDetection.test.tsx | ce832283496faa66ef18cb1d6aa950c38041e3fa |
| frontend/src/hooks/useLatestDetection.ts | cca330255e99ba1af4e6299473c89dc8179627fe |
| frontend/src/hooks/useSourcePreparation.test.tsx | 036cd6106a5b2c4689c13d6051b35694c66ea428 |
| frontend/src/hooks/useSourcePreparation.ts | f6a623f2accaa6e19bf47d718d3bf6e4f0e8bab9 |
| frontend/src/index.css | cf7c5b014087fc528657595c32b8a2745a440fdc |
| frontend/src/testUtils/appFetchStub.ts | 8be66c62aedfb8678622c7592ace5c6e78eb3748 |
| frontend/src/testUtils/controlledFetch.ts | 266625f924c25b889b15d75f1371b461d952dd4d |


## 21. Delta perbaikan tujuh temuan — awaiting re-review Claude

Codex, 2026-10-12. Owner meneruskan tujuh temuan dan mengotorisasi perbaikannya
pada scope P1d. **Ini hasil implementer, bukan verdict re-review Claude.** HEAD
`eb6a3ac9b093a99fd9e151261f984383b5b95c01`, staging kosong. §20/manifest lamanya
adalah bukti run awal 141 test; hasil dan manifest di bawah menggantikan status
perbaikan serta hash kandidat terkini. Backend, AGENTS.md, PLAN.md dan dokumen
bersama tetap tidak diedit oleh Codex.

### Keputusan owner/PM yang diterapkan

- Kegagalan capabilities tidak memilih legacy; pembacaan ulang serial boleh
  memulihkan mode yang benar. Backoff read dibatasi, abort dan generation tetap.
- 410 source_gone/outcome_evicted menyelesaikan lookup lama. Current fresh yang
  membuktikan sumber **dengan identitas yang sudah diketahui** hilang/berganti
  menyelesaikan intent lokal save/Play lama. Create tanpa ID dikecualikan: 204,
  rejection atau abort tidak menyelesaikan unknown/cancel (koreksi R6 owner). Tidak mengubah observasi VideoService atau status analisis.
- 404 lookup menyatakan hasil belum diketahui, menghentikan lookup otomatis, dan
  menawarkan GET manual serta replay **permintaan sama** (source, generation,
  revision, attempt, operation_id). Tidak membuat UUID/Play baru otomatis.
- Penantian cancel tanpa source ID perlu batas dan pemulihan manual. Melepas
  penantian lokal bukan bukti pembatalan maupun cleanup backend sudah berhasil.
- UUID guard dipisahkan dari Stop/DELETE/cleanup. Tidak ada fallback UUID.
- Field error hanya nama kontrak yang dikenal; pesan tidak mencetak field/input
  mentah. Field umum ditangani pada form/status; geometri pada input terkait.

**Pilihan implementasi, bukan angka SLA/retensi backend atau keputusan PM baru:**
capabilities sukses dibaca setiap 15 s; kegagalan memakai 1/2/4/8 s lalu ceiling
15 s. Read berikutnya dijadwalkan setelah read sebelumnya settled; unmount/refresh
membatalkan dan menolak reply lama. Sesuai koreksi R6 owner, cancel tanpa ID atau
create ACK hilang tanpa cancel menunggu **30 s**, lalu paused. Unknown/cancelling
tetap menahan upload sampai pelepasan manual; bukan otomatis EMPTY setelah settled.
Timer ini hanya membatasi UX menunggu otomatis, tidak memperpanjang TTL backend,
tidak menganggap deadline cleanup sukses, dan tidak mengirim mutation ulang.

### Delta perilaku dan regresi

| Temuan / reproduksi dari owner | Perbaikan dan bukti fake |
|---|---|
| C1 capabilities gagal sekali | null mode sampai response valid; failure→legacy pulih setelah 1 s, tanpa memilih legacy berdasarkan kegagalan; serial held request; stale reply setelah refresh tidak mengganti mode; backoff sampai 15 s |
| Unknown terminal / eviction / expiry | lookup 410/expiry/cap refusal mengakhiri unknown dan membatalkan ACK lokal; source hilang menghapus intent save; B ready tidak menjadi ACK konfigurasi lokal, tidak membuka Play |
| Lookup404 | lookup otomatis berhenti setelah satu 404; current read tetap serial untuk ownership, bukan retry Play; tombol Periksa operasi lagi / Kirim ulang permintaan yang sama manual; browser dan unit membuktikan tuple/payload replay identik |
| Cancel/create tanpa ACK (koreksi R6) | fetch reject langsung saat abort dan current204 tetap menahan intent; sesudah30s muncul recovery tanpa membuka B; Periksa operasi lagi membuka jendela pemeriksaan baru; pelepasan UI manual menginvalidasi executor/intent/generation, bukan pekerjaan backend; late create sesudah reset/unmount tidak PUT/ubah B; sumber A yang terlihat kembali memakai identitas asli tanpa DELETE otomatis |
| Secure context | unggah/save/Play baru ditahan tanpa randomUUID; hook membuktikan Stop/cleanup/DELETE tetap dapat dipanggil; actual App RUNNING membuktikan Stop enabled; browser fake memanggil ketiganya tanpa UUID |
| Server error field | allowlist field kontrak; line.p2 / inside / zones[0].rect memberi aria-invalid dan fokus input; configuration/revision/attempt memberi penjelasan dan fokus form; arbitrary field dibuang; marker mentah tidak tampil |
| Keterbacaan / stale action | UploadIntent/PlayIntent/View eksplisit, executor per action; hook dan test dipecah menjadi blok terbaca, indent dua spasi dan tanpa semicolon yang tidak diperlukan; hook F1 tetap seperti delta sebelumnya |

Regresi **App A→B** menahan response JPEG A, mengganti current ke B, memulai
preview B, lalu melepaskan A. A tidak membuat object URL, tidak melepas pending B,
dan tidak mengubah gambar B; B ready tidak membuka Play tanpa ACK operator.
Selain itu current pergantian/hilang menginvalidasi action yang masih aktif,
bukan hanya reply yang sudah settled. Finally lama hanya dapat melepas executor
milik action itu. Store/session status backend tetap authority masing-masing.

Editor/simpan ditahan selama unknown Play; kontrol lookup/replay/cleanup manual
berada di luar fieldset. Terminal evicted/cap/expiry tidak mengirim Play baru.
Pemulihan lokal tidak mengirim Stop otomatis atau menyatakan resource server bersih.

### Bukti implementer terkini

Dijalankan serial dari frontend, setelah source final:

- `npm test`: **170 passed / 16 file**, 2.47 s. Tambahan 29 test terhadap run awal
  141, termasuk kasus C1 dan keluarga unknown/cancel/404/secure-field yang diteruskan
  owner. Tidak mengklaim mengulang skrip atau mutasi reviewer yang tidak tersedia.
- `npm run build`: **PASS** TypeScript/Vite, 43 module; JS 201.25 kB / gzip63.37 kB,
  CSS 9.06 kB / gzip2.50 kB. Tidak menambah dependency/package.
- Browser fake bundle final: enam viewport 1440/1280/1024/768/390/320 ×900, delapan
  zona, zoom200%, keyboard pixel/Escape, contain landscape/portrait/square,
  letterbox rejection, kontras sampled seperti §20 — **PASS**. Tidak ada JS exception.
- Tambahan visual 1280/390/320 pada server field error dan controls404: tidak
  overflow, recovery buttons >=44 px; Codex membaca screenshot recovery404-320.
  Error field server memberi fokus zones[1].rect, marker raw tidak tampil.
- Browser: Play ACK hilang sebelum fake CAS → GET404 tepat sekali → satu klik
  replay dengan body **identik** → STARTED. Dua POST total adalah intent awal dan
  replay operator, bukan otomatis. Stop retained source tetap; HTTP LAN disimulasikan
  dengan randomUUID unavailable: unggah baru tertahan, explicit Stop/recovery/DELETE
  berhasil. Startup capabilities sengaja 503 sekali lalu pulih.
- Harness/screenshots tetap ignored di node_modules/.cache, file:// production
  bundle, API fake dan synthetic pattern; HTTP/HTTPS/resolver eksternal diblokir,
  tanpa server/provider/video/inferensi/audio/perangkat. HTTP LAN ini bukan
  pengujian deployment melalui server LAN nyata.
- Scope, Git diff/whitespace, newline/control bytes, tautan lokal rencana dan staging
  diperiksa. Tidak ada backend delta; perubahan Claude/refs lama dipertahankan.

### Catatan pemeriksaan dan batas

- Formatter sementara memakai TypeScript yang sudah terpasang, tanpa instalasi.
  Guard pertama membandingkan emitted text (beda layout); satu koreksi AST masih
  memasukkan SourceFile.text sehingga memberi false mismatch. Setelah membaca
  [referensi resmi Compiler API](https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API),
  pembanding mengabaikan source text/positions dan membandingkan node/literal emitted
  AST; identik sebelum/sesudah formatting. Failed guard berhenti sebelum menulis file.
- Satu perintah build inspeksi salah cwd root reference gagal menemukan vite;
  tidak ada root/source/package yang diubah. Build di cwd frontend lulus.
- Read capabilities yang tidak pernah settled tetap menahan read berikutnya (tidak
  melakukan overlap request). Abort/generation melindungi unmount/refresh; belum
  ada watchdog transport umum. Backoff membatasi interval, bukan total jumlah reads.
- Reset lokal bisa diikuti operasi server yang selesai kemudian. Poll current
  tetap authority/admission backend; late reply lokal tidak memulai PUT atau
  mengaktifkan analisis. Tidak ada penghapusan server otomatis setelah reset/unmount.
- Status/detection/domain/evaluation tidak digabung secara atomik; session_id UUID
  unik produksi diperlukan seperti kontrak beku. Tidak mengklaim reusable ID join.
- Full variasi acceptance §9, audit screen reader/kontras semua state, deployment
  LAN/HTTPS, decoder/provider/perangkat nyata masih batas §20. Angka test/browser
  ini bukti Codex implementer; menunggu re-review independen Claude.

### Manifest kandidat delta sebelum R6 (historis)

25 source/test + rencana ini = **26 kandidat path**, seluruhnya subset scope §3.
Manifest §20 adalah versi awal; tabel §21 ini versi sebelum R6. Gunakan §22
untuk hash final re-review sesudah koreksi R6. Rencana hash
terpisah pada laporan agar tidak rekursif. Tidak memasukkan dist/node_modules atau
screenshots/reports/refs root.

| Path lengkap relatif root | Blob |
|---|---|
| frontend/src/App.test.tsx | 60be77802bb48bd0e8ed45532c105cb34f483e2f |
| frontend/src/App.tsx | 5c34cd5f11660cc2f2254fda5286258b14bfc4fa |
| frontend/src/api/preparedSourceApi.test.ts | 6500c221c804f43a668c33dbaa8fc073e734ce65 |
| frontend/src/api/preparedSourceApi.ts | 25273d74b5b61bcf9a979612fe0a2911287febb4 |
| frontend/src/api/videoApi.ts | 837ea9e6a4b05fab1138e3e26c21150dc6dc2ea2 |
| frontend/src/components/ConfigurationOverlay.test.tsx | fe21aabeef63b64929d768254c583cb6fa431e83 |
| frontend/src/components/ConfigurationOverlay.tsx | 8067b7a6cb054d78c030f519fa147eac22ccf8fe |
| frontend/src/components/PrivateParkingConfiguration.test.tsx | b7254856fbd1f740b06e66df2467f11fbf3f30af |
| frontend/src/components/PrivateParkingConfiguration.tsx | 00e4a7e19a2a349270ed455ec85533e04c532f90 |
| frontend/src/components/VideoPreview.test.tsx | c9ffbf3ad631603f2fce7b8e06140a375abf7811 |
| frontend/src/components/VideoPreview.tsx | daac3e8ce4db9ef1841c2b8f92a9774d90cc6e7d |
| frontend/src/components/VideoSourceSelector.tsx | 1cb1c259e49586dbdacbee4a94321ae7e6e26472 |
| frontend/src/geometry/privateParkingGeometry.test.ts | 59ef0d1c81eb4fb080361f2acf93de84ecd7d03d |
| frontend/src/geometry/privateParkingGeometry.ts | 30d16484fb6da1f222f127306127dba696991837 |
| frontend/src/hooks/useCurrentDomain.test.tsx | 962da78d9cade339497899e5b1420748c43ec503 |
| frontend/src/hooks/useCurrentDomain.ts | f96dab78f9e735d9618255e4322bb3d735827fbb |
| frontend/src/hooks/useCurrentEvaluation.test.tsx | 498f98b8a2e076726fefd3cd7cb2c9860afbb1fd |
| frontend/src/hooks/useCurrentEvaluation.ts | dc1807e09fad1a81eb05f40a91481b9a07a874db |
| frontend/src/hooks/useLatestDetection.test.tsx | ce832283496faa66ef18cb1d6aa950c38041e3fa |
| frontend/src/hooks/useLatestDetection.ts | cca330255e99ba1af4e6299473c89dc8179627fe |
| frontend/src/hooks/useSourcePreparation.test.tsx | 94ae36189ba2b1016c33941fa39b162562e8e274 |
| frontend/src/hooks/useSourcePreparation.ts | bc55dee2a1b52176c8ef9a35e06a0bc55fcde029 |
| frontend/src/index.css | cf7c5b014087fc528657595c32b8a2745a440fdc |
| frontend/src/testUtils/appFetchStub.ts | 8be66c62aedfb8678622c7592ace5c6e78eb3748 |
| frontend/src/testUtils/controlledFetch.ts | 266625f924c25b889b15d75f1371b461d952dd4d |


## 22. Koreksi R6 — penahanan create tanpa ID

Codex, 2026-10-12. Owner mengotorisasi delta R6 dalam P1d. **Awaiting re-review
Claude**, bukan PASS reviewer. HEAD tetap `eb6a3ac9b093a99fd9e151261f984383b5b95c01`,
staging kosong. Baseline delta rencana §21: `81f1f6556c8a402722f0724668940a33ad671290`.
Tidak mengubah backend, AGENTS.md, PLAN.md atau dokumen bersama.

### Perbaikan yang dilakukan

- Hapus pelepasan no-ID intent berdasarkan settled+current204. Unknown create atau
  cancel tetap menahan upload walaupun fetch reject langsung saat abort dan finally
  sudah selesai. Jendela 30 s tidak reset oleh current polling dan bukan TTL backend.
- Timer berlaku untuk cancel dan lost create ACK tanpa cancel. Setelah jendela habis
  UI menawarkan Periksa operasi lagi / Lepaskan penantian lokal; unknown/cancelling
  tetap ada. Tidak ada POST B otomatis. Pesan eksplisit menyatakan pelepasan hanya
  mereset UI, tidak membatalkan pekerjaan backend.
- Pelepasan manual membuang token executor/epoch/intent A. Prepare A memeriksa
  mounted serta object intent sebelum menyimpan ACK/PUT; finally memeriksa token
  executor. A terlambat tidak dapat menyentuh pending/identitas/file B.
- Current memakai identitas backend sebenarnya. Sumber dari operation A yang telah
  dilepas mendapat pesan ditemukan kembali, bukan konfirmasi upload B; draft/ACK
  operator B dibuang pada pergantian sumber. Tidak menghapus A otomatis.
- JSX PrivateParkingConfiguration dipecah menjadi atribut/node/handler yang lebih
  mudah dibaca. Perubahan perilaku di komponen hanya aksi pelepasan untuk unknown
  create tanpa cancel, sesuai R6. Layout/label editor/dashboard dan API tetap.
- §7, P1d-08 dan §21 diselaraskan; manifest sebelumnya tetap historis, tabel di
  bawah adalah kandidat terkini. Angka 30 s mengikuti instruksi R6 owner, bukan SLA
  atau bukti cleanup deadline backend.

### Regresi dan verifikasi implementer

- `npm test`: **175 passed / 16 file**, 2.22 s; lima test tambahan terhadap §21.
  Fake clock/gates, tanpa runtime/provider/perangkat.
- Cancel create menggunakan fetch yang langsung **reject AbortError** ketika signal
  abort. Setelah finally selesai dan current204 berulang, cancelling tetap true,
  POST B ditolak; pada 29.999 s belum paused, 30.000 s paused tanpa melepas intent.
  Setelah 30 s tambahan tetap tepat satu POST A, tanpa B otomatis.
- Lost ACK tanpa cancel menggunakan immediate fetch rejection. Current204 berulang
  tidak menghapus unknown create. Sesudah jendela, state tetap unknown dan tombol
  pelepasan tersedia; B masih ditahan.
- Manual reset→create B→PUT B tertahan→ACK A terlambat: identitas tetap B, pending B
  tidak dilepas, **nol PUT A**. Setelah B selesai, current A muncul sebagai sumber
  ditemukan kembali dengan source_id/generation/operation_id A; draft B cleared,
  Play tetap disabled, nol DELETE otomatis. Transport gate kasus ACK terlambat
  sengaja mengabaikan abort: Promise yang sudah reject tidak dapat resolve lagi;
  test abort-reject dan late-ACK adalah dua interleaving valid yang berbeda.
- Dua test actual editor (cancelled/non-cancelled) membuktikan input unggah disabled
  pada 204 dan sesudah30s, tombol pelepasan tepat, penjelasan backend terlihat, dan
  klik manual tidak mengirim POST baru. Admission baru hanya setelah fresh read;
  backend tetap mengendalikan slot/cleanup ketika operasi A selesai kemudian.
- Assertion pertama untuk B memakai exact object dan gagal karena respons create
  juga memiliki state AWAITING_FILE; dikoreksi sekali menjadi matching source_id/
  generation (semua assertion token/pending/zeroPUT tetap), lalu suite lulus.
- `npm run build`: **PASS** TypeScript/Vite, 43 module; JS 202.03 kB (gzip63.55 kB),
  CSS9.06 kB (gzip2.50 kB).
- Browser fake bundle final: enam viewport, 200% zoom, keyboard, contain/letterbox,
  delapan zona, sampled contrast, server field, manual404 replay, HTTP LAN recovery
  dan Stop retained source — PASS. Tambahan R6 cancel-abort-reject dan ACK hilang
  tanpa cancel pada320 px: input tetap tertahan, pesan/backend dan tombol manual
  terlihat, tidak overflow, pelepasan tidak POST baru. Codex membaca screenshot
  r6-false-320. Timer browser visual **dikompresi 30s→300ms oleh harness fake**;
  batas tepat30s hanya diklaim dari fake-clock unit, bukan wall-clock browser.
- Semua browser memakai API fake, file:// bundle, synthetic pattern, jaringan
  HTTP/HTTPS/resolver diblokir, tanpa server/inferensi/video/audio/provider nyata.
  Harness/screenshot di node_modules/.cache ignored; tidak masuk kandidat Git.
- Scope, hash, whitespace/final newline/control bytes, tautan dan staging diperiksa.
  Batas cross-endpoint identity, screen reader/kontras exhaustive, decoder/deployment
  nyata serta full variasi §9 tetap seperti §20–§21. Bukti ini dari implementer,
  bukan review independen Claude.

### Kandidat terkini

Delta R6 hanya `useSourcePreparation.ts`, test hooknya,
`PrivateParkingConfiguration.tsx`, test komponennya, dan rencana ini. Total kandidat
P1d tetap **25 source/test + 1 rencana = 26 path**. Hash rencana dilaporkan terpisah;
tabel lengkap di bawah menggantikan manifest §21 untuk re-review.

| Path lengkap relatif root | Blob |
|---|---|
| frontend/src/App.test.tsx | 60be77802bb48bd0e8ed45532c105cb34f483e2f |
| frontend/src/App.tsx | 5c34cd5f11660cc2f2254fda5286258b14bfc4fa |
| frontend/src/api/preparedSourceApi.test.ts | 6500c221c804f43a668c33dbaa8fc073e734ce65 |
| frontend/src/api/preparedSourceApi.ts | 25273d74b5b61bcf9a979612fe0a2911287febb4 |
| frontend/src/api/videoApi.ts | 837ea9e6a4b05fab1138e3e26c21150dc6dc2ea2 |
| frontend/src/components/ConfigurationOverlay.test.tsx | fe21aabeef63b64929d768254c583cb6fa431e83 |
| frontend/src/components/ConfigurationOverlay.tsx | 8067b7a6cb054d78c030f519fa147eac22ccf8fe |
| frontend/src/components/PrivateParkingConfiguration.test.tsx | 6c4ccfe24e51b8f41505c555072ee8d441e9e37f |
| frontend/src/components/PrivateParkingConfiguration.tsx | f49c9a42362cd6bf75e90f87d6d22c2114505d17 |
| frontend/src/components/VideoPreview.test.tsx | c9ffbf3ad631603f2fce7b8e06140a375abf7811 |
| frontend/src/components/VideoPreview.tsx | daac3e8ce4db9ef1841c2b8f92a9774d90cc6e7d |
| frontend/src/components/VideoSourceSelector.tsx | 1cb1c259e49586dbdacbee4a94321ae7e6e26472 |
| frontend/src/geometry/privateParkingGeometry.test.ts | 59ef0d1c81eb4fb080361f2acf93de84ecd7d03d |
| frontend/src/geometry/privateParkingGeometry.ts | 30d16484fb6da1f222f127306127dba696991837 |
| frontend/src/hooks/useCurrentDomain.test.tsx | 962da78d9cade339497899e5b1420748c43ec503 |
| frontend/src/hooks/useCurrentDomain.ts | f96dab78f9e735d9618255e4322bb3d735827fbb |
| frontend/src/hooks/useCurrentEvaluation.test.tsx | 498f98b8a2e076726fefd3cd7cb2c9860afbb1fd |
| frontend/src/hooks/useCurrentEvaluation.ts | dc1807e09fad1a81eb05f40a91481b9a07a874db |
| frontend/src/hooks/useLatestDetection.test.tsx | ce832283496faa66ef18cb1d6aa950c38041e3fa |
| frontend/src/hooks/useLatestDetection.ts | cca330255e99ba1af4e6299473c89dc8179627fe |
| frontend/src/hooks/useSourcePreparation.test.tsx | 893be6ba3816fa95209013902af6f0913efced15 |
| frontend/src/hooks/useSourcePreparation.ts | 7ae6784ea0ac96212f4eb8471c3605510cd5505a |
| frontend/src/index.css | cf7c5b014087fc528657595c32b8a2745a440fdc |
| frontend/src/testUtils/appFetchStub.ts | 8be66c62aedfb8678622c7592ace5c6e78eb3748 |
| frontend/src/testUtils/controlledFetch.ts | 266625f924c25b889b15d75f1371b461d952dd4d |


## 23. Penutupan P1d — completed, frontend verified with API fakes

Owner/PM menyampaikan hasil review independen Claude dan mengotorisasi penutupan,
commit tepat 26 path manifest P1d (25 source/test §22 + rencana UI ini), fetch dan
push normal tanpa force. Commit message yang diotorisasi:
`feat: add private parking configuration workflow`.

**Verdict: PASS WITH LIMITATIONS — review Claude, disampaikan melalui owner/PM.**
Claude melaporkan **175 test pada 16 file dan build lulus**. Atribusi ini terpisah
dari hasil implementer Codex §20–§22; tidak mengklaim angka reviewer sebagai run
Codex baru atau sebaliknya. Source/test seluruhnya dicocokkan ulang dengan 25 blob
manifest §22 dan tetap sama. Delta penutupan hanya rencana ini; test/build tidak
diulang karena tidak ada perubahan perilaku.

**Status: completed — frontend verified with API fakes.** Ini penutupan frontend
P1d, bukan pernyataan bahwa seluruh private parking atau pengamanan telah
tervalidasi di lapangan.

Bukti visual enam viewport, zoom200%, keyboard/letterboxing/eight zones, sampled
contrast, controls404/error dan pemulihan R6 adalah **bukti implementer Codex**.
Tidak mengatribusi browser/visual tersebut kepada Claude. Timer browser R6
dikompresi **30s→300ms** hanya untuk visual; batas tepat30s diperiksa fake-clock
unit. Audit aksesibilitas belum lengkap: belum audit screen reader atau kontras
seluruh state/overlay/error. Belum ada pengujian video nyata, decoder/OpenCV nyata,
webcam/CCTV, speaker/audio atau perangkat; juga tanpa provider/server nyata.
API fakes tidak membuktikan akurasi model, gesture, keamanan field, atau deployment
HTTPS/LAN nyata. Batas identitas lintas endpoint/UUID unik dan geometri same-size
serta keterbatasan backend lain tetap sebagaimana kontrak P1c, tidak ditutup P1d.

Preflight penutupan: HEAD `eb6a3ac9b093a99fd9e151261f984383b5b95c01`, branch `main`,
staging kosong. Perubahan agen lain/referensi tetap dipertahankan dan di luar
commit. Hash rencana penutupan, commit final dan verifikasi remote dilaporkan pada
handoff owner; tidak dimasukkan ke file yang sama agar tidak membuat hash rekursif.


## 24. Delta editor sesudah uji awal owner — scope dan bug

Owner mengotorisasi perbaikan lima temuan editor pada baseline P1d committed
`67a04fb83efc3e39425a23c679673b9c83b7dc6c`. Staging kosong; source frontend bersih
saat preflight. **Reviewed: PASS WITH LIMITATIONS — Claude melalui owner/PM.** Owner telah
mengotorisasi penutupan, stage tepat delapan path, commit dan push normal;
source/test delta tidak berubah setelah review.
Status completed/review §23 hanya berlaku pada versi shipped, bukan delta ini.

| Temuan owner dan pembacaan source | Penyebab / perbaikan yang dituju |
|---|---|
| Handle garis menghalangi klik kedua di mode zona | Circle handle selalu stopPropagation dan key handler tidak memeriksa tool; hanya line valid pada mode line boleh interaktif |
| Handle di0,0 saat garis belum diisi | Default p1/p2 zeros dirender sebagai handle; sembunyikan handle/arsiran ketika line invalid |
| Sudut pertama tidak terlihat, menjadi stale sesudah edit form | first hanya ref, hint hanya SVG title; state visual marker + instruksi terlihat, cancel/Escape dan context guard draft/tool/preview |
| Klik kedua invalid menambahkan area nol/kecil | Pointer append tanpa validasi luas; tolak sebelum onChange dan jelaskan pemilihan ulang dua sudut |
| Numeric invalid tidak ditandai langsung; fokus selalu P1 | generic geometryError baru pada submit; structured local issues, aria-invalid/describedby, pesan terkait, fokus error pertama, nol request konfigurasi |

Nilai numeric di source sekarang tidak disortir pada onChange; ketentuan owner
untuk mempertahankan Kiri/Kanan/Atas/Bawah tetap dipertahankan dan diuji secara
langsung. Sorting dua sudut pointer tetap boleh untuk rectangle, tetapi tidak
berlaku pada input form. Rectangle invalid/kecil tidak boleh digambar seolah valid.

Scope: ConfigurationOverlay.tsx, PrivateParkingConfiguration.tsx dan dua testnya.
Adjacent helper/CSS diperlukan dan dicatat: privateParkingGeometry.ts + testnya
untuk satu validasi rectangle/field yang sama pada overlay/form/submit; index.css
untuk petunjuk pemilihan di luar gambar (tidak mengubah letterboxing) dan border
field invalid. Tidak mengubah parser/backend, hook request/lifecycle, stored
konfigurasi owner, API, Play, prompt/gesture atau dokumen bersama.

Own Vite live session dihentikan sebelum edit agar owner tidak terkena HMR delta
belum direview. Backend tidak dihentikan/direstart. Browser verifikasi terpisah
memakai bundle produksi + API fake; tidak ada request upload/config/DELETE/Stop/
Play ke backend nyata pada delta ini. Tidak memakai gambar CCTV nyata dalam bukti.

### 24.1 Hasil implementer dan batas bukti

Lima temuan owner ditangani: handle hanya interaktif pada alat garis valid;
marker sudut pertama dan instruksi terlihat; Escape/Batal dan perubahan konteks
membersihkan pilihan; sudut kedua invalid ditolak sebelum append; nilai form
terbalik tetap diketik, diberi aria-invalid/pesan dan rectangle tidak digambar.
Simpan menolak lokal tanpa request dan memfokuskan field invalid pertama.

Browser fake menemukan dua delta adjacent yang juga diperbaiki:
- Pembulatan pointer pecahan CSS pixel membuat klik pojok tepat dianggap di luar
  gambar. pointerPoint memakai toleransi 0.00001 CSS pixel lalu clamp batas;
  letterbox di luar toleransi dan koordinat nonfinite tetap ditolak. Regresi unit
  memakai ukuran SVG pecahan aktual browser.
- Escape panel dahulu menginvalidasi transaksi keyboard overlay. Panel kini
  menyerahkan Escape di overlay kepada overlay; Escape dari form tetap menghapus
  sudut tertunda. Regresi parent dan browser membuktikan undo nilai keyboard.

Verifikasi Codex (implementer, bukan review independen):
- `npm test`: **192 passed, 16 files**, semua fake; 17 tambahan dari baseline175.
- `npm run build`: TypeScript/Vite lulus, 43 modules; bundle terakhir
  `index-Bm_n6aey.js`, CSS `index-DGPeNoLd.css`.
- Chrome headless bundle produksi + API fake lulus true pointer hit-testing:
  klik kedua di pojok asli/penanda P1 mode zona, area nol/subminimum, nilai
  terbalik, fokus Simpan tanpa request, reset form/Escape/Batal, keyboard panah
  satu pixel dan Escape undo. Enam viewport1440/1280/1024/768/390/320 dan zoom200%
  tanpa overflow horizontal, delapan zona terlihat valid. Gambar sintetis640x480
  di container16:9 menguji letterboxing; koordinat mengikuti gambar asli.
- Screenshot sintetis1280 dan390 dibaca implementer; tidak ada CCTV dalam bukti.
  Seluruh request harness GET saja; nol upload/config/DELETE/Stop/Play, provider,
  detector/video nyata, speaker atau perangkat. Chrome sendiri ditutup finally.
- Tidak mengulang backend suite karena backend tidak berubah. Build terakhir
  mendahului tambahan test parent saja, source produksi tetap identik.

Batas: API fake dan screenshot sintetis bukan bukti decoder/video/perangkat atau
akurasi model. Audit aksesibilitas menyeluruh/screen reader dan kontras seluruh
state belum dilakukan; delta menguji aria-invalid/describedby, fokus dan keyboard.
Tidak mengubah geometri tersimpan owner. Hasil implementer di atas tetap
terpisah dari review Claude melalui owner/PM pada catatan penutupan berikut.
Scope tepat tujuh source/test/CSS berikut dan rencana ini; perubahan
dokumen/referensi agen lain dipertahankan.

### 24.2 Manifest delta untuk re-review

| Path | Git blob dari disk |
|---|---|
| `frontend/src/components/ConfigurationOverlay.tsx` | `0a6f540e006b9a2027ddc5fe375602e15efcd6a8` |
| `frontend/src/components/ConfigurationOverlay.test.tsx` | `463d612889b57b3c7a47fcfcc14b728a2962f356` |
| `frontend/src/components/PrivateParkingConfiguration.tsx` | `f737e41571bb8d433b4af6944ccb98652864bc51` |
| `frontend/src/components/PrivateParkingConfiguration.test.tsx` | `cc0e67da4ee140d1cca382a03effe456199ad4f8` |
| `frontend/src/geometry/privateParkingGeometry.ts` | `017ab8904dfe60ef7a0f95f0c81fe248057deb70` |
| `frontend/src/geometry/privateParkingGeometry.test.ts` | `805ad34d05daefdd656a290348df428f0a47cec1` |
| `frontend/src/index.css` | `60788166696fc7da3ed139d8e0aad7adde83e866` |

Hash rencana dilaporkan pada handoff agar tidak membuat hash rekursif.

### 24.3 Penutupan review melalui owner/PM

Owner/PM menyampaikan verdict independen Claude **PASS WITH LIMITATIONS** dan
mengotorisasi commit `fix: prevent parking zone editor pointer conflicts`, lalu
push normal tanpa force. Penutupan hanya mengubah rencana; ketujuh hash
source/test/CSS dicocokkan ulang dari disk dan identik dengan manifest §24.2.
Suite tidak diulang karena tidak ada perubahan perilaku. Bukti implementer tetap
192 test/16 file, build lulus, dan browser fake sebagaimana §24.1.

Rincian hasil test/build, reproduksi browser reviewer dan lima temuan UX rendah
masih menunggu transkripsi laporan Claude dari owner/PM. Temuan tersebut adalah
pekerjaan lanjutan; tidak memperluas perbaikan/source pada delta penutupan ini.
Batas visual/accessibility/perangkat pada §24.1 tetap berlaku dan tidak ditutup
oleh verdict ini. Owner kemudian menginstruksikan penyelesaian commit/push
lebih dahulu; rincian kelima temuan tetap tindak lanjut pencatatan, tanpa
perubahan source. **Completed — editor verified with API fakes, independently
reviewed PASS WITH LIMITATIONS melalui owner/PM.** Tidak menyatakan validasi
seluruh private parking di lapangan.
