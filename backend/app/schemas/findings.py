import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class EvidenceOut(BaseModel):
    document: str | None = None
    page_id: str | None = None
    bbox: dict | None = None
    quote: str = ""


class ReviewOut(BaseModel):
    id: uuid.UUID
    action: str
    reason: str | None
    reviewer: str | None
    created_at: datetime


class FindingOut(BaseModel):
    id: uuid.UUID
    rule_id: str
    rule_name: str
    layer: str
    severity: str
    title: str
    explanation: str
    evidence: list[EvidenceOut]
    amount_at_stake: Decimal | None
    suggested_deduction: Decimal | None
    confidence: float | None
    needs_medical_review: bool
    model_version: str | None = None  # set when an AI model's judgement raised it
    is_current: bool
    status: str                      # open | confirmed | dismissed (latest review; notes do not change it)
    created_at: datetime
    reviews: list[ReviewOut]


class FindingsSummary(BaseModel):
    red: int = 0
    amber: int = 0
    info: int = 0
    open: int = 0
    amount_at_stake: Decimal = Decimal("0")
    suggested_deduction: Decimal = Decimal("0")


class FindingsResponse(BaseModel):
    claim_id: uuid.UUID
    summary: FindingsSummary
    findings: list[FindingOut]


class CheckRunOut(BaseModel):
    rule_id: str
    name: str
    layer: str
    method: str
    max_severity: str
    status: str
    detail: str | None
    finding_count: int


class ChecksResponse(BaseModel):
    claim_id: uuid.UUID
    ran_at: datetime | None
    counts: dict[str, int]
    checks: list[CheckRunOut]


class ReviewIn(BaseModel):
    action: Literal["confirmed", "dismissed", "noted"]
    reason: str | None = Field(default=None, max_length=2000)


class RuleOut(BaseModel):
    id: str
    name: str
    layer: str
    method: str
    max_severity: str
    weight: int
    status: str
    version: int
    config: dict
