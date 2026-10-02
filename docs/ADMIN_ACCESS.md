# Akses Admin dan Break-Glass

Panduan ini menjelaskan akses dashboard monitoring IT P&ID Studio melalui BIMA
SSO serta jalur darurat jika role admin BIMA belum tersinkron. Dashboard berada
di `/sudo` dan sengaja tidak ditautkan dari sidebar atau navbar. URL yang tidak
ditautkan **bukan** batas keamanan: seluruh akses data tetap divalidasi backend.

## Akses rutin melalui BIMA SSO

1. Konfigurasikan `BIMA_BASE_URL` dan `BIMA_CLIENT_ID` untuk backend, serta
   `NEXT_PUBLIC_BIMA_BASE_URL` dan `NEXT_PUBLIC_BIMA_CLIENT_ID` untuk frontend.
   Daftarkan callback persis seperti `NEXT_PUBLIC_BIMA_REDIRECT_URI`; jika kosong,
   callback memakai origin website aktif ditambah `/login`. Untuk Docker, rebuild
   frontend setelah mengubah `NEXT_PUBLIC_*` karena nilainya ditanam saat build.
2. Pastikan `BIMA_SSO_MOCK_MODE=false` dan `BIMA_PROJECTS_MOCK_MODE=false`, lalu
   pastikan akun admin sudah memiliki klaim `is_admin: true` atau role BIMA
   `SUPERADMIN`, `IT_ADMIN`, atau `IT-ADMIN`.
3. Buka `/login` pada website yang dikonfigurasi dan pilih **Login dengan BIMA**.
4. BIMA mengembalikan `token` dan `state` ke callback. Frontend mencocokkan state,
   lalu backend memverifikasi token melalui `/api/sso/verify` dan menerbitkan JWT
   lokal.
5. Setelah login, buka `https://<host-aplikasi>/sudo` untuk monitoring. Backend
   menggunakan role yang sudah diverifikasi BIMA untuk menentukan akses.

Mock SSO hanya untuk tester lokal dan tidak boleh diaktifkan di environment
shared/production.

JWT frontend disimpan di browser `localStorage` dengan key `pid_access_token`.
Dashboard dan route `/project/...` mengarahkan user tanpa token ke `/login`, dan
tombol **Logout** menghapus token tersebut.

Jika role belum sampai dari BIMA, minta pemilik administrasi BIMA memperbaiki
akun/assignment. Untuk insiden yang tidak bisa menunggu sinkronisasi, gunakan
prosedur break-glass di bawah.

## Mengaktifkan break-glass

Break-glass dinonaktifkan ketika `SUPERUSER_SECRET_KEY` kosong. IT harus membuat
secret acak berentropi tinggi dan memasangnya di secret manager deployment atau
`.env` lokal yang tidak di-commit. Contoh membuat nilai acak (jangan gunakan
nilai contoh tetap):

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Konfigurasikan variabel backend berikut:

```dotenv
JWT_SECRET_KEY=<nilai-acak-stabil-untuk-semua-replika-backend>
SUPERUSER_SECRET_KEY=<secret-break-glass-yang-dibuat>
```

`docker-compose.yml` meneruskan kedua variabel dari environment/`.env` root ke
service `api`. Setelah mengubah secret, recreate container agar environment baru
diterapkan:

```powershell
docker compose up -d --force-recreate api
```

Untuk produksi multi-replica, `JWT_SECRET_KEY` harus sama dan stabil di semua
replika; jangan mengandalkan fallback random per proses.

## Masuk menggunakan break-glass

1. Buka `/sudo`, lalu pilih **Emergency IT**.
2. Masukkan `SUPERUSER_SECRET_KEY` dari secret manager dan tekan **Enter
   emergency dashboard**.
3. Backend memeriksa secret dengan perbandingan constant-time, memastikan
   principal reserved `sudo` tersedia, lalu menerbitkan JWT lokal.
4. Dashboard menggunakan JWT dari `localStorage` browser dengan key
   `pid_access_token`. Token berlaku
   sesuai `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (default 60 menit); tombol **Sign
   out** menghapus token dari tab.

Endpoint yang dipakai UI adalah:

```text
POST /api/v1/auth/sudo-login
Content-Type: application/json

{"secret_key":"<SUPERUSER_SECRET_KEY>"}
```

JWT break-glass berisi `sub: "sudo"`, `user_id: "sudo"`,
`role: "SUPERADMIN"`, dan `auth_method: "breakglass"`. Akun internal reserved
ini dibuat di tabel `users` saat login pertama berhasil. Jangan menghapus atau
mengubah row tersebut selama break-glass masih digunakan.

## Fitur dan cakupan akses

- `GET /api/v1/admin/monitoring` menampilkan semua project dan sheet, pemilik,
  marking activity terbaru (`GroundTruthLog`), serta error job terbaru.
- JWT `SUPERADMIN` dapat membuka workspace project lintas owner untuk melakukan
  review/edit melalui editor.
- Akses admin monitoring dicatat ke server log. Login break-glass yang berhasil,
  gagal, atau dicoba saat fitur nonaktif juga dicatat; nilai secret tidak pernah
  ditulis ke log.
- Dashboard menampilkan hingga 100 aktivitas/error terbaru secara default.
  Endpoint menerima `limit` dari 1 sampai 500.

## Vendor, rotasi, dan penanganan insiden

- Berikan akses rutin kepada vendor melalui akun BIMA individual dengan role dan
  cakupan project yang sesuai. Jangan membagikan satu akun SSO bersama.
- Break-glass memberi hak `SUPERADMIN` global kepada siapa pun yang memegang
  secret yang sama. Identitas principal di log adalah `sudo`, sehingga log tidak
  dapat membedakan orang yang memakai secret bersama; alamat client dicatat
  sebagai petunjuk audit.
- Jika vendor memang harus memakai break-glass untuk sesi kerja sementara,
  bagikan hanya melalui secret manager yang disetujui organisasi, batasi waktu
  akses, pantau server log, lalu rotasi `SUPERUSER_SECRET_KEY` segera setelah
  pekerjaan selesai. Setelah rotasi, jalankan kembali perintah recreate service
  API di atas. Jangan menaruh secret di chat, email, source control, atau
  dokumentasi onboarding.
- Mengosongkan/mengganti `SUPERUSER_SECRET_KEY` menghentikan login baru dengan
  secret lama setelah service API direstart. JWT yang sudah diterbitkan tetap
  berlaku sampai kedaluwarsa. Jika JWT dicurigai bocor dan perlu dibatalkan
  segera, rotasi `JWT_SECRET_KEY` akan membatalkan seluruh sesi lokal yang aktif,
  termasuk sesi engineer.
