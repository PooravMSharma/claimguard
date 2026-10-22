"""Claims: upload, queue, detail, page images, original files, reprocessing."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.permissions import Permission
from app.db.session import get_db
from app.models.claim import Claim, ClaimDocument, DocumentPage
from app.models.decision import Decision
from app.models.enums import ClaimStage, ClaimStatus, RiskBand
from app.models.user import AuditLog, User
from app.schemas.claims import (
    ClaimDetail,
    ClaimPage,
    ClaimRow,
    DocumentOut,
    PageText,
    PageTypeUpdate,
    UploadResult,
)
from app.services import intake
from app.services.audit import log_event
from app.services.storage import StorageError

router = APIRouter(prefix="/claims", tags=["claims"])
can_view = require_permission(Permission.CLAIMS_VIEW)
can_upload = require_permission(Permission.CLAIMS_UPLOAD)
can_review = require_permission(Permission.CLAIMS_REVIEW)
NEEDS_REVIEW = ("poor", "unreadable")

CHUNK = 1024 * 1024


def _value(v) -> str | None:
    return None if v is None else getattr(v, "value", v)


def _row(claim: Claim, documents: int, pages: int, assignee: str | None = None,
         recommendation: str | None = None) -> dict:
    return {
        "id": claim.id, "claim_number": claim.claim_number, "stage": _value(claim.stage),
        "status": _value(claim.status), "source": claim.source, "policy_number": claim.policy_number,
        "member_name": claim.member_name, "hospital_name": claim.hospital_name,
        "claimed_amount": claim.claimed_amount, "risk_band": _value(claim.risk_band),
        "risk_score": claim.risk_score, "documents": documents, "pages": pages, "created_at": claim.created_at,
        "processing_error": claim.processing_error, "sla_due_at": claim.sla_due_at,
        "suggested_payable": claim.suggested_payable, "assigned_to_id": claim.assigned_to_id,
        "assignee": assignee, "latest_recommendation": recommendation,
    }


def _detail(db: Session, claim_id: uuid.UUID) -> ClaimDetail:
    claim = db.scalar(select(Claim).where(Claim.id == claim_id)
                      .options(selectinload(Claim.documents).selectinload(ClaimDocument.pages)))
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    files = sorted(claim.documents, key=lambda d: d.uploaded_at)
    creator = db.get(User, claim.created_by_id) if claim.created_by_id else None
    pages = [p for d in files for p in d.pages]
    types: dict[str, int] = {}
    for p in pages:
        key = _value(p.doc_type) or "unknown"
        types[key] = types.get(key, 0) + 1
    return ClaimDetail(
        **_row(claim, len(files), sum(d.page_count or 0 for d in files)),
        created_by=creator.full_name if creator else None,
        processing_started_at=claim.processing_started_at, processing_finished_at=claim.processing_finished_at,
        pages_needing_review=sum(1 for p in pages if p.quality_flag in NEEDS_REVIEW),
        page_types=types,
        files=[DocumentOut.model_validate(d) for d in files],
    )


def _page(db: Session, claim_id: uuid.UUID, page_id: uuid.UUID) -> DocumentPage:
    page = db.scalar(select(DocumentPage).join(ClaimDocument)
                     .where(DocumentPage.id == page_id, ClaimDocument.claim_id == claim_id))
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


async def _read_uploads(files: list[UploadFile]) -> list[intake.IncomingFile]:
    """Read uploads, stopping as soon as the total passes the claim size limit."""
    limit = get_settings().max_claim_bytes
    total = 0
    out = []
    for upload in files:
        parts = []
        while chunk := await upload.read(CHUNK):
            total += len(chunk)
            if total > limit:
                raise HTTPException(status_code=413,
                                    detail=f"Files are larger than the {limit // 1048576} MB limit per claim")
            parts.append(chunk)
        out.append(intake.IncomingFile(upload.filename or "file", b"".join(parts)))
    return out


@router.post("", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
async def upload_claim(
    request: Request,
    files: list[UploadFile] = File(..., description="PDF, JPG, PNG or TIFF files"),
    stage: ClaimStage = Form(ClaimStage.FINAL_CASHLESS),
    claim_number: str | None = Form(None, max_length=64),
    policy_number: str | None = Form(None, max_length=64),
    member_name: str | None = Form(None, max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(can_upload),
) -> UploadResult:
    incoming = await _read_uploads(files)
    try:
        result = intake.create_claim(db, incoming, stage=stage, claim_number=claim_number,
                                     policy_number=policy_number, member_name=member_name, uploaded_by=user.id)
    except intake.IntakeError as exc:  # raised before anything is added to the session
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    claim = result.claim
    log_event(db, "claim.created", entity_type="claim", entity_id=claim.id, user_id=user.id, request=request,
              details={"claim_number": claim.claim_number, "files": [f.filename for f in incoming],
                       "stage": stage.value, "warnings": result.warnings})
    db.commit()
    intake.enqueue_processing(claim.id)
    return UploadResult(claim=_detail(db, claim.id), warnings=result.warnings)


@router.get("", response_model=ClaimPage)
def list_claims(
    q: str | None = Query(default=None, max_length=100,
                          description="Claim no., policy no., patient or hospital name"),
    status_filter: ClaimStatus | None = Query(default=None, alias="status"),
    stage: ClaimStage | None = None,
    source: str | None = Query(default=None, pattern="^(upload|synthetic)$"),
    band: RiskBand | None = None,
    assignee: str | None = Query(default=None, description="me, none, or a user id"),
    sort: str = Query(default="priority", pattern="^(priority|newest|deadline|score)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(can_view),
) -> ClaimPage:
    filters = []
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(Claim.claim_number.ilike(like), Claim.policy_number.ilike(like),
                           Claim.member_name.ilike(like), Claim.hospital_name.ilike(like)))
    if stage:
        filters.append(Claim.stage == stage)
    if source:
        filters.append(Claim.source == source)
    if assignee == "me":
        filters.append(Claim.assigned_to_id == user.id)
    elif assignee == "none":
        filters.append(Claim.assigned_to_id.is_(None))
    elif assignee:
        try:
            filters.append(Claim.assigned_to_id == uuid.UUID(assignee))
        except ValueError:
            raise HTTPException(status_code=422, detail="assignee must be 'me', 'none' or a user id")
    counts: dict[str, int] = {
        _value(s) or "": n
        for s, n in db.execute(select(Claim.status, func.count()).where(*filters).group_by(Claim.status)).tuples()
    }
    if status_filter:
        filters.append(Claim.status == status_filter)
    band_counts: dict[str, int] = {
        _value(b) or "none": n
        for b, n in db.execute(select(Claim.risk_band, func.count()).where(*filters).group_by(Claim.risk_band))
        .tuples()
    }
    if band:
        filters.append(Claim.risk_band == band)
    total = db.scalar(select(func.count()).select_from(Claim).where(*filters)) or 0

    docs = (select(ClaimDocument.claim_id, func.count().label("documents"),
                   func.coalesce(func.sum(ClaimDocument.page_count), 0).label("pages"))
            .group_by(ClaimDocument.claim_id).subquery())
    rows = db.execute(
        select(Claim, func.coalesce(docs.c.documents, 0), func.coalesce(docs.c.pages, 0))
        .outerjoin(docs, docs.c.claim_id == Claim.id)
        .where(*filters)
        .order_by(*_order(sort))
        .limit(limit).offset(offset)
    ).all()
    ids = [c.id for c, _, _ in rows]
    people = {u.id: u.full_name for u in db.scalars(
        select(User).where(User.id.in_({c.assigned_to_id for c, _, _ in rows if c.assigned_to_id})))}
    latest: dict[uuid.UUID, str] = {}
    for d in db.scalars(select(Decision).where(Decision.claim_id.in_(ids)).order_by(Decision.created_at)):
        latest[d.claim_id] = d.recommendation.value
    items = [ClaimRow(**_row(c, d, p, people.get(c.assigned_to_id) if c.assigned_to_id else None,
                             latest.get(c.id))) for c, d, p in rows]
    return ClaimPage(items=items, total=total, limit=limit, offset=offset, counts=counts,
                     band_counts=band_counts)


def _order(sort: str) -> list:
    """Queue order. "priority": claims waiting for a reviewer first, then red, amber, green, then the highest
    score, then the nearest deadline; decided and processing claims after them."""
    newest = [Claim.created_at.desc(), Claim.claim_number.desc()]
    if sort == "newest":
        return newest
    if sort == "deadline":
        return [Claim.sla_due_at.asc().nulls_last(), *newest]
    if sort == "score":
        return [Claim.risk_score.desc().nulls_last(), *newest]
    waiting = case((Claim.status.in_([ClaimStatus.READY_FOR_REVIEW, ClaimStatus.IN_REVIEW]), 0),
                   (Claim.status == ClaimStatus.FAILED, 1), (Claim.status == ClaimStatus.DECIDED, 3), else_=2)
    band = case((Claim.risk_band == RiskBand.RED, 0), (Claim.risk_band == RiskBand.AMBER, 1),
                (Claim.risk_band == RiskBand.GREEN, 2), else_=3)
    return [waiting, band, Claim.risk_score.desc().nulls_last(), Claim.sla_due_at.asc().nulls_last(), *newest]


VIEW_LOG_EVERY = timedelta(minutes=30)


@router.get("/{claim_id}", response_model=ClaimDetail)
def get_claim(claim_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
              user: User = Depends(can_view)) -> ClaimDetail:
    detail = _detail(db, claim_id)
    # Who opened which patient's claim (DPDP access trail). The screen re-reads the claim while it is processing,
    # so one entry per user and claim every 30 minutes, not one per request.
    recent = db.scalar(select(AuditLog.id).where(
        AuditLog.action == "claim.viewed", AuditLog.entity_id == str(claim_id), AuditLog.user_id == user.id,
        AuditLog.at > datetime.now(timezone.utc) - VIEW_LOG_EVERY).limit(1))
    if recent is None:
        log_event(db, "claim.viewed", entity_type="claim", entity_id=claim_id, user_id=user.id, request=request)
        db.commit()
    return detail


@router.get("/{claim_id}/pages/{page_id}/image")
def page_image(claim_id: uuid.UUID, page_id: uuid.UUID, db: Session = Depends(get_db),
               _: User = Depends(can_view)) -> Response:
    page = _page(db, claim_id, page_id)
    if not page.image_key:
        raise HTTPException(status_code=404, detail="Page image not available")
    try:
        data = intake.storage().get(page.image_key)
    except StorageError:
        raise HTTPException(status_code=404, detail="Page image missing; reprocess the claim")
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{claim_id}/pages/{page_id}/text", response_model=PageText)
def page_text(claim_id: uuid.UUID, page_id: uuid.UUID, db: Session = Depends(get_db),
              _: User = Depends(can_view)) -> PageText:
    page = _page(db, claim_id, page_id)
    return PageText(id=page.id, page_number=page.page_number, doc_type=_value(page.doc_type) or "unknown",
                    text_source=page.text_source, ocr_confidence=page.ocr_confidence,
                    quality_flag=page.quality_flag, text=page.ocr_text or "", words=page.words or [])


@router.patch("/{claim_id}/pages/{page_id}", response_model=ClaimDetail)
def set_page_type(claim_id: uuid.UUID, page_id: uuid.UUID, body: PageTypeUpdate, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(can_review)) -> ClaimDetail:
    """A reviewer corrects a page's document type. (Reprocessing the claim classifies pages again.)"""
    page = _page(db, claim_id, page_id)
    before = _value(page.doc_type)
    page.doc_type = body.doc_type
    page.doc_type_confidence = 1.0
    log_event(db, "page.type_changed", entity_type="document_page", entity_id=page.id, user_id=user.id,
              request=request, details={"claim_id": str(claim_id), "from": before, "to": body.doc_type.value})
    db.commit()
    return _detail(db, claim_id)


