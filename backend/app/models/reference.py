"""Reference data: package rates, tariffs, non-payable items, diagnosis codes, policy terms.

Loaded in Step 4. Every rate carries an effective date so a claim is checked
against the rates in force on its admission date.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.claim import Money
from app.models.enums import CityTier, TariffSource, WardType, db_enum


class ReferenceImport(Base):
    """One load of a reference dataset: what was loaded, from where, valid from when.

    Every reference row points back to the import that created it, so any rate
    used in a claim decision can be traced to its official source document.
    """

    __tablename__ = "reference_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    # packages | tariffs | non_payables | icd10 | policy_terms
    dataset: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[str] = mapped_column(String(60))
    source_name: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(500))
    effective_from: Mapped[date | None] = mapped_column(Date)
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    imported_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _import_fk() -> Mapped[int | None]:
    return mapped_column(ForeignKey("reference_imports.id", ondelete="SET NULL"), index=True)


class Package(TimestampMixin, Base):
    """PM-JAY health benefit package (or a client's own package)."""

    __tablename__ = "packages"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_packages_code_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[str] = mapped_column(String(20))  # e.g. "HBP2022"
    name: Mapped[str] = mapped_column(String(500))
    specialty: Mapped[str | None] = mapped_column(String(120), index=True)
    rate: Mapped[Decimal | None] = mapped_column(Money)
    expected_los_days: Mapped[int | None] = mapped_column(Integer)
    inclusions: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    mandatory_documents: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    is_day_care: Mapped[bool | None] = mapped_column()
    effective_from: Mapped[date | None] = mapped_column(Date)
    import_id: Mapped[int | None] = _import_fk()


class TariffRate(TimestampMixin, Base):
    """Rate for a procedure, test or room under a tariff (CGHS, PM-JAY or a client's)."""

    __tablename__ = "tariff_rates"
    __table_args__ = (
        Index("ix_tariff_rates_lookup", "source", "code", "city_tier", "ward_type"),
        CheckConstraint("rate >= 0", name="rate_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[TariffSource] = mapped_column(db_enum(TariffSource, "tariff_source"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(80))
    specialty: Mapped[str | None] = mapped_column(String(120))
    city_tier: Mapped[CityTier] = mapped_column(db_enum(CityTier, "city_tier"), default=CityTier.ANY)
    nabh_accredited: Mapped[bool | None] = mapped_column()
    super_speciality: Mapped[bool] = mapped_column(default=False, server_default="false")
    # True for consultations, investigations, radiotherapy, day care and minor
    # procedures: CGHS keeps these the same whatever the ward entitlement.
    ward_uniform: Mapped[bool] = mapped_column(default=False, server_default="false")
    ward_type: Mapped[WardType] = mapped_column(db_enum(WardType, "ward_type"), default=WardType.ANY)
    rate: Mapped[Decimal] = mapped_column(Money)
    max_los_days: Mapped[int | None] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    import_id: Mapped[int | None] = _import_fk()


class NonPayableItem(TimestampMixin, Base):
    """IRDAI standard list. List 1 = never paid; lists 2-4 = must be absorbed in room/procedure/treatment."""

    __tablename__ = "non_payable_items"
    __table_args__ = (CheckConstraint("list_number BETWEEN 1 AND 4", name="list_number_range"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    list_number: Mapped[int] = mapped_column(Integer, index=True)
    item_name: Mapped[str] = mapped_column(String(255))
    synonyms: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    notes: Mapped[str | None] = mapped_column(Text)
    import_id: Mapped[int | None] = _import_fk()


class Icd10Code(Base):
    __tablename__ = "icd10_codes"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    description: Mapped[str] = mapped_column(String(500))
    chapter: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(10), index=True)  # 3-character category, e.g. "A00"
    is_billable: Mapped[bool] = mapped_column(default=True, server_default="true")
    import_id: Mapped[int | None] = _import_fk()


class PolicyTerm(TimestampMixin, Base):
    """A policy rule value, e.g. initial_waiting_days = 30. Standard IRDAI terms until a client supplies its own."""

    __tablename__ = "policy_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_code: Mapped[str] = mapped_column(String(64), default="STANDARD")
    key: Mapped[str] = mapped_column(String(80))
    value: Mapped[dict] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(300))
    import_id: Mapped[int | None] = _import_fk()

    __table_args__ = (UniqueConstraint("product_code", "key", name="uq_policy_terms_product_key"),)
