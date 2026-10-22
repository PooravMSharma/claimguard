"""The rule library as an admin tunes it: change a rule safely, keep the history, show how reviewers judge it.

What may change, and the limits:
- status: non-medical rules are active or disabled. Medical-necessity rules are draft (amber at most, marked
  for medical review), clinician_reviewed (may use their full severity) or disabled. Marking a medical rule
  clinician-reviewed needs the clinician's name; it is recorded with the change.
- weight: 0-100, the points the rule adds to a claim's risk score.
- max_severity: lowered or restored, never above the severity the check was designed for (catalogue).
- config (thresholds): only the keys the check reads, each keeping its type (a number stays a number, a word
  list stays a list of words). Unknown keys are refused, so a typo cannot silently do nothing.
- source_title / source_url: the guideline, circular or rate list the rule rests on.

Every change needs a reason, bumps the rule version, is stored in rule_changes and the audit log, and applies
from the next checks run. Weight and status changes re-score open claims at once (no checks re-run).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.claim import Claim
from app.models.enums import ClaimStatus, ReviewAction, RuleStatus, Severity
from app.models.rules import Finding, FindingReview, Rule, RuleChange
from app.rules.catalogue import CHECKS, SEVERITY_ORDER
from app.rules.defaults import default_config

MEDICAL_STATUSES = {"draft", "clinician_reviewed", "disabled"}
OTHER_STATUSES = {"active", "disabled"}
HIGH_DISMISSAL_RATE = 0.5          # surfaced for retuning above this, once enough findings were reviewed
MIN_REVIEWED = 5


class RuleEditError(ValueError):
    pass


@dataclass
class RuleStats:
    findings: int = 0          # findings raised in the period (current or not)
    reviewed: int = 0          # of those, confirmed or dismissed (latest decisive review)
    confirmed: int = 0
    dismissed: int = 0

    @property
    def dismissal_rate(self) -> float | None:
        return self.dismissed / self.reviewed if self.reviewed else None

    @property
    def needs_retuning(self) -> bool:
        rate = self.dismissal_rate
        return rate is not None and self.reviewed >= MIN_REVIEWED and rate >= HIGH_DISMISSAL_RATE


def is_medical(rule_id: str) -> bool:
    return CHECKS[rule_id].layer == "medical"


def stats(db: Session, days: int = 90, rule_id: str | None = None) -> dict[str, RuleStats]:
    """How reviewers judged each rule's findings over the last `days` days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    flt = [Finding.created_at >= since]
    if rule_id:
        flt.append(Finding.rule_id == rule_id)
    out: dict[str, RuleStats] = {}
    for rid, n in db.execute(select(Finding.rule_id, func.count()).where(*flt).group_by(Finding.rule_id)):
        out[rid] = RuleStats(findings=n)
    # latest confirm / dismiss per finding (notes do not count)
    latest = (select(FindingReview.finding_id, func.max(FindingReview.created_at).label("at"))
              .where(FindingReview.action != ReviewAction.NOTED).group_by(FindingReview.finding_id).subquery())
    rows = db.execute(
        select(Finding.rule_id, FindingReview.action, func.count())
        .join(latest, latest.c.finding_id == Finding.id)
        .join(FindingReview, (FindingReview.finding_id == latest.c.finding_id)
              & (FindingReview.created_at == latest.c.at))
        .where(*flt).group_by(Finding.rule_id, FindingReview.action))
    for rid, action, n in rows:
        s = out.setdefault(rid, RuleStats())
        s.reviewed += n
        if action == ReviewAction.CONFIRMED:
            s.confirmed += n
        elif action == ReviewAction.DISMISSED:
            s.dismissed += n
    return out


def dismissal_reasons(db: Session, rule_id: str, limit: int = 10) -> list[tuple[str, datetime, str | None]]:
    """The latest reasons reviewers gave for dismissing this rule's findings: what to fix when retuning."""
    rows = db.execute(
        select(FindingReview.reason, FindingReview.created_at, Claim.claim_number)
        .join(Finding, Finding.id == FindingReview.finding_id).join(Claim, Claim.id == Finding.claim_id)
        .where(Finding.rule_id == rule_id, FindingReview.action == ReviewAction.DISMISSED)
        .order_by(FindingReview.created_at.desc()).limit(limit))
    return [(reason or "", at, number) for reason, at, number in rows]


# ---------------------------------------------------------------- validation


