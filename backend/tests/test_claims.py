"""Step 6: claim upload, storage of originals, processing queue, page splitting, claim API."""

import io
import uuid

import pytest
from PIL import Image
from pypdf import PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.claim import Claim, ClaimDocument, DocumentPage
from app.models.enums import ClaimStage, ClaimStatus, RoleName
from app.models.user import AuditLog
from app.pipeline import runner
from app.pipeline.pages import dhash
from app.services import intake
from app.services.storage import LocalStorage
from app.services.users import create_user

# ---------------------------------------------------------------- sample files


def make_pdf(pages: int = 2, text: str = "Discharge summary") -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    for i in range(pages):
        c.drawString(72, 760, f"{text} - page {i + 1}")
        c.showPage()
    c.save()
    return buffer.getvalue()


def make_image(fmt: str = "JPEG", frames: int = 1) -> bytes:
    images = [Image.new("RGB", (400, 560), (255, 255, 255 - i * 40)) for i in range(frames)]
    buffer = io.BytesIO()
    if frames > 1:
        images[0].save(buffer, fmt, save_all=True, append_images=images[1:])
    else:
        images[0].save(buffer, fmt)
    return buffer.getvalue()


def make_encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Keep test files out of the real storage, and capture queued jobs instead of sending them."""
    local = LocalStorage(str(tmp_path / "storage"))
    monkeypatch.setattr(intake, "get_storage", lambda: local)
    queued: list[uuid.UUID] = []
    monkeypatch.setattr(intake, "enqueue_processing", lambda claim_id: queued.append(claim_id) or "job")
    local.queued = queued  # type: ignore[attr-defined]
    return local


def headers(db, role: RoleName) -> dict[str, str]:
    user = create_user(db, email=f"{role.value}@claims.claimguard.in", full_name=f"{role.value} user", role=role,
                       password="Claims#2026secure")
    token, _ = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def upload(client, hdrs, files, **form):
    data = {"stage": "final_cashless", **form}
    return client.post("/api/claims", headers=hdrs, data=data,
                       files=[("files", (name, content, "application/octet-stream")) for name, content in files])


# ---------------------------------------------------------------- intake service


def test_detect_kind_uses_file_bytes_not_name():
    assert intake.detect_kind(make_pdf()) == "pdf"
    assert intake.detect_kind(make_image("JPEG")) == "jpeg"
    assert intake.detect_kind(make_image("PNG")) == "png"
    assert intake.detect_kind(make_image("TIFF")) == "tiff"
    assert intake.detect_kind(b"MZ\x90\x00 an exe") is None
    assert intake.detect_kind(b"hello") is None


def test_safe_filename():
    assert intake.safe_filename("../../etc/passwd") == "passwd"
    assert intake.safe_filename("C:\\docs\\bill (1).pdf") == "bill (1).pdf"
    assert intake.safe_filename("बिल.pdf").endswith(".pdf")


def test_dhash_survives_resizing_but_not_a_different_page():
    page = Image.new("L", (600, 800), 255)
    for y in range(0, 800, 40):
        page.paste(0, (50, y, 50 + (y % 300) + 100, y + 10))
    other = page.rotate(180)
    assert dhash(page) == dhash(page.resize((300, 400)))
    assert dhash(page) != dhash(other)


# ---------------------------------------------------------------- upload API


def test_upload_creates_claim_stores_originals_and_queues(client, db, store):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    pdf, jpg = make_pdf(3), make_image("JPEG")
    res = upload(client, hdrs, [("discharge.pdf", pdf), ("bill.jpg", jpg)], policy_number="SHP/123",
                 member_name="Asha Meena")
    assert res.status_code == 201, res.text
    body = res.json()
    claim = body["claim"]
    assert claim["status"] == "received" and claim["stage"] == "final_cashless" and claim["source"] == "upload"
    assert claim["claim_number"].startswith("CG-") and claim["member_name"] == "Asha Meena"
    assert (claim["documents"], claim["pages"]) == (2, 4)
    assert body["warnings"] == []
    assert [str(x) for x in store.queued] == [claim["id"]]

    docs = db.scalars(select(ClaimDocument).where(ClaimDocument.claim_id == uuid.UUID(claim["id"]))).all()
    by_name = {d.filename: d for d in docs}
    assert store.get(by_name["discharge.pdf"].storage_key) == pdf          # original kept byte for byte
    assert by_name["bill.jpg"].content_type == "image/jpeg"
    assert db.scalar(select(AuditLog.action).where(AuditLog.entity_id == claim["id"])) == "claim.created"


def test_upload_flags_identical_file_from_another_claim(client, db):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    pdf = make_pdf(1)
    first = upload(client, hdrs, [("a.pdf", pdf)]).json()["claim"]
    second = upload(client, hdrs, [("b.pdf", pdf)]).json()
    assert first["claim_number"] in second["warnings"][0]
    assert second["claim"]["files"][0]["duplicate_of_id"] == first["files"][0]["id"]


@pytest.mark.parametrize("files, status, message", [
    ([("notes.txt", b"just text")], 415, "not a PDF"),
    ([("virus.pdf", b"MZ\x90\x00 pretending")], 415, "not a PDF"),
    ([("empty.pdf", b"")], 400, "empty"),
    ([("locked.pdf", None)], 400, "password-protected"),
    ([("broken.pdf", b"%PDF-1.7\n garbage with no pages")], 400, "PDF"),
])
def test_upload_rejects_bad_files(client, db, store, files, status, message):
    files = [(n, make_encrypted_pdf() if c is None else c) for n, c in files]
    res = upload(client, headers(db, RoleName.ADJUDICATOR), files)
    assert res.status_code == status, res.text
    assert message in res.json()["detail"]
    assert db.scalar(select(Claim.id)) is None and store.queued == []


def test_upload_enforces_page_and_size_limits(client, db, monkeypatch):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    settings = get_settings()
    monkeypatch.setattr(settings, "max_claim_pages", 3)
    res = upload(client, hdrs, [("a.pdf", make_pdf(2)), ("b.pdf", make_pdf(2))])
    assert res.status_code == 413 and "4 pages" in res.json()["detail"]
    monkeypatch.setattr(settings, "max_claim_pages", 150)
    monkeypatch.setattr(settings, "max_claim_bytes", 2000)
    res = upload(client, hdrs, [("a.pdf", make_pdf(1)), ("b.jpg", make_image())])
    assert res.status_code == 413


def test_upload_needs_files_and_valid_stage(client, db):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    assert client.post("/api/claims", headers=hdrs, data={"stage": "final_cashless"}).status_code == 422
    assert upload(client, hdrs, [("a.pdf", make_pdf(1))], stage="approved").status_code == 422


def test_duplicate_claim_number_rejected(client, db):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    assert upload(client, hdrs, [("a.pdf", make_pdf(1))], claim_number="tpa-001").status_code == 201
    res = upload(client, hdrs, [("a.pdf", make_pdf(1))], claim_number="TPA-001")
    assert res.status_code == 409


def test_permissions(client, db):
    assert client.get("/api/claims").status_code == 401
    admin = headers(db, RoleName.ADMIN)                       # admins manage the system, not claims
    assert upload(client, admin, [("a.pdf", make_pdf(1))]).status_code == 403
    investigator = headers(db, RoleName.FRAUD_INVESTIGATOR)   # can view, cannot upload
    assert client.get("/api/claims", headers=investigator).status_code == 200
    assert upload(client, investigator, [("a.pdf", make_pdf(1))]).status_code == 403


# ---------------------------------------------------------------- processing


def test_pipeline_splits_pdf_and_images_into_pages(client, db, store):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    claim = upload(client, hdrs, [("summary.pdf", make_pdf(2, "Final diagnosis: dengue")),
                                  ("scan.tiff", make_image("TIFF", frames=3)),
                                  ("photo.png", make_image("PNG"))]).json()["claim"]
    result = runner.run_pipeline(db, uuid.UUID(claim["id"]))
    assert result.status == ClaimStatus.READY_FOR_REVIEW, result.processing_error
    assert result.processing_started_at and result.processing_finished_at

    detail = client.get(f"/api/claims/{claim['id']}", headers=hdrs).json()
    files = {f["filename"]: f for f in detail["files"]}
    assert [len(files[n]["pages"]) for n in ("summary.pdf", "scan.tiff", "photo.png")] == [2, 3, 1]
    pdf_page = files["summary.pdf"]["pages"][0]
    assert pdf_page["has_text_layer"] and pdf_page["width"] > 1000 and len(pdf_page["image_hash"]) == 16
    assert not files["scan.tiff"]["pages"][0]["has_text_layer"]
    text = db.scalar(select(DocumentPage.ocr_text).where(DocumentPage.id == uuid.UUID(pdf_page["id"])))
    assert "dengue" in text

    img = client.get(f"/api/claims/{claim['id']}/pages/{pdf_page['id']}/image", headers=hdrs)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(img.content)).mode == "L"


def test_pipeline_failure_is_recorded_and_reprocess_recovers(client, db, store):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    claim = upload(client, hdrs, [("a.pdf", make_pdf(2))]).json()["claim"]
    doc = db.scalar(select(ClaimDocument).where(ClaimDocument.claim_id == uuid.UUID(claim["id"])))
    original = store.get(doc.storage_key)
    store.delete(doc.storage_key)                                   # simulate a storage outage

    failed = runner.run_pipeline(db, uuid.UUID(claim["id"]))
    assert failed.status == ClaimStatus.FAILED and "Not found" in (failed.processing_error or "")
    listed = client.get("/api/claims?status=failed", headers=hdrs).json()
    assert listed["total"] == 1 and listed["counts"]["failed"] == 1

    store.put(doc.storage_key, original)
    res = client.post(f"/api/claims/{claim['id']}/reprocess", headers=hdrs)
    assert res.status_code == 200 and res.json()["status"] == "received"
    assert runner.run_pipeline(db, uuid.UUID(claim["id"])).status == ClaimStatus.READY_FOR_REVIEW

    # running again replaces the derived pages instead of adding more
    runner.run_pipeline(db, uuid.UUID(claim["id"]))
    assert len(db.scalars(select(DocumentPage).where(DocumentPage.document_id == doc.id)).all()) == 2


def test_reprocess_blocked_while_processing(client, db):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    claim = upload(client, hdrs, [("a.pdf", make_pdf(1))]).json()["claim"]
    db.get(Claim, uuid.UUID(claim["id"])).status = ClaimStatus.PROCESSING
    db.flush()
    assert client.post(f"/api/claims/{claim['id']}/reprocess", headers=hdrs).status_code == 409


# ---------------------------------------------------------------- queue and detail API


def test_list_search_filter_and_original_download(client, db):
    hdrs = headers(db, RoleName.ADJUDICATOR)
    a = upload(client, hdrs, [("a.pdf", make_pdf(1))], member_name="Ravi Kumar", policy_number="POL-1").json()
    upload(client, hdrs, [("b.pdf", make_pdf(2, "other"))], member_name="Neha Shah", stage="reimbursement")

    page = client.get("/api/claims", headers=hdrs).json()
    assert page["total"] == 2 and page["counts"] == {"received": 2}
    assert client.get("/api/claims?q=ravi", headers=hdrs).json()["total"] == 1
    assert client.get("/api/claims?q=POL-1", headers=hdrs).json()["items"][0]["member_name"] == "Ravi Kumar"
    assert client.get("/api/claims?stage=reimbursement", headers=hdrs).json()["total"] == 1
    assert client.get(f"/api/claims/{uuid.uuid4()}", headers=hdrs).status_code == 404

    claim = a["claim"]
    doc = claim["files"][0]
    res = client.get(f"/api/claims/{claim['id']}/documents/{doc['id']}/original", headers=hdrs)
    assert res.status_code == 200 and res.headers["x-content-sha256"] == doc["sha256"]
    assert res.content.startswith(b"%PDF")
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == doc["id"])).all()
    assert actions == ["document.downloaded"]
    other = client.get(f"/api/claims/{uuid.uuid4()}/documents/{doc['id']}/original", headers=hdrs)
    assert other.status_code == 404                                  # document must belong to that claim


def test_create_claim_service_cleans_up_files_on_failure(db, store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database down")

    monkeypatch.setattr(db, "flush", boom)
    with pytest.raises(RuntimeError):
        intake.create_claim(db, [intake.IncomingFile("a.pdf", make_pdf(1))], stage=ClaimStage.REIMBURSEMENT)
    assert not any((store.root / "claims").rglob("*.pdf")) if (store.root / "claims").exists() else True


def test_import_synthetic_claims(db, tmp_path, monkeypatch):
    from app import cli
    from app.synthetic.generate import Options, generate

    generate(Options(out_dir=tmp_path / "syn", count=3, seed=5, scanned_share=0.0))

    class Session:  # the CLI opens its own session; give it the test one
        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "SessionLocal", Session)
    cli.import_synthetic(tmp_path / "syn", limit=None, scanned=False, now=True)
    claims = db.scalars(select(Claim).where(Claim.source == "synthetic").order_by(Claim.claim_number)).all()
    assert [c.claim_number for c in claims] == ["CG-SYN-0001", "CG-SYN-0002", "CG-SYN-0003"]
    assert all(c.status == ClaimStatus.READY_FOR_REVIEW for c in claims)
    cli.import_synthetic(tmp_path / "syn", limit=None, scanned=False, now=True)   # second run skips them
    assert len(db.scalars(select(Claim).where(Claim.source == "synthetic")).all()) == 3
