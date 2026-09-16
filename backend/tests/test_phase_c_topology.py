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
from pidcorr.opc_detector import detect_off_page_connectors, find_terminal_endpoints
from app.services.topology_service import MultiPageGraphService

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.db.base import Base
from app.db.session import get_db

TEST_DB_URL = "sqlite+aiosqlite:///./test_topology_engine.db"
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


def test_terminal_endpoints_and_opc_detection():
    """Verify Task C.05: Identifying terminal pipe runs at drawing margins and extracting target drawing tags."""
    w, h = 3300, 2320
    # Pipe run 0 terminates near right margin (x=3200 > 0.82*3300 = 2706)
    # Pipe run 1 terminates near left margin (x=100 < 0.18*3300 = 594)
    mock_result = {
        "w": w,
        "h": h,
        "runs": [
            {"points": [[2000, 500], [3200, 500]], "axis": "h", "x1": 2000, "y1": 500, "x2": 3200, "y2": 500},
            {"points": [[100, 1200], [1000, 1200]], "axis": "h", "x1": 100, "y1": 1200, "x2": 1000, "y2": 1200},
        ],
        "piping_ids": [
            {"pid": '143-6"-GR-DSA-107', "run_idx": 0, "fluid": "GR", "pclass": "DSA"},
            {"pid": '605-16"-GR-CDA-002', "run_idx": 1, "fluid": "GR", "pclass": "CDA"},
        ],
    }

    terminals = find_terminal_endpoints(mock_result["runs"], w, h, margin_ratio=0.18)
    assert len(terminals) == 2
    assert terminals[0][0] == 0 and terminals[0][2] == "right"
    assert terminals[1][0] == 1 and terminals[1][2] == "left"

    # Provide OCR tokens in vicinity of terminal 0: 'TO DWG 605-42-PID-1-006' and 'SH. 6'
    mock_tokens = [
        {"text": "TO DWG 605-42-PID-1-006", "x1": 3210, "y1": 490, "x2": 3290, "y2": 510},
        {"text": "SH. 6", "x1": 3210, "y1": 515, "x2": 3260, "y2": 530},
        # Distant token (should not associate)
        {"text": "COMPRESSOR K-101", "x1": 1500, "y1": 1000, "x2": 1700, "y2": 1050},
    ]

    opcs = detect_off_page_connectors(mock_result, tokens=mock_tokens, dpi=350)
    assert len(opcs) >= 1

    opc_right = next((o for o in opcs if o["run_idx"] == 0), None)
    assert opc_right is not None
    assert opc_right["direction"] == "outgoing"
    assert "605-42-PID-1-006" in opc_right["target_drawing"]
    assert opc_right["target_sheet_number"] == "6"
    assert opc_right["piping_id"] == '143-6"-GR-DSA-107'
    print("[PASS] Task C.05: Terminal endpoint detection and OPC text parsing verified!")


