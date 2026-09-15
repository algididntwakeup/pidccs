# Walkthrough — Phase A Implementation: Architecture Modernization & Web Decoupling

> **Status**: Phase A core components implemented and verified atomically.

---

## 1. Stakeholder Decisions Incorporated

Berdasarkan review dan feedback:
1. **Deployment Target**: `docker-compose.yml` on-premise orchestrating 5 containers (`postgres`, `redis`, `api`, `worker`, `frontend`).
2. **Authentication**: Single-user (mock auth) untuk Phase A/B dengan kolom `user_id` dan `tenant_id` terintegrasi di seluruh model database dan skema Pydantic.
3. **Database**: PostgreSQL 16 dengan SQLAlchemy 2.0 asyncpg dan JSON/JSONB payload.
4. **GPU / Execution**: Hybrid architecture (GPU-first dengan graceful fallback ke CPU).
5. **Model Weights**: 5 model YOLO yang ada dikunci di Phase A untuk parity verification.
6. **Golden Test Suite**: 6 sample P&ID dan 6 Excel ground truth dataset.
7. **PDF Vector Annotation**: Editable `PolyLine` vector annotations (PyMuPDF) dipertahankan untuk Adobe Acrobat.
8. **Line List Source**: Dynamic Column Mapping architecture.

---

## 2. Directory Architecture (`backend/`, `frontend/`, `docs/`)

```
c:\Werk\pidccs\
├── backend/
│   ├── app/
│   │   ├── adapters/          # Storage and PDF rendering abstractions
│   │   │   ├── pdf_renderer.py # pypdfium2 thread-safe renderer with fitz fallback
│   │   │   └── storage.py      # Local / MinIO storage adapter
│   │   ├── config.py          # Pydantic BaseSettings (DB, Redis, storage, weights)
│   │   ├── db/                # SQLAlchemy 2.0 async engine & session
│   │   ├── models/            # Project, Sheet, Job models with user_id & tenant_id
│   │   ├── routers/           # 16 REST endpoints + WebSocket progress
│   │   ├── schemas/           # Pydantic v2 domain & API schemas
│   │   ├── services/          # ProjectService, DetectionService, GroupingService, etc.
│   │   └── main.py            # FastAPI entrypoint with CORS & lifespan
│   ├── worker/                # Celery worker application & detection tasks
│   ├── tests/                 # Automated DB, schema, grouping, and export tests
│   ├── requirements.txt       # Backend dependencies (pure, no PyQt5)
│   └── Dockerfile             # Multi-stage container build
├── frontend/                  # Next.js 14 Web UI
│   ├── src/app/               # Projects listing & OpenSeadragon canvas workspace
│   ├── src/lib/api.ts         # REST API client
│   ├── src/types/schema.ts    # TypeScript interfaces matching backend
│   └── Dockerfile
├── pidcorr/                   # Pure computer vision & domain logic
└── docker-compose.yml         # 5-container orchestration
```

---

## 3. Atomic Implementation Highlights

### A.02: Core Data Schemas & Multi-Tenancy
- Implemented `TenantUserBase` providing forward-compatible `tenant_id` and `user_id` fields.
- Created complete Pydantic v2 schemas:
  - `SymbolDetection` & `SymbolPatch`
  - `PipingID` & `PipingIDPatch`
  - `PipeRun` (polylines)
  - `ConnectionPoint` (spec breaks)
  - `CorrosionSystem` & `CorrosionCircuit` (API RP 970)
  - `DigitizationResult`
  - `ProjectResponse` & `SheetResponse`
  - `JobResponse` & `ProgressEvent`
  - `ValidationReport` & `ValidationCheck`

### A.04: PDF Renderer Adapter (`pypdfium2` + Safe Path Reading)
- Implemented `PDFRenderer` protocol using `pypdfium2` as the primary thread-safe, permissive renderer.
- Added graceful fallback to `fitz`.
- Solved Windows Unicode path decoding via `np.fromfile` and `cv2.imdecode`.
- Tested on `Contoh P&ID/BCD3-605-42-PID-1-005-01 Rev.4-CCD2.pdf`: verified rendering output shape `(1655, 1170, 3)`.

### A.05: FastAPI App & Database Layer
- Initialized FastAPI with OpenAPI 3.1.0 specification (16 endpoints registered).
- Configured SQLAlchemy models for `Project`, `Sheet`, and `Job`.
- Native JSON/JSONB serialization for detection results and corrosion circuits.

### A.06 & A.08: Async Queue & WebSocket Progress
- Configured Celery task queue with Redis broker.
- Implemented `detect_sheet_task` publishing real-time events to Redis pub/sub.
- WebSocket endpoint `/ws/progress/{job_id}` forwarding progress live to web frontend.

