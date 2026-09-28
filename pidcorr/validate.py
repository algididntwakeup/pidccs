"""
Automated validation of the DIGITIZATION result (flowchart step: "Automated validation:
consistency & confidence checks"). This is RUNTIME validation (per drawing), NOT model
evaluation — it does not need ground truth. It runs deterministic consistency checks +
confidence thresholds, flags likely errors, and returns a structured report. The engineer
only confirms the flagged items ("human-on-the-loop"), so validation is system-driven.

Two validation contexts (see thesis):
  - Development-time (once): confusion matrix / mAP on a labelled test set -> proves the
    models are accurate enough. NOT here.
  - Runtime (this module): checks THIS drawing's output is internally consistent & confident.

validate_digitization(result) -> report dict:
  { n_symbols, n_piping_ids, n_pipes, checks[], score, passed, flags[] }
"""
from __future__ import annotations
from .review import build_review


def _pct(num, den):
    return 100.0 * num / den if den else 100.0


def validate_digitization(result, sym_conf_min=0.35, pid_vote_min=2, main_len_pt=45):
    """Run automated consistency & confidence checks on a digitization result."""
    pids = result.get("piping_ids", []) if result else []
    syms = result.get("symbols", []) if result else []
    runs = result.get("runs", []) if result else []

    # consistency / anomaly findings (reuse the review engine) -> engineer-confirmation list
    issues = build_review(result, main_len_pt=main_len_pt)
    n_suspect = sum(1 for i in issues if i["kind"] == "fluid_suspect")
    n_orphan = sum(1 for i in issues if i["kind"] == "orphan_run")

    linked = sum(1 for p in pids if p.get("run_idx", -1) is not None and p.get("run_idx", -1) >= 0)
    fluided = sum(1 for p in pids if (p.get("fluid") or "").strip())
    sym_ok = sum(1 for s in syms if float(s.get("conf", 0) or 0) >= sym_conf_min)
    pid_ok = sum(1 for p in pids if int(p.get("conf", 0) or 0) >= pid_vote_min)
    low_conf_syms = len(syms) - sym_ok

    checks = [
        {"id": "pipe_link", "name": "Piping IDs linked to a pipe",
         "value": round(_pct(linked, len(pids)), 1), "detail": f"{linked}/{len(pids)}",
         "passed": linked == len(pids)},
        {"id": "fluid_parsed", "name": "Piping IDs with a process-fluid code",
         "value": round(_pct(fluided, len(pids)), 1), "detail": f"{fluided}/{len(pids)}",
         "passed": fluided == len(pids)},
        {"id": "fluid_consistency", "name": "No suspected OCR fluid codes",
         "value": 100.0 if n_suspect == 0 else round(_pct(len(pids) - n_suspect, len(pids)), 1),
         "detail": f"{n_suspect} suspect", "passed": n_suspect == 0},
        {"id": "symbol_conf", "name": f"Symbols with confidence >= {sym_conf_min}",
         "value": round(_pct(sym_ok, len(syms)), 1), "detail": f"{sym_ok}/{len(syms)}",
         "passed": low_conf_syms == 0},
        {"id": "pid_conf", "name": f"Piping IDs with >= {pid_vote_min} OCR votes",
         "value": round(_pct(pid_ok, len(pids)), 1), "detail": f"{pid_ok}/{len(pids)}",
         "passed": pid_ok == len(pids)},
        {"id": "pipe_labeled", "name": "No long unlabeled main pipes",
         "value": 100.0 if n_orphan == 0 else 0.0, "detail": f"{n_orphan} orphan",
         "passed": n_orphan == 0},
    ]
    # attach English messages (report/thesis-facing; review.py msg stays Indonesian for GUI)
    for it in issues:
        k = it["kind"]
        if k == "fluid_suspect":
            it["msg_en"] = (f"Fluid code '{it['fluid']}' used by only 1 line, similar to "
                            f"'{it['suggest']}' — likely OCR error; suggest merge.")
        elif k == "pid_none":
            it["msg_en"] = (f"'{pids[it['pid_idx']].get('pid','?')}' not linked to any pipe "
                            "— connect manually.")
        elif k == "fluid_empty":
            it["msg_en"] = (f"'{pids[it['pid_idx']].get('pid','?')}' has no fluid token "
                            "— edit so it joins systemization.")
        elif k == "orphan_run":
            it["msg_en"] = "Long main pipe without a piping ID — link it or delete."
        else:
            it["msg_en"] = it.get("msg", "")

    score = round(sum(c["value"] for c in checks) / len(checks), 1)
    # CRITICAL = must fix before proceeding; orphan pipes = soft WARNING (a long pipe
    # legitimately may not carry a label). Verdict is driven by CRITICAL only, so the
    # validator does not "cry wolf" on label-sparse drawings.
    critical = [it for it in issues if it["kind"] != "orphan_run"]
    warnings = [it for it in issues if it["kind"] == "orphan_run"]
    n_critical = len(critical) + low_conf_syms
    passed = n_critical == 0
    return {
        "n_symbols": len(syms), "n_piping_ids": len(pids), "n_pipes": len(runs),
        "checks": checks, "score": score,
        "n_critical": n_critical, "n_warnings": len(warnings), "passed": passed,
        "flags": critical, "warnings": warnings, "low_conf_symbols": low_conf_syms,
    }


