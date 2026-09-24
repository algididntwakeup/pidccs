import os
from .orchestrator import PipelineOrchestrator
from .implementations.yolo_detector import YOLOTiledDetector
from .implementations.sahi_detector import SAHISymbolDetector
from .implementations.rapidocr_extractor import RapidOCRExtractor
from .implementations.paddleocr_extractor import PaddleOCRExtractor
from .implementations.morphology_tracer import MorphologyLineTracer
from .implementations.skeleton_tracer import SkeletonLineTracer
from .implementations.vector_tracer import VectorLineTracer
from .implementations.yolo_classifier import YOLOValveClassifier


def get_configured_orchestrator() -> PipelineOrchestrator:
    """Build a PipelineOrchestrator instance based on runtime environment configuration."""
    detector_type = os.environ.get("DETECTOR_IMPL", "yolo_tiled").lower()
    tracer_type = os.environ.get("TRACER_IMPL", "morphology").lower()
    ocr_type = os.environ.get("OCR_IMPL", "rapid_ocr").lower()

    # 1. Symbol Detector selection
    if detector_type == "sahi":
        detector = SAHISymbolDetector()
    else:
        detector = YOLOTiledDetector()

    # 2. Text Extractor selection
    if ocr_type in ("paddleocr", "paddle_ocr", "paddle"):
        extractor = PaddleOCRExtractor()
    else:
        extractor = RapidOCRExtractor()

    # 3. Line Tracer selection
    #    `hybrid` (default produksi): vector-first untuk PDF vektor, otomatis
    #    jatuh ke skeleton untuk scan raster. Orchestrator yang memilih jalurnya.
    #    `skeleton`/`morphology` memaksa tracer raster (dipakai untuk benchmark
    #    dan perbandingan A/B).
    if tracer_type in ("hybrid", "vector", "auto"):
        tracer = SkeletonLineTracer()
    elif tracer_type == "skeleton":
        tracer = SkeletonLineTracer()
    else:
        tracer = MorphologyLineTracer()

    # 4. Classifier selection
    classifier = YOLOValveClassifier()

    return PipelineOrchestrator(
        detector=detector,
        extractor=extractor,
        tracer=tracer,
        classifier=classifier,
    )
