# F1 — UI pemantauan untuk operator UMKM

## 1. Status, baseline, dan tujuan

Status: **completed — scope F1**, implemented dan independently reviewed.
Verdict implementasi: **PASS WITH LIMITATIONS**, tanpa blocker
(review Claude, disampaikan melalui owner). Owner mengotorisasi penutupan dan
commit F1 setelah commit T3d; verifikasi penutupan selesai pada 2026-10-11.
Ini bukan penyelesaian seluruh pekerjaan UI/UX atau audit visual independen.
Disusun Codex dari pembacaan source pada 2026-10-11; baseline
`d075207a189a2f7003ef33aac2af8ffbd0f1f4d1`.
Instruksi awal owner mengizinkan rencana saja. Instruksi lanjutan pada 2026-10-11
mengotorisasi implementasi dalam scope setelah review desain Claude. Review itu
diterima melalui owner (PASS WITH LIMITATIONS); koreksi di bawah menutup temuan
desain tanpa perubahan backend atau perluasan scope. Tidak ada runtime/perangkat
nyata yang dijalankan.

Tujuan: operator rumah, kost, atau toko kecil segera melihat video, menemukan
tombol mulai/hentikan, dan memahami penilaian AI tanpa membaca istilah pipeline.
Preview menjadi pusat layar; evaluasi menjadi informasi pendamping yang mudah
ditinjau. Hasil AI tidak menjadi bukti pelanggaran atau identitas seseorang.

Konteks dibaca: [AGENTS.md](../../../AGENTS.md),
[FRONTEND.md](../../FRONTEND.md), [DESIGN.md](../../DESIGN.md),
[product specs](../../product-specs/index.md),
[private parking](../../design-docs/private-parking-mode.md),
[ARCHITECTURE.md](../../../ARCHITECTURE.md), [SECURITY.md](../../SECURITY.md),
[aturan rencana](../../PLANS.md), source frontend dan handler API terkait.

### Referensi visual dan perbedaan dokumentasi

- Owner memberikan `C:\Users\zenin\Videos\2026-08-27 15-47-21.mp4` saat penyusunan
  rencana. File tersedia: 1920×1080, 60 fps, durasi sekitar 164 detik. Codex membaca
  metadata dan memeriksa potongan header UI pada sekitar detik 2, bukan menonton
  seluruh rekaman; tidak ada inferensi, pemutaran audio atau request provider.
  Cuplikan menunjukkan UI gelap dengan toolbar unggah/webcam dekat jendela preview
  di kiri, panel analisis di kanan, serta indikator ROI/statistik dari proyek lama.
- Yang dipakai sebagai acuan: preview utama, toolbar ringkas, panel pendamping,
  warna gelap dengan aksi biru. Indikator ROI/statistik proyek lama tidak dipindahkan
  ke F1 karena bukan kemampuan API saat ini. Detail bagian lain rekaman belum
  ditinjau; rencana tidak mengklaim meniru seluruh layar/alur lama.
- Tiga video CCTV di root dan screenshot lainnya tidak dipakai sebagai referensi
  tambahan. Media tidak disalin ke rencana/Git; cache gambar untuk inspeksi header
  dibersihkan setelah pemeriksaan. Tidak ada hasil evaluasi/video pribadi yang
  dimasukkan sebagai data contoh F1.
- Product specs dan dokumen private parking masih menyebut T3 sebagai draft/core
  only. Source pada baseline sudah memiliki wiring privat opt-in, tetapi dispatch
  privat masih nonaktif dan API frontend belum mengekspos mode/peringatan lokal/
  otorisasi. Gap dokumentasi dicatat di sini; F1 tidak mengedit dokumen bersama dan
  tidak memakai status dokumentasi lama untuk mengarang kemampuan UI.

## 2. Scope file

**Pekerjaan awal:** hanya file rencana baru ini. Implementasi berikutnya sudah
diotorisasi owner pada 2026-10-11 dalam scope di bawah.

**Scope implementasi yang diotorisasi:**

| Path dari root repo | Perubahan yang direncanakan |
|---|---|
| `frontend/src/App.tsx` | Hierarki layout; preview dan evaluasi sejajar; ringkas status; pertahankan orchestration lifecycle |
| `frontend/src/index.css` | Token warna/typography, grid responsif, preview dominan, fokus keyboard, detail yang lebih tenang |
| `frontend/src/components/VideoPreview.tsx` | Frame preview responsif dan empty/transitional copy; pertahankan mounting stream |
| `frontend/src/components/VideoSourceSelector.tsx` | Kontrol sumber dan aksi ringkas; copy upload yang jujur; error yang membantu |
| `frontend/src/components/VideoStatusBadge.tsx` | Label status video bahasa Indonesia; tidak mengubah enum |
| `frontend/src/components/EvaluationPanel.tsx` | Bahasa sederhana, struktur alasan/detail; pisahkan status proses dari penilaian |
| `frontend/src/components/DetectionPanel.tsx` | Detail objek sekunder dalam bagian yang dapat dibuka |
| `frontend/src/components/DomainStatusBadge.tsx` | Label lingkungan ringkas; rincian model/skor di detail |
| `frontend/src/api/videoApi.ts` | Scope wajib: error HTTP bertipe dengan status dan pesan publik tetap; endpoint/payload/guard upload tetap |
| `frontend/src/App.test.tsx`, `App.status.test.tsx`, `App.lifecycle.test.tsx` | Pertahankan assertion lifecycle; sesuaikan pencarian elemen untuk copy baru |
| `frontend/src/components/EvaluationPanel.test.tsx` | Mapping proses/hasil, previous result, empty/error, markup literal dan copy |
| Baru `frontend/src/components/VideoSourceSelector.test.tsx` | Aksi, busy/error, validasi upload, 409/503 dan recovery memakai fake |
| Baru `frontend/src/App.layout.test.tsx` | Struktur semantik, prioritas preview/evaluasi, detail sekunder, tidak ada data/kontrol fiktif |
| Baru `frontend/src/api/videoApi.test.ts` | Scope wajib: status HTTP, pesan generik, guard upload dan tidak mengulang POST |
| Rencana F1 ini | Catatan review, delta scope, dan bukti verifikasi F1 |

