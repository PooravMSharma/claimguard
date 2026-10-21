"""Rules table, claim context from the database, and saving check results.

Saving is careful about reviews: a finding a reviewer has confirmed or dismissed is never deleted. When a re-run
raises the same finding again (same fingerprint) the reviewed one is kept as it is; when a re-run no longer
raises it, it stays as history with is_current = False.
"""

import uuid
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.extraction.documents import normalize
from app.extraction.evaluate import docs_from_db
from app.extraction.model import Value
from app.models.claim import Claim, ClaimDocument, DocumentPage
from app.models.enums import RuleLayer, RuleMethod, RuleStatus, Severity
from app.models.extraction import ExtractedDocument, ExtractedField
from app.models.rules import CheckRun, Finding, FindingReview, Rule
from app.rules.catalogue import CHECKS
from app.rules.context import ClaimContext, Facts, OriginalFile, Page
from app.rules.defaults import default_config, default_status, default_weight
from app.rules.engine import Outcome, RuleView
from app.rules.reference import Reference

LAYER = {"medical": RuleLayer.MEDICAL_NECESSITY}


def _method(text: str) -> RuleMethod:
    if "rule" in text or text == "image_hash":
        return RuleMethod.RULE
    if text.startswith("ai"):
        return RuleMethod.AI
    return RuleMethod(text) if text in RuleMethod._value2member_map_ else RuleMethod.RULE


def sync_rules(db: Session) -> dict[str, RuleView]:
    """Make sure every catalogue check has a row in the rules table; return what the engine needs.

    New rows get the defaults. Existing rows are never overwritten: they may have been tuned.
    """
    existing = {r.id: r for r in db.scalars(select(Rule))}
    for rule_id, c in CHECKS.items():
        if rule_id in existing:
            continue
        rule = Rule(id=rule_id, name=c.name, layer=LAYER.get(c.layer) or RuleLayer(c.layer), description=c.name,
                    method=_method(c.method), max_severity=Severity(c.max_severity), weight=default_weight(rule_id),
                    status=RuleStatus(default_status(rule_id)), version=1, config=default_config(rule_id))
        db.add(rule)
        existing[rule_id] = rule
    db.flush()
    return {rid: RuleView(rid, CHECKS[rid].layer, r.max_severity.value, r.version, r.status.value,
                          {**default_config(rid), **(r.config or {})})
            for rid, r in existing.items() if rid in CHECKS}


def load_context(db: Session, claim: Claim, ref: Reference | None = None,
                 read_file: Callable[[str], bytes] | None = None) -> ClaimContext:
    """The claim as the checks see it: documents with reviewer corrections applied, pages, original files."""
    pages = db.execute(select(DocumentPage, ClaimDocument).join(ClaimDocument)
                       .where(ClaimDocument.claim_id == claim.id)
                       .order_by(ClaimDocument.uploaded_at, DocumentPage.page_number)).all()
    order = {str(p.id): i for i, (p, _) in enumerate(pages)}
    rows = db.scalars(select(ExtractedDocument).where(ExtractedDocument.claim_id == claim.id)).all()
    rows = sorted(rows, key=lambda r: min((order.get(pid, 10**6) for pid in r.page_ids), default=10**6))
    docs = docs_from_db(rows)
    corrected = db.scalars(select(ExtractedField).where(ExtractedField.claim_id == claim.id,
                                                       ExtractedField.corrected_value.is_not(None))).all()
    by_key = {d.key: d for d in docs}
    for f in corrected:
        doc = by_key.get(f.document_key or "")
        if doc is None or f.corrected_value is None:
            continue
        old = doc.fields.get(f.field_name)
        doc.fields[f.field_name] = Value(
            f.corrected_value, normalize(f.field_name, f.corrected_value) or f.corrected_value,
            old.page_id if old else (str(f.source_page_id) if f.source_page_id else None),
            old.bbox if old else None, 1.0, "reviewer")
    page_list = [Page(str(p.id), getattr(p.doc_type, "value", str(p.doc_type)), p.text_source or "ocr",
                      p.quality_flag, p.image_hash, str(p.document_id)) for p, _ in pages]
    if read_file is None:
        from app.services import intake

        read_file = intake.storage().get
    reader = read_file
    files = [OriginalFile(str(d.id), d.filename, d.content_type, lambda key=d.storage_key: reader(key))
             for d in db.scalars(select(ClaimDocument).where(ClaimDocument.claim_id == claim.id)
                                 .order_by(ClaimDocument.uploaded_at))]
    stage = getattr(claim.stage, "value", str(claim.stage))
    return ClaimContext(Facts(claim.claim_number, stage, claim.source), docs, page_list,
                        ref or Reference.from_db(db), files)


def save_outcomes(db: Session, claim_id: uuid.UUID, outcomes: list[Outcome]) -> dict[str, int]:
    db.execute(delete(CheckRun).where(CheckRun.claim_id == claim_id))
    reviewed_ids = set(db.scalars(select(FindingReview.finding_id).join(Finding)
                                  .where(Finding.claim_id == claim_id)))
    old = db.scalars(select(Finding).where(Finding.claim_id == claim_id)).all()
    kept = {f.fingerprint: f for f in old if f.id in reviewed_ids and f.fingerprint}
    for f in old:
        if f.id not in reviewed_ids:
            db.delete(f)
    db.flush()
    raised: set[str] = set()
    counts = {"findings": 0, "red": 0, "amber": 0, "info": 0}
    for o in outcomes:
        db.add(CheckRun(claim_id=claim_id, rule_id=o.rule_id, rule_version=o.rule_version, status=o.status,
                        detail=o.detail, finding_count=len(o.findings)))
        for f in o.findings:
            raised.add(f.fingerprint)
            counts["findings"] += 1
            counts[f.severity] += 1
            if f.fingerprint in kept:
                kept[f.fingerprint].is_current = True
                continue
            db.add(Finding(claim_id=claim_id, rule_id=f.rule_id, rule_version=o.rule_version,
                           severity=Severity(f.severity), title=f.title, explanation=f.explanation,
                           evidence=f.evidence, amount_at_stake=f.amount_at_stake,
                           suggested_deduction=f.suggested_deduction, confidence=f.confidence,
                           needs_medical_review=f.needs_medical_review, fingerprint=f.fingerprint, is_current=True))
    for fingerprint, finding in kept.items():
        if fingerprint not in raised:
            finding.is_current = False
    db.flush()
    return counts


def run_for_claim(db: Session, claim: Claim, ref: Reference | None = None) -> dict[str, int]:
    """Load, run every check, save. Returns counts by severity."""
    from app.rules.engine import run_checks

    rules = sync_rules(db)
    ctx = load_context(db, claim, ref)
    return save_outcomes(db, claim.id, run_checks(ctx, rules))
