"""Draft of a query letter to the hospital, built from the claim's findings.

The adjudicator edits and sends it; ClaimGuard does not send anything itself. Missing or incomplete
documents (C-layer findings) become "documents required"; confirmed findings from other layers become
"clarifications required". Dismissed findings are left out.
"""

from dataclasses import dataclass
from datetime import date

from app.models.claim import Claim
from app.models.rules import Finding
from app.review.scoring import latest_status


@dataclass
class Letter:
    subject: str
    body: str
    documents: list[str]
    clarifications: list[str]


def draft(claim: Claim, findings: list[Finding], today: date | None = None) -> Letter:
    today = today or date.today()
    documents: list[str] = []
    clarifications: list[str] = []
    for f in sorted(findings, key=lambda f: f.rule_id):
        status = latest_status(f.reviews)
        if not f.is_current or status == "dismissed":
            continue
        if f.rule_id.startswith("C-"):
            text = f.title.removeprefix("Missing document: ").removeprefix("Missing documents: ").rstrip(".")
            documents.append(text[:1].upper() + text[1:] + ".")
        elif status == "confirmed":
            clarifications.append(f"{f.title}: {f.explanation}")
    patient = claim.member_name or "the patient"
    stay = ""
    if claim.admission_at and claim.discharge_at:
        stay = f", admitted {claim.admission_at:%d %b %Y} and discharged {claim.discharge_at:%d %b %Y}"
    lines = [
        f"To: The Medical Superintendent / TPA Desk, {claim.hospital_name or 'the hospital'}",
        f"Date: {today:%d %b %Y}",
        "",
        f"Subject: Query on claim {claim.claim_number}, {patient}{stay}",
        "",
        "Dear Sir / Madam,",
        "",
        f"We are reviewing the above claim{f' for Rs. {claim.claimed_amount:,.2f}' if claim.claimed_amount else ''}. "
        "To complete the review we need the following.",
    ]
    if documents:
        lines += ["", "Documents required:"] + [f"{n}. {d}" for n, d in enumerate(documents, 1)]
    if clarifications:
        lines += ["", "Clarifications required:"] + [f"{n}. {c}" for n, c in enumerate(clarifications, 1)]
    if not documents and not clarifications:
        lines += ["", "[No open document gaps or confirmed findings: add the points you need clarified.]"]
    lines += ["", "Please send these within 7 days so the claim can be processed. The claim will be reviewed "
                  "again when they are received.", "", "Regards,", "Claims Team"]
    return Letter(f"Query on claim {claim.claim_number}", "\n".join(lines), documents, clarifications)