`useCurrentEvaluation.test.tsx` tetap dijalankan tanpa mengurangi assertion.
Hooks polling tidak direncanakan berubah. Jika implementasi ternyata membutuhkan
perubahan kontrak/lifecycle atau file di luar daftar, laporkan delta dan tunggu
keputusan owner sesuai AGENTS.md.

Di luar F1: backend; dokumen bersama (`PLAN.md`, `AGENTS.md`, dll.); wiring/prompt/
gesture; audio; garis/zona; preview sebelum Play; API baru; pause/seek video; sejarah
insiden; multi-kamera; data demo runtime; dependencies/font eksternal. File referensi
root `geminiService.ts`, `package.json` dan folder screenshot tidak disentuh.

## 3. Kontrak API yang dipakai

Sumber: `frontend/src/api/videoApi.ts`, `backend/app/api/{video,evaluation,domain,detections}.py`
dan wire models; diperiksa lewat source, tanpa request nyata.

| Endpoint | Kemampuan dan batas F1 |
|---|---|
| `GET /api/health` | Keterjangkauan layanan saat pembacaan; bukan bukti model/cloud/audio siap |
| `GET /api/video/status` | `state`, `source`, `error`; tidak membawa session ID atau mode privat |
| `POST /api/video/webcam/start` | Mulai kamera yang disediakan backend; tidak ada daftar/pergantian perangkat kamera |
| `POST /api/video/upload` | Multipart `file`; MP4/WebM, maksimal 200 MiB sesuai guard source; upload langsung memulai pemutaran |
| `POST /api/video/stop` | Hentikan atau coba ulang cleanup/reset error; tidak dijanjikan selalu kembali IDLE |
| `GET /api/video/stream` | MJPEG dalam `<img>` saat RUNNING; 503 jika backend tidak RUNNING; bukan player file yang mendukung pause/seek |
| `GET /api/detections/latest` | Hasil objek/session/frame terbaru atau 204; bukan peristiwa pelanggaran |
| `GET /api/domain/current` | Lingkungan dan status klasifikasi; RETAIL lock bukan bukti evaluasi retail sudah terintegrasi |
| `GET /api/evaluation/current` | `enabled`, sesi, status proses, active event, hasil AI tervalidasi sesi tersebut; 503 jika reader/view tidak tersedia |

Start/upload dapat menolak dengan 409 (admission tidak tersedia) atau 503 (sink wajib
menolak). Respons sukses HTTP juga dapat membawa `state=ERROR`: UI membaca state,
bukan menganggap 2xx sebagai bukti video berjalan. Upload/Stop tidak diulang otomatis.

Tidak ada endpoint status warning lokal, audio grant/delivery, gesture, authorization,
mode/config privat atau penyuntingan garis/zona. F1 tidak menebaknya dari domain,
`enabled=false`, threat score, polling error atau status video. Callback provider
bukan endpoint yang dipanggil frontend.

## 4. Layout dan arah visual

### Desktop, mulai 1024 px

Header pendek: nama aplikasi dan informasi koneksi yang memang teramati. Hapus
pipeline empat tahap yang mengambil perhatian sebelum video. Grid utama sekitar
70% preview dan 30% evaluasi; sisi evaluasi memiliki lebar minimum sekitar 300 px.
Preview berasio wadah 16:9, gambar `object-fit: contain` tanpa crop. Kontrol ada
langsung di bawah preview. Rincian objek dan rincian sistem berada di bawah area
utama, tertutup secara default dan tetap dapat diakses.

```text
┌ Sentinel Vision AI                         Koneksi layanan ┐
│ Pemantauan video                                          │
├───────────────────────────────────┬───────────────────────┤
│ Video aktif             [status]  │ Penilaian AI          │
│                                   │ [status proses]       │
│        Preview kamera/video       │                       │
│                                   │ Status penilaian      │
│                                   │ Alasan dari AI        │
│                                   │ Catatan tinjauan      │
├───────────────────────────────────┤ [Detail pengamatan]   │
│ [Kamera] [File video]    [aksi]    │                       │
│ Sumber • lingkungan; error aksi   │                       │
├───────────────────────────────────┴───────────────────────┤
│ [Detail objek]                 [Detail sistem]             │
└───────────────────────────────────────────────────────────┘
```