def validate_grouping(result):
    """Automated validation of SYSTEMIZATION + CIRCUITIZATION (flowchart: 'logic checks').
    Deterministic invariants — each must hold by construction; a violation means corrupted
    input (e.g. mis-parsed fluid) leaked through, so it is flagged for the engineer."""
    from .systemize import circuitize, material_of
    pids = result.get("piping_ids", []) if result else []
    systems = circuitize(result) if result else []
    fluided = [i for i, p in enumerate(pids) if (p.get("fluid") or "").strip()]
    flags = []

    # 1) one fluid per system + membership consistency
    bad_fluid = 0
    for s in systems:
        for pi in s["pid_idxs"]:
            if (pids[pi].get("fluid") or "").strip().upper() != s["fluid"]:
                bad_fluid += 1
                flags.append({"kind": "system_mismatch",
                              "msg_en": f"'{pids[pi].get('pid','?')}' placed in system "
                                        f"{s['fluid']} but its fluid differs."})
    # 2) partition: every fluided pid in exactly one system
    seen = {}
    for s in systems:
        for pi in s["pid_idxs"]:
            seen[pi] = seen.get(pi, 0) + 1
    dup = [i for i, n in seen.items() if n > 1]
    miss = [i for i in fluided if i not in seen]
    for i in dup:
        flags.append({"kind": "overlap", "msg_en": f"'{pids[i].get('pid','?')}' assigned to >1 system."})
    for i in miss:
        flags.append({"kind": "ungrouped", "msg_en": f"'{pids[i].get('pid','?')}' has a fluid but no system."})
    # 3) one material per circuit + membership
    bad_mat = 0
    n_circ = 0
    for s in systems:
        for c in s["circuits"]:
            n_circ += 1
            for pi in c["pid_idxs"]:
                if material_of(pids[pi].get("pclass")) != c["material"]:
                    bad_mat += 1
                    flags.append({"kind": "circuit_mismatch",
                                  "msg_en": f"'{pids[pi].get('pid','?')}' in circuit {c['code']} "
                                            f"but its material differs."})
    # 4) unique colors across systems (palette exhaustion check)
    cols = [tuple(s["color"]) for s in systems]
    n_col_clash = len(cols) - len(set(cols))
    if n_col_clash:
        flags.append({"kind": "color_clash",
                      "msg_en": f"{n_col_clash} system color collision(s) — palette exhausted; "
                                "extend PALETTE."})
    # 5) CROSS-CHECK vs CONNECTION POINTS (spec breaks drawn on the P&ID itself).
    #    Independent evidence: the drawing states which two piping classes meet at each
    #    break. Compare against the classes the system assigned to the pipes on both
    #    sides. Agreement = the circuit boundary is where the designer put it.
    from .systemize import run_labels
    cps = result.get("conn_points", []) if result else []
    lab = run_labels(result) if result else []
    cp_ok = cp_bad = 0
    for c in cps:
        ri = c.get("run_idx", -1)
        if ri < 0 or ri >= len(lab) or not lab[ri]:
            continue
        got = (lab[ri].get("pclass") or "").upper()
        if not got:
            continue
        if got in {str(x).upper() for x in c.get("codes", [])}:
            cp_ok += 1
        else:
            cp_bad += 1
            flags.append({"kind": "specbreak_mismatch",
                          "msg_en": f"Connection point {c['codes'][0]}|{c['codes'][1]} sits on a "
                                    f"pipe the system assigned class {got} — check the line "
                                    f"number parse or the break location."})
    n_cp_checked = cp_ok + cp_bad

    checks = [
        {"id": "one_fluid", "name": "One process fluid per system",
         "value": 100.0 if bad_fluid == 0 else 0.0, "detail": f"{bad_fluid} mismatch", "passed": bad_fluid == 0},
        {"id": "partition", "name": "Each line in exactly one system",
         "value": round(_pct(len(fluided) - len(dup) - len(miss), len(fluided)), 1),
         "detail": f"{len(dup)} dup / {len(miss)} missing", "passed": not dup and not miss},
        {"id": "one_material", "name": "One material per circuit",
         "value": 100.0 if bad_mat == 0 else 0.0, "detail": f"{bad_mat} mismatch", "passed": bad_mat == 0},
        {"id": "coverage", "name": "Lines with fluid grouped",
         "value": round(_pct(len(fluided), len(pids)), 1),
         "detail": f"{len(fluided)}/{len(pids)}", "passed": len(fluided) == len(pids)},
        {"id": "colors", "name": "Distinct color per system",
         "value": 100.0 if n_col_clash == 0 else 0.0, "detail": f"{n_col_clash} clash", "passed": n_col_clash == 0},
    ]
    if n_cp_checked:
        checks.append(
            {"id": "specbreak", "name": "Circuit boundary agrees with drawn spec break",
             "value": round(_pct(cp_ok, n_cp_checked), 1),
             "detail": f"{cp_ok}/{n_cp_checked} connection points", "passed": cp_bad == 0})
    score = round(sum(c["value"] for c in checks) / len(checks), 1)
    return {"title": "GROUPING (SYSTEMIZATION + CIRCUITIZATION)",
            "header": f"{len(systems)} corrosion systems, {n_circ} circuits, "
                      f"{len(fluided)}/{len(pids)} lines grouped"
                      + (f", {len(cps)} spec breaks" if cps else ""),
            "checks": checks, "score": score, "n_critical": len(flags),
            "n_warnings": 0, "passed": not flags, "flags": flags, "warnings": []}


