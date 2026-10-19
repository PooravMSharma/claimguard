"""Step 7: reading pages (text layer and OCR), page quality, page types, and the page API."""

import io
import shutil
import uuid

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import select

from app.core.security import create_access_token
from app.models.claim import ClaimDocument, DocumentPage
from app.models.enums import ClaimStatus, DocumentType, RoleName
from app.models.user import AuditLog
from app.pipeline import ocr, runner
from app.pipeline.classify import classify
from app.pipeline.evaluate import printed_values
from app.services import intake
from app.services.storage import LocalStorage
from app.services.users import create_user

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")

BILL_LINES = ["CITY CARE HOSPITAL", "FINAL BILL - IN-PATIENT", "Patient Ravi Kumar   Bill No. IPB/2026/12345",
              "Room rent - semi-private ward    3    2,200.00    6,600.00",
              "Nursing charges    3    400.00    1,200.00", "GRAND TOTAL    7,800.00", "Net payable Rs. 7,800.00"]


def pdf_with_lines(pages: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    for lines in pages:
        y = 780
        for line in lines:
            c.setFont("Helvetica-Bold" if y == 780 else "Helvetica", 14 if y == 780 else 11)
            c.drawString(60, y, line)
            y -= 22
        c.showPage()
    c.save()
    return buffer.getvalue()


def image_with_lines(lines: list[str], size=(1240, 1754)) -> Image.Image:
    img = Image.new("L", size, 255)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=30)
    except TypeError:  # older Pillow
        font = ImageFont.load_default()
    y = 120
    for line in lines:
        draw.text((90, y), line, fill=0, font=font)
        y += 56
    return img


def png(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    local = LocalStorage(str(tmp_path / "storage"))
    monkeypatch.setattr(intake, "get_storage", lambda: local)
    monkeypatch.setattr(intake, "enqueue_processing", lambda claim_id: "job")
    return local


def headers(db, role: RoleName) -> dict[str, str]:
    user = create_user(db, email=f"{role.value}@read.claimguard.in", full_name="Reader", role=role,
                       password="Claims#2026secure")
    token, _ = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def upload_and_process(client, db, hdrs, files):
    res = client.post("/api/claims", headers=hdrs, data={"stage": "final_cashless"},
                      files=[("files", (n, c, "application/octet-stream")) for n, c in files])
    assert res.status_code == 201, res.text
    claim_id = uuid.UUID(res.json()["claim"]["id"])
    claim = runner.run_pipeline(db, claim_id)
    assert claim.status == ClaimStatus.READY_FOR_REVIEW, claim.processing_error
    return claim_id


# ---------------------------------------------------------------- classification


@pytest.mark.parametrize("text, expected", [
    ("CLAIM FORM - HEALTH INSURANCE POLICY\nPART A - To be filled by the insured\nAmount claimed", "claim_form"),
    ("Ganga Hospital\nDISCHARGE SUMMARY\nFinal diagnosis\nCourse in hospital\nAdvice on discharge",
     "discharge_summary"),
    ("\n".join(BILL_LINES), "final_bill"),
    ("IN-HOUSE PHARMACY - CREDIT BILL\nItem Batch Exp. Qty MRP Rate Amount\nRegistered Pharmacist", "pharmacy_bill"),
    ("Department of Laboratory Medicine\nCOMPLETE BLOOD COUNT\nInvestigation Result Unit Reference range\nPathologist",
     "lab_report"),
    ("Department of Radiology\nUSG ABDOMEN\nStudy date\nFindings\nImpression\nRadiologist", "imaging_report"),
    ("OPERATION NOTES AND ANAESTHESIA RECORD\nProcedure(s) performed\nOperative findings", "ot_notes"),
    ("MEDICO-LEGAL CASE (MLC) INTIMATION\nMLC No. MLC/2026/77\nPolice station Kotwali", "mlc_fir"),
    ("HEALTH CARD AND POLICY SCHEDULE\nSum insured\nPolicy period\nFirst inception", "policy_copy"),
    ("GOVERNMENT PHOTO IDENTITY (masked copy)\nYear of birth: 1980\nID: XXXX XXXX 1234", "id_proof"),
    ("IMPLANT STICKER (affixed)\nMANUFACTURER LABEL\nREF DES-3.0 LOT SN123 STERILE R", "implant_sticker"),
    ("REQUEST FOR CASHLESS HOSPITALISATION (PRE-AUTHORISATION)\nEstimated cost\nAUTHORISATION LETTER",
     "preauth_form"),
])
def test_classify_page_types(text, expected):
    result = classify(text)
    assert result.doc_type.value == expected, result.reasons
    assert result.confidence >= 0.6


def test_classify_unknown_and_continuation():
    assert classify("").doc_type == DocumentType.UNKNOWN
    assert classify("random words with nothing useful").doc_type == DocumentType.UNKNOWN
    cont = classify("12  Disposable syringe  20  12.00  240.00", previous=DocumentType.FINAL_BILL)
    assert cont.doc_type == DocumentType.FINAL_BILL and cont.confidence < 0.6
    assert classify("12 Disposable syringe", previous=DocumentType.ID_PROOF).doc_type == DocumentType.UNKNOWN


def test_quality_flags():
    good = ocr.PageText("ocr", "x", [ocr.Word("w", 0.9, 0, 0, 1, 1, 0)] * 20, 0.9, 0, 0.02)
    poor = ocr.PageText("ocr", "x", [ocr.Word("w", 0.5, 0, 0, 1, 1, 0)] * 20, 0.5, 0, 0.02)
    few = ocr.PageText("ocr", "x", [ocr.Word("w", 0.9, 0, 0, 1, 1, 0)] * 3, 0.9, 0, 0.02)
    assert ocr.quality(good)[0] == "good" and ocr.quality(poor)[0] == "poor"
    assert ocr.quality(few)[0] == "unreadable"
    assert ocr.quality(ocr.PageText("none", "", [], None, 0, 0.0))[0] == "blank"
    assert ocr.quality(ocr.PageText("text_layer", "x", [], 1.0))[0] == "good"


def test_printed_values_for_evaluation():
    values = printed_values({"patient_name": "Ravi Kumar", "bill_total": 7800.0, "age": 40, "signed": True,
                             "lines": [{"description": "Room rent", "amount": 6600.0}]})
    assert values == ["Ravi Kumar", "7,800.00", "Room rent", "6,600.00"]


# ---------------------------------------------------------------- reading


def test_text_layer_words_have_boxes():
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_with_lines([BILL_LINES]))
    page = ocr.read_pdf_page(doc, 0)
    assert page.source == "text_layer" and page.confidence == 1.0
    assert "Bill No. IPB/2026/12345" in page.text and "GRAND TOTAL" in page.text
    word = next(w for w in page.words if w.text == "IPB/2026/12345")
    assert 0 < word.x0 < word.x1 < 1 and 0 < word.y0 < word.y1 < 0.3   # near the top of the page