### A.12: Deliverable Exports
- Implemented `ExportService`:
  - Excel Line Register (`.xlsx`) via `openpyxl`
  - Word Asset Register (`.docx`) via `python-docx`
  - Vector PDF (`.pdf`) with Adobe Acrobat editable `PolyLine` annotations via `PyMuPDF`
  - Marked PNG (`.png`) with corner legend

---

## 4. Automated Test Results

Executed `backend/tests/test_api_and_db.py`:
```
Project & Sheet database test PASSED successfully!
Grouping and Validation logic test PASSED successfully!
Deliverable exports (XLSX, DOCX, Vector PDF) PASSED successfully!
```

- **OpenAPI Schema**: Validated 100% against OpenAPI 3.1.0 standard with 16 registered paths.
- **Deliverables**: Verified generation of `.xlsx`, `.docx`, and Acrobat vector `.pdf` files.
- **Frontend Build**: `next build` compiled successfully (0 errors) with standalone server tracing.
- **Docker Compose**: `docker compose config` parsed and validated with all 5 containers configured.

---

## 5. Phase B: Perception Core Modernization

### Task B.01: Perception Interfaces
- Defined formal Abstract Base Classes in [`pidcorr/interfaces/perception.py`](file:///c:/Werk/pidccs/pidcorr/interfaces/perception.py):
  - `BaseSymbolDetector`
  - `BaseTextExtractor`
  - `BaseLineTracer`
  - `BasePipingIDParser`
  - `BaseSubtypeClassifier`

### Task B.02: Production Adapter Wrappers
- Created concrete adapters conforming to ABC interfaces in [`pidcorr/implementations/`](file:///c:/Werk/pidccs/pidcorr/implementations/):
  - `YOLOTiledDetector`: Ultralytics YOLO inference with 640px sliding tiles, NMS, contour boxes, and full-page equipment detection.
  - `RapidOCRExtractor`: Baseline multi-angle (0°, 90°, 270°) tiled OCR scanner.
  - `MorphologyLineTracer`: Domain morphology line tracer with inline valve gap bridging.
  - `RegexPipingIDParser`: Regex parser supporting PetroChina and Pertamina line numbering schemas.
  - `YOLOValveClassifier`: YOLO11 valve classification and ISA-5.1 instrument OCR parsing.

### Task B.03: Pipeline Orchestrator Refactor
- Implemented [`PipelineOrchestrator`](file:///c:/Werk/pidccs/pidcorr/orchestrator.py) with full Dependency Injection (DI).
- Allows swappable Perception modules at runtime without modifying the caller.

### Task B.04: PaddleOCR Integration (`PaddleOCRExtractor`)
- Implemented [`PaddleOCRExtractor`](file:///c:/Werk/pidccs/pidcorr/implementations/paddleocr_extractor.py) conforming to `BaseTextExtractor`:
  - **Single-Pass Angle-Aware Inference**: Leverages PP-OCRv4 angle classifier (`use_angle_cls=True`) to detect text orientations natively in a single pass without brute-force 3-angle (0°, 90°, 270°) image rotations.
  - **Adaptive Tiling**: Uses 2048px tiles with 15% overlap for reduced overhead.
  - **Seamless Hybrid Engine**: Supports native Baidu `paddleocr` if available, with automatic seamless fallback to ONNX PP-OCRv4 runtime.
  - **Domain Preservation**: Fully preserves multi-line label pairing (`_pair_two_line`), unit snap (`_snap_units`), and spatial clustering (`_cluster`).
  - **Config Factory Integration**: Added `OCR_IMPL=paddleocr` support in [`pidcorr/factory.py`](file:///c:/Werk/pidccs/pidcorr/factory.py).

### Automated Test Verification
Executed `pytest backend/tests/ -v`:
```
backend/tests/test_api_and_db.py::test_db_project_sheet_lifecycle PASSED  [ 12%]
backend/tests/test_api_and_db.py::test_grouping_and_validation PASSED     [ 25%]
backend/tests/test_api_and_db.py::test_exports PASSED                     [ 37%]
backend/tests/test_phase_b_perception.py::test_interface_inheritance PASSED  [ 50%]
backend/tests/test_phase_b_perception.py::test_piping_id_parser_regex PASSED  [ 62%]
backend/tests/test_phase_b_perception.py::test_mock_orchestrator_dependency_injection PASSED [ 75%]
backend/tests/test_phase_b_perception.py::test_paddleocr_extractor_single_pass PASSED  [ 87%]
backend/tests/test_phase_b_perception.py::test_factory_config PASSED      [100%]

======================= 8 passed, 6 warnings in 18.61s ========================
```
- **100% test pass rate** (8 of 8 tests passed).
- `PaddleOCRExtractor` verified running single-pass extraction, returning valid `PipingID` dataclasses and spec break connection-point tokens.


