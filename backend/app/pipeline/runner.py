"""Runs the processing steps for one claim and records the outcome on the claim.

Status: received -> processing -> ready_for_review, or failed with a readable error.
Re-running is safe: derived data (pages and their images) is removed first.
"""

import io
import logging
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pypdfium2 as pdfium
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.claim import Claim, ClaimDocument, DocumentPage
from app.models.enums import ClaimStatus, DocumentType
from app.pipeline.classify import classify
from app.pipeline.ocr import OcrJob, PageText, image_frames, pdf_page_job, quality, run_job
from app.pipeline.pages import dhash, split
from app.services import intake
from app.services.audit import log_event

log = logging.getLogger(__name__)


class PipelineError(Exception):
    pass


def page_image_key(claim_id: uuid.UUID, doc_id: uuid.UUID, number: int) -> str:
    return f"claims/{claim_id}/pages/{doc_id}/{number:03d}.png"


def clear_derived(db: Session, claim: Claim) -> None:
    store = intake.storage()
    for doc in claim.documents:
        for page in doc.pages:
            if page.image_key:
                try:
                    store.delete(page.image_key)
                except Exception:
                    log.warning("Could not delete %s", page.image_key)
    doc_ids = [d.id for d in claim.documents]
    if doc_ids:
        db.execute(delete(DocumentPage).where(DocumentPage.document_id.in_(doc_ids)))
    db.flush()
    db.expire(claim)


def step_pages(db: Session, claim: Claim) -> None:
    store = intake.storage()
    for doc in sorted(claim.documents, key=lambda d: d.uploaded_at):
        data = store.get(doc.storage_key)
        images = split(data, doc.content_type)
        if not images:
            raise PipelineError(f"{doc.filename}: no pages could be read")
        for img in images:
            key = page_image_key(claim.id, doc.id, img.number)
            store.put(key, img.png, "image/png")
            db.add(DocumentPage(document_id=doc.id, page_number=img.number, image_key=key, image_hash=img.dhash,
                                width=img.width, height=img.height, has_text_layer=bool(img.text),
                                ocr_text=img.text))
        doc.page_count = len(images)
        db.flush()


def _read_document(data: bytes, content_type: str, pool: ThreadPoolExecutor) -> list[PageText]:
    if content_type == "application/pdf":
        doc = pdfium.PdfDocument(data)
        try:
            jobs = [pdf_page_job(doc, i) for i in range(len(doc))]
        finally:
            doc.close()
    else:
        jobs = [OcrJob(frame) for frame in image_frames(data)]
    futures = [pool.submit(run_job, job) if isinstance(job, OcrJob) else job for job in jobs]
    return [f if isinstance(f, PageText) else f.result() for f in futures]


def _turn_stored_image(page: DocumentPage, degrees: int) -> None:
    """Store the page image upright, so the viewer and word boxes line up."""
    store = intake.storage()
    if not page.image_key:
        return
    img = Image.open(io.BytesIO(store.get(page.image_key)))
    upright = img.rotate(-degrees, expand=True)
    buffer = io.BytesIO()
    upright.save(buffer, "PNG", compress_level=3)
    store.put(page.image_key, buffer.getvalue(), "image/png")
    page.width, page.height = upright.size
    page.image_hash = dhash(upright)


def step_read(db: Session, claim: Claim) -> None:
    """Text for every page (text layer or OCR), its quality, and its document type."""
    store = intake.storage()
    with ThreadPoolExecutor(max_workers=get_settings().ocr_workers) as pool:
        for doc in sorted(claim.documents, key=lambda d: d.uploaded_at):
            texts = _read_document(store.get(doc.storage_key), doc.content_type, pool)
            previous: DocumentType | None = None
            pages = db.scalars(select(DocumentPage).where(DocumentPage.document_id == doc.id)
                               .order_by(DocumentPage.page_number)).all()
            for page in pages:
                text = texts[page.page_number - 1]
                flag, score = quality(text)
                result = classify(text.text, previous)
                previous = result.doc_type if result.doc_type != DocumentType.UNKNOWN else None
                page.ocr_text = text.text or None
                page.words = [w.as_list() for w in text.words]
                page.text_source = text.source
                page.ocr_confidence = None if text.confidence is None else round(text.confidence, 3)
                page.quality_flag = flag
                page.quality_score = round(score, 3)
                page.doc_type = result.doc_type
                page.doc_type_confidence = result.confidence
                if text.rotation:
                    page.rotation = text.rotation
                    _turn_stored_image(page, text.rotation)
            db.flush()


