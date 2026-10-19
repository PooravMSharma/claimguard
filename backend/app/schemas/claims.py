import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page_number: int
    doc_type: str
    doc_type_confidence: float | None
    width: int | None
    height: int | None
    has_text_layer: bool
    image_hash: str | None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    page_count: int | None
    uploaded_at: datetime
    duplicate_of_id: uuid.UUID | None
    pages: list[PageOut]


class ClaimRow(BaseModel):
    id: uuid.UUID
    claim_number: str
    stage: str
    status: str
    source: str
    policy_number: str | None
    member_name: str | None
    hospital_name: str | None
    claimed_amount: Decimal | None
    risk_band: str | None
    risk_score: int | None
    documents: int
    pages: int
    created_at: datetime
    processing_error: str | None


class ClaimDetail(ClaimRow):
    created_by: str | None
    processing_started_at: datetime | None
    processing_finished_at: datetime | None
    files: list[DocumentOut]


class ClaimPage(BaseModel):
    items: list[ClaimRow]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]


class UploadResult(BaseModel):
    claim: ClaimDetail
    warnings: list[str]