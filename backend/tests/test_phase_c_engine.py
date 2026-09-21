import os
import sys
import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = Path(os.path.abspath(os.path.join(_BACKEND_DIR, "..")))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from app.main import app
from app.services.linelist_parser import LineListParser, canonical_line_key
from pidcorr.systemize import material_of, circuitize, systemize

from _fixtures import fixture_path

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.db.base import Base
from app.db.session import get_db

TEST_DB_URL = "sqlite+aiosqlite:///./test_linelist_engine.db"
test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def test_linelist_parser_excel():
    """Verify parsing real Excel engineering line list workbook."""
    excel_path = fixture_path("combined_dataset", "605_CCD2_loop_dataset.xlsx")
    assert excel_path.exists(), f"Sample dataset not found: {excel_path}"

    with open(excel_path, "rb") as f:
        entries = LineListParser.parse_excel(f.read())

    assert len(entries) >= 100, f"Expected >=100 entries, got {len(entries)}"
    first = entries[0]
    assert first.line_number == '143-6"-GR-DSA-107'
    assert first.material == "SS"
    assert first.operating_pressure_barg == 750.5
    assert first.operating_temperature_c == 95.3
    assert first.corrosion_loop == "605-VR-101 PS"
    assert first.service_condition == "Continuous"
    print(f"[PASS] Parsed {len(entries)} entries from {excel_path.name} with full operating parameters!")


def test_linelist_parser_csv():
    """Verify CSV line list parsing and fluid phase normalization."""
    csv_text = """Piping ID,Material,Oprt. Press,Oprt. Temp,Fluid Phase,CA,Corr. Loop
605-4"-GF-CCC-001,CS,15.2,85.0,Liquid,3.0,LOOP-101
605-4"-GF-CCC-002,CS,14.8,92.5,Gas,3.0,LOOP-102
605-4"-GF-CDA-003,SS,22.0,110.0,2-Phase,0.0,LOOP-103
"""
    entries = LineListParser.parse_csv(csv_text)
    assert len(entries) == 3
    assert entries[0].fluid_phase == "L"
    assert entries[1].fluid_phase == "G"
    assert entries[2].fluid_phase == "2P"
    assert entries[0].operating_pressure_barg == 15.2
    assert entries[1].operating_temperature_c == 92.5
    assert entries[2].material == "SS"
    print("[PASS] CSV line list parsed with normalized L/G/2P fluid phases!")


def test_material_spec_structured_lookup():
    """Verify Task C.03 structured material spec lookup from data/material_spec.json."""
    assert material_of("CDA") == "SS"
    assert material_of("CDE") == "SS"
    assert material_of("CCC") == "CS"
    assert material_of("ASA") == "CS"
    assert material_of("DSA") == "SS"
    assert material_of("DSE") == "SS"
    assert material_of("A1A2") == "CS"
    assert material_of("A4A2") == "SS"

    # Direct override priority
    assert material_of("UNKNOWN_CLASS", explicit_mat="Titanium") == "TITANIUM"
    assert material_of("CCC", explicit_mat="Inconel-625") == "INCONEL-625"
    print("[PASS] Task C.03 material specification lookup verified!")


def test_api_rp_970_phase_boundary_circuitization():
    """Verify Task C.02: Same fluid and material but different phases (L vs G) split into distinct circuits."""
    synthetic_result = {
        "runs": [
            {"points": [[100, 100], [300, 100]], "axis": "h"},
            {"points": [[100, 200], [300, 200]], "axis": "h"},
            {"points": [[100, 300], [300, 300]], "axis": "h"},
        ],
        "piping_ids": [
            {
                "pid": "605-6\"-GR-CCC-001",
                "fluid": "GR",
                "pclass": "CCC",
                "material": "CS",
                "fluid_phase": "L",  # Liquid
                "operating_temperature": 60.0,
                "operating_pressure": 10.0,
                "run_idx": 0,
            },
            {
                "pid": "605-6\"-GR-CCC-002",
                "fluid": "GR",
                "pclass": "CCC",
                "material": "CS",
                "fluid_phase": "G",  # Gas (same fluid GR, same material CS, but different phase)
                "operating_temperature": 95.0,
                "operating_pressure": 8.5,
                "run_idx": 1,
            },
            {
                "pid": "605-6\"-GR-CDA-003",
                "fluid": "GR",
                "pclass": "CDA",
                "material": "SS",  # Stainless Steel
                "fluid_phase": "L",
                "operating_temperature": 65.0,
                "operating_pressure": 10.0,
                "run_idx": 2,
            },
        ],
        "conn_points": [],
    }

    systems = circuitize(synthetic_result)
    assert len(systems) == 1, "Expected 1 corrosion system for fluid GR"
    gr_system = systems[0]
    circuits = gr_system["circuits"]

    # We expect 3 distinct circuits:
    # 1. CS + Liquid (L)
    # 2. CS + Gas (G)  <- Separated by API RP 970 §5.6.1 phase boundary!
    # 3. SS + Liquid (L) <- Separated by material
    assert len(circuits) == 3, f"Expected 3 circuits (phase & material splits), got {len(circuits)}"

    cs_circuits = [c for c in circuits if c["material"] == "CS"]
    assert len(cs_circuits) == 2, f"Expected 2 CS circuits partitioned by phase, got {len(cs_circuits)}"

    phases = {c["fluid_phase"] for c in cs_circuits}
    assert "L" in phases and "G" in phases, f"Expected both L and G phases in CS circuits, got {phases}"

    # Verify audit trail provenance
    for c in circuits:
        prov = c.get("provenance")
        assert prov is not None, f"Circuit {c['code']} missing provenance"
        assert prov["rule"] in ("API_RP_970_PHASE_BOUNDARY", "API_RP_970_MATERIAL_SPEC", "API_RP_970_MATERIAL_SPLIT")
        assert prov["source"] in ("linelist", "material_spec", "piping_class")
        assert 0.0 <= prov["confidence"] <= 1.0

    print("[PASS] Task C.02 & C.04: API RP 970 phase boundary split & provenance audit trail verified!")


