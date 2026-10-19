"""Runs the processing steps for one claim and records the outcome on the claim.

Status: received -> processing -> ready_for_review, or failed with a readable error.
Re-running is safe: derived data (pages and their images) is removed first.
"""

import logging
import traceback
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.claim import Claim, DocumentPage
from app.models.enums import ClaimStatus
from app.pipeline.pages import split
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


STEPS = [("pages", step_pages)]


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