"""The structured claim record read from the documents, and reviewer corrections to it."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.permissions import Permission
from app.db.session import get_db
from app.extraction.documents import normalize
from app.models.claim import Claim
from app.models.extraction import (
    AiCall,
    BillLineItem,
    Diagnosis,
    ExtractedDocument,
    ExtractedField,
    Investigation,
    Medication,
    Procedure,
)
from app.models.user import User
from app.schemas.record import (
    AiUsage,
    BillLineOut,
    ClaimRecord,
    DocumentRecord,
    FieldCorrection,
    FieldOut,
    InvestigationOut,
    ItemOut,
    MedicationOut,
)
from app.services.audit import log_event

router = APIRouter(prefix="/claims", tags=["claim record"])
can_view = require_permission(Permission.CLAIMS_VIEW)
can_review = require_permission(Permission.CLAIMS_REVIEW)

DOC_ORDER = ["claim_form", "preauth_form", "discharge_summary", "final_bill", "pharmacy_bill", "lab_report",
             "imaging_report", "ot_notes", "implant_invoice", "implant_sticker", "nursing_chart", "mlc_fir",
             "policy_copy", "id_proof"]


def _field(f: ExtractedField) -> FieldOut:
    return FieldOut(id=f.id, name=f.field_name, value=f.value_text,
                    normalized=(f.value_normalized or {}).get("value"), corrected_value=f.corrected_value,
                    method=f.method, confidence=f.confidence, needs_review=f.needs_review,
                    page_id=f.source_page_id, bbox=f.bbox)


def build_record(db: Session, claim: Claim) -> ClaimRecord:
    cid = claim.id
    fields = db.scalars(select(ExtractedField).where(ExtractedField.claim_id == cid)).all()
    by_doc: dict[str | None, list[ExtractedField]] = {}
    for f in fields:
        by_doc.setdefault(f.document_key, []).append(f)
    docs = sorted(db.scalars(select(ExtractedDocument).where(ExtractedDocument.claim_id == cid)).all(),
                  key=lambda d: (DOC_ORDER.index(d.doc_type) if d.doc_type in DOC_ORDER else 99, d.key))
    documents = [DocumentRecord(key=d.key, doc_type=d.doc_type, page_ids=d.page_ids, quality=d.quality,
                                ai_used=d.ai_used, missing=d.missing,
                                fields=[_field(f) for f in sorted(by_doc.get(d.key, []), key=lambda f: f.field_name)],
                                tables=(d.data or {}).get("tables", {})) for d in docs]
    calls = db.execute(select(func.count(), func.count().filter(AiCall.status == "error"),
                              func.coalesce(func.sum(AiCall.input_tokens), 0),
                              func.coalesce(func.sum(AiCall.output_tokens), 0),
                              func.coalesce(func.sum(AiCall.cost_usd), 0))
                       .where(AiCall.claim_id == cid)).one()
    models = list(db.scalars(select(AiCall.model).where(AiCall.claim_id == cid).distinct()))
    summary = {
        "patient_name": claim.member_name, "age": claim.member_age,
        "gender": getattr(claim.member_gender, "value", claim.member_gender), "policy_no": claim.policy_number,
        "hospital_name": claim.hospital_name, "rohini_id": claim.hospital_rohini_id,
        "doctor_name": claim.treating_doctor, "doctor_reg_no": claim.doctor_registration_no,
        "admission": claim.admission_at, "discharge": claim.discharge_at, "claimed_amount": claim.claimed_amount,
        "sum_insured": claim.sum_insured, "policy_start": claim.policy_start_date,
    }
    return ClaimRecord(
        summary=summary, documents=documents,
        bill_lines=[BillLineOut(line_no=b.line_no, service_date=b.service_date, description=b.description,
                                category=getattr(b.category, "value", b.category), category_text=b.category_text,
                                quantity=b.quantity, unit_rate=b.unit_rate, amount=b.amount, method=b.method,
                                page_id=b.source_page_id, bbox=b.bbox)
                    for b in db.scalars(select(BillLineItem).where(BillLineItem.claim_id == cid)
                                        .order_by(BillLineItem.line_no))],
        medications=[MedicationOut(drug_name=m.drug_name, quantity=m.quantity, batch_no=m.batch_no, expiry=m.expiry,
                                   mrp=m.mrp, unit_rate=m.unit_rate, amount=m.amount,
                                   in_medication_chart=m.in_medication_chart, page_id=m.source_page_id, bbox=m.bbox)
                     for m in db.scalars(select(Medication).where(Medication.claim_id == cid))],
        investigations=[InvestigationOut(test_name=i.test_name, kind=i.kind, performed_at=i.performed_at,
                                         billed=i.billed, has_report=i.has_report,
                                         results=i.results if isinstance(i.results, list) else None,
                                         page_id=i.source_page_id, bbox=i.bbox)
                        for i in db.scalars(select(Investigation).where(Investigation.claim_id == cid))],
        diagnoses=[ItemOut(description=d.description, source_document=d.source_document, code=d.icd10_code,
                           page_id=d.source_page_id, bbox=d.bbox)
                   for d in db.scalars(select(Diagnosis).where(Diagnosis.claim_id == cid))],
        procedures=[ItemOut(description=p.description, source_document=p.source_document, performed_at=p.performed_at,
                            page_id=p.source_page_id, bbox=p.bbox)
                    for p in db.scalars(select(Procedure).where(Procedure.claim_id == cid))],
        ai=AiUsage(calls=calls[0], failed=calls[1], input_tokens=int(calls[2]), output_tokens=int(calls[3]),
                   cost_usd=Decimal(calls[4]).quantize(Decimal("0.0001")), models=models),
        fields_needing_review=sum(1 for f in fields if f.needs_review and not f.corrected_value),
    )


@router.get("/{claim_id}/record", response_model=ClaimRecord)
def get_record(claim_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(can_view)) -> ClaimRecord:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return build_record(db, claim)


@router.patch("/{claim_id}/fields/{field_id}", response_model=FieldOut)
def correct_field(claim_id: uuid.UUID, field_id: uuid.UUID, body: FieldCorrection, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(can_review)) -> FieldOut:
    """A reviewer confirms or corrects a value read from the documents. The original reading is kept."""
    f = db.scalar(select(ExtractedField).where(ExtractedField.id == field_id, ExtractedField.claim_id == claim_id))
    if f is None:
        raise HTTPException(status_code=404, detail="Field not found")
    before = f.corrected_value or f.value_text
    f.corrected_value = body.value.strip()
    f.corrected_by_id = user.id
    f.corrected_at = datetime.now(timezone.utc)
    f.needs_review = False
    normalized = normalize(f.field_name, f.corrected_value)
    log_event(db, "field.corrected", entity_type="extracted_field", entity_id=f.id, user_id=user.id, request=request,
              details={"claim_id": str(claim_id), "field": f.field_name, "document": f.document_key,
                       "from": before, "to": f.corrected_value, "normalized": str(normalized)})
    claim = db.get(Claim, claim_id)
    if claim is not None:
        from app.extraction.store import summarise

        db.flush()
        summarise(db, claim)
    db.commit()
    return _field(f)
