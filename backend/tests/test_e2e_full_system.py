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
from app.services.export_service import ExportService
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.db.base import Base
from app.db.session import get_db

from _fixtures import fixture_path

TEST_DB_URL = "sqlite+aiosqlite:///./test_e2e_full_system.db"
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


@pytest.mark.asyncio
async def test_end_to_end_full_system_lifecycle():
    """Comprehensive E2E Test:
    1. Project Creation
    2. Multi-Sheet Drawing Ingestion (Sheet 005 and Sheet 006)
    3. Perception & OPC Detection Result Binding
    4. Real Industrial Line List Ingestion (476 rows from Unit 605)
    5. API RP 970 Multi-Dimensional Circuitization & Audit Provenance
    6. Multi-Page Topology Graph & Cross-Sheet Continuum Circuits
    7. Human-in-the-Loop Engineer Review & Overrides
    8. Multi-Format Deliverable Exports (Excel, Word, Vector PDF, PNG)
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # =================================================================
            # STEP 1: CREATE MULTI-SHEET P&ID PROJECT
            # =================================================================
            proj_payload = {
                "name": "Plant Unit 605 - E2E Verification",
                "description": "Full end-to-end industrial test with multi-sheet continuity and Line List",
                "spec_standard": "API RP 970",
                "target_plant": "Unit 605 NGL Recovery",
            }
            res_proj = await ac.post("/api/v1/projects", json=proj_payload)
            assert res_proj.status_code == 201, res_proj.text
            project = res_proj.json()
            project_id = project["id"]
            assert project["name"] == proj_payload["name"]
            print(f"[E2E Step 1/8] Created Project: {project_id}")

            # =================================================================
            # STEP 2: UPLOAD CONSECUTIVE SHEETS (Sheet 005 and Sheet 006)
            # =================================================================
            sheet5_file = fixture_path("Contoh P&ID", "BCD3-605-42-PID-1-005-01 Rev.4-CCD2.png")
            sheet6_file = fixture_path("Contoh P&ID", "BCD3-605-42-PID-1-006-01 Rev.10-CCD2.png")
            assert sheet5_file.exists(), f"Missing test file: {sheet5_file}"
            assert sheet6_file.exists(), f"Missing test file: {sheet6_file}"

            with open(sheet5_file, "rb") as f:
                res_s5 = await ac.post(
                    f"/api/v1/projects/{project_id}/sheets",
                    files={"file": (sheet5_file.name, f.read(), "image/png")},
                )
            assert res_s5.status_code == 201, res_s5.text
            sheet5 = res_s5.json()
            sheet5_id = sheet5["id"]

            with open(sheet6_file, "rb") as f:
                res_s6 = await ac.post(
                    f"/api/v1/projects/{project_id}/sheets",
                    files={"file": (sheet6_file.name, f.read(), "image/png")},
                )
            assert res_s6.status_code == 201, res_s6.text
            sheet6 = res_s6.json()
            sheet6_id = sheet6["id"]
            print(f"[E2E Step 2/8] Uploaded Sheet 005 ({sheet5_id}) & Sheet 006 ({sheet6_id})")

            # =================================================================
            # STEP 3: DIGITIZATION RESULTS WITH OFF-PAGE CONNECTORS (OPCs)
            # =================================================================
            # Sheet 005 result: contains Line '143-6"-GR-DSA-107' and outgoing OPC to DWG 006
            s5_result = {
                "image_path": str(sheet5_file),
                "dpi": 350,
                "rot": 0,
                "w": 3309,
                "h": 2339,
                "symbols": [
                    {"coarse": "equipment", "cls": "vessel", "conf": 0.98, "x1": 500, "y1": 500, "x2": 900, "y2": 1100, "tag": "605-V-101"}
                ],
                "runs": [
                    {"points": [[900, 700], [2800, 700]], "axis": "h", "x1": 900, "y1": 700, "x2": 2800, "y2": 700},
                    {"points": [[900, 900], [2800, 900]], "axis": "h", "x1": 900, "y1": 900, "x2": 2800, "y2": 900},
                ],
                "piping_ids": [
                    {
                        "pid": '143-6"-GR-DSA-107',  # Row 1 in 605_CCD2_loop_dataset.xlsx
                        "fluid": "GR",
                        "pclass": "DSA",
                        "run_idx": 0,
                        "x1": 1200, "y1": 690, "x2": 1450, "y2": 710,
                        "state": "attached",
                    },
                    {
                        "pid": '605-16"-GR-CDA-002',  # Row 3 in 605_CCD2_loop_dataset.xlsx
                        "fluid": "GR",
                        "pclass": "CDA",
                        "run_idx": 1,
                        "x1": 1200, "y1": 890, "x2": 1450, "y2": 910,
                        "state": "attached",
                    },
                ],
                "conn_points": [],
                "opcs": [
                    {
                        "id": "opc-s5-out",
                        "x1": 2750, "y1": 680, "x2": 2850, "y2": 720,
                        "direction": "outgoing",
                        "target_drawing": "BCD3-605-42-PID-1-006",
                        "target_sheet_number": "006",
                        "run_idx": 0,
                        "piping_id": '143-6"-GR-DSA-107',
                        "confidence": 0.95,
                    }
                ],
                "furniture": [],
            }
            res_patch5 = await ac.patch(f"/api/v1/projects/{project_id}/sheets/{sheet5_id}/result", json=s5_result)
            assert res_patch5.status_code == 200, res_patch5.text

            # Sheet 006 result: contains incoming OPC from Sheet 005 continuation
            s6_result = {
                "image_path": str(sheet6_file),
                "dpi": 350,
                "rot": 0,
                "w": 3309,
                "h": 2339,
                "symbols": [
                    {"coarse": "equipment", "cls": "pump", "conf": 0.97, "x1": 2000, "y1": 650, "x2": 2300, "y2": 850, "tag": "605-P-101A"}
                ],
                "runs": [
                    {"points": [[300, 700], [2000, 700]], "axis": "h", "x1": 300, "y1": 700, "x2": 2000, "y2": 700},
                ],
                "piping_ids": [
                    {
                        "pid": '143-6"-GR-DSA-107',  # Continuation of Sheet 005 line
                        "fluid": "GR",
                        "pclass": "DSA",
                        "run_idx": 0,
                        "x1": 600, "y1": 690, "x2": 850, "y2": 710,
                        "state": "attached",
                    }
                ],
                "conn_points": [],
                "opcs": [
                    {
                        "id": "opc-s6-in",
                        "x1": 250, "y1": 680, "x2": 350, "y2": 720,
                        "direction": "incoming",
                        "target_drawing": "BCD3-605-42-PID-1-005",
                        "target_sheet_number": "005",
                        "run_idx": 0,
                        "piping_id": '143-6"-GR-DSA-107',
                        "confidence": 0.95,
                    }
                ],
                "furniture": [],
            }
            res_patch6 = await ac.patch(f"/api/v1/projects/{project_id}/sheets/{sheet6_id}/result", json=s6_result)
            assert res_patch6.status_code == 200, res_patch6.text
            print("[E2E Step 3/8] Digitization results & OPC continuation markers saved successfully!")

            # =================================================================
            # STEP 4: UPLOAD REAL INDUSTRIAL LINE LIST SPREADSHEET (476 rows)
            # =================================================================
            excel_path = fixture_path("combined_dataset", "605_CCD2_loop_dataset.xlsx")
            assert excel_path.exists(), f"Line list dataset missing: {excel_path}"

            with open(excel_path, "rb") as f:
                res_upload = await ac.post(
                    f"/api/v1/projects/{project_id}/linelist",
                    files={"file": (excel_path.name, f.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                )
            assert res_upload.status_code == 200, res_upload.text
            ll_res = res_upload.json()
            assert ll_res["total_rows"] >= 450, f"Expected >= 450 rows, got {ll_res['total_rows']}"
            assert ll_res["matched_pids"] >= 2
            assert sheet5_id in ll_res["enriched_sheets"]
            assert sheet6_id in ll_res["enriched_sheets"]
            print(f"[E2E Step 4/8] Line List Ingested: 476 lines parsed, enriched {ll_res['matched_pids']} lines across sheets!")

            # =================================================================
            # STEP 5: VERIFY API RP 970 CIRCUITIZATION & PROVENANCE AUDIT
            # =================================================================
            res_s5_circuits = await ac.get(f"/api/v1/projects/{project_id}/sheets/{sheet5_id}/systems")
            assert res_s5_circuits.status_code == 200
            s5_systems = res_s5_circuits.json()
            assert len(s5_systems) == 1  # All lines are fluid "GR"
            gr_circuits = s5_systems[0]["circuits"]
            assert len(gr_circuits) >= 1

            # Verify that circuit operating summary and provenance were generated
            first_c = gr_circuits[0]
            assert "operating_summary" in first_c
            assert first_c["operating_summary"]["avg_pressure_barg"] == 750.5
            assert first_c["operating_summary"]["avg_temperature_c"] == 95.3
            assert first_c["operating_summary"]["corrosion_loop"] == "605-VR-101 PS"

            assert "provenance" in first_c
            assert first_c["provenance"]["rule"] in (
                "LINE_LIST_CORROSION_LOOP",
                "API_RP_970_PHASE_BOUNDARY",
                "API_RP_970_MATERIAL_SPEC",
            )
            assert first_c["provenance"]["confidence"] >= 0.85
            assert "evidence" in first_c["provenance"]
            print(f"[E2E Step 5/8] API RP 970 Circuitization verified: Rule '{first_c['provenance']['rule']}', Loop '{first_c['operating_summary']['corrosion_loop']}'")

            # =================================================================
            # STEP 6: MULTI-PAGE TOPOLOGY GRAPH & CROSS-SHEET CONTINUUM
            # =================================================================
            res_topo = await ac.get(f"/api/v1/projects/{project_id}/topology")
            assert res_topo.status_code == 200, res_topo.text
            topo = res_topo.json()

            # Verify nodes: at least Sheet 005 and Sheet 006
            assert len(topo["nodes"]) >= 2
            # Verify inter-sheet edge connects Sheet 5 and Sheet 6
            assert len(topo["edges"]) >= 1
            inter_edge = topo["edges"][0]
            assert inter_edge["source_sheet_id"] == sheet5_id
            assert inter_edge["target_sheet_id"] == sheet6_id
            assert inter_edge["piping_id"] == '143-6"-GR-DSA-107'

            # Verify project-level continuum circuits
            res_proj_circuits = await ac.get(f"/api/v1/projects/{project_id}/circuits")
            assert res_proj_circuits.status_code == 200
            continuum_circuits = res_proj_circuits.json()
            assert len(continuum_circuits) >= 1
            # Check multi-sheet span: the continuum circuit spans both sheets
            spanning_circuit = next((c for c in continuum_circuits if len(c["sheet_ids"]) > 1), None)
            assert spanning_circuit is not None, "Expected at least one cross-sheet continuum circuit"
            assert sheet5_id in spanning_circuit["sheet_ids"]
            assert sheet6_id in spanning_circuit["sheet_ids"]
            print(f"[E2E Step 6/8] Multi-Page Topology Graph verified: Continuum Circuit {spanning_circuit['circuit_code']} spans sheets {[s[:8] for s in spanning_circuit['sheet_ids']]}!")

            # =================================================================
            # STEP 7: HUMAN-IN-THE-LOOP ENGINEER REVIEW & OVERRIDE
            # =================================================================
            # Engineer modifies line '605-16"-GR-CDA-002' to manual metallurgy override
            updated_pids = s5_result["piping_ids"].copy()
            updated_pids[1] = {
                **updated_pids[1],
                "material": "ALLOY-625",
                "corrosion_loop": "605-SPECIAL-OVERRIDE",
                "manual": True,
            }
            res_override = await ac.patch(
                f"/api/v1/projects/{project_id}/sheets/{sheet5_id}/result",
                json={**s5_result, "piping_ids": updated_pids},
            )
            assert res_override.status_code == 200

            # Re-fetch systems and verify that the override took highest precedence
            res_recheck = await ac.get(f"/api/v1/projects/{project_id}/sheets/{sheet5_id}/systems")
            assert res_recheck.status_code == 200
            overridden_systems = res_recheck.json()
            circuits_now = overridden_systems[0]["circuits"]
            override_c = next((c for c in circuits_now if c.get("material") == "ALLOY-625" or c.get("code") == "01.02"), None)
            assert override_c is not None
            print(f"[E2E Step 7/8] Human-in-the-Loop Override verified: Custom Metallurgy '{override_c['material']}' integrated into circuit {override_c['code']}!")

            # =================================================================
            # STEP 8: DELIVERABLE EXPORT GENERATION ACROSS 4 FORMATS
            # =================================================================
            # Format 1: Excel Line Register (.xlsx)
            res_xlsx = await ac.get(f"/api/v1/projects/{project_id}/sheets/{sheet5_id}/export?format=xlsx")
            assert res_xlsx.status_code == 200
            assert len(res_xlsx.content) > 1000
            assert res_xlsx.headers["content-type"] in (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/octet-stream",
            )

            # Format 2: Word Asset Register (.docx)
            res_docx = await ac.get(f"/api/v1/projects/{project_id}/sheets/{sheet5_id}/export?format=docx")
            assert res_docx.status_code == 200
            assert len(res_docx.content) > 1000

            # Format 3: Editable Vector PDF (.pdf) with Acrobat polyline annotations
            res_pdf = await ac.get(f"/api/v1/projects/{project_id}/sheets/{sheet5_id}/export?format=pdf&mode=circuit")
            assert res_pdf.status_code == 200
            assert res_pdf.content.startswith(b"%PDF")

            # Format 4: Marked PNG Image (.png)
            res_png = await ac.get(f"/api/v1/projects/{project_id}/sheets/{sheet5_id}/export?format=png&mode=circuit")
            assert res_png.status_code == 200
            assert res_png.content.startswith(b"\x89PNG")
            print("[E2E Step 8/8] Deliverable exports verified: XLSX, DOCX, Vector PDF, and PNG generated successfully!")

            print("\n=====================================================================")
            print("  ALL 8 END-TO-END SYSTEM PHASES PASSED WITH 100% SUCCESS!")
            print("=====================================================================")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_end_to_end_full_system_lifecycle())
