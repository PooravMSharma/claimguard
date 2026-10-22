"""Risk score and band for one claim, from its findings (PRD "Risk scoring and explanations", v1 rules).

  score = sum of the weights of the rules that raised a finding (each rule counted once), capped at 100
  red   = any red finding, or score >= 70
  amber = any amber finding, or score 30-69
  green = only info findings (or none) and score < 30

Two adjustments keep the band honest:
- Medical-necessity findings alone cannot push a claim to red while those rules are not clinician-reviewed:
  if the score reaches 70 only because of medical weights, the band stays amber.
- A finding a reviewer dismissed no longer counts. Confirmed and open findings count. So the queue band follows
  the review: dismissing the only amber finding turns a claim green.

The score is rule-based on purpose: every point can be traced to a named rule and its finding.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.claim import Claim
from app.models.enums import ReviewAction, RiskBand, RuleStatus
from app.models.rules import Finding, Rule
from app.rules.catalogue import CHECKS, SEVERITY_ORDER
from app.rules.defaults import default_weight

RED_SCORE = 70
AMBER_SCORE = 30

ACTION_BY_BAND = {
    "red": "Senior review; refer to investigation if the findings are forensic or about identity.",
    "amber": "Adjudicator reviews the listed findings.",
    "green": "Fast-track review.",
}


@dataclass
class Item:
    """What scoring needs from one finding."""
    rule_id: str
    severity: str                    # red | amber | info
    status: str                      # open | confirmed | dismissed
    medical: bool = False
    amount_at_stake: Decimal | None = None
    suggested_deduction: Decimal | None = None


@dataclass
class Part:
    rule_id: str
    name: str
    severity: str                    # worst severity among the rule's counted findings
    weight: int
    medical: bool
    findings: int


@dataclass
class Score:
    score: int
    band: str
    reason: str                      # one line: why this band
    action: str
    parts: list[Part] = field(default_factory=list)
    dismissed: int = 0
    amount_at_stake: Decimal = Decimal("0")
    suggested_deduction: Decimal = Decimal("0")


def latest_status(reviews) -> str:
    """open / confirmed / dismissed from a finding's reviews; notes do not change it."""
    decisive = [r for r in sorted(reviews, key=lambda r: r.created_at) if r.action != ReviewAction.NOTED]
    return decisive[-1].action.value if decisive else "open"


def compute(items: list[Item], weights: dict[str, int]) -> Score:
    counted = [i for i in items if i.status != "dismissed"]
    by_rule: dict[str, list[Item]] = {}
    for i in counted:
        by_rule.setdefault(i.rule_id, []).append(i)
    parts = []
    for rule_id, found in by_rule.items():
        worst = max(found, key=lambda i: SEVERITY_ORDER.get(i.severity, 0)).severity
        check = CHECKS.get(rule_id)
        parts.append(Part(rule_id, check.name if check else rule_id, worst,
                          weights.get(rule_id, default_weight(rule_id) if check else 10),
                          any(i.medical for i in found), len(found)))
    parts.sort(key=lambda p: (-SEVERITY_ORDER.get(p.severity, 0), -p.weight, p.rule_id))
    total = min(100, sum(p.weight for p in parts))
    non_medical = min(100, sum(p.weight for p in parts if not p.medical))
    reds = [p for p in parts if p.severity == "red"]
    ambers = [p for p in parts if p.severity == "amber"]
    if reds:
        band, reason = "red", "Red finding: " + ", ".join(p.rule_id for p in reds) + "."
    elif total >= RED_SCORE and non_medical >= RED_SCORE:
        band, reason = "red", f"Score {total} is {RED_SCORE} or more."
    elif ambers or total >= AMBER_SCORE:
        band = "amber"
        if ambers:
            reason = "Amber finding: " + ", ".join(p.rule_id for p in ambers) + "."
        else:
            reason = f"Score {total} is {AMBER_SCORE} or more."
        if total >= RED_SCORE:
            reason += " Kept below red: medical-necessity findings alone cannot make a claim red."
    else:
        band = "green"
        reason = "No red or amber finding." if parts else "No findings."
    return Score(total, band, reason, ACTION_BY_BAND[band], parts,
                 dismissed=sum(1 for i in items if i.status == "dismissed"),
                 amount_at_stake=sum((i.amount_at_stake or Decimal(0) for i in counted), Decimal(0)),
                 suggested_deduction=sum((i.suggested_deduction or Decimal(0) for i in counted), Decimal(0)))


def items_for(db: Session, claim_id: uuid.UUID) -> list[Item]:
    """The claim's current findings, leaving out rules an admin has disabled since they were raised."""
    disabled = set(db.scalars(select(Rule.id).where(Rule.status == RuleStatus.DISABLED)))
    rows = db.scalars(select(Finding).where(Finding.claim_id == claim_id, Finding.is_current.is_(True))
                      .options(selectinload(Finding.reviews))).all()
    return [Item(f.rule_id, f.severity.value, latest_status(f.reviews), f.needs_medical_review,
                 f.amount_at_stake, f.suggested_deduction) for f in rows if f.rule_id not in disabled]


def weights(db: Session) -> dict[str, int]:
    return {r.id: r.weight for r in db.scalars(select(Rule))}


def score_claim(db: Session, claim: Claim) -> Score:
    """Compute and store the claim's score, band and suggested payable amount."""
    result = compute(items_for(db, claim.id), weights(db))
    claim.risk_score = result.score
    claim.risk_band = RiskBand(result.band)
    if claim.claimed_amount is not None:
        claim.suggested_payable = max(Decimal(0), claim.claimed_amount - result.suggested_deduction)
    db.flush()
    return result
