"""Score page reading on the synthetic claims, against their known answers.

    python -m app.cli evaluate-reading

For every synthetic claim in the database it checks:
  * page type: the type found vs the true type of that page;
  * field recall: of the values the document actually prints (record.json),
    how many appear in the page text (ignoring case, spaces and punctuation).
Scanned imports ("CG-SYN-0001-S") are scored separately from clean ones.
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.claim import Claim, ClaimDocument, DocumentPage

# synthetic document type -> page type the classifier should give
TRUE_TYPE = {
    "claim_form": "claim_form", "preauth": "preauth_form", "discharge_summary": "discharge_summary",
    "final_bill": "final_bill", "pharmacy_bill": "pharmacy_bill", "lab_report": "lab_report",
    "imaging_report": "imaging_report", "ot_note": "ot_notes", "implant_invoice": "implant_invoice",
    "implant_sticker": "implant_sticker", "nursing_chart": "nursing_chart", "mlc": "mlc_fir",
    "policy_ecard": "policy_copy", "id_proof": "id_proof",
}
SKIP_FIELDS = {"admission", "discharge", "submitted", "report_date", "procedure_date", "policy_start",
               "policy_end", "inception", "stage", "layout", "ward_type", "room_category", "gender", "age",
               "signed", "history"}


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def printed_values(fields: dict) -> list[str]:
    """The values a document prints, as text, from its record.json fields."""
    out: list[str] = []

    def add(v: object) -> None:
        if isinstance(v, bool) or v is None or v == "":
            return
        out.append(f"{v:,.2f}" if isinstance(v, float) else str(v))

    for key, value in fields.items():
        if key in SKIP_FIELDS:
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            for row in value:
                for k in ("description", "amount", "drug", "value", "parameter", "model"):
                    if k in row:
                        add(row[k])
        elif isinstance(value, (str, int, float)):
            add(value)
    return [v for v in out if len(_norm(v)) >= 3]


@dataclass
class Score:
    pages: int = 0
    pages_right: int = 0
    values: int = 0
    values_found: int = 0
    by_type: dict = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    confusion: Counter = field(default_factory=Counter)
    quality: Counter = field(default_factory=Counter)
    claims: int = 0

    def line(self, label: str) -> str:
        if not self.pages:
            return f"{label}: no claims"
        return (f"{label}: {self.claims} claims, {self.pages} pages | page type {self.pages_right / self.pages:.1%} | "
                f"field recall {self.values_found / max(self.values, 1):.1%} | quality {dict(self.quality)}")


def evaluate(db: Session, folder: Path) -> dict[str, Score]:
    scores = {"clean": Score(), "scanned": Score()}
    claims = db.scalars(select(Claim).where(Claim.source == "synthetic", Claim.status == "ready_for_review"))
    for claim in claims:
        base = claim.claim_number.removesuffix("-S")
        truth_dir = folder / "claims" / base
        if not truth_dir.exists():
            continue
        score = scores["scanned" if claim.claim_number.endswith("-S") else "clean"]
        score.claims += 1
        truth = json.loads((truth_dir / "ground_truth.json").read_text())
        record = json.loads((truth_dir / "record.json").read_text())
        pages = db.scalars(select(DocumentPage).join(ClaimDocument).where(ClaimDocument.claim_id == claim.id)
                           .order_by(DocumentPage.page_number)).all()
        by_number = {p.page_number: p for p in pages}
        for doc, rec in zip(truth["documents"], record["documents"]):
            want = TRUE_TYPE[doc["type"]]
            text = ""
            for n in range(doc["first_page"], doc["last_page"] + 1):
                page = by_number.get(n)
                if page is None:
                    continue
                got = getattr(page.doc_type, "value", page.doc_type)
                score.pages += 1
                score.by_type[want][1] += 1
                score.quality[page.quality_flag or "none"] += 1
                if got == want:
                    score.pages_right += 1
                    score.by_type[want][0] += 1
                else:
                    score.confusion[(want, got)] += 1
                text += " " + (page.ocr_text or "")
            flat = _norm(text)
            values = printed_values(rec["fields"])
            score.values += len(values)
            score.values_found += sum(1 for v in values if _norm(v) in flat)
    return scores


def report(scores: dict[str, Score]) -> str:
    lines = []
    for label, score in scores.items():
        lines.append(score.line(label))
        if score.pages:
            weak = sorted(((t, ok / n) for t, (ok, n) in score.by_type.items() if ok < n), key=lambda x: x[1])
            if weak:
                lines.append("   page types below 100%: " + ", ".join(f"{t} {r:.0%}" for t, r in weak))
            if score.confusion:
                lines.append("   most common mix-ups: " + ", ".join(
                    f"{w} read as {g} ({n})" for (w, g), n in score.confusion.most_common(5)))
    return "\n".join(lines)
