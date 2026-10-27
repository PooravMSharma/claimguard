"""Split each uploaded file into page images.

Every page becomes a grayscale PNG (for OCR and the viewer) with a perceptual
hash, so the same page reused in another file or claim can be found later
(forensic check F-03). Digital PDFs also keep their own text layer.
"""

import io
import logging
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.core.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class PageImage:
    number: int
    png: bytes
    width: int
    height: int
    dhash: str
    text: str | None


def dhash(img: Image.Image, size: int = 8) -> str:
    """Difference hash: 64 bits that stay the same through resizing, compression and small noise."""
    small = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = pixels[row * (size + 1) + col]
            right = pixels[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def _png(img: Image.Image) -> tuple[bytes, int, int]:
    gray = (ImageOps.exif_transpose(img) or img).convert("L")
    buffer = io.BytesIO()
    gray.save(buffer, "PNG", compress_level=3)  # ~9x faster than optimize=True, ~15% larger
    return buffer.getvalue(), gray.width, gray.height


def split_pdf(data: bytes) -> list[PageImage]:
    scale = get_settings().page_image_scale
    pages: list[PageImage] = []
    doc = pdfium.PdfDocument(data)
    try:
        for i in range(len(doc)):
            page = doc[i]
            bitmap = page.render(scale=scale)  # type: ignore[arg-type]
            img = bitmap.to_pil()
            text = page.get_textpage().get_text_bounded().strip()
            png, w, h = _png(img)
            pages.append(PageImage(i + 1, png, w, h, dhash(img), text or None))
            page.close()
    finally:
        doc.close()
    return pages


def split_image(data: bytes) -> list[PageImage]:
    pages: list[PageImage] = []
    with Image.open(io.BytesIO(data)) as img:
        for i in range(getattr(img, "n_frames", 1)):
            img.seek(i)
            frame = img.copy()
            png, w, h = _png(frame)
            pages.append(PageImage(i + 1, png, w, h, dhash(frame), None))
    return pages


def split(data: bytes, content_type: str) -> list[PageImage]:
    if content_type == "application/pdf":
        return split_pdf(data)
    return split_image(data)
