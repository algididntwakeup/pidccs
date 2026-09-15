import os
import sys
import pytest
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
    test_factory_config()
