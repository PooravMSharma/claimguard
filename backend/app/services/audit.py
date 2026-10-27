"""Write entries to the audit log. Never put passwords or tokens in `details`."""

import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.user import AuditLog


def client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


def log_event(
    db: Session,
    action: str,
    *,
    entity_type: str,
    entity_id: str | uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    details: dict | None = None,
    request: Request | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        user_id=user_id,
        details=details or {},
        ip_address=client_ip(request),
    )
    db.add(entry)
    return entry
