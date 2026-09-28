from pidcorr.detect import predict_tiled


class _FakeBoxes:
    def __iter__(self):
        return iter(())


class _FakeResult:
    boxes = _FakeBoxes()


class _FakeModel:
    names = {0: "valve"}

    def predict(self, *args, **kwargs):
        return [_FakeResult()]


def test_predict_tiled_reports_each_tile():
    progress = []
    detections, tiles = predict_tiled(
        _FakeModel(), np.zeros((700, 700, 3), dtype=np.uint8),
        tile=640, overlap=0.2, progress=progress.append,
    )
    assert detections == []
    assert tiles == 4
    assert progress == ["YOLO tile 1/4", "YOLO tile 2/4", "YOLO tile 3/4", "YOLO tile 4/4"]


import os
import sys
import numpy as np

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from pidcorr.interfaces.perception import (
    BaseSymbolDetector,
    BaseTextExtractor,
    BaseLineTracer,
    BasePipingIDParser,
    BaseSubtypeClassifier,
)
from pidcorr.implementations.yolo_detector import YOLOTiledDetector
from pidcorr.implementations.sahi_detector import SAHISymbolDetector
from pidcorr.implementations.rapidocr_extractor import RapidOCRExtractor
from pidcorr.implementations.paddleocr_extractor import PaddleOCRExtractor
from pidcorr.implementations.morphology_tracer import MorphologyLineTracer
from pidcorr.implementations.skeleton_tracer import SkeletonLineTracer
from pidcorr.implementations.regex_parser import RegexPipingIDParser
from pidcorr.implementations.yolo_classifier import YOLOValveClassifier
from pidcorr.orchestrator import PipelineOrchestrator
from pidcorr.factory import get_configured_orchestrator


def test_interface_inheritance():
    """Verify all concrete adapters inherit and implement ABC interfaces."""
    yolo = YOLOTiledDetector()
    assert isinstance(yolo, BaseSymbolDetector)

    sahi = SAHISymbolDetector()
    assert isinstance(sahi, BaseSymbolDetector)

    ocr = RapidOCRExtractor()
    assert isinstance(ocr, BaseTextExtractor)

    paddle_ocr = PaddleOCRExtractor()
    assert isinstance(paddle_ocr, BaseTextExtractor)

    morph = MorphologyLineTracer()
    assert isinstance(morph, BaseLineTracer)

    skel = SkeletonLineTracer()
    assert isinstance(skel, BaseLineTracer)

    parser = RegexPipingIDParser()
    assert isinstance(parser, BasePipingIDParser)

    clf = YOLOValveClassifier()
    assert isinstance(clf, BaseSubtypeClassifier)

    print("[PASS] All adapters satisfy Base perception interfaces!")


def test_piping_id_parser_regex():
    """Verify regex parser on PetroChina and Pertamina line numbers."""
    parser = RegexPipingIDParser()

    # 1. PetroChina pattern: unit-size"-fluid-pclass-seq
    res1 = parser.parse('695-6"-GF-CCB-026')
    assert res1 is not None
    assert res1["unit"] == "695"
    assert res1["size"] in ('6', '6"')
    assert res1["fluid"] == "GF"
    assert res1["pclass"] == "CCB"
    assert res1["seq"] == "026"

    # 2. PetroChina with suffix
    res2 = parser.parse('605-2"-LO-APA-022-P25')
    assert res2 is not None
    assert res2["unit"] == "605"
    assert res2["fluid"] == "LO"
    assert res2["pclass"] == "APA"

    print("[PASS] RegexPipingIDParser correctly parsed engineering line tags!")


