"""Read the text on a page, with the position of every word.

Digital PDF pages already carry their text: it is taken directly (exact, with
word boxes). Scanned or photographed pages go through Tesseract OCR. Every word
keeps its box (as fractions of the page) so later steps can point the reviewer
at the exact spot a value came from.
"""

import io
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageOps, ImageStat

from app.core.config import get_settings

log = logging.getLogger(__name__)

LATIN_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,/:()%-]*")
MIN_TEXT_LAYER_CHARS = 40     # fewer characters than this: treat the page as a scan
TESSERACT_CONFIG = "--psm 6"  # one uniform block: keeps table rows together (tested on our bills)


@dataclass
class Word:
    text: str
    conf: float              # 0-1
    x0: float
    y0: float
    x1: float
    y1: float
    line: int                # line number on the page, for rebuilding text

    def as_list(self) -> list:
        """[text, confidence, x0, y0, x1, y1, line]: boxes as page fractions; line = reading line on the page."""
        return [self.text, round(self.conf, 3), round(self.x0, 4), round(self.y0, 4), round(self.x1, 4),
                round(self.y1, 4), self.line]


@dataclass
class PageText:
    source: str              # text_layer | ocr | none
    text: str
    words: list[Word] = field(default_factory=list)
    confidence: float | None = None
    rotation: int = 0
    ink: float = 0.0         # share of dark pixels (0 = blank page)

    @property
    def word_count(self) -> int:
        return len(self.words)


# ---------------------------------------------------------------- text layer (digital PDFs)

def text_layer_words(page: pdfium.PdfPage) -> list[Word]:
    """Words from a digital page. pdfium puts line breaks (\r\n) between lines; we split on them and spaces."""
    width, height = page.get_size()
    textpage = page.get_textpage()
    words: list[Word] = []
    current: list[tuple[str, tuple[float, float, float, float]]] = []
    line = 0

    def flush() -> None:
        if not current:
            return
        text = "".join(c for c, _ in current)
        left = min(b[0] for _, b in current)
        bottom = min(b[1] for _, b in current)
        right = max(b[2] for _, b in current)
        top = max(b[3] for _, b in current)
        words.append(Word(text, 1.0, left / width, 1 - top / height, right / width, 1 - bottom / height, line))
        current.clear()

    count = textpage.count_chars()
    text = textpage.get_text_range(0, count) if count else ""
    for i, ch in enumerate(text[:count]):
        if ch in ("\r", "\n"):
            flush()
            if ch == "\n":
                line += 1
            continue
        if ch.isspace():
            flush()
            continue
        current.append((ch, textpage.get_charbox(i)))
    flush()
    textpage.close()
    return words


def words_to_text(words: list[Word]) -> str:
    lines: dict[int, list[Word]] = {}
    for w in words:
        lines.setdefault(w.line, []).append(w)
    return "\n".join(" ".join(w.text for w in sorted(ws, key=lambda w: w.x0)) for _, ws in sorted(lines.items()))


# ---------------------------------------------------------------- OCR (scans and photos)

@lru_cache
def languages() -> str:
    wanted = [lang for lang in get_settings().ocr_languages.split("+") if lang]
    try:
        have = set(pytesseract.get_languages(config=""))
    except Exception:
        have = {"eng"}
    use = [lang for lang in wanted if lang in have] or ["eng"]
    if len(use) < len(wanted):
        log.warning("OCR languages %s not installed; using %s", set(wanted) - set(use), "+".join(use))
    return "+".join(use)


def ink_share(img: Image.Image) -> float:
    """Share of the page covered by print. Text pages measure 0.007-0.04; a blank page is below 0.002."""
    gray = img.convert("L")
    small = gray.resize((600, max(1, int(600 * gray.height / max(gray.width, 1)))))
    dark = sum(small.histogram()[:170])
    return dark / max(small.width * small.height, 1)


def prepare(img: Image.Image) -> Image.Image:
    gray = (ImageOps.exif_transpose(img) or img).convert("L")
    min_width = get_settings().ocr_min_width
    if gray.width < min_width:
        factor = min_width / gray.width
        gray = gray.resize((min_width, int(gray.height * factor)), Image.Resampling.LANCZOS)
    if ImageStat.Stat(gray).mean[0] < 110:  # mostly dark: probably a negative or very dark photo
        gray = ImageOps.autocontrast(gray, cutoff=1)
    return gray