Wireframe hanya menunjukkan susunan, tidak memasukkan hasil evaluasi contoh.
Preview mengambil setidaknya dua pertiga lebar grid pada 1440 px dan 1280 px jika
minimum panel kanan terpenuhi. Pada layar pendek, detail boleh membutuhkan scroll;
preview, status dan aksi utama tetap diprioritaskan, bukan semua panel dipaksa muat.

### Tablet dan mobile

Di bawah 1024 px: satu kolom dengan urutan preview → kontrol → evaluasi → detail.
Pada 320–390 px, kontrol membungkus dan tombol aksi tetap mudah dijangkau; tidak
ada scroll horizontal. Panel evaluasi panjang menambah tinggi dokumen, tidak
mengubah ukuran preview atau menyembunyikan tombol. Gunakan DOM order yang sama
agar urutan keyboard dan pembaca layar tidak bertentangan dengan layout.

### Token usulan

| Token | Nilai | Peran |
|---|---|---|
| Canvas | `#07111F` | Latar gelap searah video referensi dan frontend saat ini |
| Surface | `#101F34` | Kontrol dan panel informasi tanpa gradient dekoratif |
| Ink | `#EDF5FF` | Teks utama yang kontras pada permukaan gelap |
| Action | `#4C91F2` | Mulai/fokus dan status proses yang aktif |
| Attention | `#F3C969` | Teks perhatian, bukan bukti pelanggaran |
| Urgent | `#FF7F8B` | Hentikan/error/penilaian CRITICAL, selalu dengan label teks |

Status NORMAL memakai teks netral; tidak ada banner hijau “aman”. Bidang gambar
tetap gelap untuk letterboxing. Gunakan Segoe UI/system sans lokal, 16 px body,
14 px metadata, heading 20–24 px; rata kiri. Kontras teks minimal 4.5:1 dan kontrol/
fokus 3:1 harus diperiksa saat implementasi. Target klik 44 px, fokus terlihat,
tanpa animasi dekoratif atau flash; hormati reduced motion.

Tinjauan arah visual terhadap brief: ciri utama berasal dari jendela video yang
besar, bukan grid kartu statistik/pipeline. Status ringkas dan detail yang dapat
dibuka mengurangi bahasa teknis. Warna gelap/aksi biru dan susunan preview kiri/
panel kanan mengambil acuan cuplikan header referensi yang diperiksa; typography,
kontrol tepat di bawah preview, dan detail yang dapat dibuka adalah usulan F1,
bukan klaim bahwa seluruh alur rekaman sudah ditinjau.

## 5. Kontrol, bahasa, dan keadaan UI

### Sumber video

- IDLE: pilihan `Kamera` / `File video`. Kamera memiliki tombol `Mulai kamera`.
  File memakai `Pilih file dan mulai` dengan keterangan “MP4 atau WebM, maks. 200 MB.
  Setelah dipilih, file langsung diunggah dan diputar.” Ini mempertahankan perilaku
  endpoint saat ini; tidak ada tombol Play terpisah atau preview sebelum upload.
- RUNNING: `Hentikan video`; pilihan sumber terkunci sampai berhenti.
- STARTING/STOPPING: teks `Memulai…` / `Menghentikan…`, aksi ganda dinonaktifkan.
  Jangan menampilkan persen/progress bar karena API tidak menyediakan progress.
- ERROR: aksi `Coba hentikan lagi` melalui Stop. Tidak menyebut “Kembali ke Idle”
  sebagai janji berhasil; status backend setelah request menentukan hasilnya.
- 409: beri arahan menghentikan sumber aktif atau menunggu cleanup. 503 saat start:
  “Video belum bisa dimulai. Coba hentikan sesi, lalu mulai kembali.” Kegagalan
  koneksi: “Layanan tidak dapat dihubungi.” Kegagalan validasi: arahan format/ukuran.
  Tidak melakukan request retry start/upload/Stop otomatis atau menampilkan body
  mentah/stack/path. Error HTTP bertipe wajib ditambahkan, tetap pertahankan validation
  sebelum POST dan signature payload API.

### Penilaian AI

| Keadaan | Copy utama usulan |
|---|---|
| Video tidak berjalan | “Mulai video untuk melihat penilaian AI.” |
| Loading/rebinding | “Memuat status penilaian…” / “Memuat sesi video terbaru…” |
| `enabled=false` | “Penilaian AI belum diaktifkan.” Tidak menebak sebab atau mode |
| IDLE tanpa hasil | “Belum ada penilaian pada sesi ini.” Tidak menyatakan area aman |
| DISPATCHING | “Mengirim untuk dinilai…” |
| WAITING_CALLBACK | “Menunggu hasil AI…” |
| FAILED | “Penilaian belum berhasil diselesaikan.” |
| TIMED_OUT | “Hasil AI belum diterima dalam batas waktu.” |
| CANCELLED | “Penilaian dibatalkan.” |
| Network error | “Status penilaian belum dapat dimuat.” Berbeda dari hasil penilaian FAILED |
| Enum tak dikenal | “Status penilaian belum dikenali.” Tidak crash/tidak menyimpulkan ancaman |

