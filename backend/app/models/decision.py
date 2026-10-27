import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.claim import Claim, Money
from app.models.enums import Recommendation, db_enum


class Decision(Base):
    """The human reviewer's outcome for a claim. A claim can have several (e.g. after a query)."""

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    recommendation: Mapped[Recommendation] = mapped_column(db_enum(Recommendation, "recommendation"))
    approved_amount: Mapped[Decimal | None] = mapped_column(Money)
    # [{"description": "...", "amount": 1200.00, "finding_id": "..."}]
    deductions: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    claim: Mapped[Claim] = relationship(back_populates="decisions")
