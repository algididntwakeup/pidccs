import io
import os
import sys
from pathlib import Path
import pytest
import openpyxl

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = Path(os.path.abspath(os.path.join(_BACKEND_DIR, "..")))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

try:
    from app.schemas.linelist import LineListEntry, LineListImportResult
    from app.services.linelist_parser import LineListParser, canonical_line_key
except ImportError:
    from backend.app.schemas.linelist import LineListEntry, LineListImportResult
    from backend.app.services.linelist_parser import LineListParser, canonical_line_key


def create_mock_excel_bytes() -> bytes:
    """Generate an in-memory mock Excel workbook with EPC contractor line list data."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "LineList_Rev1"

    # Engineering headers containing varied naming conventions
    headers = [
        "Line No.",
        "Operating Press (barg)",
        "Operating Temp (C)",
        "Design Press (barg)",
        "Design Temp (C)",
        "Fluid Phase",
        "Insulation",
        "Notes",
        "Material",
    ]
    ws.append(headers)

    # Row 1: standard numeric values
    ws.append([
        '101-4"-HC-CS-001',
        12.5,
        65.0,
        16.0,
        90.0,
        "Liquid",
        "YES",
        "Main hydrocarbon transfer header",
        "CS",
    ])

    # Row 2: text values with engineering units
    ws.append([
        '102-6"-HC-SS-002',
        "18.4 barg",
        "120.5 °C",
        "25.0 barg",
        "150 °C",
        "Gas",
        "NO",
        "Vapor return line",
        "SS",
    ])

    # Row 3: dashes, N/A, mixed formatting
    ws.append([
        '103-2"-HC-CS-003',
        "-",
        "N/A",
        "10.0",
        "45.0",
        "2-Phase",
        "IH",
        "Intermittent drain line",
        "CS",
    ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_linelist_parser_mock_excel():
    """Validate that LineListParser reads mock Excel files and normalizes data types."""
    excel_bytes = create_mock_excel_bytes()

    entries = LineListParser.parse_file(excel_bytes, filename="mock_linelist.xlsx")

    assert len(entries) == 3, f"Expected 3 entries, got {len(entries)}"

    # Row 1 validation
    e1 = entries[0]
    assert e1.line_number == '101-4"-HC-CS-001'
    assert e1.operating_pressure_barg == 12.5
    assert e1.operating_temperature_c == 65.0
    assert e1.design_pressure_barg == 16.0
    assert e1.design_temperature_c == 90.0
    assert e1.fluid_phase == "L"  # Normalized from 'Liquid'
    assert e1.insulation == "YES"
    assert e1.notes == "Main hydrocarbon transfer header"
    assert e1.material == "CS"

    # Row 2 validation (string values with units converted safely)
    e2 = entries[1]
    assert e2.line_number == '102-6"-HC-SS-002'
    assert e2.operating_pressure_barg == 18.4
    assert e2.operating_temperature_c == 120.5
    assert e2.design_pressure_barg == 25.0
    assert e2.design_temperature_c == 150.0
    assert e2.fluid_phase == "G"  # Normalized from 'Gas'
    assert e2.insulation == "NO"
    assert e2.notes == "Vapor return line"
    assert e2.material == "SS"

    # Row 3 validation (fallback to None for dashes and N/A)
    e3 = entries[2]
    assert e3.line_number == '103-2"-HC-CS-003'
    assert e3.operating_pressure_barg is None
    assert e3.operating_temperature_c is None
    assert e3.design_pressure_barg == 10.0
    assert e3.design_temperature_c == 45.0
    assert e3.fluid_phase == "2P"  # Normalized from '2-Phase'
    assert e3.insulation == "IH"
    assert e3.notes == "Intermittent drain line"


def test_linelist_parser_dynamic_column_mapping():
    """Validate dynamic column mapping for non-standard EPC contractor headers."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([
        "Tag Jalur Pipa",
        "Tekanan Kerja (barg)",
        "Suhu Operasi (C)",
        "Fasa Fluida",
        "Catatan",
    ])
    ws.append([
        "P-2001-A",
        22.5,
        180.0,
        "Steam",
        "Kondisi operasi kontinu",
    ])

    buf = io.BytesIO()
    wb.save(buf)
    file_bytes = buf.getvalue()

    custom_mapping = {
        "line_number": "Tag Jalur Pipa",
        "operating_pressure_barg": "Tekanan Kerja (barg)",
        "operating_temperature_c": "Suhu Operasi (C)",
        "fluid_phase": "Fasa Fluida",
        "notes": "Catatan",
    }

    entries = LineListParser.parse_file(file_bytes, filename="epc_contractor.xlsx", mapping_dict=custom_mapping)
    assert len(entries) == 1
    e = entries[0]
    assert e.line_number == "P-2001-A"
    assert e.operating_pressure_barg == 22.5
    assert e.operating_temperature_c == 180.0
    assert e.fluid_phase == "G"  # 'Steam' normalized to 'G'
    assert e.notes == "Kondisi operasi kontinu"