COMPLETED menampilkan hasil hanya jika hasil memang tersedia dan cocok sesi.
Label assessment: NORMAL → `Normal menurut AI`, WARNING → `Perlu perhatian`,
CRITICAL → `Perlu segera ditinjau`. Semuanya diberi judul “Penilaian AI”, bukan
“Pencurian terdeteksi”, “Pelaku”, atau “Aman”. Skor tetap tersedia di detail sebagai
“Skor penilaian AI …/100”, bukan probabilitas pelanggaran atau indikator keamanan.

Alasan dari backend tampil sebagai teks literal, tidak diringkas dengan model baru
atau ditambah saran yang tidak berasal dari kontrak. Catatan tetap terlihat di
dekat hasil: “Penilaian otomatis, bukan bukti pelanggaran. Perlu ditinjau oleh
petugas.” `suspectProfile` tetap nama field API tetapi label produk menjadi
`Detail pengamatan`, bukan identitas/pelaku. Event/sesi dan skor ada di detail.

Hasil lama **dari sesi yang sama** saat event baru/failure/timeout tetap diberi
label terlihat “Hasil sebelumnya pada sesi ini”; tidak disamarkan sebagai hasil
event yang sedang diproses. Network error atau pergantian lifecycle menyembunyikan
hasil sesuai guard yang ada. Tidak ada placeholder skor, profil, contoh alasan,
riwayat palsu, timestamp buatan, atau countdown seolah hasil akan datang.

### Peringatan akses lokal dan data yang tidak tersedia

Penilaian AI dan warning akses lokal adalah sinyal berbeda. F1 hanya menyajikan
hasil penilaian yang disediakan API. Bantuan singkat statis dapat menyebut:
“Penilaian AI berbeda dari peringatan akses lokal. Status peringatan akses lokal
belum tersedia di tampilan ini.” Ini penjelasan keterbatasan, bukan indikator status.
Tidak ada badge lokal/gesture/mode, toggle speaker/privat, jumlah peringatan,
indikator “diizinkan”, atau kesimpulan audio terdengar. `enabled=false` tidak
menyatakan audio lokal mati maupun semua analitik mati.

## 6. Pengamanan lifecycle yang dipertahankan

1. `App` tetap pemilik pending state dan lifecycle epoch. Epoch bertambah saat
   Start/Stop mulai **dan** saat selesai, termasuk request gagal. Status pertama
   dari epoch baru otoritatif; respons lama tidak boleh membatalkan keadaan baru.
2. `useVideoStatus`: serial request, AbortController, generation dan render-time
   epoch guard; interval 1 s setelah request selesai. Tidak pindah ke interval
   yang dapat menumpuk request. Tidak melakukan polling baru karena detail dibuka.
3. `useCurrentEvaluation`: abort + generation, session pin/rebinding, render-time
   guard, interval 1 s sesudah settle. Batched Stop→Start tetap menginvalidasi A
   meskipun tidak ada render non-RUNNING yang terlihat.
4. Detection/domain mempertahankan request serial, abort, active guard, interval
   500/750 ms dan session handling sesuai source; bukan dianggap memiliki semua
   jaminan epoch milik hook evaluasi. Membuka/menutup detail tidak remount hooks
   atau memulai transport kedua.
5. EOF yang membawa status ke IDLE tanpa tombol Stop: unmount gambar stream,
   hentikan polling analitik/evaluasi yang bergantung RUNNING, bersihkan tampilan
   hasil lama dan aktifkan kembali kontrol. Tidak mengirim Stop/Start otomatis.
6. URL MJPEG stabil selama satu transisi RUNNING; cache-buster baru saat masuk
   RUNNING, bukan setiap render. Parameter `sid` yang sekarang memakai Date.now()
   adalah cache-buster frontend, **bukan** lease/session binding backend.
   Error gambar (termasuk balapan RUNNING→EOF/503) mengganti gambar dengan
   “Preview tidak tersedia” tanpa mengubah state backend atau mengirim retry/Stop.
   Error diikat ke URL stream saat itu; hanya transisi RUNNING berikutnya membuat
   URL baru dan memungkinkan stream lagi. Render biasa tidak mencoba ulang.
7. Perubahan label hanya menyesuaikan selector test yang sah; assertion respons
   lambat/ignored abort, batching dan cleanup tidak dihapus demi layout baru.

Batas yang sudah ada: `/video/status` tanpa session ID berarti pergantian sesi
dari luar UI diketahui evaluasi melalui polling dan konfirmasi ulang, bukan
seketika. Detection/domain tidak memiliki lifecycle epoch yang sama dengan
evaluasi; F1 tidak mengklaim telah memperluas isolasi itu. Jika temuan implementasi
memerlukan perluasan hook, laporkan scope terpisah. Mengubah layout tidak membuktikan
cleanup backend, akurasi model, atau suara benar-benar terdengar.

## 7. Acceptance