def validate_marking(result):
    """Automated validation of MARKING: every colored pipe's color equals its group color,
    all grouped pipes (incl. twin-label extra runs) are painted, legend matches groups."""
    from .systemize import circuitize, run_color_map
    runs = result.get("runs", []) if result else []
    systems = circuitize(result) if result else []
    flags = []

    # systemize-level color map (what render uses)
    cmap = run_color_map(systems)
    grouped_runs = set()
    for s in systems:
        for ri in s["run_idxs"]:
            grouped_runs.add(ri)
    bad_color = 0
    for s in systems:
        for ri in s["run_idxs"]:
            if tuple(cmap.get(ri, ())) != tuple(s["color"]):
                bad_color += 1
                flags.append({"kind": "color_mismatch",
                              "msg_en": f"Pipe #{ri} painted differently from its system "
                                        f"({s['fluid']})."})
    # all runs referenced exist
    ghost = [ri for ri in grouped_runs if not (0 <= ri < len(runs))]
    for ri in ghost:
        flags.append({"kind": "ghost_run", "msg_en": f"Group references pipe #{ri} that no longer exists."})
    # circuit colors unique within each system
    n_cclash = 0
    for s in systems:
        cols = [tuple(c["color"]) for c in s["circuits"]]
        n_cclash += len(cols) - len(set(cols))
    if n_cclash:
        flags.append({"kind": "circuit_color_clash",
                      "msg_en": f"{n_cclash} circuit color collision(s) within a system."})
    # legend consistency: unique circuit codes
    codes = [c["code"] for s in systems for c in s["circuits"]]
    n_code_dup = len(codes) - len(set(codes))
    if n_code_dup:
        flags.append({"kind": "legend_dup", "msg_en": f"{n_code_dup} duplicate circuit code(s) in legend."})

    checks = [
        {"id": "color_match", "name": "Pipe colors match their group",
         "value": round(_pct(len(grouped_runs) - bad_color, len(grouped_runs)), 1),
         "detail": f"{bad_color} mismatch", "passed": bad_color == 0},
        {"id": "refs", "name": "All painted pipes exist",
         "value": 100.0 if not ghost else 0.0, "detail": f"{len(ghost)} ghost", "passed": not ghost},
        {"id": "circuit_colors", "name": "Distinct circuit colors in a system",
         "value": 100.0 if n_cclash == 0 else 0.0, "detail": f"{n_cclash} clash", "passed": n_cclash == 0},
        {"id": "legend", "name": "Legend codes unique",
         "value": 100.0 if n_code_dup == 0 else 0.0, "detail": f"{n_code_dup} dup", "passed": n_code_dup == 0},
    ]

    # COVERAGE: how much of the traced piping actually carries a marking colour. A line
    # number is written once but its route splits into many runs, so coverage — not the
    # per-line grouping — is what decides whether the marked-up drawing looks complete.
    # Measured by pipe LENGTH so long mains count more than short stubs.
    def _plen(r):
        p = r.get("points") or [[r["x1"], r["y1"]], [r["x2"], r["y2"]]]
        return sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in zip(p, p[1:]))
    pipe_idx = [i for i, r in enumerate(runs) if not r.get("underline")]
    tot_len = sum(_plen(runs[i]) for i in pipe_idx)
    pai_len = sum(_plen(runs[i]) for i in pipe_idx if i in grouped_runs)
    if tot_len > 0:
        checks.append(
            {"id": "coverage", "name": "Traced piping covered by a marking",
             "value": round(_pct(pai_len, tot_len), 1),
             "detail": f"{len(grouped_runs & set(pipe_idx))}/{len(pipe_idx)} pipes by count",
             "passed": pai_len / tot_len >= 0.75})

    score = round(sum(c["value"] for c in checks) / len(checks), 1)
    return {"title": "MARKING (CORROSION SYSTEM & CIRCUIT)",
            "header": f"{len(grouped_runs)} pipes painted across {len(systems)} systems"
                      + (f", {_pct(pai_len, tot_len):.0f}% of pipe length" if tot_len else ""),
            "checks": checks, "score": score, "n_critical": len(flags),
            "n_warnings": 0, "passed": not flags, "flags": flags, "warnings": []}


