import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pidcorr.lines import PipeRun, stitch_region_runs


def _runs(points, **kw):
    return {"id": kw.pop("id", "run-0"), "points": [list(p) for p in points], **kw}


# ---------------------------------------------------------------- SKENARIO A ------
def test_scenario_a_extends_single_existing_run():
    """New path touching exactly one existing endpoint extends it; no new run is created."""
    existing = [_runs([[0, 50], [100, 50]], id="run-0")]
    new = [_runs([[100, 50], [200, 50]], id="rescan-1")]

    upd, remaining, consumed = stitch_region_runs(
        existing, new, bounds_roi=(80, 40, 220, 60), snap_radius=18.0,
    )

    assert remaining == []
    assert consumed == ["rescan-1"]
    assert len(upd) == 1
    assert upd[0]["points"] == [[0, 50], [100, 50], [200, 50]]
    assert upd[0]["x2"] == 200


def test_scenario_a_extends_when_new_touches_start_end():
    """Extending the 'a' (start) end must prepend, not append, the new geometry."""
    existing = [_runs([[100, 50], [200, 50]], id="run-0")]
    new = [_runs([[0, 50], [100, 50]], id="rescan-1")]

    upd, remaining, consumed = stitch_region_runs(
        existing, new, bounds_roi=(0, 40, 120, 60), snap_radius=18.0,
    )

    assert remaining == []
    assert len(upd) == 1
    assert upd[0]["points"] == [[0, 50], [100, 50], [200, 50]]
    assert upd[0]["x1"] == 0


def test_scenario_a_propagates_label_from_new_run():
    """A detected OCR label on the new path is inherited by the extended pipe."""
    existing = [_runs([[0, 50], [100, 50]], id="run-0", label="", pid="", fluid="")]
    new = [_runs([[100, 50], [200, 50]], id="rescan-1",
                 label='605-4"-LO-APA-010', pid='605-4"-LO-APA-010', fluid="LO")]

    upd, _, consumed = stitch_region_runs(
        existing, new, bounds_roi=(80, 40, 220, 60), snap_radius=18.0,
    )

    assert "rescan-1" in consumed
    assert upd[0]["label"] == '605-4"-LO-APA-010'
    assert upd[0]["pid"] == '605-4"-LO-APA-010'
    assert upd[0]["fluid"] == "LO"


def test_scenario_a_does_not_overwrite_existing_label():
    """An already-labelled pipe keeps its own label after being extended."""
    existing = [_runs([[0, 50], [100, 50]], id="run-0", label="OLD-TAG")]
    new = [_runs([[100, 50], [200, 50]], id="rescan-1", label="NEW-TAG")]

    upd, _, _ = stitch_region_runs(existing, new, bounds_roi=(80, 40, 220, 60))

    assert upd[0]["label"] == "OLD-TAG"


# ---------------------------------------------------------------- SKENARIO B ------
def test_scenario_b_bridges_two_severed_pipes():
    """A new path joining two severed ends merges M + New + F into one run, dropping F."""
    m = _runs([[0, 50], [100, 50]], id="run-m")
    f = _runs([[200, 50], [300, 50]], id="run-f")
    new = [_runs([[100, 50], [200, 50]], id="rescan-1")]

    upd, remaining, consumed = stitch_region_runs(
        [m, f], new, bounds_roi=(90, 40, 210, 60), snap_radius=18.0,
    )

    assert remaining == []
    assert "rescan-1" in consumed
    assert "run-f" in consumed
    # F was absorbed -> only one run left, spanning M + bridge + F.
    assert len(upd) == 1
    assert upd[0]["id"] == "run-m"
    assert upd[0]["points"] == [[0, 50], [100, 50], [200, 50], [300, 50]]


def test_scenario_b_bridges_reversed_new_path():
    """The bridge must work regardless of the new path's direction."""
    m = _runs([[0, 50], [100, 50]], id="run-m")
    f = _runs([[200, 50], [300, 50]], id="run-f")
    new = [_runs([[200, 50], [100, 50]], id="rescan-1")]

    upd, remaining, consumed = stitch_region_runs(
        [m, f], new, bounds_roi=(90, 40, 210, 60), snap_radius=18.0,
    )

    assert remaining == []
    assert len(upd) == 1
    assert upd[0]["points"] == [[0, 50], [100, 50], [200, 50], [300, 50]]


def test_scenario_b_inherits_label_from_absorbed_pipe():
    """When M has no label, the absorbed pipe F's label is carried over."""
    m = _runs([[0, 50], [100, 50]], id="run-m", label="")
    f = _runs([[200, 50], [300, 50]], id="run-f", label="F-TAG", pid="F-TAG")
    new = [_runs([[100, 50], [200, 50]], id="rescan-1")]

    upd, _, _ = stitch_region_runs(
        [m, f], new, bounds_roi=(90, 40, 210, 60), snap_radius=18.0,
    )

    assert upd[0]["label"] == "F-TAG"
    assert upd[0]["pid"] == "F-TAG"


# ---------------------------------------------------------------- SKENARIO C ------
def test_scenario_c_keeps_independent_run_when_far_from_pipes():
    """A path that touches no existing endpoint stays an independent new run."""
    existing = [_runs([[0, 500], [100, 500]], id="run-0")]
    new = [_runs([[1000, 1000], [1100, 1000]], id="rescan-1")]

    upd, remaining, consumed = stitch_region_runs(
        existing, new, bounds_roi=(980, 980, 1120, 1020), snap_radius=18.0,
    )

    assert consumed == []
    assert len(remaining) == 1
    assert remaining[0]["id"] == "rescan-1"
    assert remaining[0]["points"] == [[1000, 1000], [1100, 1000]]
    assert upd == existing


def test_ambiguous_branch_keeps_run_but_snaps_endpoints():
    """A path near >2 existing ends stays independent but its ends snap to nearest ends."""
    e1 = _runs([[0, 50], [100, 50]], id="run-1")
    e2 = _runs([[0, 55], [100, 55]], id="run-2")
    e3 = _runs([[0, 60], [100, 60]], id="run-3")
    # New path starts right at e1's end and ends at e2's end -> touches 3 candidate ends.
    new = [_runs([[100, 50], [100, 55]], id="rescan-1")]

    upd, remaining, consumed = stitch_region_runs(
        [e1, e2, e3], new, bounds_roi=(90, 40, 110, 70), snap_radius=18.0,
    )

    assert len(remaining) == 1
    pts = remaining[0]["points"]
    assert pts[0] == [100, 50]
    assert pts[-1] == [100, 55]


# ---------------------------------------------------------------- ROBUSTNESS ------
def test_empty_new_runs_returns_existing_untouched():
    existing = [_runs([[0, 50], [100, 50]], id="run-0")]
    upd, remaining, consumed = stitch_region_runs(existing, [], bounds_roi=(0, 0, 10, 10))
    assert upd == existing
    assert remaining == []
    assert consumed == []


def test_pipe_run_objects_are_accepted_as_existing():
    """Existing runs may be PipeRun dataclasses (as persisted in result_json flows)."""
    existing = [PipeRun(points=[(0, 50), (100, 50)], axis="h", id="run-0")]
    new = [_runs([[100, 50], [200, 50]], id="rescan-1")]

    upd, remaining, consumed = stitch_region_runs(
        existing, new, bounds_roi=(80, 40, 220, 60), snap_radius=18.0,
    )

    assert remaining == []
    assert consumed == ["rescan-1"]
    assert upd[0]["points"] == [[0, 50], [100, 50], [200, 50]]
