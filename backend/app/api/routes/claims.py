"""Claims: upload, queue, detail, page images, original files, reprocessing."""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.permissions import Permission
from app.db.session import get_db
from app.models.claim import Claim, ClaimDocument, DocumentPage
from app.models.enums import ClaimStage, ClaimStatus
from app.models.user import User
from app.schemas.claims import ClaimDetail, ClaimPage, ClaimRow, DocumentOut, UploadResult
from app.services import intake
from app.services.audit import log_event
from app.services.storage import StorageError

router = APIRouter(prefix="/claims", tags=["claims"])
can_view = require_permission(Permission.CLAIMS_VIEW)
can_upload = require_permission(Permission.CLAIMS_UPLOAD)

CHUNK = 1024 * 1024


def _value(v) -> str | None:
    return None if v is None else getattr(v, "value", v)


def _row(claim: Claim, documents: int, pages: int) -> dict:
    return {
        "id": claim.id, "claim_number": claim.claim_number, "stage": _value(claim.stage),
        "status": _value(claim.status), "source": claim.source, "policy_number": claim.policy_number,
        "member_name": claim.member_name, "hospital_name": claim.hospital_name,
        "claimed_amount": claim.claimed_amount, "risk_band": _value(claim.risk_band),
        "risk_score": claim.risk_score, "documents": documents, "pages": pages, "created_at": claim.created_at,
        "processing_error": claim.processing_error,
    }


def _detail(db: Session, claim_id: uuid.UUID) -> ClaimDetail:
    claim = db.scalar(select(Claim).where(Claim.id == claim_id)
                      .options(selectinload(Claim.documents).selectinload(ClaimDocument.pages)))
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    files = sorted(claim.documents, key=lambda d: d.uploaded_at)
    creator = db.get(User, claim.created_by_id) if claim.created_by_id else None
    return ClaimDetail(
        **_row(claim, len(files), sum(d.page_count or 0 for d in files)),
        created_by=creator.full_name if creator else None,
        processing_started_at=claim.processing_started_at, processing_finished_at=claim.processing_finished_at,
        files=[DocumentOut.model_validate(d) for d in files],
    )


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
    q: str | None = Query(default=None, max_length=100, description="Claim no., policy no. or patient name"),
    status_filter: ClaimStatus | None = Query(default=None, alias="status"),
    stage: ClaimStage | None = None,
    source: str | None = Query(default=None, pattern="^(upload|synthetic)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(can_view),
) -> ClaimPage:
    filters = []
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(Claim.claim_number.ilike(like), Claim.policy_number.ilike(like),
                           Claim.member_name.ilike(like)))
    if stage:
        filters.append(Claim.stage == stage)
    if source:
        filters.append(Claim.source == source)
    counts: dict[str, int] = {
        _value(s) or "": n
        for s, n in db.execute(select(Claim.status, func.count()).where(*filters).group_by(Claim.status)).tuples()
    }
    if status_filter:
        filters.append(Claim.status == status_filter)
    total = db.scalar(select(func.count()).select_from(Claim).where(*filters)) or 0

    docs = (select(ClaimDocument.claim_id, func.count().label("documents"),
                   func.coalesce(func.sum(ClaimDocument.page_count), 0).label("pages"))
            .group_by(ClaimDocument.claim_id).subquery())
    rows = db.execute(
        select(Claim, func.coalesce(docs.c.documents, 0), func.coalesce(docs.c.pages, 0))
        .outerjoin(docs, docs.c.claim_id == Claim.id)
        .where(*filters)
        .order_by(Claim.created_at.desc(), Claim.claim_number.desc())
        .limit(limit).offset(offset)
    ).all()
    return ClaimPage(items=[ClaimRow(**_row(c, d, p)) for c, d, p in rows], total=total, limit=limit,
                     offset=offset, counts=counts)


@router.get("/{claim_id}", response_model=ClaimDetail)
def get_claim(claim_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(can_view)) -> ClaimDetail:
    return _detail(db, claim_id)


@router.get("/{claim_id}/pages/{page_id}/image")
def page_image(claim_id: uuid.UUID, page_id: uuid.UUID, db: Session = Depends(get_db),
               _: User = Depends(can_view)) -> Response:
    page = db.scalar(select(DocumentPage).join(ClaimDocument)
                     .where(DocumentPage.id == page_id, ClaimDocument.claim_id == claim_id))
    if page is None or not page.image_key:
        raise HTTPException(status_code=404, detail="Page not found")
    try:
        data = intake.storage().get(page.image_key)
    except StorageError:
        raise HTTPException(status_code=404, detail="Page image missing; reprocess the claim")
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


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