def test_mock_orchestrator_dependency_injection():
    """Verify PipelineOrchestrator with mock adapters for fast deterministic unit testing."""
    class MockDetector(BaseSymbolDetector):
        def detect(self, img_bgr, conf=0.3, progress=None):
            return [{"coarse": "equipment", "cls": "vessel", "conf": 0.99, "x1": 50, "y1": 50, "x2": 250, "y2": 250}]
        def load_weights(self, path): pass

    from pidcorr.piping_id import PipingID
    from pidcorr.lines import PipeRun

    class MockExtractor(BaseTextExtractor):
        def extract(self, img_bgr, tile=1200, progress=None):
            pids = [PipingID(pid='605-4"-GF-CCB-101', x1=60, y1=380, x2=200, y2=400, unit="605", size='4"', fluid="GF", pclass="CCB", seq="101", conf=3, orient=0, manual=False)]
            tokens = [{"t": "CCB", "x1": 100, "y1": 500, "x2": 150, "y2": 520, "cx": 125, "cy": 510, "ang": 0}]
            return pids, tokens

    class MockTracer(BaseLineTracer):
        def trace(self, img_bgr, dpi=350, detections=None, furniture=None, progress=None):
            return [PipeRun(points=[(50, 400), (500, 400)], axis="h", underline=False)]

    orchestrator = PipelineOrchestrator(
        detector=MockDetector(),
        extractor=MockExtractor(),
        tracer=MockTracer(),
    )

    dummy_img = np.ones((600, 800, 3), dtype=np.uint8) * 255
    res = orchestrator.run(dummy_img, image_path="test_mock.png", dpi=100)

    assert res["w"] == 800
    assert res["h"] == 600
    assert len(res["symbols"]) == 1
    assert len(res["runs"]) >= 1
    assert len(res["piping_ids"]) == 1

    print("[PASS] PipelineOrchestrator DI execution verified successfully!")