| ID | Bukti yang diperlukan setelah implementasi |
|---|---|
| F1-01 | Pada 1440/1280 px preview mendominasi grid, evaluasi di kanan, kontrol tepat di bawah preview; tidak ada pipeline besar di atasnya |
| F1-02 | Pada 1024, 768, 390 dan 320 px layout terbaca, urutan fokus sesuai DOM, tanpa crop gambar/scroll horizontal/tombol terpotong |
| F1-03 | Kamera mulai sekali; memilih file menjalankan upload+start sekali, guard MIME/ekstensi/ukuran sebelum request tetap bekerja; tidak ada Play palsu |
| F1-04 | Busy mengunci sumber/aksi ganda; Stop/error retry mengikuti state; 409/503/network/HTTP 200 dengan ERROR tidak ditampilkan sebagai sukses |
| F1-05 | Semua process state, disabled/loading/rebinding/network error/unknown punya label teks yang sederhana; termasuk 503 view yang tidak disamakan dengan FAILED/ancaman |
| F1-06 | Hasil hanya berasal dari API tervalidasi, cocok sesi; hasil sebelumnya di sesi yang sama tetap ditandai; tidak ada mock assessment pada runtime |
| F1-07 | Note otomatis bukan bukti tetap tampak; NORMAL tidak berarti aman; profil anonim tidak dinamai pelaku; markup provider dirender literal |
| F1-08 | Tidak ada status/angka/toggle lokal, gesture, authorization, mode atau konfigurasi yang tidak disediakan API; bantuan statis tidak menyamar sebagai data |
| F1-09 | Suite lifecycle existing lulus: serial request, abort, ignored abort, late A vs B, epoch saat settle/gagal, batched Stop→Start, unmount/timer cleanup |
| F1-10 | EOF IDLE tanpa aksi membersihkan hasil dan stream serta menghentikan polling terkait; tidak mengirim request mutasi otomatis |
| F1-11 | URL stream stabil selama RUNNING; error gambar menampilkan preview tidak tersedia tanpa loop retry, pulih pada transisi RUNNING baru; detail tidak menggandakan polling |
| F1-12 | Keyboard/focus/label input jelas, status tidak hanya warna, target klik 44 px; kontras dan reduced motion diperiksa; live region tidak membaca profil berulang setiap poll |
| F1-13 | `npm run build` dan seluruh `npm test` lulus dengan fake fetch/timer/MJPEG placeholder; tanpa backend, GPU, jaringan provider atau perangkat |
| F1-14 | Diff hanya scope F1; tidak ada raw provider body, CCTV, screenshot sensitif, secret, path config, file upload atau report runtime masuk Git/log |

Uji DOM/jsdom membuktikan perilaku dan semantik, bukan ukuran CSS. Acceptance visual
memerlukan pemeriksaan render pada viewport di atas melalui harness browser lokal
dengan semua request/stream di-stub. Screenshot verifikasi hanya memakai empty
state/elemen geometris non-CCTV; hasil evaluasi sintetis hanya di automated tests,
tidak dalam runtime atau screenshot yang terlihat sebagai hasil sungguhan. Jika
harness browser tidak tersedia, laporkan acceptance visual belum terverifikasi;
jangan mengklaim lulus dari Vitest saja atau menjalankan backend sebagai pengganti.

## 8. Verifikasi dan urutan kerja

Setelah owner menyetujui rencana:

1. Preflight HEAD/staging/status dan file milik F1; berhenti jika scope bersinggungan
   dengan pekerjaan agent lain. Implementasi oleh Codex; reviewer read-only oleh
   agent lain sesuai keputusan owner, tidak melakukan edit bersama di file ini.
2. Ubah struktur dan CSS, kemudian copy/komponen; pertahankan orchestration hooks.
   Terapkan typed/public errors untuk acceptance 409/503.
3. Tambahkan test yang membuktikan state/error/lifecycle dan data yang tidak boleh
   muncul; fixture sintetik hanya dalam test. Jangan mengurangi cakupan suite lama.
4. Jalankan dari `frontend/` secara serial, tanpa install dan tanpa server backend:

   ```powershell
   npm run build
   npm test
   ```

   Dari root repo:

   ```powershell
   git diff --check
   git diff --cached --check
   git status --short
   ```

   Periksa whitespace/final newline file baru, scope diff, dan test tidak mengakses
   provider/perangkat. Full backend tidak diperlukan bila backend tetap sama.
5. Verifikasi visual dengan harness fake yang tersedia; catat viewport dan batas
   pengujian. Tidak menginstal browser/package atau memulai backend tanpa izin
   owner untuk tindakan tersebut. Angka hasil implementasi tercatat di §11;
   acceptance tidak dianggap lulus hanya karena sudah tertulis di rencana.
6. Laporkan perubahan, command/hasil independen, keterbatasan, HEAD, scope dan Git
   status. Tunggu reviewer/owner. Stage/commit/push dan live checks tetap memerlukan
   instruksi owner tersendiri.

## 9. Dependensi, keputusan, dan task terpisah

- Baseline T3c `d075207`; API di §3 tidak berubah untuk F1. Dependency frontend
  sudah tercantum di `frontend/package.json`; tidak menambah library desain.
