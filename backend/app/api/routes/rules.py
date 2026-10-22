"""Rule library: every check with its settings and how reviewers judge it; admins tune it (rules.manage)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.permissions import Permission
from app.db.session import get_db
from app.models.rules import Rule, RuleChange
from app.models.user import User
from app.rules import library
from app.rules.catalogue import CHECKS
from app.rules.defaults import default_config
from app.rules.store import sync_rules
from app.schemas.rules import DismissalOut, RuleChangeOut, RuleDetail, RuleOut, RuleStatsOut, RuleUpdate
from app.services.audit import log_event

router = APIRouter(prefix="/rules", tags=["rules"])
can_view = require_permission(Permission.RULES_VIEW)
can_manage = require_permission(Permission.RULES_MANAGE)


def _out(rule: Rule, stats: library.RuleStats, days: int) -> dict:
    check = CHECKS[rule.id]
    return dict(
        id=rule.id, name=rule.name, layer=check.layer, method=check.method, max_severity=rule.max_severity.value,
        designed_max_severity=check.max_severity, weight=rule.weight, status=rule.status.value,
        allowed_statuses=sorted(library.MEDICAL_STATUSES if library.is_medical(rule.id) else library.OTHER_STATUSES),
        version=rule.version, config={**default_config(rule.id), **(rule.config or {})},
        default_config=default_config(rule.id), source_title=rule.source_title, source_url=rule.source_url,
        updated_at=rule.updated_at,
        stats=RuleStatsOut(days=days, findings=stats.findings, reviewed=stats.reviewed, confirmed=stats.confirmed,
                           dismissed=stats.dismissed, dismissal_rate=stats.dismissal_rate,
                           needs_retuning=stats.needs_retuning))


def _rule(db: Session, rule_id: str) -> Rule:
    sync_rules(db)
    rule = db.get(Rule, rule_id.upper())
    if rule is None or rule.id not in CHECKS:
        raise HTTPException(status_code=404, detail="No such rule")
    return rule


@router.get("", response_model=list[RuleOut])
def rules(days: int = Query(default=90, ge=1, le=3650), db: Session = Depends(get_db),
          _: User = Depends(can_view)) -> list[RuleOut]:
    sync_rules(db)
    db.commit()
    stats = library.stats(db, days)
    return [RuleOut(**_out(r, stats.get(r.id, library.RuleStats()), days))
            for r in db.scalars(select(Rule).order_by(Rule.id)) if r.id in CHECKS]


def _detail(db: Session, rule: Rule, days: int) -> RuleDetail:
    stats = library.stats(db, days, rule.id).get(rule.id, library.RuleStats())
    changes = db.scalars(select(RuleChange).where(RuleChange.rule_id == rule.id)
                         .order_by(RuleChange.created_at.desc())).all()
    names = {u.id: u.full_name for u in db.scalars(
        select(User).where(User.id.in_({c.changed_by_id for c in changes if c.changed_by_id})))} if changes else {}
    return RuleDetail(
        **_out(rule, stats, days), description=rule.description,
        history=[RuleChangeOut(version=c.version, changes=c.changes, reason=c.reason, clinician=c.clinician,
                               changed_by=names.get(c.changed_by_id) if c.changed_by_id else None,
                               created_at=c.created_at) for c in changes],
        dismissals=[DismissalOut(claim_number=n or "—", reason=r, created_at=at)
                    for r, at, n in library.dismissal_reasons(db, rule.id)])


@router.get("/{rule_id}", response_model=RuleDetail)
def rule_detail(rule_id: str, days: int = Query(default=90, ge=1, le=3650), db: Session = Depends(get_db),
                _: User = Depends(can_view)) -> RuleDetail:
    rule = _rule(db, rule_id)
    db.commit()
    return _detail(db, rule, days)


@router.patch("/{rule_id}", response_model=RuleDetail)
def update(rule_id: str, body: RuleUpdate, request: Request, db: Session = Depends(get_db),
           user: User = Depends(can_manage)) -> RuleDetail:
    rule = _rule(db, rule_id)
    savepoint = db.begin_nested()       # a refused edit must leave the rule exactly as it was
    try:
        change = library.update_rule(db, rule, user_id=user.id, reason=body.reason, status=body.status,
                                     weight=body.weight, max_severity=body.max_severity, config=body.config,
                                     source_title=body.source_title, source_url=body.source_url,
                                     clinician=body.clinician)
    except library.RuleEditError as exc:
        savepoint.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    savepoint.commit()
    if change is None:
        raise HTTPException(status_code=422, detail="Nothing changed")
    log_event(db, "rule.updated", entity_type="rule", entity_id=rule.id, user_id=user.id, request=request,
              details={"rule_id": rule.id, "version": rule.version, "changes": change.changes,
                       "reason": change.reason, "clinician": change.clinician})
    db.commit()
    return _detail(db, rule, 90)