@pytest.mark.asyncio
async def test_multi_page_topology_and_circuits_service():
    """Verify Task C.06: MultiPageGraphService building inter-sheet edges and multi-sheet continuum circuits."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    from app.models.project import Project
    from app.models.sheet import Sheet

    async with TestSessionLocal() as session:
        # Create Project
        proj = Project(
            id="proj-top-001",
            name="Plant 605 Multi-Sheet Unit",
            description="Testing multi-page continuum",
        )
        session.add(proj)

        # Create Sheet 1 (001) with outgoing OPC to Sheet 006
        sheet1 = Sheet(
            id="sheet-001",
            project_id=proj.id,
            filename="BCD3-605-42-PID-1-001.png",
            sheet_number="001",
            file_path="dummy/001.png",
            status="detected",
            result_json={
                "w": 3300,
                "h": 2320,
                "runs": [
                    {"points": [[100, 500], [3200, 500]], "axis": "h", "x1": 100, "y1": 500, "x2": 3200, "y2": 500}
                ],
                "piping_ids": [
                    {"pid": '143-6"-GR-DSA-107', "fluid": "GR", "pclass": "DSA", "material": "SS", "run_idx": 0}
                ],
                "opcs": [
                    {
                        "id": "opc-001",
                        "x1": 3200, "y1": 490, "x2": 3250, "y2": 510,
                        "direction": "outgoing",
                        "target_drawing": "BCD3-605-42-PID-1-006",
                        "target_sheet_number": "006",
                        "piping_id": '143-6"-GR-DSA-107',
                        "run_idx": 0,
                        "confidence": 0.95,
                    }
                ],
            },
        )

        # Create Sheet 2 (006) with incoming OPC from Sheet 001
        sheet2 = Sheet(
            id="sheet-006",
            project_id=proj.id,
            filename="BCD3-605-42-PID-1-006.png",
            sheet_number="006",
            file_path="dummy/006.png",
            status="detected",
            result_json={
                "w": 3300,
                "h": 2320,
                "runs": [
                    {"points": [[100, 500], [2000, 500]], "axis": "h", "x1": 100, "y1": 500, "x2": 2000, "y2": 500}
                ],
                "piping_ids": [
                    {"pid": '143-6"-GR-DSA-107', "fluid": "GR", "pclass": "DSA", "material": "SS", "run_idx": 0}
                ],
                "opcs": [
                    {
                        "id": "opc-002",
                        "x1": 90, "y1": 490, "x2": 110, "y2": 510,
                        "direction": "incoming",
                        "target_drawing": "BCD3-605-42-PID-1-001",
                        "target_sheet_number": "001",
                        "piping_id": '143-6"-GR-DSA-107',
                        "run_idx": 0,
                        "confidence": 0.95,
                    }
                ],
            },
        )
        session.add(sheet1)
        session.add(sheet2)
        await session.commit()

        # Build topology
        topology = await MultiPageGraphService.build_project_topology(session, proj.id)

        # Verify graph nodes & edges
        sheet_nodes = [n for n in topology.nodes if n.type == "sheet"]
        assert len(sheet_nodes) == 2, f"Expected 2 sheet nodes, got {len(sheet_nodes)}"

        assert len(topology.edges) == 1, f"Expected 1 inter-sheet edge connecting 001 and 006, got {len(topology.edges)}"
        edge = topology.edges[0]
        assert edge.source_sheet_id == "sheet-001"
        assert edge.target_sheet_id == "sheet-006"
        assert edge.source_opc_id == "opc-001"
        assert edge.target_opc_id == "opc-002"
        assert edge.piping_id == '143-6"-GR-DSA-107'

        # Verify unified multi-sheet circuits
        assert len(topology.circuits) >= 1
        gr_circuit = next((c for c in topology.circuits if c.fluid == "GR"), None)
        assert gr_circuit is not None
        assert "sheet-001" in gr_circuit.sheet_ids and "sheet-006" in gr_circuit.sheet_ids
        assert gr_circuit.total_pipes == 2
        assert '143-6"-GR-DSA-107' in gr_circuit.member_pids
        assert topology.summary["multi_sheet_circuits"] >= 1

        print("[PASS] Task C.06: MultiPageGraphService inter-sheet topology & circuit continuum verified!")


@pytest.mark.asyncio
async def test_topology_api_endpoint_lifecycle():
    """Verify Task C.06 via FastAPI async HTTP REST endpoints."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create project
            res_p = await ac.post("/api/v1/projects", json={"name": "API Topology Project"})
            assert res_p.status_code == 201
            project_id = res_p.json()["id"]

            # 2. Upload 2 sheets
            dummy_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
            s1_res = await ac.post(
                f"/api/v1/projects/{project_id}/sheets",
                files={"file": ("PID-001.png", dummy_png, "image/png")},
                data={"sheet_number": "001"},
            )
            s2_res = await ac.post(
                f"/api/v1/projects/{project_id}/sheets",
                files={"file": ("PID-002.png", dummy_png, "image/png")},
                data={"sheet_number": "002"},
            )
            assert s1_res.status_code == 201 and s2_res.status_code == 201
            s1_id = s1_res.json()["id"]
            s2_id = s2_res.json()["id"]

            # 3. Patch sheets with OPC connectivity
            res1_payload = {
                "image_path": "dummy1.png",
                "dpi": 350, "rot": 0, "w": 3300, "h": 2320,
                "symbols": [], "conn_points": [], "furniture": [],
                "runs": [{"points": [[100, 100], [3200, 100]], "axis": "h", "x1": 100, "y1": 100, "x2": 3200, "y2": 100}],
                "piping_ids": [{"pid": '605-4"-GF-CCC-001', "fluid": "GF", "pclass": "CCC", "material": "CS", "run_idx": 0, "x1": 150, "y1": 90, "x2": 300, "y2": 110}],
                "opcs": [{"id": "opc-1", "x1": 3200, "y1": 90, "x2": 3250, "y2": 110, "direction": "outgoing", "target_drawing": "PID-002", "target_sheet_number": "002", "piping_id": '605-4"-GF-CCC-001', "run_idx": 0, "confidence": 0.95}],
            }
            res2_payload = {
                "image_path": "dummy2.png",
                "dpi": 350, "rot": 0, "w": 3300, "h": 2320,
                "symbols": [], "conn_points": [], "furniture": [],
                "runs": [{"points": [[100, 100], [1500, 100]], "axis": "h", "x1": 100, "y1": 100, "x2": 1500, "y2": 100}],
                "piping_ids": [{"pid": '605-4"-GF-CCC-001', "fluid": "GF", "pclass": "CCC", "material": "CS", "run_idx": 0, "x1": 150, "y1": 90, "x2": 300, "y2": 110}],
                "opcs": [{"id": "opc-2", "x1": 90, "y1": 90, "x2": 110, "y2": 110, "direction": "incoming", "target_drawing": "PID-001", "target_sheet_number": "001", "piping_id": '605-4"-GF-CCC-001', "run_idx": 0, "confidence": 0.95}],
            }
            p1_res = await ac.patch(f"/api/v1/projects/{project_id}/sheets/{s1_id}/result", json=res1_payload)
            p2_res = await ac.patch(f"/api/v1/projects/{project_id}/sheets/{s2_id}/result", json=res2_payload)
            assert p1_res.status_code == 200 and p2_res.status_code == 200

            # 4. Call GET /topology
            top_res = await ac.get(f"/api/v1/projects/{project_id}/topology")
            assert top_res.status_code == 200, top_res.text
            top_data = top_res.json()
            assert top_data["project_id"] == project_id
            assert len(top_data["nodes"]) >= 2
            assert len(top_data["edges"]) == 1
            assert top_data["edges"][0]["piping_id"] == '605-4"-GF-CCC-001'

            # 5. Call GET /circuits
            circ_res = await ac.get(f"/api/v1/projects/{project_id}/circuits")
            assert circ_res.status_code == 200, circ_res.text
            circ_data = circ_res.json()
            assert len(circ_data) >= 1
            assert len(circ_data[0]["sheet_ids"]) == 2

            print("[PASS] Task C.06: /topology and /circuits endpoints lifecycle verified!")
    finally:
        app.dependency_overrides.clear()