- Layout, copy dan scope telah diotorisasi owner setelah review desain Claude.
  Referensi video UI lama sudah diberikan dan cuplikan awal diperiksa; detail rekaman
  lain belum ditinjau dan bukan bukti kemampuan API frontend saat ini.
- Backend contract task terpisah: upload → preview → konfigurasi garis/zona → Play,
  termasuk reservation/lease, batas resource, EOF/Stop/rollback dan admission saat
  config belum valid. F1 tidak membuat tombol tersebut seolah backend mendukungnya.
- Backend contract task terpisah: view mode/konfigurasi/otorisasi/warning lokal
  terikat sesi/generation serta semantik offer/grant/delivery audio. F1 tidak
  menambah endpoint atau menyimpulkan nilai yang tidak tersedia.
- T3d prompt/dispatch privat, gesture source, aset suara lokal dan T3e perangkat
  nyata tetap pekerjaan terpisah. Kebutuhan UMKM tidak membuat retail evaluation
  atau pengenalan identitas menjadi kemampuan yang sudah terintegrasi.
- Risiko UI: copy terlalu ringkas dapat menyembunyikan hasil lama/error; mitigasi
  label previous/error tetap terlihat. Detail tersembunyi dapat mengurangi konteks;
  alasan dan disclaimer tetap terbuka. Bagian video referensi yang belum ditinjau
  membatasi bukti kesesuaian visual. Polling tidak menjamin latensi
  maksimum dan video dominan tidak membuktikan kualitas deteksi.

## 10. Catatan pemeriksaan rencana

- Codex: pembacaan source/dokumen dan penyusunan rencana pada baseline di §1.
- Review desain Claude melalui owner: PASS WITH LIMITATIONS (read-only source pada
  d075207, tidak menjalankan test/browser). Temuan medium error bertipe menjadi
  scope wajib; temuan rendah 503 evaluasi dan error stream ditambahkan ke kontrak
  serta acceptance. Koreksi desain ditulis sebelum implementasi oleh Codex.
- Baseline frontend oleh Codex: `npm test`, 38 passed (5 files). Sandbox pertama
  gagal saat esbuild membaca parent dengan Access is denied; perintah yang sama
  dijalankan dengan approval di luar sandbox dan lulus, tanpa perubahan kode.
- Review implementasi independen selesai: PASS WITH LIMITATIONS, tanpa blocker
  (review Claude, disampaikan melalui owner); bukti dan tindak lanjut di §12.
  Verifikasi visual fake oleh implementer tetap tercatat di §11. Tidak ada
  provider/perangkat nyata. `PLAN.md`, dokumentasi frontend dan backend tidak
  diubah oleh Codex.

## 11. Hasil implementasi — Codex, 2026-10-11

Baseline tetap `d075207a189a2f7003ef33aac2af8ffbd0f1f4d1`, working tree, staging
kosong. Ini hasil implementer, **bukan** verdict review independen Claude.

Perubahan:
- Grid desktop preview/evaluasi, kontrol sumber di bawah preview, detail objek dan
  sistem dalam `<details>`. Pipeline besar dihapus. Layout satu kolom di bawah
  1024 px. Warna gelap/aksi biru searah potongan referensi yang sudah diperiksa;
  tidak membaca video referensi lagi pada penugasan implementasi ini.
- Bahasa sederhana untuk status proses/penilaian. Hasil sebelumnya dari sesi yang
  sama tetap ditandai; disclaimer tetap terlihat. Skor/metadata/profil anonim ada
  di detail. NORMAL berlabel “Normal menurut AI”, bukan “Aman”. Hanya status proses
  memakai live region, bukan seluruh reasoning/profil yang dipoll berulang.
- Bantuan peringatan lokal bersifat statis dan menyatakan data tidak tersedia;
  tidak ada status gesture/mode/audio yang disimpulkan. Tidak ada assessment demo
  dalam runtime. Input file menyebut upload langsung memulai pemutaran.
- `ApiRequestError(status)` tidak membaca/menyimpan body error. Semua wrapper
  mempertahankan endpoint, payload sukses dan AbortSignal. Selector memetakan
  409/503/422/kegagalan umum ke copy tetap; unknown exception tidak ditampilkan.
  HTTP 200 dengan state ERROR tetap mengikuti ERROR. Tidak ada retry POST otomatis.
- Error gambar mengganti stream dengan “Preview tidak tersedia”, bukan ikon rusak;
  tidak memanggil Stop atau retry. URL stabil dalam RUNNING, cache-buster memakai
  Date.now + penghitung lokal agar dua transisi pada milidetik yang sama tetap
  berbeda. Cache-buster bukan lease. Epoch/pending action App serta seluruh hook
  polling tidak diubah. Bahasa dokumen diatur `id` oleh App dalam scope yang sama.
- Temuan review desain Claude ditangani: typed error menjadi wajib (medium), 503
  current evaluation diuji sebagai fetch error (low), stream failure/recovery diuji
  tanpa retry (low). Ketiganya dinyatakan tertutup dalam review Claude, disampaikan
  melalui owner; lihat §12. Bukti implementer di bagian ini tetap dipisahkan.

