"""The structured claim record, filled from the documents.

Every row points back to the page it came from (and its position on the page),
so each finding can show the exact evidence.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
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


class ExtractedField(SourceMixin, Base):
    """A single key value, e.g. admission_date or doctor_registration_no."""

    __tablename__ = "extracted_fields"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(String(50))  # patient, policy, hospital, encounter, ...
    field_name: Mapped[str] = mapped_column(String(80))
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

    claim: Mapped[Claim] = relationship(back_populates="procedures")


class Investigation(SourceMixin, Base):
    """A test or scan: billed, reported, or both. has_report=False and billed=True is a red flag."""

    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    test_name: Mapped[str] = mapped_column(String(255))
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
    mrp: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal | None] = mapped_column(Money)
    in_medication_chart: Mapped[bool | None] = mapped_column(Boolean)

    claim: Mapped[Claim] = relationship(back_populates="medications")