def step_extract(db: Session, claim: Claim) -> None:
    """Structured claim record: rules on every document, the AI reader on scans and gaps."""
    from app.ai.client import AIError, get_provider
    from app.extraction import ai_extract, store
    from app.extraction.documents import build_documents
    from app.extraction.layout import words_of
    from app.models.extraction import AiCall

    settings = get_settings()
    pages = db.scalars(select(DocumentPage).join(ClaimDocument).where(ClaimDocument.claim_id == claim.id)
                       .order_by(ClaimDocument.uploaded_at, DocumentPage.page_number)).all()
    records = [{"id": str(p.id), "file_id": str(p.document_id), "doc_type": getattr(p.doc_type, "value", p.doc_type),
                "words": p.words or [], "quality": p.quality_flag, "text": p.ocr_text or "",
                "source": p.text_source, "image_key": p.image_key} for p in pages]
    by_id = {r["id"]: r for r in records}
    docs = [d for d in build_documents([r for r in records if r["doc_type"] != "unknown"])]
    corrections = store.clear(db, claim.id)

    provider = get_provider()
    if provider is not None:
        def scanned(doc) -> bool:
            return any(by_id[pid]["source"] != "text_layer" for pid in doc.page_ids)

        todo = [d for d in docs if ai_extract.needs_ai(d, settings.ai_extraction, scanned(d))]
        todo = todo[:settings.ai_max_calls_per_claim]
        store_ = intake.storage()

        stop: list[str] = []          # set after a fatal error: the remaining documents are not sent

        def run(doc):
            if stop:
                return doc, None, None
            pages_ = [by_id[pid] for pid in doc.page_ids]
            images = [store_.get(p["image_key"]) for p in pages_ if p["image_key"]]
            try:
                return doc, ai_extract.read_with_ai(provider, doc, images, [p["text"] for p in pages_]), None
            except AIError as exc:
                if exc.fatal:
                    stop.append(str(exc))
                return doc, None, str(exc)

        with ThreadPoolExecutor(max_workers=max(1, settings.ai_parallel_calls)) as pool:
            results = list(pool.map(run, todo))
        for doc, result, error in results:
            if result is None and error is None:
                continue                # skipped after a fatal error; nothing was sent
            if result is not None:
                words = [w for n, pid in enumerate(doc.page_ids) for w in words_of(pid, n, by_id[pid]["words"])]
                digital = all(by_id[pid]["source"] == "text_layer" for pid in doc.page_ids)
                ai_extract.merge(doc, result.data, words, digital)
            db.add(AiCall(claim_id=claim.id, purpose=f"extract:{doc.doc_type}",
                          provider=result.provider if result else provider.name,
                          model=result.model if result else provider.model,
                          input_tokens=result.input_tokens if result else 0,
                          output_tokens=result.output_tokens if result else 0,
                          cost_usd=result.cost_usd if result else 0, duration_ms=result.duration_ms if result else 0,
                          status="ok" if result else "error", error=error))
    store.save(db, claim, docs, corrections)


def step_checks(db: Session, claim: Claim) -> None:
    """Step 9: run the check catalogue over the claim record and store findings with their evidence."""
    from app.rules.store import run_for_claim

    counts = run_for_claim(db, claim)
    log_event(db, "claim.checks_run", entity_type="claim", entity_id=claim.id, details=counts)


STEPS = [("pages", step_pages), ("read", step_read), ("extract", step_extract), ("checks", step_checks)]


def run_pipeline(db: Session, claim_id: uuid.UUID) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise PipelineError(f"Claim {claim_id} not found")
    claim.status = ClaimStatus.PROCESSING
    claim.processing_error = None
    claim.processing_started_at = datetime.now(timezone.utc)
    claim.processing_finished_at = None
    db.commit()

    try:
        clear_derived(db, claim)
        for name, step in STEPS:
            log.info("claim %s: %s", claim.claim_number, name)
            step(db, claim)
        claim.status = ClaimStatus.READY_FOR_REVIEW
        claim.processing_finished_at = datetime.now(timezone.utc)
        log_event(db, "claim.processed", entity_type="claim", entity_id=claim.id,
                  details={"pages": sum(d.page_count or 0 for d in claim.documents)})
        db.commit()
    except Exception as exc:
        db.rollback()
        claim = db.get(Claim, claim_id)
        assert claim is not None
        claim.status = ClaimStatus.FAILED
        claim.processing_error = str(exc)[:2000] or exc.__class__.__name__
        claim.processing_finished_at = datetime.now(timezone.utc)
        log_event(db, "claim.processing_failed", entity_type="claim", entity_id=claim.id,
                  details={"error": claim.processing_error})
        db.commit()
        log.error("claim %s failed: %s", claim_id, traceback.format_exc())
    return claim
