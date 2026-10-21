import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class FieldOut(BaseModel):
    id: uuid.UUID
    name: str
    value: str | None
    normalized: object = None
    corrected_value: str | None
    method: str
    confidence: float | None
    needs_review: bool
    page_id: uuid.UUID | None
    bbox: dict | None


class DocumentRecord(BaseModel):
    key: str
    doc_type: str
    page_ids: list[str]
    quality: str | None
    ai_used: bool
    missing: list[str]
    fields: list[FieldOut]
    tables: dict[str, list[dict]]


class BillLineOut(BaseModel):
    line_no: int | None
    service_date: datetime | None
    description: str
    category: str
    category_text: str | None
    quantity: Decimal | None
    unit_rate: Decimal | None
    amount: Decimal
    method: str
    page_id: uuid.UUID | None
    bbox: dict | None


class MedicationOut(BaseModel):
    drug_name: str
    quantity: Decimal | None
    batch_no: str | None
    expiry: str | None
    mrp: Decimal | None
    unit_rate: Decimal | None
    amount: Decimal | None
    in_medication_chart: bool | None
    page_id: uuid.UUID | None
    bbox: dict | None


class InvestigationOut(BaseModel):
    test_name: str
    kind: str | None
    performed_at: datetime | None
    billed: bool
    has_report: bool
    results: list | None
    page_id: uuid.UUID | None
    bbox: dict | None


class ItemOut(BaseModel):
    description: str
    source_document: str | None
    code: str | None = None
    performed_at: datetime | None = None
    page_id: uuid.UUID | None
    bbox: dict | None


class AiUsage(BaseModel):
    calls: int
    failed: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    models: list[str]


class ClaimRecord(BaseModel):
    summary: dict
    documents: list[DocumentRecord]
    bill_lines: list[BillLineOut]
    medications: list[MedicationOut]
    investigations: list[InvestigationOut]
    diagnoses: list[ItemOut]
    procedures: list[ItemOut]
    ai: AiUsage
    fields_needing_review: int


class FieldCorrection(BaseModel):
    value: str = Field(min_length=1, max_length=2000)