@needs_tesseract
def test_ocr_reads_a_scanned_image_and_finds_rotation():
    img = image_with_lines(BILL_LINES)
    upright = ocr.ocr_image(img)
    assert upright.source == "ocr" and upright.rotation == 0
    assert "FINAL BILL" in upright.text and "7,800.00" in upright.text
    assert ocr.quality(upright)[0] in ("good", "fair")

    sideways = ocr.ocr_image(img.rotate(90, expand=True))
    assert sideways.rotation == 90 and "FINAL BILL" in sideways.text


@needs_tesseract
def test_blank_page_is_not_read():
    page = ocr.ocr_image(Image.new("L", (1240, 1754), 255))
    assert page.source == "none" and ocr.quality(page)[0] == "blank"


# ---------------------------------------------------------------- pipeline and API


@needs_tesseract
def test_pipeline_reads_classifies_and_serves_text(client, db, store):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    discharge = ["SHREE HOSPITAL", "DISCHARGE SUMMARY", "Final diagnosis: Dengue fever", "Course in hospital: stable",
                 "Advice on discharge: oral fluids"]
    claim_id = upload_and_process(client, db, hdrs, [
        ("digital.pdf", pdf_with_lines([discharge, BILL_LINES])),
        ("scan.png", png(image_with_lines(BILL_LINES).rotate(180))),
    ])
    detail = client.get(f"/api/claims/{claim_id}", headers=hdrs).json()
    pages = [p for f in detail["files"] for p in f["pages"]]
    assert [p["doc_type"] for p in pages] == ["discharge_summary", "final_bill", "final_bill"]
    assert [p["text_source"] for p in pages] == ["text_layer", "text_layer", "ocr"]
    assert pages[2]["rotation"] == 180 and pages[2]["quality_flag"] in ("good", "fair")
    assert detail["page_types"] == {"discharge_summary": 1, "final_bill": 2}
    assert detail["pages_needing_review"] == 0

    text = client.get(f"/api/claims/{claim_id}/pages/{pages[2]['id']}/text", headers=hdrs).json()
    assert "FINAL BILL" in text["text"] and "7,800.00" in text["text"] and len(text["words"][0]) == 6

    # the stored image was turned upright to match the words
    stored = db.get(DocumentPage, uuid.UUID(pages[2]["id"]))
    assert stored.width < stored.height


def test_reviewer_can_correct_a_page_type(client, db):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    letter = pdf_with_lines([["Some letter", "nothing known"] * 12])
    claim_id = upload_and_process(client, db, hdrs, [("a.pdf", letter)])
    page = db.scalar(select(DocumentPage).join(ClaimDocument).where(ClaimDocument.claim_id == claim_id))
    assert page.doc_type == DocumentType.UNKNOWN

    res = client.patch(f"/api/claims/{claim_id}/pages/{page.id}", headers=hdrs, json={"doc_type": "case_sheet"})
    assert res.status_code == 200 and res.json()["page_types"] == {"case_sheet": 1}
    assert db.scalar(select(AuditLog.action).where(AuditLog.entity_id == str(page.id))) == "page.type_changed"
    assert client.patch(f"/api/claims/{claim_id}/pages/{page.id}", headers=hdrs,
                        json={"doc_type": "selfie"}).status_code == 422

    viewer = headers(db, RoleName.FRAUD_INVESTIGATOR)   # can view, cannot change
    assert client.patch(f"/api/claims/{claim_id}/pages/{page.id}", headers=viewer,
                        json={"doc_type": "other"}).status_code == 403