def format_report(report) -> str:
    """Human-readable English report (for CLI / thesis evidence / demo)."""
    L = []
    L.append("=" * 62)
    L.append(f"  AUTOMATED VALIDATION — {report.get('title', 'DIGITIZATION')}")
    L.append("=" * 62)
    if "header" in report:
        L.append(f"  {report['header']}")
    else:
        L.append(f"  Detected: {report['n_symbols']} symbols, "
                 f"{report['n_piping_ids']} piping IDs, {report['n_pipes']} pipes")
    L.append(f"  Quality score: {report['score']} / 100    "
             f"Verdict: {'PASS' if report['passed'] else 'REVIEW NEEDED'}")
    L.append("-" * 62)
    for c in report["checks"]:
        mark = "OK " if c["passed"] else "!! "
        L.append(f"  [{mark}] {c['name']:<42} {c['value']:>5}%  ({c['detail']})")
    L.append("-" * 62)
    if report["flags"]:
        L.append(f"  {report['n_critical']} critical item(s) for engineer confirmation:")
        for f in report["flags"][:12]:
            L.append(f"    - [{f['kind']}] {f.get('msg_en') or f['msg']}")
        if len(report["flags"]) > 12:
            L.append(f"    ... and {len(report['flags']) - 12} more")
    else:
        L.append("  No critical anomalies — output internally consistent.")
    if report["n_warnings"]:
        L.append(f"  Warnings: {report['n_warnings']} non-blocking item(s) "
                 "(e.g. long pipes without a label — optional review).")
    L.append("=" * 62)
    return "\n".join(L)
