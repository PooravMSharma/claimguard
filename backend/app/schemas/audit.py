import uuid
from datetime import datetime

from pydantic import BaseModel


class AuditEntryOut(BaseModel):
    id: int
    at: datetime
    user_id: uuid.UUID | None
    user_email: str | None
    action: str
    entity_type: str
    entity_id: str | None
    details: dict
    ip_address: str | None


class AuditPage(BaseModel):
    items: list[AuditEntryOut]
    total: int
    limit: int
    offset: int