@router.get("/{claim_id}/documents/{document_id}/original")
def original_file(claim_id: uuid.UUID, document_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(can_view)) -> Response:
    doc = db.scalar(select(ClaimDocument).where(ClaimDocument.id == document_id,
                                                ClaimDocument.claim_id == claim_id))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        data = intake.storage().get(doc.storage_key)
    except StorageError:
        raise HTTPException(status_code=404, detail="Original file missing from storage")
    log_event(db, "document.downloaded", entity_type="claim_document", entity_id=doc.id, user_id=user.id,
              request=request, details={"claim_id": str(claim_id), "filename": doc.filename})
    db.commit()
    safe = doc.filename.replace('"', "")
    return Response(data, media_type=doc.content_type,
                    headers={"Content-Disposition": f'inline; filename="{safe}"', "X-Content-SHA256": doc.sha256})


@router.post("/{claim_id}/reprocess", response_model=ClaimDetail)
def reprocess(claim_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
              user: User = Depends(can_upload)) -> ClaimDetail:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim.status == ClaimStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Claim is already being processed")
    if claim.status in (ClaimStatus.IN_REVIEW, ClaimStatus.DECIDED):
        raise HTTPException(status_code=409, detail="Claim is already under review or decided")
    claim.status = ClaimStatus.RECEIVED
    claim.processing_error = None
    log_event(db, "claim.reprocess", entity_type="claim", entity_id=claim.id, user_id=user.id, request=request)
    db.commit()
    intake.enqueue_processing(claim.id)
    return _detail(db, claim.id)
