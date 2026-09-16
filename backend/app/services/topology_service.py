import re
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..models.project import Project
from ..models.sheet import Sheet
from ..schemas.topology import (
    TopologyNode,
    TopologyEdge,
    ProjectCircuit,
    ProjectTopologyResponse,
)
from ..schemas.system import CircuitProvenance
from ..services.grouping_service import GroupingService


def _normalize_drawing_ref(ref: str) -> str:
    """Normalize sheet or drawing number string for flexible matching."""
    s = str(ref or "").strip().upper()
    # Strip extension if any
    s = re.sub(r'\.(png|jpg|jpeg|pdf)$', '', s, flags=re.I)
    # Remove whitespace and common separators
    s = re.sub(r'[\s_\-]', '', s)
    return s


class MultiPageGraphService:
    """Service to stitch multi-page P&ID drawings into an interconnected plant unit topology."""

    @staticmethod
    async def build_project_topology(
        db: AsyncSession,
        project_id: str,
    ) -> ProjectTopologyResponse:
        """Construct multi-page graph and aggregated project corrosion circuits."""
        # 1. Fetch sheets
        stmt = select(Sheet).where(Sheet.project_id == project_id).order_by(Sheet.sheet_number, Sheet.filename)
        res = await db.execute(stmt)
        sheets = res.scalars().all()

        if not sheets:
            return ProjectTopologyResponse(
                project_id=project_id,
                nodes=[],
                edges=[],
                circuits=[],
                summary={"total_sheets": 0, "total_edges": 0, "total_circuits": 0},
            )

        # 2. Build index of sheets by ID and by drawing/sheet number variations
        sheet_index: Dict[str, Sheet] = {s.id: s for s in sheets}
        ref_to_sheet: Dict[str, str] = {}

        for s in sheets:
            # Index by raw sheet number
            if s.sheet_number:
                ref_to_sheet[_normalize_drawing_ref(s.sheet_number)] = s.id
                ref_to_sheet[str(s.sheet_number).strip().lower()] = s.id
            # Index by filename patterns (e.g. PID-1-005, 005)
            fname_clean = _normalize_drawing_ref(s.filename)
            ref_to_sheet[fname_clean] = s.id

            # Extract 3-digit sheet sequence from filename (e.g. 1-005 -> 005)
            m_seq = re.search(r'(\d{3})', s.filename)
            if m_seq:
                ref_to_sheet[_normalize_drawing_ref(m_seq.group(1))] = s.id

            m_pid = re.search(r'(PID[-_\d]+)', s.filename, re.I)
            if m_pid:
                ref_to_sheet[_normalize_drawing_ref(m_pid.group(1))] = s.id

        # 3. Create Sheet nodes
        nodes: List[TopologyNode] = []
        for s in sheets:
            nodes.append(
                TopologyNode(
                    id=s.id,
                    type="sheet",
                    label=s.sheet_number or s.filename,
                    metadata={
                        "filename": s.filename,
                        "sheet_number": s.sheet_number,
                        "status": s.status,
                        "dpi": s.dpi,
                    },
                )
            )

        # 4. Resolve OPC edges across sheets
        edges: List[TopologyEdge] = []
        seen_edge_pairs = set()

        for s in sheets:
            result_json = s.result_json or {}
            opcs = result_json.get("opcs", [])

            for opc in opcs:
                target_dwg = opc.get("target_drawing", "")
                target_sh = opc.get("target_sheet_number", "")
                opc_pid = opc.get("piping_id")
                opc_line = opc.get("target_line")

                # Find candidate target sheet ID
                target_sheet_id = None
                for cand in (target_sh, target_dwg):
                    if not cand:
                        continue
                    norm = _normalize_drawing_ref(cand)
                    if norm in ref_to_sheet and ref_to_sheet[norm] != s.id:
                        target_sheet_id = ref_to_sheet[norm]
                        break

                if not target_sheet_id:
                    # Try matching by identical piping_id across other sheets
                    check_pid = opc_line or opc_pid
                    if check_pid:
                        for other in sheets:
                            if other.id == s.id:
                                continue
                            other_pids = [
                                p.get("pid")
                                for p in (other.result_json or {}).get("piping_ids", [])
                            ]
                            if check_pid in other_pids:
                                target_sheet_id = other.id
                                break

                if target_sheet_id and target_sheet_id != s.id:
                    # Find matching reciprocal OPC on target sheet if possible
                    target_sheet = sheet_index[target_sheet_id]
                    target_opcs = (target_sheet.result_json or {}).get("opcs", [])
                    matched_target_opc_id = None

                    for t_opc in target_opcs:
                        # Match if reciprocal drawing points back, or line matches
                        if t_opc.get("piping_id") and (
                            t_opc.get("piping_id") == opc_pid or t_opc.get("piping_id") == opc_line
                        ):
                            matched_target_opc_id = t_opc.get("id")
                            break

                    edge_key = (
                        min(s.id, target_sheet_id),
                        max(s.id, target_sheet_id),
                        opc_pid or opc_line or opc["id"],
                    )
                    if edge_key not in seen_edge_pairs:
                        seen_edge_pairs.add(edge_key)
                        edges.append(
                            TopologyEdge(
                                id=f"edge-{len(edges) + 1:03d}",
                                source_sheet_id=s.id,
                                target_sheet_id=target_sheet_id,
                                source_opc_id=opc["id"],
                                target_opc_id=matched_target_opc_id,
                                piping_id=opc_pid or opc_line,
                                fluid=opc_pid.split("-")[2] if opc_pid and "-" in opc_pid else None,
                                confidence=0.95 if matched_target_opc_id else 0.85,
                            )
                        )

        # 5. Aggregate Project-Wide Corrosion Circuits across sheets
        circuit_groups: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

        for s in sheets:
            systems = s.systems_json or []
            # If not precomputed, compute on the fly
            if not systems and s.result_json:
                systems = GroupingService.compute_circuits(s.result_json)

            for sys in systems:
                fluid = sys.get("fluid", "")
                sys_color = sys.get("color", [128, 128, 128])

                for circ in sys.get("circuits", []):
                    code = circ.get("code", "")
                    mat = circ.get("material", "CS")
                    phase = circ.get("fluid_phase", "")
                    op_sum = circ.get("operating_summary") or {}
                    loop = op_sum.get("corrosion_loop") or ""

                    # Grouping key across sheets
                    group_key = (fluid, mat, phase, loop)

                    # Extract piping IDs belonging to this circuit in this sheet
                    sheet_pids = (s.result_json or {}).get("piping_ids", [])
                    circ_pid_idxs = circ.get("pid_idxs", [])
                    circ_pids = [
                        sheet_pids[pi].get("pid")
                        for pi in circ_pid_idxs
                        if 0 <= pi < len(sheet_pids) and sheet_pids[pi].get("pid")
                    ]

                    if group_key not in circuit_groups:
                        circuit_groups[group_key] = {
                            "circuit_code": code,
                            "fluid": fluid,
                            "material": mat,
                            "fluid_phase": phase,
                            "color": circ.get("color", sys_color),
                            "sheet_ids": [s.id],
                            "member_pids": list(set(circ_pids)),
                            "total_pipes": len(circ.get("run_idxs", [])),
                            "operating_summary": dict(op_sum),
                            "provenance": circ.get("provenance"),
                        }
                    else:
                        g = circuit_groups[group_key]
                        if s.id not in g["sheet_ids"]:
                            g["sheet_ids"].append(s.id)
                        g["member_pids"] = sorted(list(set(g["member_pids"] + circ_pids)))
                        g["total_pipes"] += len(circ.get("run_idxs", []))
                        # Merge operating summary
                        if not g["operating_summary"].get("corrosion_loop") and loop:
                            g["operating_summary"]["corrosion_loop"] = loop

        project_circuits: List[ProjectCircuit] = [
            ProjectCircuit(**cg) for cg in circuit_groups.values()
        ]

        # Add circuit nodes to topology
        for pc in project_circuits:
            nodes.append(
                TopologyNode(
                    id=f"circ-{pc.circuit_code}-{pc.fluid}-{pc.material}",
                    type="circuit",
                    label=f"Circuit {pc.circuit_code} ({pc.fluid} - {pc.material})",
                    metadata={
                        "fluid": pc.fluid,
                        "material": pc.material,
                        "fluid_phase": pc.fluid_phase,
                        "sheets_count": len(pc.sheet_ids),
                        "pipes_count": pc.total_pipes,
                    },
                )
            )

        summary = {
            "total_sheets": len(sheets),
            "total_opc_edges": len(edges),
            "total_unified_circuits": len(project_circuits),
            "multi_sheet_circuits": len([pc for pc in project_circuits if len(pc.sheet_ids) > 1]),
        }

        return ProjectTopologyResponse(
            project_id=project_id,
            nodes=nodes,
            edges=edges,
            circuits=project_circuits,
            summary=summary,
        )
