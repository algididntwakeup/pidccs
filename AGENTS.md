# Panduan agent: stabilisasi HITL & Fast Trace

Baca `README.md` bagian **Alur Aktif: HITL & Fast Trace** dan catatan awal
`docs/SYSTEM_ARCHITECTURE.md` sebelum mengubah pipeline. Diagram pipeline
otomatis di dokumen lama menjelaskan mode full/opsional; cocokkan setiap klaim
tentang perilaku saat ini dengan kode dan test.

## Keputusan produk yang harus dipertahankan

- Workspace aktif ada di `frontend/src/app/project/[id]/sheet/[sheet_id]/page.tsx`.
  `frontend/src/app/project/[id]/page.tsx` adalah halaman proyek, bukan editor
  sheet. Aksi utama pada editor adalah Fast Trace (`lines_only`).
- Fast Trace melewati OCR dan YOLO. Untuk PDF vektor, ambil geometri dan ukuran
  halaman dari PDF tanpa merender bitmap resolusi penuh terlebih dahulu. Muat
  citra hanya jika tracer raster atau fallback membutuhkannya. PDF raster dan
  PNG/JPG tetap memakai tracer raster.
- Engineer mengoreksi hasil lewat Magic Wand, Box Trace, pen manual, edit titik,
  dan Undo/Redo. Pertahankan hasil edit manual saat hasil tracing, enrichment,
  atau data line list diperbarui. Perubahan `runs` dan `piping_ids` harus tetap
  sinkron dengan hasil yang disimpan di backend, terutama karena beberapa API
  mengacu pada indeks run.
- Full detection dan AI enrichment adalah aksi terpisah atas permintaan.
  Modul YOLO/OCR masih dipakai di kedua jalur itu; jangan menghapusnya hanya
  karena tidak dipanggil oleh Fast Trace. Jangan membuat Fast Trace menunggu
  model AI atau mengubah default UI kembali ke full detection tanpa permintaan
  produk yang jelas.

## Peta kode dan batas refactor

- Titik masuk backend: `backend/app/services/detection_service.py`, lalu
  `pidcorr/orchestrator.py`. Pertahankan cabang `lines_only` yang sederhana dan
  jalur fallback raster yang dapat diuji.
- Editor sheet memakai `frontend/src/hooks/usePipeTracer.ts` untuk aksi tracing,
  `useHistory.ts` untuk Undo/Redo, dan `useCanvasOverlay.ts` untuk overlay
  OpenSeadragon. Hindari menyalin logika itu kembali ke page/komponen kanvas.
- Jaga koordinat PDF, rotasi, dimensi gambar, dan titik run tetap konsisten
  antara tracer, overlay, dan API. Saat mengoptimalkan `pidcorr/lines.py`,
  pertahankan hasil konektivitas, lalu ukur/tes kasus garis yang berdekatan.
- Sebelum menghapus kode yang tampak lama, telusuri pemanggilnya di UI, API,
  worker, mode full/enrichment, export, dan test. Refactor hanya dalam cakupan
  tugas; jangan sekaligus mengubah algoritme deteksi atau alur produk.

## Verifikasi minimum sebelum menyatakan selesai

- Backend CI memakai Python 3.13 dan menjalankan `pytest backend/tests/`.
  `Contoh P&ID` berisi gambar referensi lokal yang tidak di-commit. Test CI
  harus memakai fixture sintetis atau `skip` yang jelas jika gambar lokal
  memang wajib; jangan menganggap gambar tersebut tersedia di checkout baru.
- Dari `frontend/`, jalankan `npm run lint`, `npx tsc --noEmit`, dan
  `npm run build` untuk perubahan frontend. Perubahan pipeline perlu test
  Fast Trace vektor dan fallback raster yang relevan.
- Bedakan hasil lokal dari GitHub CI. Jangan menyebut CI GitHub hijau sebelum
  ada run baru yang berhasil. Jangan push atau membuka PR tanpa instruksi
  pengguna; perubahan stabilisasi sebelumnya sengaja disimpan lokal.

Instruksi eksplisit pengguna untuk tugas berikutnya dapat mengubah keputusan
di atas. Jika demikian, perbarui dokumen dan test bersamaan dengan kode.