def tesseract_words(img: Image.Image, lang: str | None = None) -> list[Word]:
    data = pytesseract.image_to_data(img, lang=lang or languages(), config=TESSERACT_CONFIG,
                                     output_type=pytesseract.Output.DICT)
    w, h = img.size
    words = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        conf = float(data["conf"][i])
        if not text or conf < 0:
            continue
        line = data["block_num"][i] * 10000 + data["par_num"][i] * 100 + data["line_num"][i]
        x, y, bw, bh = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append(Word(text, conf / 100, x / w, y / h, (x + bw) / w, (y + bh) / h, line))
    return words


def mean_conf(words: list[Word]) -> float | None:
    real = [w.conf for w in words if re.search(r"[A-Za-z0-9]", w.text)]
    return sum(real) / len(real) if real else None


def detect_rotation(img: Image.Image) -> int:
    """Degrees the page is turned (0/90/180/270), judged by which orientation reads best on a small copy.

    Tesseract's own orientation detector gives up on form-like pages ("too few characters"), so we test.
    English only, counting only real Latin words: with Hindi enabled, sideways print gets "read" as
    Devanagari with fair confidence, which would make a wrong orientation look good.
    """
    small = img.resize((1000, max(1, int(img.height * 1000 / img.width))), Image.Resampling.LANCZOS)
    best_turn, best_score = 0, -1.0
    for turn in (0, 90, 180, 270):
        candidate = small.rotate(-turn, expand=True) if turn else small
        words = [w for w in tesseract_words(candidate, lang="eng") if LATIN_WORD.fullmatch(w.text)]
        conf = mean_conf(words) or 0.0
        score = conf * min(len(words), 60)
        if score > best_score:
            best_turn, best_score = turn, score
    return best_turn


def ocr_image(img: Image.Image) -> PageText:
    ink = ink_share(img)
    if ink < 0.002:
        return PageText("none", "", [], None, 0, ink)
    prepared = prepare(img)
    words = tesseract_words(prepared)
    conf = mean_conf(words)
    rotation = 0
    if conf is None or conf < 0.6 or len(words) < 15:  # poor result: the page may be sideways
        turn = detect_rotation(img.convert("L"))
        if turn:
            turned = prepared.rotate(-turn, expand=True)
            turned_words = tesseract_words(turned)
            turned_conf = mean_conf(turned_words)
            if turned_conf is not None and (conf is None or turned_conf > conf):
                words, conf, rotation = turned_words, turned_conf, turn
    return PageText("ocr" if words else "none", words_to_text(words), words, conf, rotation, ink)


# ---------------------------------------------------------------- one page of an uploaded file

@dataclass
class OcrJob:
    """A page that needs OCR, plus its short text layer (if any) to fall back on."""
    image: Image.Image
    fallback: PageText | None = None


def pdf_page_job(doc: pdfium.PdfDocument, index: int) -> PageText | OcrJob:
    """Text-layer pages are read here; other pages come back as an OcrJob.

    pdfium is not thread-safe, so this runs on one thread; only Tesseract runs in parallel.
    """
    page = doc[index]
    try:
        words = text_layer_words(page)
        text = words_to_text(words)
        layer = PageText("text_layer", text, words, 1.0, 0, 0.05)
        chars = len(re.sub(r"\s", "", text))
        if chars >= MIN_TEXT_LAYER_CHARS:
            return layer
        image = page.render(scale=get_settings().ocr_render_scale).to_pil()  # type: ignore[arg-type]
        return OcrJob(image, layer if chars else None)
    finally:
        page.close()


def run_job(job: OcrJob) -> PageText:
    """OCR the page; keep a short text layer instead if OCR found less (e.g. one small line of text)."""
    result = ocr_image(job.image)
    if job.fallback is None:
        return result
    found = len(re.sub(r"\W", "", result.text))
    if found < len(re.sub(r"\W", "", job.fallback.text)):
        return job.fallback
    return result


def read_pdf_page(doc: pdfium.PdfDocument, index: int) -> PageText:
    job = pdf_page_job(doc, index)
    return job if isinstance(job, PageText) else run_job(job)


def image_frames(data: bytes) -> list[Image.Image]:
    with Image.open(io.BytesIO(data)) as img:
        frames = []
        for i in range(getattr(img, "n_frames", 1)):
            img.seek(i)
            frames.append(img.copy())
        return frames


def quality(page: PageText) -> tuple[str, float]:
    """(flag, score 0-1). Pages flagged poor/unreadable need a person (or the AI reader) to confirm values."""
    if page.source == "text_layer":
        return "good", 1.0
    if page.source == "none":
        return ("blank", 0.0) if page.ink < 0.002 else ("unreadable", 0.0)
    conf = page.confidence or 0.0
    if page.word_count < 8 or conf < 0.40:
        return "unreadable", conf
    if conf < 0.60:
        return "poor", conf
    if conf < 0.78:
        return "fair", conf
    return "good", conf
