"""Fonts of the words printed in a digital PDF page (F-02 tampered amounts, F-04 odd report layout).

A digital PDF records which font draws every character. A number typed over a bill with an editor almost
always comes out in a different font from the hospital system's own printout, which no OCR or image model
can tell as reliably. Scanned pages have no fonts: these checks then report that they could not check.
"""

import ctypes
import re
from collections import Counter
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


@dataclass
class PdfWord:
    text: str
    font: str                         # full font name, e.g. Helvetica-Bold
    bbox: list[float]                 # [x0, y0, x1, y1] as fractions of the page, y from the top

    @property
    def family(self) -> str:
        return family(self.font)


def family(font: str) -> str:
    """'ABCDEF+Arial-BoldMT' -> 'arial'; 'Helvetica-Bold' -> 'helvetica'; 'Times-Roman' -> 'times'."""
    name = SUBSET_PREFIX.sub("", font or "")
    name = re.split(r"[-,]", name)[0]
    name = re.sub(r"(MT|PS|Std)$", "", name)
    return name.strip().lower()


def page_words(pdf: pdfium.PdfDocument, index: int) -> list[PdfWord]:
    """Words of one page with their font; empty when the page has no text layer."""
    page = pdf[index]
    try:
        width, height = page.get_size()
        textpage = page.get_textpage()
        count = textpage.count_chars()
        if count <= 0:
            return []
        text = textpage.get_text_bounded()
        buffer = ctypes.create_string_buffer(256)
        flags = ctypes.c_int()
        words: list[PdfWord] = []
        current: list[tuple[str, str, tuple[float, float, float, float]]] = []

        def flush() -> None:
            if not current:
                return
            fonts = Counter(f for _, f, _ in current)
            boxes = [b for _, _, b in current]
            x0, x1 = min(b[0] for b in boxes), max(b[2] for b in boxes)
            bottom, top = min(b[1] for b in boxes), max(b[3] for b in boxes)
            words.append(PdfWord("".join(c for c, _, _ in current), fonts.most_common(1)[0][0],
                                 [round(x0 / width, 4), round(1 - top / height, 4), round(x1 / width, 4),
                                  round(1 - bottom / height, 4)]))
            current.clear()

        for i in range(min(count, len(text))):
            char = text[i]
            if char.isspace():
                flush()
                continue
            pdfium_c.FPDFText_GetFontInfo(textpage.raw, i, buffer, 256, ctypes.byref(flags))
            font = buffer.value.decode(errors="ignore")
            if current and current[-1][1] != font and family(current[-1][1]) != family(font):
                flush()
            current.append((char, font, textpage.get_charbox(i)))
        flush()
        return words
    finally:
        page.close()


def dominant_family(words: list[PdfWord]) -> str | None:
    counts = Counter(w.family for w in words for _ in w.text)    # weighted by characters
    return counts.most_common(1)[0][0] if counts else None


def header_family(words: list[PdfWord], lines: int = 3) -> str | None:
    """Font family of the first lines on the page (the letterhead): what identifies who printed it."""
    tops: list[float] = []
    for y in sorted(w.bbox[1] for w in words):
        if not tops or y - tops[-1] > 0.008:
            tops.append(y)
        if len(tops) > lines:
            break
    limit = tops[lines] if len(tops) > lines else 1.0
    return dominant_family([w for w in words if w.bbox[1] < limit - 0.004])