File source yang diubah: App.tsx, index.css, api/videoApi.ts, serta
VideoPreview.tsx, VideoSourceSelector.tsx, VideoStatusBadge.tsx, EvaluationPanel.tsx,
DetectionPanel.tsx dan DomainStatusBadge.tsx, seluruhnya di `frontend/src/`.
Test lama yang disesuaikan: App.test.tsx, App.status.test.tsx dan
components/EvaluationPanel.test.tsx. App.lifecycle.test.tsx serta test hook evaluasi
tetap sama. Test baru: App.layout.test.tsx, api/videoApi.test.ts,
components/VideoSourceSelector.test.tsx. Rencana F1 ini satu-satunya dokumen yang
diedit Codex. Rencana T3 adalah perubahan Claude dan tidak disentuh.

Verifikasi serial oleh Codex:

| Command / metode | Hasil |
|---|---|
| `npm test` dari frontend, baseline sebelum implementasi | 38 passed, 5 files |
| `npm test` dari frontend, implementasi final | **61 passed, 8 files** |
| `npm run build` dari frontend, implementasi final | TypeScript + Vite lulus; 38 modules |
| `node frontend/node_modules/.cache/f1-browser-check.cjs` | Chrome headless, file:// build lokal, CDP pipe (tanpa port/server), fetch fake, HTTP/HTTPS diblokir; enam viewport lulus |
| Viewport 1440/1280/1024/768/390/320, tinggi 900 px | Tanpa overflow horizontal, kontrol tombol ≥44 px, input file muat; rasio preview/grid desktop 0.688/0.686/0.670; mobile satu kolom |
| Browser fake: cloud disabled + error stream | Label disabled dan preview unavailable, JS exception 0; tidak ada hasil evaluasi/video nyata |
| Kontras/fokus keyboard | Empat sampel computed-style teks (aksi, panel, koneksi, summary) ≥4.5:1; Tab fokus kontrol dengan outline 3 px |
| Inspeksi gambar empty state 1280 dan 390 px | Preview dominan, evaluasi pendamping/mobile di bawah; tidak ada CCTV atau assessment sintetis pada gambar |

Kegagalan awal dan penanganan:
- `npm test` dan build dalam sandbox gagal pada esbuild parent directory dengan
  “Access is denied”; perintah sama dengan approval di luar sandbox lulus tanpa
  workaround source. Tidak ada package diinstal.
- Run pertama setelah perubahan UI: 60 pass, satu assertion lama mencari teks
  “Selesai” pada pipeline yang sudah dihapus. Satu perbaikan selector mencari
  “Status: Selesai” pada panel, tetap memeriksa proses terpisah dari penilaian;
  run berikutnya 61 pass. Assertion late response/epoch/EOF tidak dihapus.
- Harness browser disposable awal memakai root path salah (ENOENT), diperbaiki
  sekali. Eksekusi sandbox berikutnya timeout `Page.enable`; command sama dengan
  approval di luar sandbox lulus. Browser ditutup di finally. Harness dan gambar
  empty state berada di cache node_modules yang diabaikan Git, bukan kandidat commit.

Batas bukti:
- Test fixtures sintetis hanya untuk automated tests. Harness visual tidak memakai
  hasil assessment, video, CCTV, provider, webcam atau speaker. Tidak ada server
  frontend/backend dimulai. Full backend tidak diulang karena tidak diedit.
- Pengukuran visual mencakup empty state, file controls, disabled evaluation dan
  stream failure dengan fake. Belum ada pemeriksaan gambar video nyata, kelengkapan
  detail panjang pada semua viewport, atau audit screen reader menyeluruh.
- Kontras diverifikasi pada empat sampel, bukan semua kombinasi state; reduced
  motion disediakan CSS dan tidak ada animasi dekoratif, belum audit aksesibilitas
  eksternal. Ini tidak membuktikan akurasi/latensi model, gesture atau audio delivery.
- Batas session ID/polling detection-domain di §6 tetap berlaku. F1 tidak
  mengubah backend/API kontrak, garis/zona atau preview-before-Play.

Fokus yang diserahkan untuk review Claude: diff source/test, perlindungan lifecycle,
sanitasi 409/503/unknown exceptions, stream URL/error/recovery, pemisahan proses/
penilaian/local warning, dan batas bukti visual di atas. Hasil review di §12.
Pada penyerahan implementasi belum ada stage/commit/push; keputusan penutupan
owner dan verifikasinya dicatat di §12.

## 12. Pencatatan penutupan — review independen dan tindak lanjut

Status: **completed — scope F1**. Penutupan dan commit diotorisasi owner setelah
T3d selesai di-commit. Codex memverifikasi HEAD T3d
`1771d6b94e9075cebf7170d3cb3a225b54246291` dan staging kosong sebelum penutupan
F1. Seluruh 15 hash source/test di bawah tetap cocok dengan versi review Claude;
test/build tidak diulang karena source/test tidak berubah. Tidak ada perubahan
source/test dalam delta pencatatan ini. Commit dibatasi tepat 16 path di bawah;
hash commit dilaporkan kepada owner setelah commit.

