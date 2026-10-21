"""Step 10: F-02 (amount in a different font) and F-04 (lab report printed differently), on real small PDFs."""

import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.extraction.model import DocExtract
from app.rules.context import OriginalFile, Page
from app.rules.engine import run_checks
from app.rules.pdf_fonts import family, header_family, page_words
from tests.test_rules_checks import context, genuine_docs, v

AMOUNTS = ["350.00", "1,200.00", "4,400.00", "800.00", "12,250.00", "4,200.00", "5,250.00"]


def bill_pdf(odd_font: str | None = None) -> bytes:
    """One bill page: every amount in Helvetica, the fifth one optionally in another font."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 800, "Ganga Superspeciality Hospital - Final Bill")
    for n, amount in enumerate(AMOUNTS):
        c.setFont("Helvetica", 10)
        c.drawString(50, 760 - n * 20, f"Line {n + 1}")
        c.setFont(odd_font if odd_font and n == 4 else "Helvetica", 10)
        c.drawRightString(500, 760 - n * 20, amount)
    c.showPage()
    c.save()
    return buffer.getvalue()


def labs_pdf(fonts: list[str]) -> bytes:
    """One lab report per page, each with its letterhead in the given font."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    for font in fonts:
        c.setFont(font, 14)
        c.drawString(50, 800, "Ganga Superspeciality Hospital")
        c.setFont(font, 10)
        c.drawString(50, 785, "Department of Laboratory Medicine")
        c.drawString(50, 770, "NABL accredited")
        c.setFont("Helvetica", 10)
        c.drawString(50, 700, "Haemoglobin 13.5 g/dL 12.0 - 16.0")
        c.showPage()
    c.save()
    return buffer.getvalue()


def ctx_with(docs, pdfs: dict[str, tuple[bytes, list[str]]]):
    """Context whose pages come from the given PDFs: {file id: (pdf bytes, [page ids])}."""
    ctx = context(docs)
    files, pages = [], []
    for file_id, (data, page_ids) in pdfs.items():
        files.append(OriginalFile(file_id, f"{file_id}.pdf", "application/pdf", lambda d=data: d))
        doc_type = {d.page_ids[0]: d.doc_type for d in docs}
        pages += [Page(pid, doc_type.get(pid, "other"), "text_layer", "good", None, file_id, n)
                  for n, pid in enumerate(page_ids, 1)]
    known = {p.id for p in pages}
    ctx.pages = pages + [p for p in ctx.pages if p.id not in known]
    ctx.files = files
    return ctx


def test_font_family_names():
    assert family("ABCDEF+Arial-BoldMT") == "arial"
    assert family("Helvetica-Bold") == "helvetica"
    assert family("Times-Roman") == "times"


def test_page_words_carry_fonts_and_boxes():
    import pypdfium2 as pdfium

    words = page_words(pdfium.PdfDocument(bill_pdf("Times-Roman")), 0)
    odd = next(w for w in words if w.text == AMOUNTS[4])
    assert odd.family == "times" and 0 < odd.bbox[0] < odd.bbox[2] <= 1 and odd.bbox[1] < odd.bbox[3]
    assert next(w for w in words if w.text == AMOUNTS[0]).family == "helvetica"


def test_f02_amount_in_another_font_is_flagged():
    o = next(o for o in run_checks(ctx_with(genuine_docs(), {"bill": (bill_pdf("Times-Roman"), ["p4"])}), {})
             if o.rule_id == "F-02")
    assert o.status == "flagged" and len(o.findings) == 1
    f = o.findings[0]
    assert AMOUNTS[4] in f.title and f.severity == "amber" and f.evidence[0]["bbox"]
    assert f.amount_at_stake is not None and float(f.amount_at_stake) == 12250   # matched to the surgeon fee line


def test_f02_same_font_passes_and_scans_cannot_be_checked():
    o = next(o for o in run_checks(ctx_with(genuine_docs(), {"bill": (bill_pdf(), ["p4"])}), {})
             if o.rule_id == "F-02")
    assert o.status == "passed"
    ctx = context()
    for p in ctx.pages:
        p.source = "ocr"
    o = next(o for o in run_checks(ctx, {}) if o.rule_id == "F-02")
    assert o.status == "not_evaluable"


def lab_docs(n: int):
    docs = genuine_docs()
    docs = [x for x in docs if x.doc_type != "lab_report"]
    for i in range(n):
        pid = f"lab{i}"
        docs.append(DocExtract(f"lab_report:{i + 1}", "lab_report", [pid], "good",
                               {"test": v(f"Test {i + 1}", page=pid),
                                "hospital_name": v("Ganga Superspeciality Hospital", page=pid)}))
    return docs


def test_f04_lab_report_with_a_different_letterhead():
    docs = lab_docs(3)
    ctx = ctx_with(docs, {"labs": (labs_pdf(["Helvetica-Bold", "Helvetica-Bold", "Courier-Bold"]),
                                   ["lab0", "lab1", "lab2"])})
    o = next(o for o in run_checks(ctx, {}) if o.rule_id == "F-04")
    assert o.status == "flagged" and len(o.findings) == 1 and "Test 3" in o.findings[0].title


def test_f04_single_report_compared_with_the_hospitals_documents():
    docs = lab_docs(1)
    ctx = ctx_with(docs, {"labs": (labs_pdf(["Courier-Bold"]), ["lab0"]),
                          "bill": (bill_pdf(), ["p4"])})
    o = next(o for o in run_checks(ctx, {}) if o.rule_id == "F-04")
    assert o.status == "flagged"
    ctx = ctx_with(lab_docs(1), {"labs": (labs_pdf(["Helvetica-Bold"]), ["lab0"]), "bill": (bill_pdf(), ["p4"])})
    assert next(o for o in run_checks(ctx, {}) if o.rule_id == "F-04").status == "passed"


def test_header_family_reads_the_letterhead():
    import pypdfium2 as pdfium

    words = page_words(pdfium.PdfDocument(labs_pdf(["Courier-Bold"])), 0)
    assert header_family(words) == "courier"
