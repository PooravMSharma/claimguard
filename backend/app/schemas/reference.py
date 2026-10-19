from datetime import date, datetime
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class ImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset: str
    version: str
    source_name: str
    source_url: str | None
    effective_from: date | None
    file_name: str | None
    file_sha256: str | None
    row_count: int
    notes: str | None
    imported_at: datetime


class DatasetSummary(BaseModel):
    dataset: str
    label: str
    rows: int
    latest_import: ImportOut | None


class PackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    version: str
    name: str
    specialty: str | None
    rate: Decimal | None
    expected_los_days: int | None
    is_day_care: bool | None
    mandatory_documents: list
    inclusions: list
    effective_from: date | None


class TariffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    code: str
    name: str
    category: str | None
    specialty: str | None
    city_tier: str
    nabh_accredited: bool | None
    super_speciality: bool
    ward_type: str
    ward_uniform: bool
    rate: Decimal
    effective_from: date
    effective_to: date | None


class AdjustmentOut(BaseModel):
    step: str
    factor: str
    note: str


class PriceOut(BaseModel):
    source: str
    code: str
    name: str
    category: str | None
    city_tier: str
    grade: str
    ward_type: str
    ward_uniform: bool
    base_rate: Decimal
    base_grade: str
    base_city_tier: str
    rate: Decimal
    effective_from: date
    adjustments: list[AdjustmentOut]
    source_name: str | None
    source_url: str | None


class NonPayableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    list_number: int
    item_name: str
    synonyms: list
    notes: str | None


class MatchRequest(BaseModel):
    lines: list[str] = Field(min_length=1, max_length=500)


class MatchOut(BaseModel):
    line: str
    matched: bool
    list_number: int | None = None
    item_name: str | None = None
    matched_term: str | None = None
    method: str | None = None
    score: float | None = None
    notes: str | None = None


class IcdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    description: str
    chapter: str | None
    category: str | None
    is_billable: bool


class IcdLookupOut(BaseModel):
    input: str
    code: str | None
    valid_format: bool
    match_level: str
    description: str | None
    category: str | None
    category_description: str | None
    chapter: str | None
    is_billable: bool | None


class PolicyTermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_code: str
    key: str
    value: dict
    description: str | None
    source: str | None


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
