"""Claim intake: check uploaded files, keep the originals unchanged, create the claim, queue processing.

Only PDF, JPEG, PNG and TIFF are accepted, judged by the file's own bytes, not
its name. Originals are stored once and never modified; everything the system
derives later (page images, OCR text) is stored separately.
"""

import hashlib
import io
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.claim import Claim, ClaimDocument
from app.models.enums import ClaimStage, ClaimStatus
from app.services.queue import job_queue
from app.services.storage import Storage, get_storage

Image.MAX_IMAGE_PIXELS = 120_000_000  # refuse decompression bombs, allow large scans

KINDS = {
    "pdf": "application/pdf",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "tiff": "image/tiff",
}


class IntakeError(Exception):
    """A problem the uploader can fix (wrong type, too big, unreadable...). Nothing is saved."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class IncomingFile:
    filename: str
    data: bytes


@dataclass
class CheckedFile:
    filename: str
    data: bytes
    kind: str
    content_type: str
    pages: int
    sha256: str


@dataclass
class IntakeResult:
    claim: Claim
    warnings: list[str] = field(default_factory=list)


def storage() -> Storage:
    return get_storage()


def detect_kind(data: bytes) -> str | None:
    """File type from its first bytes (magic numbers)."""
    head = data[:8]
    if head.startswith(b"%PDF-") or data[:1024].find(b"%PDF-") != -1:
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


def count_pages(data: bytes, kind: str, filename: str) -> int:
    if kind == "pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise IntakeError(f"{filename}: the PDF is password-protected. Ask for an unlocked copy.")
            pages = len(reader.pages)
        except PdfReadError as exc:
            raise IntakeError(f"{filename}: the PDF is damaged or unreadable ({exc}).")
        if pages == 0:
            raise IntakeError(f"{filename}: the PDF has no pages.")
        return pages
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            return getattr(img, "n_frames", 1)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise IntakeError(f"{filename}: the image is damaged or unreadable ({exc}).")


def safe_filename(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip() or "file"
    name = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", name)
    return name[:150]


def check_files(files: list[IncomingFile]) -> list[CheckedFile]:
    settings = get_settings()
    if not files:
        raise IntakeError("Attach at least one file.")
    if len(files) > settings.max_files_per_claim:
        raise IntakeError(f"Too many files: at most {settings.max_files_per_claim} per claim.")
    total = sum(len(f.data) for f in files)
    if total > settings.max_claim_bytes:
        raise IntakeError(f"Files total {total / 1048576:.1f} MB; the limit is "
                          f"{settings.max_claim_bytes / 1048576:.0f} MB per claim.", 413)

    checked: list[CheckedFile] = []
    for f in files:
        name = safe_filename(f.filename)
        if not f.data:
            raise IntakeError(f"{name}: the file is empty.")
        kind = detect_kind(f.data)
        if kind is None:
            raise IntakeError(f"{name}: not a PDF, JPG, PNG or TIFF file.", 415)
        pages = count_pages(f.data, kind, name)
        checked.append(CheckedFile(name, f.data, kind, KINDS[kind], pages, hashlib.sha256(f.data).hexdigest()))

    total_pages = sum(c.pages for c in checked)
    if total_pages > settings.max_claim_pages:
        raise IntakeError(f"The files have {total_pages} pages; the limit is {settings.max_claim_pages} "
                          "per claim.", 413)
    return checked


def new_claim_number(db: Session) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    while True:
        number = f"CG-{today}-{secrets.token_hex(3).upper()}"
        if db.scalar(select(Claim.id).where(Claim.claim_number == number)) is None:
            return number


def original_key(claim_id: uuid.UUID, doc_id: uuid.UUID, filename: str) -> str:
    return f"claims/{claim_id}/original/{doc_id}/{filename}"


def create_claim(
    db: Session,
    files: list[IncomingFile],
    *,
    stage: ClaimStage,
    claim_number: str | None = None,
    policy_number: str | None = None,
    member_name: str | None = None,
    uploaded_by: uuid.UUID | None = None,
    source: str = "upload",
) -> IntakeResult:
    """Check files, store originals, create the claim. The caller commits, then calls enqueue_processing."""
    checked = check_files(files)
    number = (claim_number or "").strip().upper() or new_claim_number(db)
    if db.scalar(select(Claim.id).where(Claim.claim_number == number)) is not None:
        raise IntakeError(f"Claim number {number} already exists.", 409)

    claim = Claim(id=uuid.uuid4(), claim_number=number, stage=stage, status=ClaimStatus.RECEIVED,
                  policy_number=(policy_number or "").strip() or None,
                  member_name=(member_name or "").strip() or None, created_by_id=uploaded_by, source=source)
    db.add(claim)
    result = IntakeResult(claim)
    store = storage()
    stored: list[str] = []
    try:
        for f in checked:
            doc_id = uuid.uuid4()
            key = original_key(claim.id, doc_id, f.filename)
            store.put(key, f.data, f.content_type)
            stored.append(key)
            earlier = db.execute(
                select(ClaimDocument.id, Claim.claim_number)
                .join(Claim, Claim.id == ClaimDocument.claim_id)
                .where(ClaimDocument.sha256 == f.sha256)
                .order_by(ClaimDocument.uploaded_at)
                .limit(1)
            ).first()
            if earlier:
                result.warnings.append(f"{f.filename} is identical to a file already uploaded with claim "
                                       f"{earlier.claim_number}.")
            db.add(ClaimDocument(id=doc_id, claim_id=claim.id, filename=f.filename, content_type=f.content_type,
                                 storage_key=key, size_bytes=len(f.data), sha256=f.sha256, page_count=f.pages,
                                 uploaded_by_id=uploaded_by, duplicate_of_id=earlier.id if earlier else None))
        db.flush()
    except Exception:
        for key in stored:  # don't leave orphan files behind
            try:
                store.delete(key)
            except Exception:
                pass
        raise
    return result


def enqueue_processing(claim_id: uuid.UUID) -> str:
    """Queue the processing pipeline for a claim. Call only after the claim is committed."""
    job = job_queue.enqueue("app.worker.jobs.process_claim", str(claim_id),
                            job_timeout=get_settings().job_timeout_seconds, result_ttl=3600, failure_ttl=7 * 86400)
    return job.id