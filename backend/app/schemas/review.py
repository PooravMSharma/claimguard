import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

RecommendationValue = Literal["approve", "approve_with_deductions", "query_hospital", "escalate", "refer_siu"]


class ScorePartOut(BaseModel):
    rule_id: str
    name: str
    severity: str
    weight: int
    medical: bool
    findings: int


class PersonOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str


class DeductionLine(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    finding_id: uuid.UUID | None = None


class DecisionIn(BaseModel):
    recommendation: RecommendationValue
    deductions: list[DeductionLine] = Field(default_factory=list, max_length=100)
    reason: str = Field(min_length=1, max_length=4000)


class DecisionOut(BaseModel):
    id: uuid.UUID
    recommendation: str
    approved_amount: Decimal | None
    deductions: list[dict]
    reason: str
    decided_by: str | None
    created_at: datetime


class ReviewSummary(BaseModel):
    claim_id: uuid.UUID
    claim_number: str
    status: str
    stage: str
    source: str
    patient_name: str | None
    age: int | None
    gender: str | None
    policy_number: str | None
    sum_insured: Decimal | None
    hospital_name: str | None
    rohini_id: str | None
    doctor: str | None
    diagnosis: str | None
    icd_code: str | None
    procedures: list[str]
    admission_at: datetime | None
    discharge_at: datetime | None
    stay_nights: int | None
    claimed_amount: Decimal | None
    amount_at_stake: Decimal
    suggested_deduction: Decimal
    suggested_payable: Decimal | None
    score: int | None
    band: str | None
    band_reason: str
    suggested_action: str
    score_parts: list[ScorePartOut]
    dismissed_findings: int
    open_findings: int
    open_blocking: int                 # open red / amber findings: approval waits for them
    sla_due_at: datetime | None
    assignee: PersonOut | None
    escalated: bool                    # latest recommendation is "escalate": a senior reviewer decides
    decisions: list[DecisionOut]
    approve_blockers: list[str]        # why the current user cannot approve yet (empty: can approve)
    can_decide: bool


class AssignIn(BaseModel):
    user_id: uuid.UUID | None = None   # None: unassign


class LetterOut(BaseModel):
    subject: str
    body: str
    documents: list[str]
    clarifications: list[str]
