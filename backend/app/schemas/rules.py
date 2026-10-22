from datetime import datetime

from pydantic import BaseModel, Field


class RuleStatsOut(BaseModel):
    days: int
    findings: int
    reviewed: int
    confirmed: int
    dismissed: int
    dismissal_rate: float | None
    needs_retuning: bool


class RuleOut(BaseModel):
    id: str
    name: str
    layer: str
    method: str
    max_severity: str
    designed_max_severity: str         # the catalogue's ceiling: max_severity can be lowered, not raised above it
    weight: int
    status: str
    allowed_statuses: list[str]
    version: int
    config: dict
    default_config: dict
    source_title: str | None
    source_url: str | None
    updated_at: datetime | None
    stats: RuleStatsOut


class RuleChangeOut(BaseModel):
    version: int
    changes: dict
    reason: str
    clinician: str | None
    changed_by: str | None
    created_at: datetime


class DismissalOut(BaseModel):
    claim_number: str
    reason: str
    created_at: datetime


class RuleDetail(RuleOut):
    description: str
    history: list[RuleChangeOut]
    dismissals: list[DismissalOut]


class RuleUpdate(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    status: str | None = None
    weight: int | None = Field(default=None, ge=0, le=100)
    max_severity: str | None = None
    config: dict | None = None
    source_title: str | None = Field(default=None, max_length=300)
    source_url: str | None = Field(default=None, max_length=500)
    clinician: str | None = Field(default=None, max_length=200)
