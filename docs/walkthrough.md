# Walkthrough — Phase A Implementation: Architecture Modernization & Web Decoupling

## Sprint Handover: Test-Suite Green, Playwright E2E & Responsive Toolbar Fix

**Status**: Implemented, built, and deployed locally on 2026-09-21.

### Scope

- **Full backend suite now 100% green (was 4 pre-existing failures).** Root cause was *fixture path
  resolution inside the Docker container*, not the tracing code. Tests computed `_ROOT_DIR = <backend>/..`,
  which on the host is the repo root but **inside the `api` container (`./backend:/app`) collapses to `/`**,
  so fixtures resolved to the non-existent `/Contoh P&ID/…` and `/combined_dataset/…`. Additionally
  `combined_dataset` was **never mounted** into the containers.
  - New helper [`backend/tests/_fixtures.py`](file:///c:/Werk/pidccs/backend/tests/_fixtures.py) —
    `fixture_path(*parts)` probes candidate roots (`<repo>`, `/app`, cwd), drops a redundant leading
    `backend` segment, and returns the first existing match.
  - Updated `test_api_and_db.py`, `test_e2e_full_system.py`, `test_phase_c_engine.py`, and
    `test_snap_equipment.py` to resolve fixtures through the helper. No assertion was weakened.
  - [`docker-compose.yml`](file:///c:/Werk/pidccs/docker-compose.yml): mounted `./combined_dataset:/app/combined_dataset`
    into **`api`** and **`worker`** (previously missing).
  - **Result**: `pytest tests/ -q` → **38 passed, 0 skipped, 0 failed** both on host and in-container
    (previously 4 failed).
- **Playwright E2E harness (overlay & toolbar persistence).** Added browser-level regression tests for
  the two behaviours that unit tests cannot cover.
  - [`frontend/playwright.config.ts`](file:///c:/Werk/pidccs/frontend/playwright.config.ts) — two viewport
    projects: `desktop-1080p` (1920×1080) and `laptop-14in` (1366×768). Drives the live Docker stack
    (frontend :3000), does **not** start a dev server.
  - [`frontend/e2e/canvas-overlay.spec.ts`](file:///c:/Werk/pidccs/frontend/e2e/canvas-overlay.spec.ts) —
    (a) the bottom tool dock (*Box Trace* / *Manual Pen*) must not intersect the floating page-controls
    bar (*Pipa: ON/OFF* + opacity); (b) the tracing SVG overlay (`[data-pipe-interactive="true"]`) must
    stay attached across Digitization ⇄ Corrosion System/Circuit switches and extreme resizes.
  - E2E code is excluded from the production image (`frontend/.dockerignore`) and from the Next
    typecheck (`tsconfig.e2e.json`). Scripts: `npm run e2e`.
  - **Result**: `npx playwright test` → **4 passed, 0 skipped, 0 failed** (both viewports).
- **Responsive layout fix for <1080p / 14" laptops.** Root cause: the tool dock
  (`InteractivePipeCanvas.tsx`, `bottom-6 left-1/2`, z-40) and the page-controls bar
  (`page.tsx`, `bottom-6 left-6`, z-40) shared the **same bottom band and the same z-index**, so at
  canvas widths below ~1470px — exactly what a 14" Full-HD panel yields after the fixed 384px right
  panel — the page bar painted on top of the dock and hid *Box Trace* / *Manual Pen*.
  - Dock is now lifted to `bottom-24` on anything below `2xl` (1536px) and only shares `bottom-6` on
    wide desktops; it gained `z-50`, `flex-wrap`, and `max-w-[calc(100%-1.5rem)]`.
  - `page.tsx`: page bar wraps (`flex-wrap` + `max-w`), header wraps and its verbose button labels are
    hidden below `xl`/`2xl`, the view-mode switcher scrolls horizontally, and the right panel is
    `w-80 xl:w-96` instead of a fixed `w-96`.
  - The status toast moved to `bottom-36 2xl:bottom-20`; the selection popover and re-scan modal are
    clamped (`max-height` + `overflow-y-auto`).

### Verification

- `npx playwright test` → **4 passed** (desktop-1080p + laptop-14in).
- `pytest tests/ -q` (host & container) → **38 passed, 0 failed, 0 skipped**.
- `npx tsc --noEmit` → 0 errors.
- `docker compose build frontend` + `up -d frontend` → all 5 containers Up/healthy.

---

## Sprint Handover: Text-Artifact Suppression, Highlight Toggle & Canvas Deselect

**Status**: Implemented, built, and deployed locally on 2026-09-18.

### Scope

- **Pre-skeletonization text-artifact suppression (line-tracing quality).** Audit of the tracing
  pipeline showed why garbage traces persisted despite dilated OCR masking: OCR returns only the
  full *strings* it recognises, so any character it misses (size fractions like `3/4`, note words
  like `BY INSTR`, hand scratches) stays as white ink and gets skeletonized into a stray run. On a
  real sheet the pre-skeleton binary held **~2,270 glyph-sized islands** while OCR emitted only
  **162 tokens** — i.e. masking removed boxes, not the leaked glyphs.
  - New `suppress_text_artifacts()` in [`pidcorr/lines.py`](file:///c:/Werk/pidccs/pidcorr/lines.py)
    runs `cv2.connectedComponentsWithStats` on the pre-skeleton binary and blackouts compact
    glyph islands: `max_side <= 10pt`, `area <= 40pt²`, `aspect_ratio < 4.0`. Elongated pipe
    stubs (w=2, h=12 → AR 6) and large networks are preserved; `protect_boxes` (equipment/valve/
    instrument bboxes) shield sensitive regions. All thresholds scale with DPI.
  - New `suppress_floating_stubs()` post-filter removes only **short, isolated, diagonal** strokes
    that touch no detected symbol (no-op when `detections` is empty, e.g. ROI re-scan).
  - Wired into `SkeletonLineTracer` between furniture masking and skeletonization, behind the
    constructor flags `suppress_text_artifacts` / `suppress_floating_stubs` (both default `True`).
- **Measured impact on real P&IDs** (200 dpi, full pipeline incl. detections):

  | Sheet | Runs before | Runs after | Δ | total_len before | total_len after |
  |---|---:|---:|---:|---:|---:|
  | PID-1-011-02 | 341 | 235 | **−31.1%** | 41,280 | 38,515 (−6.7%) |
  | PID-1-005-01 | 257 | 154 | **−40.1%** | 37,854 | 36,738 (−3.0%) |
  | PID-1-012-01 | 329 | 221 | **−32.8%** | 36,433 | 32,907 (−9.7%) |

  Run count drops by ~⅓ while total traced length barely moves — the removed runs are short junk,
  not real pipes. Scripts: [`backend/scripts/compare_runs_count.py`](file:///c:/Werk/pidccs/backend/scripts/compare_runs_count.py),
  [`backend/scripts/diag_trace_artifacts.py`](file:///c:/Werk/pidccs/backend/scripts/diag_trace_artifacts.py).
- **Purple focus highlight toggle + canvas deselect.** Clicking the same Corrosion System / Circuit
  card again now clears the highlight and deselects it; clicking empty canvas space also clears the
  purple box (and the System/Circuit/Piping-ID selection) without disturbing clicks that land on the
  pipe overlay or its popover.

### Verification

- `pytest tests/test_masking_and_runs.py tests/test_snap_equipment.py tests/test_split_and_color.py -q`
  → **17 passed, 1 skipped** (4 new tests for the artifact/stub filters).
- Full `pytest tests/ -q` → 29 passed, 1 skipped, 4 failed; the 4 failures
  (`test_exports`, `test_end_to_end_full_system_lifecycle`, `test_linelist_parser_excel`,
  `test_linelist_api_endpoint_lifecycle`) are **pre-existing** — reproduced identically on the
  pristine checkout via `git stash` (missing `/Contoh P&ID/...png` fixture path), unrelated to tracing.
- `npx tsc --noEmit` → 0 errors.
- `docker compose build frontend` + `up -d frontend` + `restart api worker` → all 5 containers Up/healthy.

---

## Sprint Handover: Line Tag Save Fix + Duplicate-Tag Merge Fallback

**Status**: Implemented, built, and deployed locally on 2026-09-18.

### Scope

- **Fixed "Run index N out of range" when saving a line tag.** Tag saves went through the
  index-based `PATCH /runs/{run_idx}/label`, but several local-only edits (undo/redo, manual pen,
  box trace) change `result.runs` in memory without round-tripping to the DB. Whenever the frontend's
  run array drifted from the backend's, the remembered index pointed past the stored array and the
  request failed. The tag save now persists the **whole result** via `patchResult`, and undo/redo also
  sync the restored state back to the server, so the backend's `runs` array can never diverge from
  what the user sees. A cheap range-guard shows a friendly toast instead of an alert popup.
- **Added a duplicate-tag fallback with merge.** Previously typing an existing tag silently failed
  with no explanation and no way to reuse the line. Now, when the entered tag already belongs to
  another line, the user is prompted: **OK = merge** this segment into the existing line (attach as
  `run_idx`/`extra_runs` + stamp the tag), **Cancel = abort** so they can type a different tag. A new
  `handleMergeRunIntoPid` performs the merge and records it in undo history.

### Verification

- `npx tsc --noEmit` → 0 errors.
- `npm run build` → success (only pre-existing lint warnings).
- `docker compose build frontend` + `up -d frontend` → all 5 containers Up/healthy.

---

## Sprint Handover: Selection Cleanup, Export Modal & Mode-Aware Export

**Status**: Implemented, built, and deployed locally on 2026-09-18.

### Scope

- **Orange selection halo no longer survives navigation.** The line selection only makes sense in
  Digitization mode, yet `selectedRunIndices` was never cleared when leaving it, so the pulsing orange
  halo kept rendering on the selected run in Corrosion System/Circuit (and after split+delete the
  remembered index drifted to a surviving run). Added an effect keyed on `[mode, activeSheet.id]` that
  drops the selection + split mode on any mode/sheet change (on top of the existing out-of-range
  sanitizer).
- **Export is now a modal, not a hover dropdown.** The old `group-hover:block` menu vanished when the
  cursor crossed the gap on its way down, making options hard to click. Replaced with a proper modal
  (`showExportModal`): each format has an icon, a title, and a plain-language description of what the
  file contains, plus a header banner showing the currently active view mode.
- **Export now follows the active view mode.** PDF/PNG export was hard-coded to `mode=engineer`, so
  exporting from Corrosion System/Circuit always produced per-pipe coloring. Added an `exportMode`
  memo (`digitize→engineer`, `system→system`, `circuit→circuit`) and pass it through `getExportUrl`
  for the mode-aware formats (PNG/PDF). spreadsheet formats (xlsx/docx) remain mode-independent
  line/asset registers.

### Verification

- `npx tsc --noEmit` → 0 errors.
- `npm run build` → success (only pre-existing lint warnings).
- `docker compose build frontend` + `up -d frontend` → all 5 containers Up/healthy.

---

## Sprint Handover: Overlay State Leak Fixes (Stuck Selection & Stuck Highlight)

**Status**: Implemented, built, and deployed locally on 2026-09-18.

### Scope

- **Fixed stuck orange "selected" halo after split + delete.** The backend re-indexes runs on every
  mutation (`split_poly_run` inserts `run_b` at `run_idx + 1`, shifting all later indices; delete shifts
  indices down). The frontend kept a `selectedRunIndices` set that was never validated against the new
  runs array, so an index remembered from an earlier state could point past the end / at a different
  run — leaving the pulsing orange selection (halo + white vertex handles) on a line the user had
  already removed. A sanitizing `useEffect` now prunes any out-of-range index whenever `result.runs`
  changes and exits split mode when the selection becomes empty.
- **Fixed stuck purple "focused target" highlight.** `zoomToBbox` both zooms and installs an indigo
  highlight overlay, but nothing ever removed it when the user navigated away. Switching Corrosion
  System ⇄ Circuit ⇄ Digitize (or sheets) left the pulsing box on the canvas. Added a reusable
  `clearHighlight()` helper, refactored `zoomToBbox` to use it, and a `useEffect` keyed on
  `[mode, activeSheet.id]` that clears the highlight on navigation (these transitions never co-occur
  with a fresh `zoomToBbox`, so there's no race with a newly installed highlight).

### Verification

- `npx tsc --noEmit` → 0 errors.
- `npm run build` → success (only pre-existing lint warnings).
- `docker compose build frontend` + `up -d frontend` → all 5 containers Up/healthy.

---

## Sprint Handover: Draggable Line Action Popover

**Status**: Implemented, built, and deployed locally on 2026-09-21.

### Scope

- **Made the floating "Pipa #" action popover draggable.** Previously the panel that appears when a
  traced line is clicked was pinned to a fixed spot near the line, so it could obscure the very
  polyline the user wanted to edit (especially short/tight lines). The popover header is now a drag
  handle (`GripVertical` icon + `cursor-grab`/`cursor-grabbing`); grabbing it and dragging moves the
  panel anywhere on the canvas, clamped to stay inside the viewer bounds.
- **Drag implementation** (`InteractivePipeCanvas.tsx`): added `popoverDragging` state + a
  `popoverDragRef` grab-offset ref, a `handlePopoverPointerDown` on the header, and a
  `window` `pointermove`/`pointerup`/`pointercancel` listener registered only while dragging. The
  close (X) button stops pointer propagation so it can't accidentally start a drag.
- **No regression**: because the popover renders inside the OpenSeadragon overlay container (portal),
  dragging never triggers OSD's `canvas-click` empty-space deselect, and `popoverPos` stays in the
  same viewer-relative coordinate space used for initial placement.

### Verification

- `npx tsc --noEmit` → 0 errors.
- `npm run build` → success (only pre-existing `finishManual` / `handleDeleteRuns` lint warnings).
- `docker compose build frontend` + `up -d frontend` → all 5 containers Up/healthy.

---

## Sprint Handover: Overlay Persistence, Cross-Tab Tracing, & Performance Hardening

**Status**: Implemented and locally verified on 2026-09-18.

### Scope

- **Fixed disappearing line tracing across tab switches.** `viewer.open()` internally calls
  `close()`, which runs `clearOverlays()` and empties the overlays container. The parent used to
  call `viewer.open()` on every DIGITIZE ⇄ SYSTEM ⇄ CIRCUIT switch, silently destroying the
  `InteractivePipeCanvas` SVG layer. The overlay is now re-attached on the OpenSeadragon `open`
  event, so tracing survives mode changes.
- **Eliminated the server-side marked-PNG base swap.** Circuit/System coloring is now rendered as a
  vector layer via a client-computed `colorOverrideMap` (`systems[].circuits[].color` / `run_idxs`)
  on top of the raw CAD image. The base image is pinned to `getRawImageUrl` in all modes, so no
  full-resolution PNG is fetched/rendered on mode change.
- **Stopped resource leaks that made the app heavier per project opened.** The detection
  `pollInterval`, progress `WebSocket`, all toast `setTimeout`s, and the OpenSeadragon viewer are
  now torn down on unmount / sheet change (`pollIntervalRef`, `detectionWsRef`, `toastTimeoutsRef`,
  `stopDetectionResources`).
- **Added HTTP caching + thumbnails for sheet imagery.** `/raw` now sends
  `Cache-Control: public, max-age=31536000, immutable` + `ETag`. New `GET
  /projects/{id}/sheets/{id}/thumbnail?size=` serves a cached downscaled PNG used by the project
  cards (previously the cards downloaded full-resolution drawings).
- **Cached the full-resolution base render for ROI re-scan.** `POST /trace-region` reuses
  `ExportService._cached_base_image` instead of re-rasterizing the entire PDF on every request.

### Verification

- `pytest backend/tests -q`: **34 passed** (added `test_sheet_tiles_cache.py`).
- `npx tsc --noEmit`: **0 errors**.
- `npm run build`: **passed**.

---

## Sprint Handover: Canvas Stability, Single Sidebar Tab, Re-scan ROI, & Manual Pen

**Status**: Implemented and locally verified on 2026-09-18.

### Scope

- One sidebar tab row now contains `Lines`, `Pipa (Inspector)`, `Symbols`, and `OPCs`; the duplicate row is removed.
- `viewer.clearOverlays()` was removed from run focus so clicks, sidebar zoom, and viewport changes do not delete the React SVG tracing layer.
- Focus rectangles are independent, SVG events are isolated from OpenSeadragon, run keys are stable, and viewport sync covers `update-viewport`, `animation-finish`, and `resize`.
- `POST /api/v1/projects/{project_id}/trace-region` crops the selected sheet region, runs `SkeletonLineTracer(min_length_px=6)`, offsets local points globally, persists runs with `source: "rescan"`, and returns `new_runs`.
- `Box Trace / Re-scan` provides rubber-band ROI selection. `Manual Pen` creates persisted `manual-run-[timestamp]` runs with `manual: true` and default color `#2563EB`.

### Shortcuts

| Shortcut | Behavior |
|---|---|
| `Delete` / `Backspace` | Delete selected runs |
| `Escape` | Cancel tool/selection and return to Pan |
| `Enter` / double-click | Finish Manual Pen |
| `Ctrl/Cmd+Z`, `Ctrl/Cmd+Y`, `Ctrl/Cmd+S` | Undo, redo, save |

### Verification

- `pytest backend/tests -q`: **33 passed**.
- `npm run build`: **passed**.
- `git diff --check`: **passed**.
- Follow-up: add ROI boundary/empty-result tests and browser E2E overlay persistence tests.

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

### Task B.09: Junction Classifier & Crossover Resolution
- Implemented `_classify_junction_geometry` in [`pidcorr/implementations/skeleton_tracer.py`](file:///c:/Werk/pidccs/pidcorr/implementations/skeleton_tracer.py):
  - Evaluates local unit departure vectors $\mathbf{u}_i$ from junction centroids.
  - **4-Way Crossover**: Identifies collinear opposite edge pairs ($\mathbf{u}_a \cdot \mathbf{u}_b \le -0.35$ and $\mathbf{u}_c \cdot \mathbf{u}_d \le -0.35$) and pairs them into two independent through-pipes. Prevents artificial hydraulic fusion of crossing lines!
  - **3-Way T-Junction**: Merges collinear through-edges while preserving perpendicular takeoff edges as distinct branch lines.
  - **2-Way Elbow Corner**: Chains directional bends into continuous polyline runs.
- Enhanced `PipeRun` in [`pidcorr/lines.py`](file:///c:/Werk/pidccs/pidcorr/lines.py) with dual-access semantics (both attribute and dictionary item access `run["axis"]`, `run["points"]`) to guarantee 100% interoperability with downstream algorithms.

### Task B.10: Tracing A/B Benchmark (`MorphologyLineTracer` vs `SkeletonLineTracer`)
- Implemented [`backend/tests/benchmark_tracing_phase_b.py`](file:///c:/Werk/pidccs/backend/tests/benchmark_tracing_phase_b.py).
- Benchmarked on 6 representative P&ID drawings (native 350 DPI, 138 ground-truth piping IDs).
- **Results**:
  - **Piping ID Association Rate**: `SkeletonLineTracer` achieved **100.0% (138/138 attached)**, vastly outperforming `MorphologyLineTracer` at 87.7% (121/138 attached).
  - **Latency**: `SkeletonLineTracer` executes in **2.141 seconds per sheet** (via optimized touch-mask and bounding-box extractions), well within web interactive limits.
  - **Crossover Separation**: Crossing pipes are cleanly kept as independent entities, preventing circuitization bleed.


---

## 6. Phase B.5: Pivot Sprint — Tracer Snap-to-Equipment, Line Splitting & Interactive Web Canvas Tooling

### 6.1 Strategic Pivot & Directive
Berdasarkan arahan prioritas CTO:
- **HOLD / TUNDA**: Pewarnaan otomatis multi-warna API RP 970 (systemize & circuitize) ditunda sementara karena ketergantungan pada kelengkapan anotasi spec break dan line list.
- **TARGET PRIORITAS**:
  1. Jalur pipa diekstrak secara utuh dan menempel persis ke perimeter equipment (snap-to-equipment).
  2. Semua hasil tracing diberi warna netral seragam `#2563EB` pada tampilan awal.
  3. Dibangun **Interactive Web Canvas Tooling** lengkap bagi corrosion engineer untuk inspeksi, seleksi, pewarnaan manual, pemotongan garis (split line), dan ekspor deliverable vector PDF/PNG beranotasi.

---

### 6.2 CV & Pipeline Adjustments (`pidcorr/`)
1. **Snap Endpoints to Equipment Perimeter** ([`pidcorr/lines.py`](file:///c:/Werk/pidccs/pidcorr/lines.py)):
   - Fungsi `snap_endpoints_to_equipment(runs, detections, snap_threshold_pt=35.0)`:
     - Mendeteksi simbol berkategori equipment (`vessel`, `tank`, `heat_exchanger`, `pump`, `compressor`, dll).
     - Mengevaluasi jarak Euclidean dari titik ujung polyline (`p0` dan `pn`) ke 4 sisi bounding box equipment.
     - Jika jarak $\le \text{threshold}$, titik ujung diproyeksikan secara ortogonal atau sudut ke perimeter equipment.
2. **Relaksasi Segmen Pendek** ([`pidcorr/implementations/skeleton_tracer.py`](file:///c:/Werk/pidccs/pidcorr/implementations/skeleton_tracer.py)):
   - `min_length_px` direlaksasi dari 40px ke 25px agar pipa drain, vent, dan connection niple pendek tidak tereliminasi prematur.
   - Mengintegrasikan fungsi snapping sebelum asosiasi label piping ID.
3. **Pemberian Warna Default Netral**:
   - `PipeRun.color` default diatur ke `#2563EB`.

---

### 6.3 Backend Endpoints & Deliverable Export
1. **Line Splitting & Recoloring Endpoints**:
   - `POST /api/v1/projects/{project_id}/sheets/{sheet_id}/results/runs/{run_idx}/split`: Memotong polyline pada koordinat `(x, y)` terdekat menjadi 2 PipeRun terpisah.
   - `PATCH /api/v1/projects/{project_id}/sheets/{sheet_id}/results/runs/{run_idx}/color`: Memperbarui warna single run.
   - `PATCH /api/v1/projects/{project_id}/sheets/{sheet_id}/results/runs/batch-color`: Memperbarui warna banyak run sekaligus secara atomik.
2. **PyMuPDF Vector PDF & PNG Export (`engineer` mode)**:
   - Menambahkan mode `mode=engineer` pada export service:
     - Menggunakan properti `run.color` per garis untuk rendering anotasi `PolyLine` Adobe Acrobat di PDF.
     - Menggambar garis berbobot warna kustom pada PNG beresolusi tinggi.

---

### 6.4 Interactive Web Canvas Tooling (`frontend/`)
1. **Interactive SVG Canvas Overlay** ([`InteractivePipeCanvas.tsx`](file:///c:/Werk/pidccs/frontend/src/components/InteractivePipeCanvas.tsx)):
   - Dirender menggunakan `ReactDOM.createPortal` langsung ke dalam container overlay OpenSeadragon.
   - Sinkron 1:1 terhadap pergerakan pan dan zoom OpenSeadragon tanpa lag matriks koordinat.
   - Polyline dilengkapi dengan **invisible hit target** (`strokeWidth = 22px`, `pointerEvents = "stroke"`) untuk seleksi mudah tanpa pixel-hunting.
   - Visual feedback seketika: **Hover glow** dan **Selection halo** berwarna amber (`#f59e0b`).
   - Mendukung **Shift + Click** untuk seleksi multi-garis sekaligus.
2. **Floating Popover Toolbar**:
   - Tampil melayang otomatis di dekat posisi mouse saat garis dipilih.
   - Palet warna kurasi (8 swatch: Biru, Hijau, Merah, Kuning, Ungu, Cyan, Oranye, Abu-abu) + input hex kustom.
   - Mode **Split Line**: Mengaktifkan kursor crosshair dengan preview lingkaran koordinat proyeksi pemotongan secara live.
   - Informasi run: panjang pipa (px) dan jumlah vertex.
3. **Full Undo/Redo & Canvas Controls** ([`page.tsx`](file:///c:/Werk/pidccs/frontend/src/app/project/[id]/page.tsx)):
   - Riwayat aksi frontend (20 langkah stack): membatalkan (`Ctrl+Z`) atau mengulang (`Ctrl+Y`) pemotongan dan pewarnaan garis.
   - Slider opacity interaktif (10% hingga 100%) dan tombol toggle visibilitas Show/Hide.
   - Status dirty indicator dan tombol persisten "Save Changes" (`Ctrl+S`).
   - Tautan langsung unduh PDF Vector dan PNG beranotasi mode engineer.

---

### 6.5 Automated Test Suite Verification
Dijalankan melalui `pytest backend/tests/ -v`:
- `test_snap_equipment.py`: 6 tests passing (snapping horizontal, vertical, corner, non-snapping distant lines, tracer integration).
- `test_split_and_color.py`: 5 tests passing (orthogonal line split, run recoloring, batch recoloring, persistence di database, export vector PDF mode engineer).
- Total seluruh suite: **14 tests, 100% PASSED**.

---

## 7. Pivot Sprint Completion: Tracing Quality, Dilated Masking & Pipe Inspector Sidebar

### 7.1 Status Terakhir Komponen yang Dikerjakan

1. **Eliminasi Garis Ganda & Lock Base Image CAD**:
   - **Status**: 100% Selesai & Terverifikasi.
   - **Implementasi**:
     - Mengunci base image OpenSeadragon dalam mode digitasi secara permanen ke `getRawImageUrl`.
     - Menonaktifkan pembuatan gambar raster marked (`getMarkedImageUrl`) dan menonaktifkan kotak legenda `"CORROSION SYSTEM (per process fluid)"` di canvas.
     - Seluruh jalur pipa kini dirender murni dari satu lapisan vektor SVG dengan warna netral default `#2563EB`.
2. **Perbaikan Masking Tracing & Gap Bridging**:
   - **Status**: 100% Selesai & Terverifikasi.
   - **Implementasi**:
     - **Dilated OCR Text Masking**: Bounding box hasil OCR teks di-dilate selebar 8 piksel kemudian dihitamkan (*blackout*) sebelum skeletisasi. Teks seperti `"BY INSTR."`, garis bawah catatan (*underlines*), dan tanda slash ukuran (`"3/4"`) bersih terhapus.
     - **Equipment Interior Inset Masking**: Bagian dalam bejana/vessel/tank (misal `605-V-218`) dihitamkan dengan inset 4 piksel dari garis perimeter terluar. Sekat dan baffle internal terhapus tanpa merusak tepian snap pipa.
     - **Inline Valve Gap Bridging**: Algoritma `bridge_inline_valve_gaps` menyambungkan gap pipa pada celah valve berdasarkan uji tangen kolinear dan kedekatan spasial.
     - **Relaksasi Cabang Pendek**: Parameter `min_length_px` diturunkan ke 18px agar cabang kecil (drain, vent, sampling) tetap tertangkap.
     - **Optimasi $O(N \log N)$**: `bridge_collinear_headers` dioptimasi menjadi 1D coordinate interval merge, memangkas durasi tracing dari 3 menit menjadi < 0.02 detik.
3. **Perbaikan Toggle Visibilitas Pipa ("Pipa: ON/OFF")**:
   - **Status**: 100% Selesai & Terverifikasi.
   - **Implementasi**:
     - Menghapus pemanggilan `viewer.open()` pada event toggle overlay.
     - Visibilitas pipa dikendalikan murni via CSS `display: showOverlay ? 'block' : 'none'`. SVG overlay tidak lagi ter-unmount atau hilang saat di-toggle ulang.
4. **Pipe Inspector Sidebar & Two-Way Sync**:
   - **Status**: 100% Selesai & Terverifikasi.
   - **Implementasi**:
     - Sub-tab dedicated **"Pipa (Inspector)"** di sidebar kanan menampilkan daftar seluruh segmen run.
     - **Two-Way Sync**: Klik baris tabel di sidebar memicu animasi pan & zoom OpenSeadragon ke bounding box pipa terkait; klik segmen pipa di kanvas memicu *smooth auto-scroll* tabel sidebar ke baris terkait.
     - **Single-Run Editor**: Rename label pipa, swatch 5 quick colors + hex picker, tombol hapus segmen.
     - **Batch Editing Toolbar**: Checkbox per baris, tombol "Pilih Semua", batch recolor, dan batch delete.
     - **20-Step Undo/Redo & Manual Save**: Mendukung riwayat aksi frontend (Ctrl+Z / Ctrl+Y) dan penyimpanan ke database PostgreSQL (Ctrl+S).

---

### 7.2 Daftar File yang Dimodifikasi & Fungsi Utamanya

| No | File Path | Layer | Fungsi Utama |
|:---:|---|---|---|
| 1 | [`pidcorr/lines.py`](file:///c:/Werk/pidccs/pidcorr/lines.py) | Core CV | Menambahkan atribut `id`, `label`, `manual` pada kelas `PipeRun`; algoritma `bridge_inline_valve_gaps`; optimasi $O(N \log N)$ `bridge_collinear_headers`; fungsi snapping titik ujung pipa ke bounding perimeter equipment. |
| 2 | [`pidcorr/implementations/skeleton_tracer.py`](file:///c:/Werk/pidccs/pidcorr/implementations/skeleton_tracer.py) | Core CV | Masking dilasi 8px untuk teks OCR; inset 4px blackout interior vessel/tank; suppression furniture/instrument; integrasi valve gap bridging; batas relaksasi `min_length_px = 18`. |
| 3 | [`pidcorr/orchestrator.py`](file:///c:/Werk/pidccs/pidcorr/orchestrator.py) | Pipeline | Inspeksi signature adapter tracer (`inspect.signature`) dan serialisasi bersih `PipeRun` ke dictionary JSON. |
| 4 | [`backend/worker/tasks.py`](file:///c:/Werk/pidccs/backend/worker/tasks.py) | Celery Worker | Menonaktifkan sementara propagasi multi-warna sirkuit API RP 970; mengosongkan `systems = []` untuk single-layer neutral blue tracing. |
| 5 | [`backend/app/schemas/run.py`](file:///c:/Werk/pidccs/backend/app/schemas/run.py) | Backend Schema | Penambahan field `id`, `label`, `manual` pada Pydantic model `PipeRun`. |
| 6 | [`backend/app/routers/results.py`](file:///c:/Werk/pidccs/backend/app/routers/results.py) | REST API | Endpoint manipulasi per-run: `DELETE /runs/{idx}`, `POST /runs/batch-delete`, `PATCH /runs/{idx}/label`, `POST /runs/{idx}/split`, `PATCH /runs/{idx}/color`. |
| 7 | [`backend/app/routers/projects.py`](file:///c:/Werk/pidccs/backend/app/routers/projects.py) | REST API | Registrasi rute manipulasi run pipa di bawah resource sheet project. |
| 8 | [`backend/tests/test_masking_and_runs.py`](file:///c:/Werk/pidccs/backend/tests/test_masking_and_runs.py) | Tests | Unit test suite baru untuk dilated text masking, equipment interior masking, valve gap bridging, dan skema run. |
| 9 | [`backend/tests/test_snap_equipment.py`](file:///c:/Werk/pidccs/backend/tests/test_snap_equipment.py) | Tests | Penyesuaian threshold `min_length_px <= 25` dan graceful fallback import `app` / `backend.app`. |
| 10 | [`backend/tests/test_split_and_color.py`](file:///c:/Werk/pidccs/backend/tests/test_split_and_color.py) | Tests | Fallback import modul `app` vs `backend.app` untuk eksekusi container maupun host. |
| 11 | [`frontend/src/types/schema.ts`](file:///c:/Werk/pidccs/frontend/src/types/schema.ts) | Frontend Types | Interface TypeScript `PipeRun` dengan field `id`, `label`, `manual`, `pid`, `fluid`. |
| 12 | [`frontend/src/lib/api.ts`](file:///c:/Werk/pidccs/frontend/src/lib/api.ts) | Frontend Client | Method API client: `deleteRun`, `batchDeleteRuns`, `updateRunLabel`, `splitRun`, `updateRunColor`, `batchUpdateRunColors`. |
| 13 | [`frontend/src/components/InteractivePipeCanvas.tsx`](file:///c:/Werk/pidccs/frontend/src/components/InteractivePipeCanvas.tsx) | Frontend UI | Komponen SVG overlay interaktif: hit-target 22px, hover/selection halo amber, floating action popover, CSS display toggle, dan live split projection crosshair. |
| 14 | [`frontend/src/app/project/[id]/page.tsx`](file:///c:/Werk/pidccs/frontend/src/app/project/[id]/page.tsx) | Frontend Page | Lock raw CAD base image, perbaikan toggle visibilitas tanpa viewer reset, sub-tab Pipe Inspector sidebar, two-way pan/zoom sync, batch toolbar, dan 20-step undo/redo stack. |

---

### 7.3 Handover Roadmap: Task Berikutnya yang Belum Sempat Dieksekusi

Sebagai acuan untuk sesi berikutnya, berikut adalah backlog prioritas yang belum sempat dieksekusi:

1. **Perbaikan Tab Duplikat di Sidebar**:
   - *Issue*: Saat ini di panel kanan terdapat tab `Lines` (daftar piping ID bawaan OCR/Line List) dan sub-tab baru `Pipa (Inspector)` (daftar segmen fisik run hasil tracer).
   - *Rencana*: Konsolidasi struktur tab agar tidak membingungkan pengguna. Gabungkan tampilan menjadi panel hierarkis terpadu: Piping Tag $\rightarrow$ Child Runs.
2. **Investigasi Overlay Lenyap pada Skenario Khusus**:
   - *Issue*: Potensi edge case di mana SVG overlay tersembunyi saat resizing window browser ekstrem atau saat berpindah tab browser pada level zoom maksimal.
   - *Rencana*: Memastikan listener viewport OpenSeadragon (`resize`, `animation-finish`, `update-viewport`) selalu memicu sinkronisasi matriks transformasi SVG secara deterministik.
3. **Tool Re-scan ROI (Region of Interest)**:
   - *Status*: **100% Selesai di Pivot Sprint B.6**.
4. **Manual Pen / Polyline Draw Tool**:
   - *Status*: **100% Selesai di Pivot Sprint B.6**.

---

## 8. Pivot Sprint B.6 Completion: Canvas Interactivity Fix, Draggable Vertices & Tool Activation

### 8.1 Status Terakhir Komponen yang Dikerjakan

1. **Perbaikan Seleksi Garis di Kanvas**:
   - **Status**: 100% Selesai & Terverifikasi.
   - **Implementasi**:
     - Mengubah strategi pointer events pada container SVG overlay.
     - Menghapus pemanggilan `e.preventDefault()` pada handler `onMouseDown` garis hit-target yang membatalkan event klik pada browser Chromium.
     - Menggunakan deteksi jarak pergerakan pointer (< 6px) pada `onPointerDown` + `onPointerUp` untuk deteksi klik yang 100% deterministik.
     - Menambahkan listener `canvas-click` native OpenSeadragon untuk melakukan deselect otomatis saat pengguna mengklik area kosong kanvas.
2. **Draggable Vertex Control Points & Snap Alignment**:
   - **Status**: 100% Selesai & Terverifikasi.
   - **Implementasi**:
     - Semua titik vertex polyline (baik titik ujung maupun titik belokan intermediate) kini dirender sebagai kontrol lingkaran interaktif (`fill="#F59E0B"` untuk endpoint, `fill="#3B82F6"` untuk intermediate).
     - Mengaktifkan `pointerEvents: 'all'` dan cursor `grab`/`grabbing`.
     - Saat drag dimulai, navigasi OpenSeadragon dimatikan sementara (`viewer.setMouseNavEnabled(false)`).
     - **Snap-to-axis assist**: Menyelaraskan koordinat vertex secara otomatis ke sumbu horizontal atau vertikal terhadap titik adjacent ($\le 8$px) dengan visual guide line berwarna hijau.
     - Endpoint backend baru `PATCH /api/v1/projects/{id}/sheets/{id}/result/runs/{idx}/points` menyimpan posisi titik baru secara persisten.
3. **Aktivasi Box Trace (ROI Re-scan) & Dialog Option (C)**:
   - **Status**: 100% Selesai & Terverifikasi.
   - **Implementasi**:
     - Pengguna dapat menarik kotak seleksi (*rubber-band rectangle*) di kanvas dengan outline putus-putus biru.
     - Selesai menarik kotak, muncul modal konfirmasi Option (C):
       - **Replace**: Menghapus pipa-pipa lama di dalam ROI dan menggantinya dengan hasil re-scan.
       - **Append**: Menambahkan pipa baru hasil re-scan tanpa menghapus pipa yang sudah ada.
       - **Batal**: Membatalkan seleksi.
     - Power-user shortcut: Menahan tombol `Shift` saat melepaskan drag langsung memicu mode auto-replace instan tanpa modal.
4. **Aktivasi Manual Pen Tool**:
   - **Status**: 100% Selesai & Terverifikasi.
   - **Implementasi**:
     - Mengklik di kanvas meletakkan titik demi titik polyline dengan preview garis langsung.
     - **Magnet snap**: Mendeteksi ujung pipa terdekat dalam radius 18px dan menempelkan titik secara presisi (indikator dot hijau).
     - Menahan tombol `Shift` mengunci gerakan ke garis ortogonal (horizontal/vertikal).
     - Tombol Enter, klik tombol "Selesai", atau Double-Click menyelesaikan penggambaran garis baru (`manual: true`, warna netral `#2563EB`).
5. **Sistem Kursor Dinamis**:
   - **Status**: 100% Selesai & Terverifikasi.
   - **Implementasi**:
     - Kursor berubah secara responsif sesuai tool: `default`/`pointer`/`grab` pada Pan & Select, `crosshair` pada Box Trace dan Manual Pen, serta `crosshair` pada mode Split Line.
6. **Tuning Parameter Skeleton Line Tracer**:
   - **Status**: 100% Selesai & Terverifikasi.
   - **Implementasi**:
     - `min_length_px`: diturunkan ke 12px untuk menangkap cabang pendek.
     - `approxPolyDP epsilon`: 1.5 untuk kurva elbow yang lebih akurat.
     - `adaptiveThreshold`: `blockSize=21, C=6` untuk mendeteksi garis CAD tipis dan pudar.
     - Dilasi teks OCR: diskalakan dinamis dengan DPI (`max(3, int(5 * dpi/350))`) agar pipa dekat label teks tidak terpotong lubang.
     - Gap bridging: `bridge_collinear_headers` ditingkatkan ke 55px dan `bridge_inline_valve_gaps` ke 90px.

---

### 8.2 Automated Test Suite Verification

- `pytest tests/test_split_and_color.py tests/test_snap_equipment.py tests/test_masking_and_runs.py -v`:
  - **13 passed, 1 skipped, 100% PASSED**.
  - Termasuk verifikasi unit test baru untuk endpoint `update_run_points`.
- Frontend compile: `npx tsc --noEmit` $\rightarrow$ **0 errors (100% Clean TypeScript build)**.
