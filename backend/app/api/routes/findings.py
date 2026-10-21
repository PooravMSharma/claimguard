"""Findings raised by the checks, their reviews, every check's run status, and the rules list."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_permission
from app.api.routes.review import mark_in_review
from app.core.permissions import Permission, has_permission
from app.db.session import get_db
from app.models.claim import Claim
from app.models.enums import ClaimStatus, ReviewAction
from app.models.rules import CheckRun, Finding, FindingReview, Rule
from app.models.user import User
from app.review.scoring import latest_status
from app.rules.catalogue import CHECKS, SEVERITY_ORDER
from app.schemas.findings import (
    CheckRunOut,
    ChecksResponse,
    EvidenceOut,
    FindingOut,
    FindingsResponse,
    FindingsSummary,
    ReviewIn,
    ReviewOut,
)
from app.schemas.findings import RuleOut as RuleSchema
from app.services.audit import log_event

router = APIRouter(tags=["findings"])
can_view = require_permission(Permission.CLAIMS_VIEW)
can_view_rules = require_permission(Permission.RULES_VIEW)
LAYER_ORDER = ["completeness", "forensics", "consistency", "medical", "billing", "policy", "registry"]


def can_review(user: User = Depends(get_current_user)) -> User:
    """Adjudicators and senior reviewers review findings; fraud investigators too."""
    role = user.role.name
    if not (has_permission(role, Permission.CLAIMS_REVIEW) or has_permission(role, Permission.CLAIMS_INVESTIGATE)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this")
    return user


def _claim(db: Session, claim_id: uuid.UUID) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


def _finding_out(f: Finding, names: dict[uuid.UUID, str]) -> FindingOut:
    reviews = sorted(f.reviews, key=lambda r: r.created_at)
    check = CHECKS.get(f.rule_id)
    return FindingOut(
        id=f.id, rule_id=f.rule_id, rule_name=check.name if check else f.rule_id,
        layer=check.layer if check else "", severity=f.severity.value, title=f.title, explanation=f.explanation,
        evidence=[EvidenceOut(**e) for e in (f.evidence or [])], amount_at_stake=f.amount_at_stake,
        suggested_deduction=f.suggested_deduction, confidence=f.confidence,
        needs_medical_review=f.needs_medical_review, model_version=f.model_version, is_current=f.is_current,
        status=latest_status(reviews), created_at=f.created_at,
        reviews=[ReviewOut(id=r.id, action=r.action.value, reason=r.reason, created_at=r.created_at,
                           reviewer=names.get(r.reviewer_id) if r.reviewer_id else None) for r in reviews])


def _sort_key(f: FindingOut):
    return (not f.is_current, -SEVERITY_ORDER.get(f.severity, 0),
            LAYER_ORDER.index(f.layer) if f.layer in LAYER_ORDER else 99, f.rule_id)


@router.get("/claims/{claim_id}/findings", response_model=FindingsResponse)
def findings(claim_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(can_view)) -> FindingsResponse:
    _claim(db, claim_id)
    rows = db.scalars(select(Finding).where(Finding.claim_id == claim_id)
                      .options(selectinload(Finding.reviews))).all()
    reviewer_ids = {r.reviewer_id for f in rows for r in f.reviews if r.reviewer_id}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(reviewer_ids)))} \
        if reviewer_ids else {}
    out = sorted((_finding_out(f, names) for f in rows), key=_sort_key)
    summary = FindingsSummary()
    for f in out:
        if not f.is_current:
            continue
        setattr(summary, f.severity, getattr(summary, f.severity) + 1)
        summary.open += f.status == "open"
        if f.status != "dismissed":
            summary.amount_at_stake += f.amount_at_stake or Decimal(0)
            summary.suggested_deduction += f.suggested_deduction or Decimal(0)
    return FindingsResponse(claim_id=claim_id, summary=summary, findings=out)


@router.get("/claims/{claim_id}/checks", response_model=ChecksResponse)
def checks(claim_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(can_view)) -> ChecksResponse:
    _claim(db, claim_id)
    runs = {r.rule_id: r for r in db.scalars(select(CheckRun).where(CheckRun.claim_id == claim_id))}
    out, counts = [], {}
    for rule_id, c in CHECKS.items():
        run = runs.get(rule_id)
        state = run.status if run else "not_run"
        counts[state] = counts.get(state, 0) + 1
        out.append(CheckRunOut(rule_id=rule_id, name=c.name, layer=c.layer, method=c.method,
                               max_severity=c.max_severity, status=state, detail=run.detail if run else None,
                               finding_count=run.finding_count if run else 0))
    ran = max((r.created_at for r in runs.values()), default=None)
    return ChecksResponse(claim_id=claim_id, ran_at=ran, counts=counts, checks=out)


@router.post("/findings/{finding_id}/review", response_model=FindingOut)
def review(finding_id: uuid.UUID, body: ReviewIn, request: Request, db: Session = Depends(get_db),
           user: User = Depends(can_review)) -> FindingOut:
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    claim = _claim(db, finding.claim_id)
    if claim.status == ClaimStatus.DECIDED:
        raise HTTPException(status_code=409, detail="The claim is already decided")
    reason = (body.reason or "").strip() or None
    if body.action == "dismissed" and not reason:
        raise HTTPException(status_code=422, detail="Give a reason for dismissing a finding")
    if body.action == "noted" and not reason:
        raise HTTPException(status_code=422, detail="Write the note")
    if (body.action != "noted" and finding.needs_medical_review
            and not has_permission(user.role.name, Permission.CLAIMS_REVIEW_ESCALATED)):
        raise HTTPException(status_code=403, detail="A medical-necessity finding is settled by a senior / medical "
                                                    "reviewer. Add a note, or escalate the claim.")
    db.add(FindingReview(finding_id=finding.id, reviewer_id=user.id, action=ReviewAction(body.action), reason=reason))
    if body.action != "noted":
        db.flush()
        db.refresh(finding)
        mark_in_review(db, claim)
    log_event(db, f"finding.{body.action}", entity_type="finding", entity_id=finding.id, user_id=user.id,
              request=request, details={"claim_id": str(claim.id), "rule_id": finding.rule_id, "reason": reason})
    db.commit()
    db.refresh(finding)
    return _finding_out(finding, {user.id: user.full_name})


@router.post("/claims/{claim_id}/run-checks", response_model=ChecksResponse)
def run_checks(claim_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
               user: User = Depends(can_review)) -> ChecksResponse:
    """Re-run the checks on the current claim record (after corrections), without re-reading the pages."""
    from app.rules.store import run_for_claim

    claim = _claim(db, claim_id)
    if claim.status in (ClaimStatus.RECEIVED, ClaimStatus.PROCESSING):
        raise HTTPException(status_code=409, detail="The claim is still being processed")
    if claim.status == ClaimStatus.DECIDED:
        raise HTTPException(status_code=409, detail="The claim is already decided")
    counts = run_for_claim(db, claim)
    log_event(db, "claim.checks_run", entity_type="claim", entity_id=claim.id, user_id=user.id, request=request,
              details={**counts, "via": "manual re-run"})
    db.commit()
    return checks(claim_id, db, user)


@router.get("/rules", response_model=list[RuleSchema])
def rules(db: Session = Depends(get_db), _: User = Depends(can_view_rules)) -> list[RuleSchema]:
    from app.rules.store import sync_rules

    sync_rules(db)
    db.commit()
    return [RuleSchema(id=r.id, name=r.name, layer=r.layer.value, method=r.method.value,
                       max_severity=r.max_severity.value, weight=r.weight, status=r.status.value, version=r.version,
                       config=r.config or {})
            for r in db.scalars(select(Rule).order_by(Rule.id))]
