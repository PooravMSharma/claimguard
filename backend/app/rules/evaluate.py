"""Score the checks against the synthetic ground truth (python -m app.cli evaluate-checks).

For every synthetic claim that is ready for review, the checks that raised a current finding are compared with
ground_truth.json -> expected_checks. AI checks (Step 10) are left out of both sides.

  recall     seeded issues that were found
  precision  flags that match a seeded issue (or a documented side effect of one)
  false reds genuine claims (no seeded issue) with any red finding
  band       worst severity of the findings vs the band the seeded rule checks imply
"""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.claim import Claim
from app.models.enums import ClaimStatus
from app.models.rules import Finding
from app.rules.catalogue import CHECKS, SEVERITY_ORDER, expected_band
from app.rules.engine import PENDING_AI_CHECKS

RULE_CHECKS = [c for c in CHECKS if c not in PENDING_AI_CHECKS]


@dataclass
class Score:
    claims: int = 0
    genuine: int = 0
    genuine_with_red: int = 0
    band_agree: int = 0
    found: Counter = field(default_factory=Counter)
    missed: Counter = field(default_factory=Counter)
    extra: Counter = field(default_factory=Counter)
    examples: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def add(self, claim_number: str, truth: dict, flags: dict[str, list[tuple[str, str]]]) -> None:
        self.claims += 1
        expected = set(truth["expected_checks"]) - set(PENDING_AI_CHECKS)
        got = {c for c in flags if c not in PENDING_AI_CHECKS}
        for c in expected:
            (self.found if c in got else self.missed)[c] += 1
            if c not in got:
                self.examples[f"missed {c}"].append(claim_number)
        for c in got - set(truth["expected_checks"]):
            self.extra[c] += 1
            self.examples[f"extra {c}"].append(f"{claim_number}: {flags[c][0][1]}")
        severities = [s for items in flags.values() for s, _ in items]
        if truth.get("genuine"):
            self.genuine += 1
            self.genuine_with_red += "red" in severities
        worst = max((SEVERITY_ORDER[s] for s in severities if s != "info"), default=-1)
        band = {2: "red", 1: "amber"}.get(worst, "green")
        self.band_agree += band == expected_band(sorted(expected))

    @property
    def recall(self) -> float:
        total = sum(self.found.values()) + sum(self.missed.values())
        return sum(self.found.values()) / total if total else 0.0

    @property
    def precision(self) -> float:
        total = sum(self.found.values()) + sum(self.extra.values())
        return sum(self.found.values()) / total if total else 0.0

    def report(self, label: str, misses: int = 0) -> str:
        found, seeded = sum(self.found.values()), sum(self.found.values()) + sum(self.missed.values())
        lines = [f"{label} ({self.claims} claims): recall {found}/{seeded} = {self.recall:.1%} | precision "
                 f"{self.precision:.1%} | genuine claims with a red: {self.genuine_with_red}/{self.genuine} | "
                 f"band agrees on {self.band_agree}/{self.claims}"]
        weak = [c for c in RULE_CHECKS if self.missed[c] or self.extra[c]]
        if weak:
            lines.append("   by check: " + ", ".join(
                f"{c} found {self.found[c]}/{self.found[c] + self.missed[c]} extra {self.extra[c]}" for c in weak))
        if misses:
            for key in sorted(self.examples):
                lines += [f"      {key}: {x}" for x in self.examples[key][:misses]]
        return "\n".join(lines)


def evaluate(db: Session, folder: Path) -> dict[str, Score]:
    scores = {"clean": Score(), "scanned": Score()}
    claims = db.scalars(select(Claim).where(Claim.source == "synthetic",
                                            Claim.status == ClaimStatus.READY_FOR_REVIEW)).all()
    for claim in claims:
        base = claim.claim_number.removesuffix("-S")
        path = folder / "claims" / base / "ground_truth.json"
        if not path.exists():
            continue
        flags: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for f in db.scalars(select(Finding).where(Finding.claim_id == claim.id, Finding.is_current.is_(True))):
            flags[f.rule_id].append((f.severity.value, f.title))
        scores["scanned" if claim.claim_number.endswith("-S") else "clean"].add(
            claim.claim_number, json.loads(path.read_text()), flags)
    return scores
