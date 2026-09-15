import os
import sys
from typing import List, Dict, Any

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pidcorr.systemize import systemize, circuitize, recolor_fluid, fluid_color
from pidcorr.validate import validate_digitization, validate_grouping, validate_marking


class GroupingService:
    @staticmethod
    def compute_systems(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Compute API RP 970 Corrosion Systems by process fluid."""
        return systemize(result)

    @staticmethod
    def compute_circuits(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Compute API RP 970 Corrosion Circuits by fluid and material."""
        return circuitize(result)

    @staticmethod
    def recolor(fluid: str):
        return recolor_fluid(fluid)

    @staticmethod
    def validate(result: Dict[str, Any]) -> Dict[str, Any]:
        """Perform full automated runtime validation (checks & confidence metrics)."""
        dig_val = validate_digitization(result)
        grp_val = validate_grouping(result)
        mrk_val = validate_marking(result)

        # Merge validation results
        all_checks = dig_val["checks"] + grp_val["checks"] + mrk_val["checks"]
        all_flags = dig_val["flags"] + grp_val["flags"] + mrk_val["flags"]
        all_warnings = dig_val.get("warnings", [])

        overall_score = round(
            sum(c["value"] for c in all_checks) / len(all_checks) if all_checks else 100.0, 1
        )
        passed = (dig_val["passed"] and grp_val["passed"] and mrk_val["passed"])

        return {
            "title": "AUTOMATED VALIDATION (API RP 970)",
            "header": f"{grp_val.get('header', '')} | Quality Score: {overall_score}/100",
            "n_symbols": dig_val["n_symbols"],
            "n_piping_ids": dig_val["n_piping_ids"],
            "n_pipes": dig_val["n_pipes"],
            "score": overall_score,
            "passed": passed,
            "n_critical": len(all_flags),
            "n_warnings": len(all_warnings),
            "checks": all_checks,
            "flags": all_flags,
            "warnings": all_warnings,
        }
