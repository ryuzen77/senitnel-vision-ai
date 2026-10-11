# R1 — Risiko dan validasi Sentinel AI

Tanggal keputusan PM: 12 Oktober 2026 (Asia/Jakarta).

Status: disetujui untuk pencatatan planning; implementasi kontrol baru dan pengujian live belum diotorisasi.

Lokasi kanonis dalam repo: `docs/exec-plans/active/r1-risk-validation.md` (keputusan PM, 12 Oktober 2026).

## Tujuan dan batas

Mengelola risiko parkiran privat dan retail menggunakan risiko, dampak, probabilitas, mitigasi, owner, KPI, dan bukti. Status teknis di bawah berasal dari laporan implementer/reviewer melalui owner, bukan pemeriksaan repo baru oleh PM.

Dokumen ini tidak memperluas scope P1b. Dokumen dicatat di lokasi kanonis dan ditautkan dari PLAN.md sebagai perubahan dokumentasi yang terpisah dari P1b. Jangan duplikasi kontrak lifecycle P1 atau desain camera-health; gunakan tautan setelah file tersebut tersedia di commit.

## Keputusan yang mengikat

1. Pisahkan aturan akses lokal, penilaian tindakan oleh VLM, dan otorisasi pengguna. Crossing, mendekati motor, atau tidak melakukan gesture bukan bukti pencurian.
2. Jangan menerapkan bobot +20/+25 atau klasifikasi Safe/Observe yang diusulkan sebagai keputusan produksi. Bobot tersebut dapat memberi pemilik sah skor Critical dan menjumlahkan sinyal yang berkaitan.
3. Jangan menambahkan status Unknown tanpa perubahan kontrak. Ketidakpastian dapat dijelaskan pada reasoning; kontrak saat ini NORMAL/WARNING/CRITICAL.
4. Gesture nyata, backup authorization, whitelist tamu, kesehatan kamera dan efektivitas TTS belum boleh disebut kontrol yang terbukti.
5. Lima frame adalah sampling temporal saat ini, bukan jumlah optimal atau frame terbaik secara semantik. Penghematan biaya dan latensi belum diukur terhadap baseline.
6. Target dampak, probabilitas, dan KPI numerik tidak boleh ditampilkan sebagai hasil pengukuran. Baseline pilot harus mendahului komitmen angka.
7. Penilaian tidak menyimpulkan identitas, kepemilikan, niat atau kejahatan dari gambar. Track ID bersifat anonim.

## Matriks risiko awal

Probabilitas seluruh baris: belum terukur; estimasi awal memerlukan dataset dan baseline. Urutan mengikuti dampak serta dependensi, bukan skor probabilitas yang dibuat-buat. Owner di bawah adalah penugasan planning, bukan bukti pekerjaan selesai.

| Prioritas / risiko | Dampak | Kontrol saat ini / tindak lanjut | Owner | KPI dan bukti yang diperlukan |
|---|---|---|---|---|
| Tinggi: pemilik/tamu sah mendapat teguran | Gangguan dan hilangnya kepercayaan | Aturan crossing dan pendekatan terkonfirmasi tersedia; otorisasi nyata dan suara lokal belum tersedia. Uji aktivitas sah sebelum aktivasi live. | PM/owner: kebijakan; Claude: backend; Codex: review | Alarm palsu per jam, precision event, proporsi aktivitas sah yang ditegur, tingkat keberhasilan otorisasi |
| Tinggi: kejadian terlewat | Aset tidak mendapat respons | UNKNOWN_INSIDE, cold start domain, oklusi dan perubahan track ID membatasi cakupan. Uji setiap kondisi secara terpisah. | Claude; reviewer Codex | Recall kejadian beranotasi, kasus missed event menurut penyebab, waktu kesiapan detector/domain |
| Tinggi: ROI/geometri salah | Aturan dipakai pada area yang keliru | P1 konfigurasi operator masih berjalan; geometri per frame sedang diintegrasikan. Perubahan sudut dengan ukuran sama belum terdeteksi. | Claude: kontrak; Codex: editor; owner: kalibrasi | Mismatch yang diblokir, akurasi koordinat preview, drift yang terdeteksi, waktu verifikasi ulang |
| Tinggi: lifecycle/cleanup gagal | Resource tertahan, admission tertutup, state salah | P1a telah direview; P1b masih diperbaiki. Pertahankan ownership dan retry; deadline bukan bukti cleanup selesai. | Claude; reviewer Codex | Obligation pending, retry berhasil, admission sesuai state, nol cleanup terhadap sesi baru |
| Tinggi: bukti tidak mencakup tindakan | Penilaian AI kehilangan konteks | Sampel crossing–journey–approach dapat tidak mencakup pemindahan aset setelah trigger. Jangan mengklaim seluruh tindakan terekam. | Claude; owner: anotasi | Coverage interval tindakan, seal failure per alasan, penilaian dengan bukti tidak cukup |
| Tinggi: TTS tidak efektif atau terlalu sering | Alarm fatigue, gangguan, efek pencegahan rendah | Gate/cooldown/dedup diuji dengan fake; aset lokal, device dan efektivitas belum terbukti. Teguran lokal tidak bergantung pada skor VLM. | Owner/PM: teks dan izin; Claude: audio; Codex: review | Frekuensi teguran, penilaian pengguna, respons terhadap teguran, efek pencegahan dengan konteks dan keterbatasan |
| Sedang: VLM terlambat, internet/cloud gagal | Penilaian dashboard terlambat atau hilang | Respons lokal terpisah, timeout dan tanpa retry tersedia. Offline berarti fungsi lokal yang tersedia tetap berjalan, bukan seluruh sistem berfungsi setara. | Claude; reviewer Codex | Median/p95 latensi lokal dan cloud terpisah, timeout rate, expired candidates, hasil saat cloud disabled |
| Sedang: biaya API tidak ekonomis | UMKM sulit mengadopsi | Bukti terpilih, budget dan batas dispatch tersedia; biaya riil belum memiliki baseline. | PM/owner; Claude: metrik | Biaya per kejadian/hari, jumlah request, token/ukuran gambar jika provider menyediakannya |
| Sedang: domain salah atau VLM keliru | Aturan atau penilaian tidak sesuai | CLIP dan validasi output terstruktur tersedia; valid JSON tidak membuktikan penilaian benar. | Claude; reviewer Codex | Akurasi domain pada dataset, waktu lock, agreement terhadap anotasi, kesalahan penilaian ambigu |
| Sedang: lighting/occlusion/overload | Deteksi dan tracking menurun | Deteksi/tracking lokal tersedia; mutu lapangan belum terkalibrasi. Uji siang/malam, ramai, antrean dan frame drop. | Claude; owner: sumber uji | Recall per kondisi, ID switch/track loss dengan definisi, frame drop, latensi pemrosesan, panjang antrean, memori |
| Tinggi sebelum deployment: privasi dan kontrol akses | Data orang terpapar atau tidak dikelola tepat | Pengiriman bukti terpilih membatasi data tetapi tidak otomatis menjamin privasi. Audit retensi, akses, log dan tujuan penggunaan. | Owner/PM; Claude: backend; Codex: UI/review | Retensi sesuai konfigurasi, akses terbatas, log bebas gambar/secret, persetujuan dan dokumentasi lokasi |

