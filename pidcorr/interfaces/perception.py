from abc import ABC, abstractmethod
from typing import Callable, Dict, Any, List, Optional, Tuple
import numpy as np


class BaseSymbolDetector(ABC):
    """Abstract Base Class for P&ID engineering symbol detection (Equipment, Valves, Instruments)."""

    @abstractmethod
    def detect(
        self,
        img_bgr: np.ndarray,
        conf: float = 0.3,
        progress: Optional[Callable[[str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Detect symbols on the P&ID drawing image.

        Returns a list of dicts with keys:
          coarse: 'equipment' | 'instrument' | 'valve'
          cls: str
          conf: float
          x1, y1, x2, y2: float
          subtype?: str
        """
        ...

    @abstractmethod
    def load_weights(self, weights_path: str) -> None:
        """Load or reload model weights."""
        ...


class BaseTextExtractor(ABC):
    """Abstract Base Class for OCR text detection and Piping ID extraction."""

    @abstractmethod
    def extract(
        self,
        img_bgr: np.ndarray,
        tile: int = 1200,
        progress: Optional[Callable[[str], None]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extract candidate piping IDs and raw short tokens from image.

        Returns:
          (piping_ids, raw_tokens)
        """
        ...


class BaseLineTracer(ABC):
    """Abstract Base Class for P&ID pipe line tracing and polyline reconstruction."""

    @abstractmethod
    def trace(
        self,
        img_bgr: np.ndarray,
        dpi: int = 350,
        detections: Optional[List[Dict[str, Any]]] = None,
        furniture: Optional[List[List[int]]] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Trace connected pipe lines and return polylines.

        Returns a list of dicts with keys:
          points: [[x, y], ...]
          axis: 'h' | 'v' | 'd' | 'poly'
          x1, y1, x2, y2: int
          underline: bool
        """
        ...


class BasePipingIDParser(ABC):
    """Abstract Base Class for parsing raw piping ID strings into structured engineering attributes."""

    @abstractmethod
    def parse(self, pid_text: str) -> Optional[Dict[str, str]]:
        """Parse raw text into {unit, size, fluid, pclass, seq}. Returns None if no schema matched."""
        ...

    @abstractmethod
    def register_schema(self, example_pid: str, schema: Dict[str, Any]) -> None:
        """Learn and register a new company-specific schema from user example."""
        ...


class BaseSubtypeClassifier(ABC):
    """Abstract Base Class for refining coarse detections into specific engineering subtypes."""

    @abstractmethod
    def classify_valve(self, img_bgr: np.ndarray, sym: Dict[str, Any]) -> str:
        """Classify a valve detection into Ball, Gate, Globe, Check, Control, or Relief."""
        ...

    @abstractmethod
    def classify_instrument(self, img_bgr: np.ndarray, sym: Dict[str, Any]) -> Tuple[str, str]:
        """Read ISA-5.1 variable and loop number from instrument bubble."""
        ...
