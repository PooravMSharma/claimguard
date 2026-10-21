"""The check engine: one small function per check, run over a ClaimContext.

A check returns a Result: passed, flagged (with Flags), not applicable, or not evaluable (the data it needs
is missing or unreadable). The engine then applies the severity policy, the same for every check:

- never above the rule's maximum severity (from the catalogue / rules table);
- medical-necessity checks are amber at most and need medical review until a clinician has reviewed them;
- a red flag that rests on any value that could be a misread (read by OCR or the AI reader, or low
  confidence, and not corrected by a reviewer) is shown as amber: a misread must never create a red flag.

One check failing never stops the others: it is recorded with status "error".
"""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from app.rules.catalogue import CHECKS, SEVERITY_ORDER
from app.rules.context import ClaimContext, Evidence

log = logging.getLogger(__name__)

PASSED, FLAGGED, NOT_APPLICABLE, NOT_EVALUABLE, PENDING_AI, DISABLED, ERROR = (
    "passed", "flagged", "not_applicable", "not_evaluable", "pending_ai", "disabled", "error")

# Judgement checks done by the AI in Step 10. C-02, X-06 and M-03 have a rule part that runs now.
PENDING_AI_CHECKS = ("F-02", "F-04", "X-08", "M-01", "M-02", "M-04", "M-06", "B-11")


@dataclass
class Flag:
    title: str
    explanation: str
    evidence: list[Evidence] = field(default_factory=list)
    severity: str | None = None                 # default: the rule's maximum
    amount_at_stake: Decimal | float | int | None = None
    suggested_deduction: Decimal | float | int | None = None
    confidence: float | None = None
    key: str | None = None                      # what makes this flag the same flag next time


@dataclass
class Result:
    status: str
    flags: list[Flag] = field(default_factory=list)
    detail: str | None = None


def passed(detail: str | None = None) -> Result:
    return Result(PASSED, detail=detail)


def not_applicable(detail: str) -> Result:
    return Result(NOT_APPLICABLE, detail=detail)


def not_evaluable(detail: str) -> Result:
    return Result(NOT_EVALUABLE, detail=detail)


def flagged(*flags: Flag, detail: str | None = None) -> Result:
    return Result(FLAGGED, list(flags), detail) if flags else passed(detail)


CheckFn = Callable[[ClaimContext], Result]
REGISTRY: dict[str, CheckFn] = {}


def check(rule_id: str) -> Callable[[CheckFn], CheckFn]:
    if rule_id not in CHECKS:
        raise ValueError(f"{rule_id} is not in the catalogue")

    def register(fn: CheckFn) -> CheckFn:
        REGISTRY[rule_id] = fn
        return fn
    return register


@dataclass
class RuleView:
    """The parts of a rule (DB row or catalogue default) the engine needs."""
    id: str
    layer: str
    max_severity: str
    version: int = 1
    status: str = "active"              # draft | clinician_reviewed | active | disabled
    config: dict = field(default_factory=dict)


@dataclass
class FindingOut:
    rule_id: str
    severity: str
    title: str
    explanation: str
    evidence: list[dict]
    amount_at_stake: Decimal | None
    suggested_deduction: Decimal | None
    confidence: float | None
    needs_medical_review: bool
    fingerprint: str
    downgraded: bool = False


@dataclass
class Outcome:
    rule_id: str
    rule_version: int
    status: str
    detail: str | None = None
    findings: list[FindingOut] = field(default_factory=list)


def _cap(severity: str, ceiling: str) -> str:
    return severity if SEVERITY_ORDER[severity] <= SEVERITY_ORDER[ceiling] else ceiling


def finalise(rule: RuleView, flag: Flag) -> FindingOut:
    medical = rule.layer in ("medical", "medical_necessity")
    severity = _cap(flag.severity or rule.max_severity, rule.max_severity)
    if medical and rule.status != "clinician_reviewed":
        severity = _cap(severity, "amber")
    downgraded = False
    if severity == "red" and any(not e.reliable for e in flag.evidence):
        severity, downgraded = "amber", True
    explanation = flag.explanation
    if downgraded:
        explanation += " Shown as amber because a value it rests on was read from a scan; confirm it on the page."
    key = flag.key or flag.title + "|" + "|".join(e.quote for e in flag.evidence)
    fingerprint = hashlib.sha256(f"{rule.id}|{key}".encode()).hexdigest()[:40]
    return FindingOut(rule.id, severity, flag.title[:300], explanation, [e.as_json() for e in flag.evidence],
                      _money(flag.amount_at_stake), _money(flag.suggested_deduction), flag.confidence,
                      medical, fingerprint, downgraded)


def _money(value: Decimal | float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def run_checks(ctx: ClaimContext, rules: dict[str, RuleView]) -> list[Outcome]:
    """Run every catalogue check on one claim. Always returns one Outcome per check."""
    import app.rules.checks  # noqa: F401  (registers the check functions)

    outcomes = []
    for rule_id in CHECKS:
        rule = rules.get(rule_id) or default_rule(rule_id)
        if rule.status == "disabled":
            outcomes.append(Outcome(rule_id, rule.version, DISABLED, "Rule is disabled"))
            continue
        fn = REGISTRY.get(rule_id)
        if fn is None:
            status = PENDING_AI if rule_id in PENDING_AI_CHECKS else NOT_EVALUABLE
            detail = "Needs the AI reviewer (Step 10)" if status == PENDING_AI else "Check not implemented"
            outcomes.append(Outcome(rule_id, rule.version, status, detail))
            continue
        ctx.config[rule_id] = rule.config
        try:
            result = fn(ctx)
        except Exception as exc:  # one check must never stop the others
            log.exception("check %s failed on %s", rule_id, ctx.facts.claim_number)
            outcomes.append(Outcome(rule_id, rule.version, ERROR, f"{exc.__class__.__name__}: {exc}"[:500]))
            continue
        findings = [finalise(rule, f) for f in result.flags]
        seen: set[str] = set()
        unique = [f for f in findings if not (f.fingerprint in seen or seen.add(f.fingerprint))]
        status = result.status
        if rule_id in ("C-02", "X-06", "M-03") and status == PASSED:
            detail = (result.detail + " " if result.detail else "") + "AI part pending (Step 10)."
        else:
            detail = result.detail
        outcomes.append(Outcome(rule_id, rule.version, FLAGGED if unique else status, detail, unique))
    return outcomes


def default_rule(rule_id: str) -> RuleView:
    from app.rules.defaults import default_config, default_status

    c = CHECKS[rule_id]
    return RuleView(rule_id, c.layer, c.max_severity, 1, default_status(rule_id), default_config(rule_id))
