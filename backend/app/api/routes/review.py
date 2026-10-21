"""Claim review: the summary at the top of the review screen, assignment, the decision panel, query letters.

Who may do what:
- Adjudicators and senior reviewers record recommendations (claims.decide).
- Approving needs every red and amber finding reviewed first (confirmed or dismissed); the other
  recommendations (query, escalate, refer to investigation) can be made at any time.
- Once a claim is escalated, only a senior reviewer (claims.review_escalated) records the next recommendation.
- A team lead assigns claims to anyone who reviews; a reviewer can take an unassigned claim or hand it back.
- Approve and approve-with-deductions close the claim (status decided); the others keep it in review.
Nothing is ever rejected automatically: the system only recommends.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_permission
from app.core.permissions import Permission, has_permission
from app.db.session import get_db
from app.models.claim import Claim
from app.models.decision import Decision
from app.models.enums import ClaimStatus, Recommendation
from app.models.extraction import Diagnosis, Procedure
from app.models.rules import CheckRun, Finding
from app.models.user import Role, User
from app.review import letters
from app.review.scoring import compute, items_for, score_claim, weights
from app.schemas.review import (
    AssignIn,
    DecisionIn,
    DecisionOut,
    LetterOut,
    PersonOut,
    ReviewSummary,
    ScorePartOut,
)
from app.services.audit import log_event

router = APIRouter(tags=["review"])
can_view = require_permission(Permission.CLAIMS_VIEW)
CLOSING = (Recommendation.APPROVE, Recommendation.APPROVE_WITH_DEDUCTIONS)
REVIEWABLE = (ClaimStatus.READY_FOR_REVIEW, ClaimStatus.IN_REVIEW)
DIAGNOSIS_SOURCES = ["discharge_summary", "claim_form", "preauth_form"]


def _claim(db: Session, claim_id: uuid.UUID) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


def _person(user: User | None) -> PersonOut | None:
    return PersonOut(id=user.id, name=user.full_name, role=user.role.name.value) if user else None


def _decisions(db: Session, claim_id: uuid.UUID) -> list[Decision]:
    return list(db.scalars(select(Decision).where(Decision.claim_id == claim_id).order_by(Decision.created_at)))


def _decision_out(d: Decision, names: dict[uuid.UUID, str]) -> DecisionOut:
    return DecisionOut(id=d.id, recommendation=d.recommendation.value, approved_amount=d.approved_amount,
                       deductions=d.deductions or [], reason=d.reason,
                       decided_by=names.get(d.decided_by_id) if d.decided_by_id else None, created_at=d.created_at)


def escalated(decisions: list[Decision]) -> bool:
    return bool(decisions) and decisions[-1].recommendation == Recommendation.ESCALATE


def approve_blockers(claim: Claim, user: User, open_blocking: int, decisions: list[Decision]) -> list[str]:
    """Why this user cannot approve the claim yet. Empty: approval is allowed."""
    role = user.role.name
    out = []
    if not has_permission(role, Permission.CLAIMS_DECIDE):
        out.append("Your role does not record recommendations.")
    if claim.status not in REVIEWABLE:
        out.append("The claim is not open for review." if claim.status != ClaimStatus.DECIDED
                   else "The claim is already decided.")
    if escalated(decisions) and not has_permission(role, Permission.CLAIMS_REVIEW_ESCALATED):
        out.append("The claim was escalated: a senior reviewer decides.")
    if open_blocking:
        out.append(f"{open_blocking} red or amber finding{'s' if open_blocking != 1 else ''} not reviewed yet: "
                   "confirm or dismiss each one first.")
    return out


def _can_decide(claim: Claim, user: User, decisions: list[Decision]) -> bool:
    role = user.role.name
    return (has_permission(role, Permission.CLAIMS_DECIDE) and claim.status in REVIEWABLE
            and (not escalated(decisions) or has_permission(role, Permission.CLAIMS_REVIEW_ESCALATED)))


@router.get("/claims/{claim_id}/review", response_model=ReviewSummary)
def summary(claim_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(can_view)) -> ReviewSummary:
    claim = _claim(db, claim_id)
    items = items_for(db, claim.id)
    result = compute(items, weights(db))
    decisions = _decisions(db, claim.id)
    names = {u.id: u.full_name for u in db.scalars(
        select(User).where(User.id.in_({d.decided_by_id for d in decisions if d.decided_by_id})))} \
        if decisions else {}
    diagnoses = list(db.scalars(select(Diagnosis).where(Diagnosis.claim_id == claim.id)))
    diagnoses.sort(key=lambda d: DIAGNOSIS_SOURCES.index(d.source_document)
                   if d.source_document in DIAGNOSIS_SOURCES else 99)
    procedures: list[str] = []
    for p in db.scalars(select(Procedure).where(Procedure.claim_id == claim.id)):
        if p.description and p.description.lower() not in (x.lower() for x in procedures):
            procedures.append(p.description)
    nights = None
    if claim.admission_at and claim.discharge_at:
        nights = (claim.discharge_at.date() - claim.admission_at.date()).days
    open_blocking = sum(1 for i in items if i.status == "open" and i.severity in ("red", "amber"))
    payable = (max(Decimal(0), claim.claimed_amount - result.suggested_deduction)
               if claim.claimed_amount is not None else None)
    assignee = db.get(User, claim.assigned_to_id) if claim.assigned_to_id else None
    checked = db.scalar(select(CheckRun.id).where(CheckRun.claim_id == claim.id).limit(1)) is not None
    return ReviewSummary(
        claim_id=claim.id, claim_number=claim.claim_number, status=claim.status.value, stage=claim.stage.value,
        source=claim.source, patient_name=claim.member_name, age=claim.member_age,
        gender=getattr(claim.member_gender, "value", None), policy_number=claim.policy_number,
        sum_insured=claim.sum_insured, hospital_name=claim.hospital_name, rohini_id=claim.hospital_rohini_id,
        doctor=claim.treating_doctor, diagnosis=diagnoses[0].description if diagnoses else None,
        icd_code=next((d.icd10_code for d in diagnoses if d.icd10_code), None), procedures=procedures,
        admission_at=claim.admission_at, discharge_at=claim.discharge_at, stay_nights=nights,
        claimed_amount=claim.claimed_amount, amount_at_stake=result.amount_at_stake,
        suggested_deduction=result.suggested_deduction, suggested_payable=payable,
        score=result.score if checked else None, band=result.band if checked else None,
        band_reason=result.reason, suggested_action=result.action,
        score_parts=[ScorePartOut(**vars(p)) for p in result.parts], dismissed_findings=result.dismissed,
        open_findings=sum(1 for i in items if i.status == "open"), open_blocking=open_blocking,
        sla_due_at=claim.sla_due_at, assignee=_person(assignee), escalated=escalated(decisions),
        decisions=[_decision_out(d, names) for d in decisions],
        approve_blockers=approve_blockers(claim, user, open_blocking, decisions),
        can_decide=_can_decide(claim, user, decisions))


@router.get("/review/assignees", response_model=list[PersonOut])
def assignees(db: Session = Depends(get_db), _: User = Depends(can_view)) -> list[PersonOut]:
    """Active users who review claims: who a claim can be assigned to."""
    users = db.scalars(select(User).join(Role).where(User.is_active.is_(True)).order_by(User.full_name)).all()
    return [p for u in users if has_permission(u.role.name, Permission.CLAIMS_REVIEW)
            and (p := _person(u)) is not None]


@router.post("/claims/{claim_id}/assign", response_model=ReviewSummary)
def assign(claim_id: uuid.UUID, body: AssignIn, request: Request, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> ReviewSummary:
    claim = _claim(db, claim_id)
    role = user.role.name
    lead = has_permission(role, Permission.CLAIMS_ASSIGN)
    if not lead:
        reviewer = has_permission(role, Permission.CLAIMS_REVIEW)
        taking = body.user_id == user.id and claim.assigned_to_id in (None, user.id)
        handing_back = body.user_id is None and claim.assigned_to_id == user.id
        if not (reviewer and (taking or handing_back)):
            raise HTTPException(status_code=403, detail="Only a team lead assigns claims to other people")
    target = None
    if body.user_id is not None:
        target = db.get(User, body.user_id)
        if target is None or not target.is_active or not has_permission(target.role.name, Permission.CLAIMS_REVIEW):
            raise HTTPException(status_code=422, detail="That user cannot review claims")
    if claim.status == ClaimStatus.DECIDED:
        raise HTTPException(status_code=409, detail="The claim is already decided")
    before = claim.assigned_to_id
    claim.assigned_to_id = target.id if target else None
    log_event(db, "claim.assigned", entity_type="claim", entity_id=claim.id, user_id=user.id, request=request,
              details={"from": str(before) if before else None, "to": str(target.id) if target else None})
    db.commit()
    return summary(claim_id, db, user)


@router.get("/claims/{claim_id}/query-letter", response_model=LetterOut)
def query_letter(claim_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(can_view)) -> LetterOut:
    claim = _claim(db, claim_id)
    findings = db.scalars(select(Finding).where(Finding.claim_id == claim.id)
                          .options(selectinload(Finding.reviews))).all()
    letter = letters.draft(claim, list(findings))
    return LetterOut(subject=letter.subject, body=letter.body, documents=letter.documents,
                     clarifications=letter.clarifications)


@router.post("/claims/{claim_id}/decisions", response_model=ReviewSummary, status_code=201)
def decide(claim_id: uuid.UUID, body: DecisionIn, request: Request, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> ReviewSummary:
    claim = _claim(db, claim_id)
    decisions = _decisions(db, claim.id)
    if not has_permission(user.role.name, Permission.CLAIMS_DECIDE):
        raise HTTPException(status_code=403, detail="Your role does not record recommendations")
    if claim.status == ClaimStatus.DECIDED:
        raise HTTPException(status_code=409, detail="The claim is already decided")
    if claim.status not in REVIEWABLE:
        raise HTTPException(status_code=409, detail="The claim is still being processed")
    if escalated(decisions) and not has_permission(user.role.name, Permission.CLAIMS_REVIEW_ESCALATED):
        raise HTTPException(status_code=403, detail="The claim was escalated: a senior reviewer decides")
    recommendation = Recommendation(body.recommendation)
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(status_code=422, detail="Give the reason for this recommendation")
    total = sum((d.amount for d in body.deductions), Decimal(0))
    if recommendation == Recommendation.APPROVE and body.deductions:
        raise HTTPException(status_code=422, detail="Use 'approve with deductions' when there are deductions")
    if recommendation == Recommendation.APPROVE_WITH_DEDUCTIONS and not body.deductions:
        raise HTTPException(status_code=422, detail="Add at least one deduction line")
    if recommendation not in CLOSING and body.deductions:
        raise HTTPException(status_code=422, detail="Deductions go with an approval only")
    if claim.claimed_amount is not None and total > claim.claimed_amount:
        raise HTTPException(status_code=422, detail="Deductions are more than the claimed amount")
    own = {f.id for f in db.scalars(select(Finding).where(Finding.claim_id == claim.id))}
    if any(d.finding_id and d.finding_id not in own for d in body.deductions):
        raise HTTPException(status_code=422, detail="A deduction refers to a finding of another claim")
    if recommendation in CLOSING:
        items = items_for(db, claim.id)
        blocking = sum(1 for i in items if i.status == "open" and i.severity in ("red", "amber"))
        if blocking:
            raise HTTPException(status_code=409, detail=f"{blocking} red or amber finding(s) not reviewed yet")
        if claim.claimed_amount is None:
            raise HTTPException(status_code=422, detail="The claimed amount is not known: correct the claim record")
    approved = (claim.claimed_amount - total) if recommendation in CLOSING and claim.claimed_amount is not None \
        else None
    lines = [{"description": d.description.strip(), "amount": str(d.amount),
              "finding_id": str(d.finding_id) if d.finding_id else None} for d in body.deductions]
    decision = Decision(claim_id=claim.id, decided_by_id=user.id, recommendation=recommendation,
                        approved_amount=approved, deductions=lines, reason=reason)
    db.add(decision)
    claim.status = ClaimStatus.DECIDED if recommendation in CLOSING else ClaimStatus.IN_REVIEW
    if recommendation in CLOSING:
        claim.suggested_payable = approved
    log_event(db, f"claim.{recommendation.value}", entity_type="claim", entity_id=claim.id, user_id=user.id,
              request=request, details={"recommendation": recommendation.value,
                                        "approved_amount": str(approved) if approved is not None else None,
                                        "deductions": lines, "reason": reason,
                                        "score": claim.risk_score,
                                        "band": claim.risk_band.value if claim.risk_band else None})
    db.commit()
    return summary(claim_id, db, user)


def mark_in_review(db: Session, claim: Claim) -> None:
    """A reviewer acted on the claim: it is now in review, and its score follows the review."""
    if claim.status == ClaimStatus.READY_FOR_REVIEW:
        claim.status = ClaimStatus.IN_REVIEW
    score_claim(db, claim)