def test_paddleocr_extractor_single_pass():
    """Verify PaddleOCRExtractor runs single-pass extraction without crashing and conforms to return contract."""
    import cv2
    extractor = PaddleOCRExtractor(tile=512, overlap=0.15)

    # Create synthetic drawing canvas with crisp engineering text
    img = np.ones((600, 800, 3), dtype=np.uint8) * 255
    cv2.putText(img, '695-6"-GF-CCB-026', (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    cv2.putText(img, 'CCB', (400, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)

    pids, tokens = extractor.extract(img, tile=512)
    assert isinstance(pids, list)
    assert isinstance(tokens, list)
    print(f"[PASS] PaddleOCRExtractor single-pass test passed ({len(pids)} PIDs, {len(tokens)} tokens)!")


def test_skeleton_tracer_extracts_graph_edges():
    """Verify B.08 extracts graph edges for a line, T-junction, and diagonal.

    Sejak `bridge_polyline_elbows` aktif, cabang yang MENEMPEL pada ujung run lain
    digabung menjadi satu polyline (pipa bercabang = satu jaringan, supaya klik-ID
    menyala sepanjang pipa). Karena itu jumlah run tidak lagi sama dengan jumlah
    goresan di gambar; yang diperiksa adalah ARAH yang terwakili.
    """
    import cv2

    img = np.full((240, 320, 3), 255, dtype=np.uint8)
    cv2.line(img, (30, 120), (290, 120), (0, 0, 0), 5)      # horizontal utama
    cv2.line(img, (160, 120), (160, 45), (0, 0, 0), 5)      # cabang vertikal (T)
    cv2.line(img, (35, 205), (125, 150), (0, 0, 0), 5)      # diagonal

    runs = SkeletonLineTracer(min_length_px=25).trace(img, dpi=350)

    # Goresan boleh tergabung menjadi polyline; yang penting SEMUA arah terwakili.
    axes = set()
    for run in runs:
        axes.add(run["axis"])
        pts = run["points"]
        for a, b in zip(pts, pts[1:]):
            dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
            if dx > dy * 1.5:
                axes.add("h")
            elif dy > dx * 1.5:
                axes.add("v")
            elif dx > 0 and dy > 0:
                axes.add("d")
    assert "h" in axes, f"horizontal run missing (axes={axes})"
    assert "v" in axes, f"vertical/T-branch missing (axes={axes})"
    assert "d" in axes, f"diagonal missing (axes={axes})"

    assert len(runs) >= 1
    for run in runs:
        assert len(run["points"]) >= 2
        assert run["x1"] <= run["x2"]
        assert run["y1"] <= run["y2"]
        assert run["underline"] is False

    print(f"[PASS] Skeleton graph tracer extracted {len(runs)} runs covering axes {sorted(axes)}!")


def test_crossover_and_t_junction_classification():
    """Verify B.09 accurately resolves 4-way crossover into 2 independent through-pipes and preserves T-junction."""
    import cv2
    tracer = SkeletonLineTracer(min_length_px=20)

    # Create canvas with:
    # 1. Horizontal main line: (30, 100) -> (270, 100)
    # 2. Vertical line crossing it at (150, 100): (150, 30) -> (150, 270)  <-- 4-way Crossover
    # 3. T-junction branch off the vertical line: (150, 200) -> (230, 200)  <-- 3-way T-junction
    img = np.full((300, 300, 3), 255, dtype=np.uint8)
    cv2.line(img, (30, 100), (270, 100), (0, 0, 0), 4)
    cv2.line(img, (150, 30), (150, 270), (0, 0, 0), 4)
    cv2.line(img, (150, 200), (230, 200), (0, 0, 0), 4)

    runs = tracer.trace(img, dpi=350)

    # We expect:
    # - At least 1 horizontal through-run spanning ~240px (from ~30 to ~270)
    # - At least 1 vertical through-run spanning ~240px (from ~30 to ~270)
    # - 1 branch run extending to the right at y=200
    h_spans = [r for r in runs if r["axis"] == "h" and (r["x2"] - r["x1"]) >= 200]
    v_spans = [r for r in runs if r["axis"] == "v" and (r["y2"] - r["y1"]) >= 200]
    branches = [r for r in runs if r["axis"] == "h" and (r["x2"] - r["x1"]) < 120]

    assert len(h_spans) >= 1, f"Expected horizontal through-run, got {len(h_spans)}"
    assert len(v_spans) >= 1, f"Expected vertical through-run, got {len(v_spans)}"
    assert len(branches) >= 1, f"Expected T-junction branch run, got {len(branches)}"

    # Crossover verification: The horizontal and vertical through-runs must be distinct objects!
    assert h_spans[0] is not v_spans[0]
    print(f"[PASS] B.09 Junction Classifier verified: 4-way crossover split into 2 through-pipes, T-branch preserved!")


def test_factory_config():
    """Verify get_configured_orchestrator picks up environment variables."""
    os.environ["DETECTOR_IMPL"] = "sahi"
    os.environ["TRACER_IMPL"] = "skeleton"
    os.environ["OCR_IMPL"] = "paddleocr"

    orch = get_configured_orchestrator()
    assert isinstance(orch.detector, SAHISymbolDetector)
    assert isinstance(orch.tracer, SkeletonLineTracer)
    assert isinstance(orch.extractor, PaddleOCRExtractor)

    # Reset
    os.environ["DETECTOR_IMPL"] = "yolo_tiled"
    os.environ["TRACER_IMPL"] = "morphology"
    os.environ["OCR_IMPL"] = "rapid_ocr"
    orch_default = get_configured_orchestrator()
    assert isinstance(orch_default.detector, YOLOTiledDetector)
    assert isinstance(orch_default.tracer, MorphologyLineTracer)
    assert isinstance(orch_default.extractor, RapidOCRExtractor)

    print("[PASS] Config-driven factory selection verified successfully!")


if __name__ == "__main__":
    test_interface_inheritance()
    test_piping_id_parser_regex()
    test_mock_orchestrator_dependency_injection()
    test_paddleocr_extractor_single_pass()
    test_skeleton_tracer_extracts_graph_edges()
    test_crossover_and_t_junction_classification()
    test_factory_config()
