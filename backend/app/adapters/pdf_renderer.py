from typing import Protocol, runtime_checkable
from importlib import import_module
import os
import cv2
import numpy as np


@runtime_checkable
class PDFRenderer(Protocol):
    """Protocol for PDF rasterization adapters."""
    def render_page(self, path: str, page_number: int = 0, dpi: int = 350) -> np.ndarray:
        """Render a PDF page into BGR numpy array format."""
        ...


class PyPdfiumRenderer:
    """PDF renderer using pypdfium2 (permissive Apache-2.0, thread-safe)."""

    def render_page(self, path: str, page_number: int = 0, dpi: int = 350) -> np.ndarray:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(path)
        if page_number < 0 or page_number >= len(pdf):
            raise ValueError(f"Page number {page_number} out of range (total pages: {len(pdf)})")
        page = pdf[page_number]
        # 72 points per inch in PDF spec
        scale = dpi / 72.0
        pil_img = page.render(scale=scale).to_pil()
        rgb = np.array(pil_img)
        # Convert RGB to BGR for OpenCV compatibility
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


class PyMuPDFRenderer:
    """PDF renderer fallback using PyMuPDF (fitz)."""

    def render_page(self, path: str, page_number: int = 0, dpi: int = 350) -> np.ndarray:
        import fitz
        doc = fitz.open(path)
        if page_number < 0 or page_number >= len(doc):
            raise ValueError(f"Page number {page_number} out of range (total pages: {len(doc)})")
        pg = doc[page_number]
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = pg.get_pixmap(matrix=mat, alpha=False)
        rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        doc.close()
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def get_default_pdf_renderer() -> PDFRenderer:
    """Factory to get the best available PDF renderer with graceful fallback."""
    try:
        import_module("pypdfium2")
        return PyPdfiumRenderer()
    except ImportError:
        try:
            import_module("fitz")
            return PyMuPDFRenderer()
        except ImportError:
            raise RuntimeError("No PDF renderer found. Please install either pypdfium2 or PyMuPDF.")


def read_image_safe(path: str) -> np.ndarray:
    """Read an image file with Windows Unicode / non-ASCII path support."""
    try:
        # np.fromfile handles Unicode paths on Windows safely
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is not None:
            return img
    except Exception:
        pass
    return cv2.imread(path)


def load_drawing_image(path: str, dpi: int = 350, page_number: int = 0) -> np.ndarray:
    """Universal drawing loader for PDF or raster images (PNG, JPG, TIFF)."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Drawing file not found: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        renderer = get_default_pdf_renderer()
        return renderer.render_page(path, page_number=page_number, dpi=dpi)
    else:
        img = read_image_safe(path)
        if img is None:
            raise ValueError(f"Failed to decode image from: {path}")
        return img
