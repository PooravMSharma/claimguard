"""The structured claim record, filled from the documents.

Every row points back to the page it came from (and its position on the page),
so each finding can show the exact evidence.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.claim import Claim, Money
from app.models.enums import BillCategory, db_enum


class SourceMixin:
    """Where a value was read from: page, box on the page, and how sure we are."""

    source_page_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_pages.id", ondelete="SET NULL")
    )
    bbox: Mapped[dict | None] = mapped_column(JSONB)  # {"x":..,"y":..,"w":..,"h":..} in page fractions
    confidence: Mapped[float | None] = mapped_column(Float)


class ExtractedDocument(Base):
    """One document found in the claim (a bill, a lab report...) and everything read from it."""

    __tablename__ = "extracted_documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(60))            # "final_bill", "lab_report:2"
    doc_type: Mapped[str] = mapped_column(String(40))
    page_ids: Mapped[list] = mapped_column(JSONB, default=list)
    quality: Mapped[str | None] = mapped_column(String(20))
    ai_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    missing: Mapped[list] = mapped_column(JSONB, default=list)   # required items still not found
    data: Mapped[dict] = mapped_column(JSONB, default=dict)      # {"fields": {...}, "tables": {...}}


class ExtractedField(SourceMixin, Base):
    """A single key value, e.g. admission_date or doctor_registration_no."""

    __tablename__ = "extracted_fields"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(String(50))  # document type: claim_form, final_bill, ...
    document_key: Mapped[str | None] = mapped_column(String(60), index=True)
    field_name: Mapped[str] = mapped_column(String(80))
    method: Mapped[str] = mapped_column(String(10), default="rule", server_default="rule")  # rule | ai | reviewer
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    value_text: Mapped[str | None] = mapped_column(Text)
    value_normalized: Mapped[dict | None] = mapped_column(JSONB)
    corrected_value: Mapped[str | None] = mapped_column(Text)
    corrected_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    claim: Mapped[Claim] = relationship(back_populates="extracted_fields")


class BillLineItem(SourceMixin, Base):
    __tablename__ = "bill_line_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int | None] = mapped_column(Integer)
    service_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str] = mapped_column(String(500))
    category: Mapped[BillCategory] = mapped_column(
        db_enum(BillCategory, "bill_category"), default=BillCategory.OTHER
    )
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit_rate: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal] = mapped_column(Money)
    package_code: Mapped[str | None] = mapped_column(String(32))
    category_text: Mapped[str | None] = mapped_column(String(80))   # the category as printed on the bill
    method: Mapped[str] = mapped_column(String(10), default="rule", server_default="rule")

    claim: Mapped[Claim] = relationship(back_populates="bill_line_items")


class Diagnosis(SourceMixin, Base):
    __tablename__ = "diagnoses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(String(500))
    icd10_code: Mapped[str | None] = mapped_column(String(10), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source_document: Mapped[str | None] = mapped_column(String(40))  # preauth, discharge_summary, ...

    claim: Mapped[Claim] = relationship(back_populates="diagnoses")


class Procedure(SourceMixin, Base):
    __tablename__ = "procedures"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(String(500))
    package_code: Mapped[str | None] = mapped_column(String(32), index=True)
    performed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    surgeon: Mapped[str | None] = mapped_column(String(200))
    source_document: Mapped[str | None] = mapped_column(String(40))  # claim_form, discharge_summary, ot_notes, ...

    claim: Mapped[Claim] = relationship(back_populates="procedures")


class Investigation(SourceMixin, Base):
    """A test or scan: billed, reported, or both. has_report=False and billed=True is a red flag."""

    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    test_name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str | None] = mapped_column(String(10))   # lab | imaging
    performed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    results: Mapped[dict | None] = mapped_column(JSONB)  # [{"name","value","unit","range"}]
    lab_name: Mapped[str | None] = mapped_column(String(255))
    billed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    has_report: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    claim: Mapped[Claim] = relationship(back_populates="investigations")


class Medication(SourceMixin, Base):
    __tablename__ = "medications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    drug_name: Mapped[str] = mapped_column(String(255))
    strength: Mapped[str | None] = mapped_column(String(80))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    batch_no: Mapped[str | None] = mapped_column(String(64))
    expiry: Mapped[str | None] = mapped_column(String(20))
    mrp: Mapped[Decimal | None] = mapped_column(Money)
    unit_rate: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal | None] = mapped_column(Money)
    in_medication_chart: Mapped[bool | None] = mapped_column(Boolean)

    claim: Mapped[Claim] = relationship(back_populates="medications")


class AiCall(Base):
    """Every call to the AI provider: what for, which model, tokens and estimated cost."""

    __tablename__ = "ai_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(80))         # e.g. "extract:final_bill"
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 5), default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10))          # ok | error
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiCheckCache(Base):
    """Answers of the AI reviewer, keyed by a hash of (model, question, claim facts).

    Re-running the checks on an unchanged claim reuses the answer instead of asking the model again (a local
    model takes a minute or more per claim). Any change to the facts, the question or the model is a new key.
    """

    __tablename__ = "ai_check_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
