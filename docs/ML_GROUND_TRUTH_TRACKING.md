# Silent Tracking dan Kurasi Data untuk ML

## Tujuan

Editor sheet mengirim jejak aksi engineer ke `POST /api/v1/tracking/log`. Tujuannya adalah menyediakan bahan untuk memahami koreksi HITL, membangun kandidat dataset, dan mengevaluasi model/engine setelah data dikurasi. Implementasi ini **tidak** melatih model, mengubah hasil deteksi, atau mengirim aksi ke layanan AI. Tracking berjalan fire-and-forget agar tidak menahan perubahan visual di editor; kegagalan jaringan atau server tidak ditampilkan ke engineer.

Backend mengautentikasi request dengan JWT dan memeriksa akses project serta sheet. Baris `GroundTruthLog` menyimpan `user_id`, `project_id`, `sheet_id`, `action_type`, `payload_json`, dan timestamp server. Payload adalah JSON terbuka agar event dapat berkembang tanpa mengubah skema tabel.

## Aksi yang direkam

| `action_type` | Pemicu | Data payload utama | Interpretasi awal |
| --- | --- | --- | --- |
| `mark_pipe` | Engineer pertama kali menandai run otomatis, baik dengan warna default maupun kuas grup aktif | `run_id`, `run_idx`, `bbox`, `points`, `color`, `group_id`, `group_name` (jika ada), `piping_id` | Sinyal bahwa engineer memilih geometri sebagai pipa yang perlu ditandai. Saat memakai kuas grup, event juga merekam assignment grup. |
| `assign_group` | Grup baru dibuat, satu run dimasukkan ke grup kuas aktif, atau beberapa run terpilih dimasukkan ke grup dari sidebar | `group_id`, `group_name`, `color`, `kind`, `affected_run_ids`; assignment juga menyertakan `run_indices` bila dilakukan secara batch serta `piping_id` bila tersedia | Sinyal relasi run-ke-grup yang dipilih engineer. Pembuatan grup tanpa anggota memiliki `affected_run_ids: []`. |
| `rename_pipe` | Piping ID/label run benar-benar berubah setelah penyimpanan hasil berhasil | `run_id`, `run_idx`, `previous_piping_id`, `piping_id`, `affected_run_ids` | Koreksi identitas/tag line. Nilai kosong (`null`) berarti tag dilepas. |

Semua koordinat `bbox` dan `points` pada event `mark_pipe` berada dalam koordinat piksel gambar yang sama dengan geometri run editor. `run_idx` adalah indeks run saat event dibuat dan dapat berubah setelah split, delete, atau hasil tracing baru. Gunakan `run_id` bila tersedia; jangan menganggap indeks sebagai ID stabil. Sebagian run lama atau run manual mungkin tidak mempunyai `id` stabil.

Contoh request marking:

```json
{
  "project_id": "project-uuid",
  "sheet_id": "sheet-uuid",
  "action_type": "mark_pipe",
  "payload_json": {
    "run_id": "run-uuid",
    "run_idx": 12,
    "bbox": { "x1": 80, "y1": 120, "x2": 640, "y2": 120 },
    "points": [[80, 120], [640, 120]],
    "color": "#10B981",
    "group_id": "grp-abc123",
    "group_name": "Process Line",
    "piping_id": null
  }
}
```

API merespons `202 Accepted`; penulisan ke tabel berlangsung sebagai background task backend. Frontend tidak menunggu respons tracking sebelum mengganti state editor. Error API/logging memang sengaja tidak mengganggu pekerjaan engineer. Karena itu ketiadaan baris log tidak membuktikan aksi tidak terjadi, dan keberadaan log bukan jaminan aksi tersimpan permanen.

## Memakai log sebagai bahan ML

Perlakukan log sebagai **feedback event**, bukan dataset berlabel final. Sebelum dipakai untuk melatih atau menilai engine:

1. Gabungkan event dengan hasil sheet tersimpan dan geometri pada waktu yang sesuai menggunakan `project_id`, `sheet_id`, identitas run, dan konteks waktu. Indeks run bisa berubah; jika tidak ada ID stabil, lakukan pencocokan geometri secara hati-hati dan simpan tingkat keyakinan pencocokan.
2. Kurasi event `mark_pipe` sebagai kandidat label geometri, `assign_group` sebagai kandidat relasi ke grup, dan `rename_pipe` sebagai kandidat koreksi Piping ID. Pertahankan nilai sebelum/sesudah jika tersedia; jangan menyimpulkan kualitas teknis hanya dari warna atau nama grup.
3. Deduplicasi dan resolusikan konflik: engineer dapat mengganti label, memindahkan grup, membatalkan perubahan lewat edit berikutnya, atau menghapus/mengubah run setelah event tercatat. `GroundTruthLog` saat ini tidak merekam event undo/redo atau snapshot penuh hasil sesudah aksi.
4. Pisahkan data train/validation/test berdasarkan project atau sumber gambar, bukan dengan membagi event acak dari sheet/proyek yang sama. Ini mengurangi kebocoran geometri dan pola drafting.
5. Catat versi skema payload, tracer/model sumber, serta aturan kurasi saat membentuk dataset. Field versi model dan snapshot hasil belum disertakan pada event tracking saat ini; tambahkan dengan perubahan skema/payload yang disengaja sebelum menjadikan log sebagai dataset produksi.
6. Batasi akses data ke pengguna yang berwenang. Log mengandung identitas project/sheet, geometri teknik, dan kemungkinan Piping ID yang sensitif. Anonimkan atau minimalkan identitas sesuai kebijakan data sebelum ekspor ke lingkungan eksperimen.

Data ini berpotensi mendukung pembelajaran/evaluasi pada: pemilihan run yang perlu ditandai, pengelompokan run ke corrosion group, dan koreksi Piping ID. Namun, `mark_pipe` mencerminkan tindakan opt-in engineer; run yang tidak pernah diklik bukan otomatis contoh negatif. Bias pengguna, cakupan sheet yang tidak merata, kualitas OCR, serta proses pembatalan harus diperhitungkan sebelum menentukan target model.

## Perluasan yang disarankan sebelum training rutin

- Tambahkan `schema_version` dan identitas versi tracer/model pada payload saat tersedia.
- Rekam snapshot run dan hasil sesudah aksi untuk assignment grup serta rename, bukan hanya indeks/ID, agar pencocokan tetap dapat diaudit setelah result berubah.
- Tambahkan event eksplisit untuk undo, unassign, penghapusan, dan koreksi ulang; definisikan apakah label lama dibatalkan atau menjadi bagian riwayat.
- Bangun proses kurasi yang menghasilkan dataset versi tetap dengan provenance ke baris log, result, dan file sheet sumber.
- Evaluasi model pada proyek/sheet yang tidak muncul pada data training, dan minta validasi engineer sebelum saran model mengubah data engineering.

Status saat ini: hanya pengumpulan event best-effort dan penyimpanan backend. Belum ada job export dataset, pipeline labeling, retraining, atau deployment model berbasis log ini.