def test_linelist_import_result_summary_schema():
    """Verify that LineListImportResult contains the expected summary fields."""
    result = LineListImportResult(
        status="success",
        entries_parsed=5,
        pids_enriched=3,
        filename="test.xlsx",
        total_rows=5,
        matched_pids=3,
        unmatched_pids=2,
    )
    dump = result.model_dump()
    assert dump["status"] == "success"
    assert dump["entries_parsed"] == 5
    assert dump["pids_enriched"] == 3


@pytest.mark.asyncio
async def test_linelist_endpoint_combined_dataset_enrichment():
    """Validate POST /api/v1/projects/{id}/linelist using combined_dataset Excel workbook."""
    from httpx import AsyncClient, ASGITransport
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.main import app
    from app.db.base import Base
    from app.db.session import get_db

    db_url = "sqlite+aiosqlite:///./test_unit_linelist.db"
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _test_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app.dependency_overrides[get_db] = _test_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create project
            resp_p = await ac.post("/api/v1/projects", json={"name": "Unit LineList Test Plant", "description": "Unit test"})
            assert resp_p.status_code == 201
            project_id = resp_p.json()["id"]

            # 2. Create sheet
            dummy_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
            resp_s = await ac.post(
                f"/api/v1/projects/{project_id}/sheets",
                files={"file": ("test_sheet.png", dummy_png, "image/png")},
                data={"sheet_number": "001"},
            )
            assert resp_s.status_code == 201
            sheet_id = resp_s.json()["id"]

            # 3. Add detection result with matching line tags from 605_CCD2_loop_dataset.xlsx
            result_payload = {
                "image_path": "test_sheet.png",
                "dpi": 300,
                "rot": 0,
                "w": 1000,
                "h": 1000,
                "symbols": [],
                "runs": [{"points": [[100, 100], [400, 100]], "axis": "h", "x1": 100, "y1": 100, "x2": 400, "y2": 100}],
                "piping_ids": [
                    {
                        "pid": '143-6"-GR-DSA-107',
                        "fluid": "GR",
                        "pclass": "DSA",
                        "run_idx": 0,
                        "x1": 100, "y1": 100, "x2": 200, "y2": 120,
                    }
                ],
                "conn_points": [],
                "furniture": [],
            }
            resp_patch = await ac.patch(f"/api/v1/projects/{project_id}/sheets/{sheet_id}/result", json=result_payload)
            assert resp_patch.status_code == 200

            # 4. Upload actual dataset from combined_dataset
            excel_path = Path(_ROOT_DIR) / "combined_dataset" / "605_CCD2_loop_dataset.xlsx"
            assert excel_path.exists(), f"Excel dataset not found: {excel_path}"

            with open(excel_path, "rb") as f:
                excel_bytes = f.read()

            resp_upload = await ac.post(
                f"/api/v1/projects/{project_id}/linelist",
                files={"file": (excel_path.name, excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
            assert resp_upload.status_code == 200, resp_upload.text
            res_json = resp_upload.json()

            # Verify response summary format
            assert res_json["status"] == "success"
            assert res_json["entries_parsed"] >= 500
            assert res_json["pids_enriched"] >= 1

            # 5. Verify the piping_ids on the sheet were enriched
            resp_res = await ac.get(f"/api/v1/projects/{project_id}/sheets/{sheet_id}/result")
            assert resp_res.status_code == 200
            sheet_result = resp_res.json()
            pids = sheet_result["piping_ids"]
            assert len(pids) == 1
            enriched_pid = pids[0]
            assert enriched_pid["operating_pressure"] == 750.5
            assert enriched_pid["operating_temperature"] == 95.3
            assert enriched_pid["corrosion_loop"] == "605-VR-101 PS"
            assert enriched_pid["material"] == "SS"
    finally:
        app.dependency_overrides.clear()
        if os.path.exists("./test_unit_linelist.db"):
            try:
                os.remove("./test_unit_linelist.db")
            except OSError:
                pass