def check_value(path: str, value: object, default: object) -> object:
    """Keep the default's shape: numbers stay numbers, lists stay lists of the same kind, dicts keep their keys
    (open dicts of numbers, like per-item limits, may gain or lose keys)."""
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise RuleEditError(f"{path} must be true or false")
        return value
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuleEditError(f"{path} must be a number")
        if value < 0:
            raise RuleEditError(f"{path} cannot be negative")
        return int(value) if isinstance(default, int) and float(value).is_integer() else value
    if isinstance(default, str):
        if not isinstance(value, str) or not value.strip():
            raise RuleEditError(f"{path} must be text")
        return value.strip()
    if isinstance(default, list):
        if not isinstance(value, list):
            raise RuleEditError(f"{path} must be a list")
        sample = default[0] if default else ""
        cleaned = [check_value(f"{path}[{i}]", v, sample) for i, v in enumerate(value)]
        if isinstance(sample, str):
            cleaned = [v for v in dict.fromkeys(cleaned)]          # drop duplicates, keep order
        return cleaned
    if isinstance(default, dict):
        if not isinstance(value, dict):
            raise RuleEditError(f"{path} must be a set of named values")
        numbers_only = bool(default) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                             for v in default.values())
        if numbers_only:      # e.g. {"syringe": 8, "cannula": 2}: items may be added or removed
            return {str(k).strip(): check_value(f"{path}.{k}", v, 0) for k, v in value.items() if str(k).strip()}
        unknown, missing = set(value) - set(default), set(default) - set(value)
        if unknown:
            raise RuleEditError(f"{path}: unknown setting(s) {', '.join(sorted(unknown))}")
        if missing:
            raise RuleEditError(f"{path}: missing {', '.join(sorted(missing))}")
        return {k: check_value(f"{path}.{k}", value[k], d) for k, d in default.items()}
    return value


def check_config(rule_id: str, config: dict) -> dict:
    defaults = default_config(rule_id)
    if not defaults:
        if config:
            raise RuleEditError("This rule has no settings to change")
        return {}
    unknown = set(config) - set(defaults)
    if unknown:
        raise RuleEditError(f"Unknown setting(s) for {rule_id}: {', '.join(sorted(unknown))}")
    return {k: check_value(k, config.get(k, d), d) for k, d in defaults.items()}


# ---------------------------------------------------------------- change


def update_rule(db: Session, rule: Rule, *, user_id: uuid.UUID | None, reason: str, status: str | None = None,
                weight: int | None = None, max_severity: str | None = None, config: dict | None = None,
                source_title: str | None = None, source_url: str | None = None,
                clinician: str | None = None) -> RuleChange | None:
    """Apply an admin's edit. Returns the recorded change, or None when nothing actually changed."""
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise RuleEditError("Give the reason for this change (it is kept with the rule's history)")
    changes: dict[str, list] = {}

    if status is not None and status != rule.status.value:
        allowed = MEDICAL_STATUSES if is_medical(rule.id) else OTHER_STATUSES
        if status not in allowed:
            raise RuleEditError(f"{rule.id} can be: {', '.join(sorted(allowed))}")
        if status == "clinician_reviewed" and not (clinician or "").strip():
            raise RuleEditError("Name the clinician who reviewed this rule")
        changes["status"] = [rule.status.value, status]
        rule.status = RuleStatus(status)
    if weight is not None and weight != rule.weight:
        if not 0 <= weight <= 100:
            raise RuleEditError("Weight must be between 0 and 100")
        changes["weight"] = [rule.weight, weight]
        rule.weight = weight
    if max_severity is not None and max_severity != rule.max_severity.value:
        ceiling = CHECKS[rule.id].max_severity
        if max_severity not in SEVERITY_ORDER:
            raise RuleEditError("Severity must be red, amber or info")
        if SEVERITY_ORDER[max_severity] > SEVERITY_ORDER[ceiling]:
            raise RuleEditError(f"{rule.id} was designed for {ceiling} at most; it cannot be raised above that")
        changes["max_severity"] = [rule.max_severity.value, max_severity]
        rule.max_severity = Severity(max_severity)
    if config is not None:
        new = check_config(rule.id, config)
        old = {**default_config(rule.id), **(rule.config or {})}
        if new != old:
            changes["config"] = [old, new]
            rule.config = new
    for name, value in (("source_title", source_title), ("source_url", source_url)):
        if value is not None:
            value = value.strip() or None
            if value != getattr(rule, name):
                if name == "source_url" and value and not value.startswith(("http://", "https://")):
                    raise RuleEditError("The source link must start with http:// or https://")
                changes[name] = [getattr(rule, name), value]
                setattr(rule, name, value)
    if not changes:
        return None
    rule.version += 1
    change = RuleChange(rule_id=rule.id, version=rule.version, changed_by_id=user_id, changes=changes,
                        reason=reason, clinician=(clinician or "").strip() or None)
    db.add(change)
    db.flush()
    if {"weight", "status", "max_severity"} & changes.keys():
        rescore_open(db)
    return change


def rescore_open(db: Session) -> int:
    """Re-score the claims still in review after a weight or status change (findings are not re-run)."""
    from app.review.scoring import score_claim

    claims = db.scalars(select(Claim).where(Claim.status.in_([ClaimStatus.READY_FOR_REVIEW,
                                                               ClaimStatus.IN_REVIEW]))).all()
    for claim in claims:
        score_claim(db, claim)
    return len(claims)