## Skenario wajib per domain

### Parkiran privat

- Pemilik masuk tanpa gesture, tamu sah, orang lewat, interaksi singkat normal.
- Orang pertama kali terlihat sudah di dalam, detector/domain belum siap, oklusi, ID track berubah.
- Crossing lalu pendekatan motor; target motor berubah; motor bergerak setelah approach.
- Cloud mati, kandidat expired saat evaluasi lain in-flight, suara tidak tersedia atau device gagal.
- Tidak bergantung pada wajah, helm atau masker sebagai indikator kriminalitas.

### Retail

- Pelanggan melihat, mengambil lalu mengembalikan, dan membawa barang untuk pembelian normal.
- Banyak pelanggan, track bersilangan, barang kecil, rak padat, dan interaksi tertutup tubuh.
- Bukti ambigu dijelaskan sebagai tidak jelas; keputusan high-risk ditinjau manusia.
- Jangan mengklaim missing-scan detection tanpa data/transaksi dan rancangan kasir tersendiri.

## Definisi pengukuran

- Precision event: true positive dibagi seluruh event yang ditandai, dengan definisi event positif ditetapkan sebelum uji.
- Recall: kejadian positif beranotasi yang mendapat respons dibagi seluruh kejadian positif beranotasi. Catat juga kesempatan deteksi yang hilang karena cold start/occlusion.
- Alarm palsu: jumlah peringatan yang tidak sesuai label kebijakan per jam observasi. Pisahkan peringatan akses dari penilaian VLM.
- Latensi: timestamp pemicu hingga offer lokal, hingga playback aktual, dan hingga hasil AI. Laporkan median/p95 secara terpisah.
- Biaya: request aktual, biaya per event/hari dan beban komputasi lokal. Jangan menyatakan lebih murah tanpa pembanding yang setara.
- Beban operator: waktu peninjauan dan jumlah event yang perlu ditinjau, dibanding baseline dengan kondisi setara.
- Kehilangan aset/shrinkage: indikator jangka lebih panjang; pilot kecil tidak membuktikan penurunan disebabkan Sentinel.

Nilai FP <10%, recall >90%, authorization/domain >95%, respons <5 detik dan GPU <80% belum diputuskan sebagai SLA atau hasil. Tentukan target setelah baseline; penggunaan GPU saja bukan ukuran kestabilan.

## Fase validasi dan acceptance

1. Dokumentasi: catat risiko, owner, kontrol tersedia versus direncanakan, dan keterbatasan. Review silang sebelum commit dokumentasi.
2. Software/fake: pertahankan acceptance P1 dan T3, tanpa mengklaim akurasi CV/VLM. Hasil implementer dan reviewer dibedakan.
3. Video beranotasi: tetapkan dataset, izin penggunaan, label, definisi event dan baseline sebelum uji. Pisahkan parkiran dan retail.
4. Pilot perangkat: gesture, suara, geometri, kesehatan kamera serta cloud diuji setelah wiring dan izin owner. Tidak ada aktivasi live otomatis dari dokumen ini.
5. Evaluasi: laporan metrik, kasus gagal, risiko tersisa, dan keputusan go/no-go oleh owner/PM.

Acceptance dokumen: semua kontrol memiliki status dan owner; semua klaim terhubung ke bukti atau diberi label target; tidak ada label aman berdasarkan tidak adanya event; tidak ada bobot risiko baru diterapkan; tautan repo hanya dibuat jika target sudah tersedia.

## Integrasi planning repo

Status: dokumen berada di lokasi kanonis dan ditautkan dari satu entri pada PLAN.md, sebagai commit dokumentasi terpisah dari P1b yang direview Codex secara read-only. Tidak ada source, threshold, model atau dependency berubah karena pencatatan ini.

Tindak lanjut yang masih terbuka:

1. Rujukan dari rencana pilot dan camera-health ditunda sampai dokumen tersebut tersedia dalam commit; ditambahkan tanpa menimpa dokumen agen lain.
2. Salinan asal dokumen ini dipertahankan sementara dan dibersihkan sebagai langkah terpisah setelah isi kanonis terverifikasi.