### Review Claude, disampaikan melalui owner

- Verdict: **PASS WITH LIMITATIONS**, tanpa blocker.
- Reviewer menjalankan `npm test`: **61 passed pada 8 file**; build TypeScript/Vite
  lulus. Reviewer membaca diff, test, dan CSS. Ini bukti reviewer yang disampaikan
  melalui owner, bukan hasil pengulangan baru oleh Codex pada delta dokumentasi.
- Tiga temuan desain tertutup: error HTTP bertipe tanpa body mentah, 503 evaluasi
  diperlakukan sebagai kegagalan memuat status, serta error MJPEG tanpa retry.
- Claude tidak menjalankan browser, audit kontras, atau screen reader. Enam
  viewport browser fake, pemeriksaan sampel kontras dan fokus keyboard di §11
  tetap **bukti implementer Codex**, tidak diatribusi sebagai verifikasi Claude.

### Temuan rendah — pekerjaan lanjutan, tidak diperbaiki dalam delta ini

| Temuan | Pekerjaan lanjutan | Status |
|---|---|---|
| Ref penghitung preview dimutasi di `useMemo` | Tinjau pembuatan identitas stream agar tidak bergantung pada mutasi ref selama render; pertahankan stabilitas URL, isolasi error dan tanpa retry | Terbuka, low; bukan blocker review ini |
| Respons 2xx rusak dapat memperoleh pesan koneksi yang kurang tepat | Bedakan kegagalan parsing/kontrak respons dari koneksi dengan pesan publik tetap; jangan menampilkan body mentah atau menambah retry POST | Terbuka, low; bukan blocker review ini |
| `lang="id"` baru diset setelah render oleh App | Tinjau bahasa dokumen sebelum render dalam perubahan frontend berikutnya; file entry HTML belum termasuk delta ini | Terbuka, low; bukan blocker review ini |

Tidak ada perbaikan source untuk ketiga temuan tersebut pada pencatatan ini.
Batas akurasi/latensi, mode/gesture/audio yang tidak tersedia di API, detail panjang
yang belum diperiksa pada semua viewport, dan audit screen reader yang belum
dilakukan tetap berlaku. F1 tidak dianggap sebagai validasi perangkat nyata.

### Kandidat path F1 (16 path; tidak termasuk delta T3d Claude)

Hash berikut adalah blob Git source/test sebelum pencatatan, untuk dicocokkan
sesudahnya. Rencana ini berubah sehingga tidak memakai hash lama sebagai syarat.

| Path lengkap dari root repo | Blob source/test |
|---|---|
| `frontend/src/App.tsx` | `5e29cda15c1c76652b2b8c6fc6af231956a2c902` |
| `frontend/src/index.css` | `06483fdcea30952ab7f6599e548bf7546d1e9d7a` |
| `frontend/src/api/videoApi.ts` | `ef0d3c089f86eee2c8335e7048ae5a2521f83381` |
| `frontend/src/components/VideoPreview.tsx` | `2e58732ecc7efc9cdba79c2db2bbc36ec6fe169f` |
| `frontend/src/components/VideoSourceSelector.tsx` | `2226d9b4596132bea0aaf0a529c5ce46d997d835` |
| `frontend/src/components/VideoStatusBadge.tsx` | `1bbc22ae3262e89df51e7c930286f0f0465e57e1` |
| `frontend/src/components/EvaluationPanel.tsx` | `4ab06154143929647f785073403e40f0eb797189` |
| `frontend/src/components/DetectionPanel.tsx` | `6a290b6bc77804772d24ad94469cc1b31e800188` |
| `frontend/src/components/DomainStatusBadge.tsx` | `84854ea1048ecab16d2987a929da1fc58a933147` |
| `frontend/src/App.test.tsx` | `bb5ca43ee0013df38b48245c371b96d0214cabe2` |
| `frontend/src/App.status.test.tsx` | `7fbd7b3f1516897f73723ee97fc989879640ba8f` |
| `frontend/src/components/EvaluationPanel.test.tsx` | `2df520febe7bc08d32592418738395344ea699c3` |
| `frontend/src/App.layout.test.tsx` | `519df6312d9177da166f60ec38e89ec488c75acd` |
| `frontend/src/api/videoApi.test.ts` | `6cf8a69364e94e0fec4bed3532ac9ec79c664785` |
| `frontend/src/components/VideoSourceSelector.test.tsx` | `efebc3a19a39817a222ecd8d83f71e13f338c581` |
| `docs/exec-plans/active/f1-frontend-ui-ux.md` | Dokumentasi pencatatan ini |

Pemeriksaan pencatatan awal: baseline HEAD
`d075207a189a2f7003ef33aac2af8ffbd0f1f4d1`, staging kosong; bandingkan semua 24
blob file di `frontend/src/` sebelum/sesudah (termasuk hook dan test yang tidak
masuk kandidat). Periksa diff, whitespace/newline dan tautan lokal. Test/build
tidak perlu diulang bila source/test tetap identik. Tidak ada backend, rencana T3,
AGENTS.md atau PLAN.md yang diedit Codex; perubahan Claude tetap dipertahankan.
