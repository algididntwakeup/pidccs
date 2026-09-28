# PIDCCS: P&ID Studio Web Platform
### Next-Gen Automated P&ID Perception, API RP 970 Corrosion Circuit Synthesis & Interactive Engineering Review

[![Continuous Integration (CI)](https://github.com/algididntwakeup/pidccs/actions/workflows/ci.yml/badge.svg)](https://github.com/algididntwakeup/pidccs/actions/workflows/ci.yml)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js 14](https://img.shields.io/badge/Next.js-14.2+-black?logo=next.js&logoColor=white)](https://nextjs.org/)
[![API RP 970](https://img.shields.io/badge/Standard-API%20RP%20970-orange)](https://www.api.org/)
[![Docker Ready](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

---

## Alur Aktif: HITL & Fast Trace

Panduan untuk agent yang melanjutkan pekerjaan ada di [AGENTS.md](AGENTS.md).
Dokumen itu mencatat keputusan produk, batas refactor, dan pemeriksaan sebelum
menyatakan perubahan selesai.

Workspace sheet mengutamakan **Fast Trace** (`lines_only`): PDF vektor ditelusuri langsung dari geometri PDF tanpa OCR/YOLO atau rasterisasi resolusi penuh. Untuk PDF raster dan gambar PNG/JPG, tracer raster digunakan sebagai fallback. Engineer kemudian memperbaiki hasil melalui Magic Wand, Box Trace, pen manual, edit titik, dan Undo/Redo. OCR/YOLO tetap tersedia melalui aksi **full detection** atau **enrichment** atas permintaan; keduanya bukan prasyarat Fast Trace. Uraian pipeline otomatis di bawah ini adalah kemampuan mode full/legacy, bukan jalur Fast Trace.

CI menjalankan pytest, ESLint, pemeriksaan tipe TypeScript, dan production build. Dataset gambar industri di `Contoh P&ID` bersifat lokal dan tidak tersimpan di Git; pengujian yang memerlukannya dilewati di CI, sedangkan pengujian sintetis tetap dijalankan.

---

## Ringkasan Eksekutif / Executive Summary

**PIDCCS (P&ID Studio Web Platform)** adalah platform re-engineering modern dari perangkat lunak desktop monolitik PyQt5 menjadi sistem *cloud-native / web-first distributed architecture*. Platform ini dirancang khusus untuk memproses dokumen **Piping & Instrumentation Diagram (P&ID)** skala industri minyak, gas, dan petrokimia secara otomatis, akurat, dan sesuai standar keselamatan proses internasional.

Platform ini memadukan:
1. **Computer Vision & Perception Core Modern**: Deteksi simbol teknik (YOLOv8 + SAHI multi-scale tiling), ekstraksi teks single-pass (RapidOCR/PaddleOCR), pelacakan pipa morfologi & skeleton dengan pemisahan persilangan (*crossover line separation*), serta identifikasi *Off-Page Connector* (OPC).
2. **API RP 970 Corrosion Circuit Rule Engine**: Partisi otomatis sirkuit korosi berdasarkan batas fase proses, gradien suhu & tekanan operasi, spesifikasi metalurgi material pipa, dan batas unit proses (*battery limits*).
3. **Multi-Sheet Project Topology Graph**: Penyambungan continuum sirkuit korosi lintas gambar P&ID berbasis pengenalan cerdas OPC.
4. **Interactive Human-in-the-Loop Web Workspace**: Antarmuka web mutakhir berbasis Next.js 14 dan OpenSeadragon deep-zoom canvas untuk audit, review, koreksi manual (*override*), pelacakan provenance, dan ekspor dokumen deliverable teknik (PDF beranotasi vektor, Excel Line Register, Word Asset Register, DXF/SVG, dan JSON).

---

## Arsitektur Sistem / System Architecture

Platform dibangun menggunakan arsitektur *clean layered microservices* yang memisahkan frontend reaktif, backend API asinkron, task queue terdistribusi, dan modul komputasi persepsi teknik.

```mermaid
flowchart TB
    subgraph Client["Frontend Client (Next.js 14 + Tailwind CSS)"]
        UI["Web Browser / Engineer Desktop"]
        OSD["OpenSeadragon Deep-Zoom Canvas"]
        Table["Line Register & Circuit Review Table"]
        Editor["Interactive Circuit & Material Editor"]
        WSClient["WebSocket Client (Live Progress & Stream)"]
    end

    subgraph Gateway["API & Application Gateway (FastAPI Async)"]
        REST["REST Endpoints (/api/v1)"]
        WS["WebSocket Hub (/api/v1/ws)"]
        AuthTenant["Tenant & Project Context Handler"]
    end

    subgraph Queue["Async Task Queue & Real-Time Broker"]
        Redis[("Redis 7 (Broker & State Cache)")]
        CeleryWorker["Celery Worker Pool (Heavy Perception Tasks)"]
    end

    subgraph Perception["Perception & Vision Pipeline (pidcorr)"]
        SAHI["SAHI Tiled Detector (YOLOv8)"]
        OCR["Single-Pass RapidOCR / PaddleOCR"]
        Tracer["Skeleton Line Tracer + Crossover Separator"]
        OPC["Off-Page Connector Extractor"]
    end

    subgraph Engine["Engineering Logic Engine"]
        APIRule["API RP 970 Rule Engine"]
        LineMap["Line List Ingestion & Fuzzy Matcher"]
        TopoGraph["Multi-Sheet Topology Graph Engine"]
        Provenance["Audit Trail & Circuit Provenance Logger"]
    end

    subgraph Persistence["Storage & Database Layer"]
        DB[("PostgreSQL 16 / Async SQLite")]
        Storage[("Local / S3 Storage (StorageAdapter)")]
    end

    UI --> REST
    UI --> WS
    REST --> DB
    REST --> Storage
    REST --> Redis
    WS <--> Redis
    CeleryWorker <--> Redis
    CeleryWorker --> Perception
    Perception --> Engine
    Engine --> DB
    Engine --> Storage
```

---

## Alur Kerja Komputasi / Core Processing Pipeline

```mermaid
sequenceDiagram
    autonumber
    actor Eng as Corrosion Engineer
    participant Web as Web Frontend (Next.js)
    participant API as FastAPI Gateway
    participant Q as Celery / Redis
    participant P as Perception Core
    participant E as API RP 970 Engine
    participant DB as Database & Storage

    Eng->>Web: 1. Unggah Dokumen P&ID (PDF/PNG) & Line List (Excel)
    Web->>API: POST /api/v1/projects/{id}/sheets & /linelist
    API->>DB: Simpan file asli & metadata sheet
    API->>Q: Dispatch background task `run_sheet_detection`
    Q->>P: Render 350 DPI -> SAHI YOLO -> OCR -> Skeleton Tracer
    P-->>Web: Streaming progress bar via WebSocket (10%..100%)
    P->>E: Parsing Piping ID & Binding Data Line List
    E->>E: Partisi Sirkuit Korosi (Fase, Suhu, Tekanan, Material)
    E->>E: Multi-Sheet Stitching via OPC Topology Graph
    E->>DB: Simpan DigitizationResult & Circuit Entities
    DB-->>Web: Tampilkan gambar interaktif, layer sirkuit, & tabel register
    Eng->>Web: 2. Review, Validasi & Override Manual jika diperlukan
    Web->>API: PATCH /api/v1/projects/{id}/circuits/overrides
    API->>DB: Update sirkuit dengan Audit Provenance
    Eng->>Web: 3. Download Deliverables (PDF Vektor, Excel, Word)
    Web->>API: GET /api/v1/projects/{id}/export?format=pdf
    API-->>Eng: File hasil deliverable beresolusi tinggi
```

---

## Fitur Unggulan / Key Features

### 1. Modern Perception & Vision Core
* **Abstraksi Berbasis Interface**: Modul deteksi dibangun dengan *Dependency Injection* melalui abstract base class (`BaseSymbolDetector`, `BaseTextExtractor`, `BaseLineTracer`, `BasePipingIDParser`, `BaseSubtypeClassifier`), memudahkan penggantian model AI tanpa merombak logika aplikasi.
* **SAHI Multi-Scale Tiling**: Mengatasi kendala resolusi dokumen teknik berukuran raksasa (hingga 8000x6000 piksel) tanpa kehilangan simbol-simbol kecil seperti *valves*, *reducers*, dan *spectacle blinds*.
* **Single-Pass Text Extraction**: Ekstraksi teks OCR dilakukan satu kali untuk seluruh kanvas dengan pemetaan koordinat global, menghasilkan peningkatan kecepatan ekstraksi teks hingga 2.5x dibanding sistem lawas.
* **Skeleton Line Tracer dengan Pemisahan Persilangan (Crossover Separation)**: Menganalisis topologi persimpangan garis (*junction degree analysis*) untuk membedakan percabangan pipa (T-junctions) dengan persilangan pipa tanpa sambungan (crossovers), mencegah penggabungan sirkuit yang salah.
* **Deteksi Off-Page Connector (OPC)**: Mendeteksi simbol panah referensi gambar keluar/masuk (*incoming/outgoing OPC*), mengekstrak nomor sheet referensi dan line tag terkait.

### 2. API RP 970 Engineering Rule Engine
* **Partisi Berbasis Standar**: Membagi jaringan pipa menjadi Sirkuit Korosi (*Corrosion Loops*) berdasarkan kriteria ketat API RP 970:
  * **Proses & Fase Fluida**: Memisahkan fase gas, cair, dua fase (*two-phase*), uap air (*steam*), dan kondensat.
  * **Batas Operasi Suhu & Tekanan**: Mengidentifikasi titik lonjakan suhu (misalnya sebelum/sesudah *heat exchanger* atau *reboiler*).
  * **Material Konstruksi**: Membagi sirkuit jika terjadi transisi metalurgi (misalnya dari *Carbon Steel* ke *Stainless Steel 316* atau *Inconel*).
  * **Batas Unit Proses (*Battery Limits*)**: Mengisolasi unit proses yang memiliki mekanisme degradasi korosi berbeda.
* **Ingesti & Rekonsiliasi Line List Dinamis**: Ingestion engine fleksibel yang dapat membaca format Excel/CSV kustom dengan pencocokan nama kolom otomatis (*dynamic column aliases*) dan *fuzzy tag reconciliation* (telah divalidasi dengan dataset 476 baris Plant Unit 605).
* **Audit Trail & Circuit Provenance**: Setiap segmen pipa mencatat riwayat pembuatannya: algoritma penentu, baris referensi Line List, alasan partisi, timestamp, serta catatan insinyur jika ada *manual override*.

### 3. Topologi Proyek Multi-Halaman (*Multi-Sheet Continuum*)
* **Multi-Page Graph Service**: Menghubungkan sheet P&ID yang berurutan melalui pencocokan OPC cerdas.
* **Continuum Circuits**: Sirkuit korosi yang mengalir melintasi beberapa gambar P&ID otomatis digabungkan menjadi satu kesatuan entitas sirkuit tingkat fasilitas/unit.
* **Deteksi Disinkronisasi OPC**: Menandai jika ada pipa keluar dari Sheet A menuju Sheet B namun tag pipa atau kelas perpipaannya tidak cocok pada Sheet B (*integrity alert*).

### 4. Interactive Human-in-the-Loop Web Workspace
* **OpenSeadragon Deep-Zoom**: Navigasi kanvas ultra-halus (pan/zoom hingga 350 DPI) tanpa freeze memori pada browser.
* **Layer Visualisasi Warna Sirkuit**: Setiap sirkuit korosi diberi warna kontras unik dengan slider transparansi (*opacity control*).
* **Line Register Interaktif**: Tabel lengkap seluruh segmen pipa dengan filter status, pencarian instan, dan inspektur atribut.
* **Circuit Override Modal**: Antarmuka mudah untuk memindahkan garis antar sirkuit, membuat sirkuit baru, memecah sirkuit, serta menyertakan justifikasi teknis resmi.
* **Live WebSocket Telemetry**: Indikator progres inference real-time langkah-demi-langkah (Render PDF -> Deteksi Simbol -> Tracing Garis -> Synthesis API RP 970). Progres ini **tetap tampil walau pengguna berpindah halaman dan kembali** — state deteksi yang berjalan di server di-*resume* otomatis saat halaman dibuka ulang, sehingga kanvas tidak pernah tampak kosong tanpa penjelasan.

### 5. Format Ekspor Deliverable Lengkap
* **Annotated Vector PDF**: Dokumen PDF berkualitas tinggi dengan garis pipa diwarnai sesuai sirkuit korosi dan dilengkapi blok legenda teknis.
* **High-Res Annotated PNG**: Gambar kanvas 350 DPI dengan anotasi warna sirkuit.
* **Excel Line & Circuit Register (`.xlsx`)**: Tabel data lengkap siap integrasi ke perangkat lunak Manajemen Integritas Aset (AIMS / SAP PM).
* **Word Asset Register (`.docx`)**: Laporan ringkasan naratif sirkuit korosi untuk dokumentasi inspeksi berkala.
* **JSON Interchange Format**: Skema data terstruktur untuk integrasi REST API pihak ketiga.

---

## Tumpukan Teknologi / Tech Stack

| Komponen | Teknologi | Keterangan |
| :--- | :--- | :--- |
| **Backend Framework** | Python 3.13, FastAPI | Async high-performance REST & WebSocket API |
| **ORM & Database** | SQLAlchemy 2.0 (Async), asyncpg, aiosqlite | Support PostgreSQL (produksi) & SQLite (testing) |
| **Task Queue & Broker** | Celery 5.4, Redis 7 | Antrean komputasi asinkron terdistribusi |
| **Computer Vision** | OpenCV 4.9, PyMuPDF, pypdfium2, Shapely | Rendering gambar beresolusi tinggi & operasi geometri spasial |
| **AI / ML Models** | Ultralytics YOLOv8, SAHI, RapidOCR (ONNX) | Deteksi simbol teknik & ekstraksi teks single-pass |
| **Frontend Framework** | Next.js 14.2 (App Router), TypeScript, React 18 | Web interface modern, server-side rendering & client components |
| **Styling & Icons** | Tailwind CSS 3.4, Lucide React | Web UI/UX responsif (dioptimalkan untuk 1080p dan laptop 14" ke bawah) |
| **Deep Zoom Canvas** | OpenSeadragon 4.1 | Visualisasi gambar dokumen resolusi raksasa (20MP+) |
| **Container & CI/CD** | Docker, Docker Compose, GitHub Actions | Otomatisasi pengujian & packaging produksi |

---

## Panduan Memulai / Quickstart Guide

Anda dapat menjalankan platform ini menggunakan dua metode: **Docker Compose** (disarankan untuk produksi) atau **Native Local Development**.

### Metode 1: Menjalankan Menggunakan Docker Compose (Direkomendasikan)

Pastikan Docker Desktop sudah terinstal dan berjalan di sistem Anda.

#### Di Windows:
Cukup klik ganda pada file `start_docker.bat` atau jalankan via PowerShell/CMD:
```cmd
start_docker.bat
```

#### Di Linux / macOS:
```bash
chmod +x start_docker.sh
./start_docker.sh
```

Atau jalankan perintah Docker Compose langsung:
```bash
# Salin konfigurasi environment jika belum ada
cp .env.example .env

# Jalankan seluruh service (PostgreSQL, Redis, Celery Worker, Backend API, Frontend)
docker compose up --build -d
```

Setelah semua kontainer berjalan:
* **Frontend Web App**: Buka browser di [http://localhost:3000](http://localhost:3000)
* **Backend API Swagger Docs**: Buka [http://localhost:8000/docs](http://localhost:8000/docs)
* **Health Check Endpoint**: [http://localhost:8000/health](http://localhost:8000/health)

---

### Metode 2: Menjalankan Pengembangan Lokal (Local Development)

#### 1. Prasyarat Sistem
* **Python**: Versi 3.11, 3.12, atau 3.13
* **Node.js**: Versi 18.x atau 20.x (disertai `npm`)
* **Redis Server**: Berjalan di `localhost:6379` (atau gunakan Docker: `docker run -d -p 6379:6379 redis:7-alpine`)

#### 2. Di Windows (Otomatis):
Jalankan skrip peluncur lokal satu klik:
```cmd
start_local.bat
```

#### 3. Menjalankan Manual (Step-by-Step):

**Langkah A: Setup Lingkungan Backend**
```bash
# 1. Buat dan aktifkan virtual environment (opsional jika sudah ada)
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 2. Instal dependensi backend
pip install -r backend/requirements.txt

# 3. Jalankan Celery Worker (di Terminal 1)
# Mode dev tanpa Celery worker: atur CELERY_TASK_ALWAYS_EAGER=true di .env
celery -A backend.app.workers.tasks worker --loglevel=info -P solo

# 4. Jalankan FastAPI Server (di Terminal 2)
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Langkah B: Setup Lingkungan Frontend**
```bash
# Di Terminal 3
cd frontend

# 1. Instal dependencies
npm install

# 2. Jalankan development server
npm run dev
```

Buka [http://localhost:3000](http://localhost:3000) pada browser Anda.

---

## Konfigurasi Lingkungan / Environment Configuration

File `.env` di root direktori mengatur seluruh parameter konfigurasi aplikasi:

| Variabel | Default | Deskripsi |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/pidstudio` | URL koneksi database asinkron (Gunakan `sqlite+aiosqlite:///./test_pidstudio.db` untuk dev lokal tanpa Postgres) |
| `DATABASE_SYNC_URL` | `postgresql://postgres:postgres@localhost:5432/pidstudio` | URL koneksi database sinkron untuk migrasi Alembic |
| `REDIS_URL` | `redis://localhost:6379/0` | URL broker antrean Celery dan state cache |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Atur `true` untuk mengeksekusi task sinkron di thread utama (berguna saat debug lokal tanpa worker) |
| `STORAGE_DIR` | `./data/storage` | Direktori penyimpanan file hasil unggahan dan hasil export |
| `WEIGHTS_DIR` | `./runs` | Direktori model weights YOLO |
| `CACHE_DIR` | `./.pidcache` | Direktori cache intermediate gambar |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | URL endpoint Backend API yang diakses oleh Frontend |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000` | URL endpoint WebSocket untuk telemetry live progress |

---

## Verifikasi & Pengujian Otomatis / Automated Testing

Platform ini dilengkapi rangkaian pengujian otomatis end-to-end yang menguji integritas seluruh modul:

### 1. Menjalankan Backend Pytest Suite
```bash
# Menjalankan seluruh 38 unit & integration test
pytest backend/tests/ -q

# Menjalankan pengujian End-to-End sistem industri penuh (Unit 605 Real Data)
pytest backend/tests/test_e2e_full_system.py -v -s
```

*Cakupan Pengujian:*
* `test_api_and_db.py`: Verifikasi lifecycle pembuatan proyek, upload sheet, dan isolasi multi-tenant.
* `test_phase_b_perception.py`: Verifikasi kontrak interface, parsing tag perpipaan regex, dan dependency injection orchestrator.
* `test_phase_c_engine.py`: Verifikasi engine API RP 970, partisi fase/suhu/material, dan audit trail provenance.
* `test_phase_c_topology.py`: Verifikasi pembentukan graph multi-sheet dan continuum sirkuit korosi lintas halaman.
* `test_snap_equipment.py` / `test_split_and_color.py`: Verifikasi snapping endpoint ke equipment, pemisahan run, dan persistensi warna.
* `test_masking_and_runs.py`: Verifikasi penekan artefak teks pra-skeletisasi dan filter stub pada line tracing.
* `test_sheet_tiles_cache.py`: Verifikasi header cache `/raw` dan `/thumbnail`.
* `test_e2e_full_system.py`: Pengujian siklus penuh 8 tahap (Proyek -> 2 Sheet berturut-turut -> 476 baris Line List -> API RP 970 -> Multi-Sheet Graph -> Manual Override -> Ekspor PDF/Excel/Word).

> Catatan: fixture pengujian diresolusi melalui `backend/tests/_fixtures.py` (`fixture_path()`), yang tahan terhadap perbedaan path root antara host dan container Docker.

### 2. Menjalankan Frontend Linting & Build Verification
```bash
cd frontend

# Verifikasi ESLint
npm run lint

# Verifikasi TypeScript type-checking & production bundling
npm run build
```

### 3. Menjalankan Frontend End-to-End (Playwright)
Pengujian browser E2E yang menguji perilaku kanvas terhadap stack Docker yang sedang berjalan
(frontend di `:3000`, api di `:8000`):
```bash
cd frontend
npx playwright install chromium   # sekali saja (unduh binary browser)
npm run e2e                       # jalankan semua project viewport
npm run e2e:report                # buka laporan HTML hasil run
```

*Cakupan E2E* (`frontend/e2e/canvas-overlay.spec.ts`, dijalankan pada viewport `desktop-1080p` 1920x1080 dan `laptop-14in` 1366x768):
* Guard layout responsif: tool dock (*Box Trace* / *Manual Pen*) tidak boleh tertutup bar kontrol halaman pada layar sempit.
* Persistensi SVG overlay tracing saat berpindah mode (Digitization <-> Corrosion System/Circuit) dan saat resize ekstrem.

> Praktik: jalankan E2E ini secara berkala (mis. setelah *big revamp*), bukan setiap edit kecil.

### 4. Continuous Integration (GitHub Actions)
Setiap *push* atau *pull request* ke branch `main`/`master` secara otomatis memicu alur kerja CI di `.github/workflows/ci.yml`:
* **Job Backend**: Menyiapkan runner `ubuntu-latest`, menginstal library sistem (`libgl1`, `libglib2.0-0`, `libgomp1`), menginstal Python 3.13, dan mengeksekusi seluruh pengujian pytest.
* **Job Frontend**: Menyiapkan Node.js 20, mengeksekusi `npm ci`, menjalankan validasi `npm run lint`, dan memverifikasi kompilasi `npm run build`.

---

## Struktur Direktori Proyek / Project Layout

```text
pidccs/
├── .github/
│   └── workflows/
│       └── ci.yml                 # GitHub Actions CI pipeline configuration
├── backend/                       # Modern Backend Service
│   ├── app/
│   │   ├── adapters/              # Storage adapters & PDF renderer adapters
│   │   ├── db/                    # SQLAlchemy async engine, base model, & session
│   │   ├── models/                # Project, Sheet, & Corrosion Circuit models
│   │   ├── routers/               # FastAPI endpoints (projects, sheets, circuits, exports, linelist)
│   │   ├── schemas/               # Pydantic validation models & schemas
│   │   ├── services/              # API RP 970 Engine, Topology Graph, Ingestion, & Export services
│   │   ├── workers/               # Celery worker application & background tasks
│   │   ├── config.py              # Centralized environment settings
│   │   └── main.py                # FastAPI ASGI application entrypoint
│   ├── tests/                     # Automated test suites (Pytest)
│   │   ├── _fixtures.py           # Path-resolution helper (host vs container)
│   │   ├── test_api_and_db.py
│   │   ├── test_e2e_full_system.py
│   │   ├── test_masking_and_runs.py
│   │   ├── test_phase_b_perception.py
│   │   ├── test_phase_c_engine.py
│   │   ├── test_phase_c_topology.py
│   │   ├── test_sheet_tiles_cache.py
│   │   ├── test_snap_equipment.py
│   │   └── test_split_and_color.py
│   ├── Dockerfile                 # Backend container definition
│   └── requirements.txt           # Python dependencies
├── frontend/                      # Web Application (Next.js 14)
│   ├── src/
│   │   ├── app/                   # App Router pages (Dashboard, Project Review Workspace)
│   │   ├── components/            # UI components (Viewer, CircuitPanel, Table, Upload)
│   │   ├── lib/                   # API client, WebSocket hooks, Zustand stores
│   │   └── types/                 # TypeScript type definitions
│   ├── e2e/                       # Playwright browser E2E specs
│   │   └── canvas-overlay.spec.ts # Guard layout toolbar & persistensi overlay
│   ├── public/                    # Static assets & OpenSeadragon images
│   ├── playwright.config.ts       # Konfigurasi E2E (viewports desktop & laptop)
│   ├── .eslintrc.json             # ESLint configuration
│   ├── Dockerfile                 # Frontend container definition
│   ├── package.json               # Node.js dependencies & scripts
│   └── tsconfig.json              # TypeScript compiler configuration
├── pidcorr/                       # Core Perception & Engineering Library
│   ├── implementations/           # SAHI, YOLO, RapidOCR, PaddleOCR, Skeleton tracer
│   ├── interfaces/                # Abstract base classes & interfaces
│   ├── factory.py                 # Perception pipeline factory
│   ├── orchestrator.py            # Pipeline orchestrator
│   └── export.py                  # Deliverable export utilities
├── Contoh P&ID/                   # Dataset gambar P&ID sampel industri (Unit 605)
├── combined_dataset/              # Dataset Line List nyata (605_CCD2_loop_dataset.xlsx, dll.)
├── data/                          # Spesifikasi material (material_spec.json) & storage
├── docs/                          # Dokumentasi teknis & panduan deployment
│   └── DEPLOYMENT_GUIDE.md
├── scripts/                       # Skrip automasi & smoke testing
│   └── smoke_test.py
├── .env.example                   # Contoh konfigurasi variabel lingkungan
├── docker-compose.yml             # Orkestrasikan multi-container environment
├── start_docker.bat               # Peluncur satu-klik Docker di Windows
├── start_docker.sh                # Peluncur satu-klik Docker di Linux/macOS
├── start_local.bat                # Peluncur satu-klik Local Dev di Windows
├── BACA DULU - Cara Menjalankan.txt # Panduan cepat bahasa Indonesia
└── README.md                      # Dokumentasi komprehensif proyek
```

---

## Standar Acuan Teknis / References & Standards

1. **API Recommended Practice 970 (API RP 970)**: *Corrosion Control Documents*, American Petroleum Institute, 1st Edition.
2. **API 570**: *Piping Inspection Code: In-service Inspection, Rating, Repair, and Alteration of Piping Systems*.
3. **NACE SP0206 / ISO 21457**: *Materials selection and corrosion control for oil and gas production systems*.
4. **ASME B31.3**: *Process Piping Design Code*.

---

## Lisensi & Hak Cipta / License

Hak Cipta © 2026. Seluruh hak cipta dilindungi undang-undang.  
Didesain dan dibangun untuk digitalisasi dan integritas aset fasilitas industri energi dan petrokimia.
