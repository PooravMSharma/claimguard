import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.permissions import Permission
from app.db.session import get_db
from app.models.user import AuditLog, User
from app.schemas.audit import AuditEntryOut, AuditPage

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditPage)
def list_audit(
    action: str | None = Query(default=None, max_length=80),
    user_id: uuid.UUID | None = None,
    entity_type: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(Permission.AUDIT_VIEW)),
) -> AuditPage:
    filters = []
    if action:
        filters.append(AuditLog.action.startswith(action))
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)

    total = db.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    rows = db.execute(
        select(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(*filters)
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    items = [
        AuditEntryOut(
            id=entry.id, at=entry.at, user_id=entry.user_id, user_email=email, action=entry.action,
            entity_type=entry.entity_type, entity_id=entry.entity_id, details=entry.details,
            ip_address=entry.ip_address,
        )
        for entry, email in rows
    ]
    return AuditPage(items=items, total=total, limit=limit, offset=offset)