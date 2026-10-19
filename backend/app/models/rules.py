import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.claim import Claim, Money
from app.models.enums import ReviewAction, RuleLayer, RuleMethod, RuleStatus, Severity, db_enum


class Rule(TimestampMixin, Base):
    """One check in the rule library. Stored as data so it can be reviewed and tuned without a release."""

    __tablename__ = "rules"
    __table_args__ = (CheckConstraint("weight BETWEEN 0 AND 100", name="weight_range"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # e.g. "B-04"
    name: Mapped[str] = mapped_column(String(200))
    layer: Mapped[RuleLayer] = mapped_column(db_enum(RuleLayer, "rule_layer"), index=True)
    description: Mapped[str] = mapped_column(Text)
    method: Mapped[RuleMethod] = mapped_column(db_enum(RuleMethod, "rule_method"))
    max_severity: Mapped[Severity] = mapped_column(db_enum(Severity, "severity"))
    weight: Mapped[int] = mapped_column(Integer, default=10)
    fraud_type: Mapped[str | None] = mapped_column(String(80))
    source_title: Mapped[str | None] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[RuleStatus] = mapped_column(
        db_enum(RuleStatus, "rule_status"), default=RuleStatus.DRAFT, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # thresholds etc.

    findings: Mapped[list["Finding"]] = relationship(back_populates="rule")


class Finding(Base):
    """A flag raised by a rule on one claim, with the evidence behind it."""

    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint("confidence IS NULL OR (confidence BETWEEN 0 AND 1)", name="confidence_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("rules.id", ondelete="RESTRICT"), index=True)
    rule_version: Mapped[int] = mapped_column(Integer)
    severity: Mapped[Severity] = mapped_column(db_enum(Severity, "severity"))
    title: Mapped[str] = mapped_column(String(300))
    explanation: Mapped[str] = mapped_column(Text)
    # [{"page_id": "...", "bbox": {...}, "quote": "CT abdomen ₹6,500"}]
    evidence: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    amount_at_stake: Mapped[Decimal | None] = mapped_column(Money)
    suggested_deduction: Mapped[Decimal | None] = mapped_column(Money)
    confidence: Mapped[float | None] = mapped_column(Float)
    needs_medical_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    model_version: Mapped[str | None] = mapped_column(String(80))  # set for AI findings
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    claim: Mapped[Claim] = relationship(back_populates="findings")
    rule: Mapped[Rule] = relationship(back_populates="findings")
    reviews: Mapped[list["FindingReview"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", passive_deletes=True
    )


class FindingReview(Base):
    """A reviewer confirming or dismissing a finding. Kept as history, never overwritten."""

    __tablename__ = "finding_reviews"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[ReviewAction] = mapped_column(db_enum(ReviewAction, "review_action"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    finding: Mapped[Finding] = relationship(back_populates="reviews")