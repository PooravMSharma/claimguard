import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:  # imported for type hints only; at runtime SQLAlchemy resolves the names
    from app.models.decision import Decision
    from app.models.extraction import (
        BillLineItem,
        Diagnosis,
        ExtractedField,
        Investigation,
        Medication,
        Procedure,
    )
    from app.models.rules import Finding
from app.models.enums import (
    ClaimStage,
    ClaimStatus,
    DocumentType,
    Gender,
    RiskBand,
    db_enum,
)

Money = Numeric(14, 2)


class Claim(TimestampMixin, Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("risk_score IS NULL OR (risk_score BETWEEN 0 AND 100)", name="risk_score_range"),
        CheckConstraint("claimed_amount IS NULL OR claimed_amount >= 0", name="claimed_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    stage: Mapped[ClaimStage] = mapped_column(db_enum(ClaimStage, "claim_stage"))
    status: Mapped[ClaimStatus] = mapped_column(
        db_enum(ClaimStatus, "claim_status"), default=ClaimStatus.RECEIVED, index=True
    )

    # Patient and policy (filled by extraction, editable by reviewers)
    policy_number: Mapped[str | None] = mapped_column(String(64), index=True)
    member_name: Mapped[str | None] = mapped_column(String(200))
    member_age: Mapped[int | None] = mapped_column(Integer)
    member_gender: Mapped[Gender | None] = mapped_column(db_enum(Gender, "gender"))
    policy_start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sum_insured: Mapped[Decimal | None] = mapped_column(Money)

    # Hospital and treating doctor
    hospital_name: Mapped[str | None] = mapped_column(String(255))
    hospital_rohini_id: Mapped[str | None] = mapped_column(String(32), index=True)
    hospital_city: Mapped[str | None] = mapped_column(String(100))
    treating_doctor: Mapped[str | None] = mapped_column(String(200))
    doctor_registration_no: Mapped[str | None] = mapped_column(String(64))

    # Encounter
    admission_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discharge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Money and result
    claimed_amount: Mapped[Decimal | None] = mapped_column(Money)
    suggested_payable: Mapped[Decimal | None] = mapped_column(Money)
    risk_score: Mapped[int | None] = mapped_column(Integer)
    risk_band: Mapped[RiskBand | None] = mapped_column(db_enum(RiskBand, "risk_band"), index=True)

    # Workflow
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_error: Mapped[str | None] = mapped_column(Text)

    documents: Mapped[list["ClaimDocument"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )
    extracted_fields: Mapped[list["ExtractedField"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )
    bill_line_items: Mapped[list["BillLineItem"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )
    diagnoses: Mapped[list["Diagnosis"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )
    procedures: Mapped[list["Procedure"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )
    investigations: Mapped[list["Investigation"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )
    medications: Mapped[list["Medication"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )
    decisions: Mapped[list["Decision"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )


class ClaimDocument(Base):
    """One uploaded file. The original is kept unchanged in storage."""

    __tablename__ = "claim_documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    claim: Mapped[Claim] = relationship(back_populates="documents")
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentPage.page_number",
    )


class DocumentPage(Base):
    """One page of a document, with its detected type and OCR text."""

    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_pages_document_page"),
        CheckConstraint("page_number >= 1", name="page_number_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claim_documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    doc_type: Mapped[DocumentType] = mapped_column(
        db_enum(DocumentType, "document_type"), default=DocumentType.UNKNOWN
    )
    doc_type_confidence: Mapped[float | None] = mapped_column(Float)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    quality_score: Mapped[float | None] = mapped_column(Float)
    image_key: Mapped[str | None] = mapped_column(String(500))
    image_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    document: Mapped[ClaimDocument] = relationship(back_populates="pages")