@pytest.mark.asyncio
async def test_linelist_api_endpoint_lifecycle():
    """Verify Task C.01 & C.04 via full FastAPI async HTTP lifecycle."""
    # 0. Initialize SQLite in-memory tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create test project
            res_proj = await ac.post("/api/v1/projects", json={"name": "LineList Test Plant", "description": "Phase C test"})
            assert res_proj.status_code == 201, res_proj.text
            proj_data = res_proj.json()
            project_id = proj_data["id"]

            # 2. Create test sheet with initial P&ID detection result
            dummy_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
            res_sheet = await ac.post(
                f"/api/v1/projects/{project_id}/sheets",
                files={"file": ("BCD3-605-42-PID-1-001-01 REV.6J0-CCD2.png", dummy_png, "image/png")},
                data={"sheet_number": "001"},
            )
            assert res_sheet.status_code == 201, res_sheet.text
            sheet_id = res_sheet.json()["id"]

            # 3. Populate sheet with sample detection result conforming to DigitizationResult
            initial_result = {
                "image_path": "Contoh P&ID/BCD3-605-42-PID-1-001-01 REV.6J0-CCD2.png",
                "dpi": 350,
                "rot": 0,
                "w": 3300,
                "h": 2320,
                "symbols": [],
                "runs": [
                    {"points": [[100, 100], [500, 100]], "axis": "h", "x1": 100, "y1": 100, "x2": 500, "y2": 100},
                    {"points": [[100, 200], [500, 200]], "axis": "h", "x1": 100, "y1": 200, "x2": 500, "y2": 200},
                ],
                "piping_ids": [
                    {
                        "pid": '143-6"-GR-DSA-107',  # Matches Row 0 in 605_CCD2_loop_dataset.xlsx
                        "fluid": "GR",
                        "pclass": "DSA",
                        "run_idx": 0,
                        "x1": 150, "y1": 90, "x2": 300, "y2": 110,
                    },
                    {
                        "pid": '605-16"-GR-CDA-002',  # Matches Row 2 in 605_CCD2_loop_dataset.xlsx
                        "fluid": "GR",
                        "pclass": "CDA",
                        "run_idx": 1,
                        "x1": 150, "y1": 190, "x2": 300, "y2": 210,
                    },
                ],
                "conn_points": [],
                "furniture": [],
            }
            res_patch = await ac.patch(f"/api/v1/projects/{project_id}/sheets/{sheet_id}/result", json=initial_result)
            assert res_patch.status_code == 200, res_patch.text

            # 4. Upload Line List Excel spreadsheet
            excel_path = fixture_path("combined_dataset", "605_CCD2_loop_dataset.xlsx")
            with open(excel_path, "rb") as f:
                files = {"file": (excel_path.name, f.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
                res_upload = await ac.post(f"/api/v1/projects/{project_id}/linelist", files=files)

            assert res_upload.status_code == 200, res_upload.text
            upload_data = res_upload.json()
            assert upload_data["total_rows"] >= 100
            assert upload_data["matched_pids"] >= 2
            assert sheet_id in upload_data["enriched_sheets"]

            # 5. Retrieve project line list via GET
            res_get = await ac.get(f"/api/v1/projects/{project_id}/linelist")
            assert res_get.status_code == 200
            ll_data = res_get.json()
            assert ll_data["total_rows"] >= 100

            # 6. Verify sheet circuits were updated with operating parameters & provenance
            res_circuits = await ac.get(f"/api/v1/projects/{project_id}/sheets/{sheet_id}/systems")
            assert res_circuits.status_code == 200
            systems = res_circuits.json()
            assert len(systems) >= 1
            circuits = systems[0]["circuits"]
            assert len(circuits) >= 1

            first_circuit = circuits[0]
            assert first_circuit.get("provenance") is not None
            assert first_circuit["provenance"]["rule"] in ("LINE_LIST_CORROSION_LOOP", "API_RP_970_MATERIAL_SPEC", "API_RP_970_PHASE_BOUNDARY")
            assert first_circuit["operating_summary"] is not None

            print(f"[PASS] Line list upload, enrichment, and API RP 970 circuitization verified via REST API!")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    test_linelist_parser_excel()
    test_linelist_parser_csv()
    test_material_spec_structured_lookup()
    test_api_rp_970_phase_boundary_circuitization()
    import asyncio
    asyncio.run(test_linelist_api_endpoint_lifecycle())
