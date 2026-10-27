"""Make a 'scanned' copy of a clean claim PDF: rasterised, slightly rotated, noisy, blurred, JPEG-compressed.

Used to test OCR on poor scans (PRD target: >= 85% field accuracy on noisy files).
"""

import io
from random import Random

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageEnhance, ImageFilter

SCALE = 1.4  # ~100 dpi: the low end of what TPAs receive


def _degrade(page: Image.Image, rng: Random) -> Image.Image:
    img = page.convert("L")
    img = img.rotate(rng.uniform(-1.8, 1.8), resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    # sparse dark specks on a white page, like dust and toner noise
    speck = rng.uniform(1.2, 2.2)
    table = [255 - int(max(0, abs(v - 128) - 45) * speck) for v in range(256)]
    noise = Image.effect_noise(img.size, 32).point(table)
    img = ImageChops.multiply(img, noise)
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.9)))
    img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.75, 0.95))
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.92, 1.05))
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=rng.randint(40, 65))
    return Image.open(io.BytesIO(buffer.getvalue()))


def scan_pdf(pdf_bytes: bytes, rng: Random) -> bytes:
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        bitmaps = [doc[i].render(scale=SCALE) for i in range(len(doc))]  # type: ignore[arg-type]
        pages = [_degrade(b.to_pil(), rng) for b in bitmaps]
    finally:
        doc.close()
    out = io.BytesIO()
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=100)
    return out.getvalue()
