"""Save extraction results: documents, fields with evidence, bill lines, medicines, tests, diagnoses,
procedures; then fill the claim's own summary columns. Reviewer corrections survive re-processing."""

import re
import uuid
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.extraction.documents import normalize
from app.extraction.model import DocExtract, Value, missing
from app.extraction.values import norm_text
from app.models.claim import Claim
from app.models.enums import BillCategory, Gender
from app.models.extraction import (
    BillLineItem,
    Diagnosis,
    ExtractedDocument,
    ExtractedField,
    Investigation,
    Medication,
    Procedure,
)

# Which document to trust first for each claim-level value
PRIORITY = {
    "patient_name": ["claim_form", "policy_copy", "discharge_summary", "final_bill", "id_proof"],
    "policy_no": ["claim_form", "policy_copy", "preauth_form"],
    "age": ["claim_form", "discharge_summary", "policy_copy"],
    "gender": ["claim_form", "discharge_summary", "policy_copy", "id_proof"],
    "hospital_name": ["claim_form", "discharge_summary", "final_bill"],
    "rohini_id": ["claim_form", "discharge_summary", "final_bill"],
    "doctor_name": ["claim_form", "discharge_summary"],
    "doctor_reg_no": ["claim_form", "discharge_summary"],
    "admission": ["discharge_summary", "claim_form", "final_bill"],
    "discharge": ["discharge_summary", "claim_form", "final_bill"],
    "claimed_amount": ["claim_form"],
    "sum_insured": ["policy_copy"],
    "policy_start": ["policy_copy"],
}
CATEGORY_WORDS = [
    (BillCategory.PACKAGE, ("package",)), (BillCategory.ICU, ("icu", "iccu", "intensive care")),
    (BillCategory.ROOM, ("room rent", "room charges", "bed charges", "ward charges")),
    (BillCategory.NURSING, ("nursing",)), (BillCategory.SURGEON_FEE, ("surgeon fee", "surgeon's fee", "surgery fee")),
    (BillCategory.ANAESTHESIA, ("anaesthetist", "anesthetist", "anaesthesia")),
    (BillCategory.OT_CHARGES, ("operation theatre", "ot charges", "theatre")),
    (BillCategory.CONSULTATION, ("consultant", "consultation", "doctor visit", "visit charges")),
    (BillCategory.IMPLANT, ("implant", "stent", "nail", "plate", "iol", "prosthesis")),
    (BillCategory.PHARMACY, ("pharmacy", "medicines", "drugs")),
    (BillCategory.CONSUMABLES, ("consumable", "cannula", "syringe", "infusion set", "gloves", "catheter")),
    (BillCategory.INVESTIGATION, ("investigation", "laboratory", "lab ", "test", "x-ray", "scan", "usg", "ecg",
                                  "mri", "echo", "count", "profile", "culture")),
]


def bill_category(printed: str | None, description: str) -> BillCategory:
    text = f"{printed or ''} {description}".lower()
    for category, words in CATEGORY_WORDS:
        if any(w in text for w in words):
            return category
    return BillCategory.OTHER


def _num(v: Value | None) -> Decimal | None:
    if v is None or v.normalized is None:
        return None
    try:
        return Decimal(str(v.normalized)).quantize(Decimal("0.01"))
    except Exception:
        return None


IST = ZoneInfo("Asia/Kolkata")


def _dt(text: object) -> datetime | None:
    """Dates on Indian documents are Indian time."""
    if not text:
        return None
    try:
        value = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return value.replace(tzinfo=IST) if value.tzinfo is None else value


def _uuid(page_id: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(page_id) if page_id else None
    except ValueError:
        return None


def _bbox(v: Value | None) -> dict | None:
    if v is None or not v.bbox:
        return None
    return dict(zip(("x0", "y0", "x1", "y1"), v.bbox))


def _src(v: Value | None) -> dict:
    return {"source_page_id": _uuid(v.page_id) if v else None, "bbox": _bbox(v),
            "confidence": round(v.confidence, 3) if v else None}


def clear(db: Session, claim_id: uuid.UUID) -> dict[tuple[str | None, str], ExtractedField]:
    """Remove previous extraction; return reviewer corrections so they can be re-applied."""
    kept = {(f.document_key, f.field_name): f for f in db.scalars(
        select(ExtractedField).where(ExtractedField.claim_id == claim_id, ExtractedField.corrected_value.is_not(None)))}
    for model in (ExtractedField, BillLineItem, Diagnosis, Procedure, Investigation, Medication, ExtractedDocument):
        db.execute(delete(model).where(model.claim_id == claim_id))  # type: ignore[attr-defined]
    db.flush()
    return kept


def save(db: Session, claim: Claim, docs: list[DocExtract],
         corrections: dict[tuple[str | None, str], ExtractedField]) -> None:
    for doc in docs:
        db.add(ExtractedDocument(
            claim_id=claim.id, key=doc.key, doc_type=doc.doc_type, page_ids=doc.page_ids, quality=doc.quality,
            ai_used=doc.ai_used, missing=missing(doc),
            data={"fields": {k: v.as_json() for k, v in doc.fields.items()},
                  "tables": {t: [{c: v.as_json() for c, v in row.items()} for row in rows]
                             for t, rows in doc.tables.items()}},
        ))
        for name, v in doc.fields.items():
            old = corrections.get((doc.key, name))
            db.add(ExtractedField(
                claim_id=claim.id, section=doc.doc_type, document_key=doc.key, field_name=name,
                value_text=v.text[:4000], value_normalized={"value": v.normalized}, method=v.method,
                needs_review=v.confidence < 0.8 or (v.method == "ai" and doc.quality in ("poor", "unreadable")),
                corrected_value=old.corrected_value if old else None,
                corrected_by_id=old.corrected_by_id if old else None, corrected_at=old.corrected_at if old else None,
                **_src(v),
            ))
    _save_lists(db, claim, docs)
    db.flush()
    summarise(db, claim)


def _save_lists(db: Session, claim: Claim, docs: list[DocExtract]) -> None:
    charted: set[str] = set()
    for doc in docs:
        if doc.doc_type == "nursing_chart":
            for day in doc.tables.get("days", []):
                if "meds" in day:
                    charted.update(norm_text(m) for m in re.split(r";", day["meds"].text) if m.strip())

    reports: list[Investigation] = []
    for doc in docs:
        t = doc.doc_type
        if t == "final_bill":
            for n, row in enumerate(doc.tables.get("lines", []), 1):
                desc, amount = row.get("description"), _num(row.get("amount"))
                if desc is None or amount is None:
                    continue
                cat_text = row["category"].text if "category" in row else None
                db.add(BillLineItem(
                    claim_id=claim.id, line_no=n, service_date=_dt(row["date"].normalized) if "date" in row else None,
                    description=desc.text[:500], category=bill_category(cat_text, desc.text), category_text=cat_text,
                    quantity=_num(row.get("qty")), unit_rate=_num(row.get("rate")), amount=amount,
                    method=desc.method, **_src(desc)))
                if desc.text.lower().startswith(("surgeon fee -", "surgeon fee –")):
                    db.add(Procedure(claim_id=claim.id, description=desc.text.split("-", 1)[1].strip()[:500],
                                     source_document="final_bill", **_src(desc)))
        elif t == "pharmacy_bill":
            for row in doc.tables.get("lines", []):
                drug = row.get("drug")
                if drug is None:
                    continue
                db.add(Medication(
                    claim_id=claim.id, drug_name=drug.text[:255], quantity=_num(row.get("qty")),
                    batch_no=row["batch"].text[:64] if "batch" in row else None,
                    expiry=row["expiry"].text[:20] if "expiry" in row else None, mrp=_num(row.get("mrp")),
                    unit_rate=_num(row.get("rate")), amount=_num(row.get("amount")),
                    in_medication_chart=(norm_text(drug.text) in charted) if charted else None, **_src(drug)))
        elif t in ("lab_report", "imaging_report"):
            test = doc.fields.get("test")
            if test is None:
                continue
            results = [{c: v.text for c, v in row.items()} for row in doc.tables.get("results", [])]
            if "impression" in doc.fields:
                results.append({"parameter": "Impression", "value": doc.fields["impression"].text})
            inv = Investigation(
                claim_id=claim.id, test_name=test.text[:255], kind="lab" if t == "lab_report" else "imaging",
                performed_at=_dt(doc.get("report_date")), results=results,
                lab_name=doc.fields["hospital_name"].text[:255] if "hospital_name" in doc.fields else None,
                billed=False, has_report=True, **_src(test))
            db.add(inv)
            reports.append(inv)
        if t in ("claim_form", "discharge_summary", "preauth_form"):
            name = "preauth_diagnosis" if t == "preauth_form" else "diagnosis"
            v = doc.fields.get(name)
            if v is not None:
                db.add(Diagnosis(claim_id=claim.id, description=str(v.normalized or v.text)[:500],
                                 icd10_code=(doc.get("icd_code") or None) if t != "preauth_form" else None,
                                 is_primary=t == "discharge_summary", source_document=t, **_src(v)))
        if t in ("claim_form", "discharge_summary", "ot_notes"):
            v = doc.fields.get("procedures")
            for item in (v.normalized if v and isinstance(v.normalized, list) else []):
                db.add(Procedure(claim_id=claim.id, description=str(item)[:500], source_document=t,
                                 performed_at=_dt(doc.get("procedure_date")) if t == "ot_notes" else None,
                                 surgeon=doc.fields["surgeon"].text[:200] if t == "ot_notes" and "surgeon" in doc.fields
                                 else None, **_src(v)))

    # billed tests: matched to their report, or recorded as billed without a report
    billed = [d for d in docs if d.doc_type == "final_bill"]
    for doc in billed:
        for row in doc.tables.get("lines", []):
            desc = row.get("description")
            if desc is None or bill_category(row["category"].text if "category" in row else None,
                                            desc.text) != BillCategory.INVESTIGATION:
                continue
            match = next((r for r in reports if not r.billed and norm_text(r.test_name) == norm_text(desc.text)),
                         None)
            if match:
                match.billed = True
            else:
                db.add(Investigation(claim_id=claim.id, test_name=desc.text[:255], billed=True, has_report=False,
                                     performed_at=_dt(row["date"].normalized) if "date" in row else None, **_src(desc)))


def summarise(db: Session, claim: Claim) -> None:
    """Fill the claim's summary columns from the best document for each value (upload form values kept)."""
    rows = db.scalars(select(ExtractedField).where(ExtractedField.claim_id == claim.id)).all()
    best: dict[str, object] = {}
    corrected: set[str] = set()
    for name, order in PRIORITY.items():
        for doc_type in order:
            hit = next((f for f in rows if f.field_name == name and f.section == doc_type), None)
            if hit is not None:
                if hit.corrected_value:
                    best[name] = normalize(name, hit.corrected_value) or hit.corrected_value
                    corrected.add(name)
                    break
                value = (hit.value_normalized or {}).get("value") or hit.value_text
                if value not in (None, ""):
                    best[name] = value
                    break
    # Values typed at upload are kept unless a reviewer corrected the document reading.
    if "patient_name" in best and (not claim.member_name or "patient_name" in corrected):
        claim.member_name = str(best["patient_name"])[:200]
    if "policy_no" in best and (not claim.policy_number or "policy_no" in corrected):
        claim.policy_number = str(best["policy_no"])[:64]
    if "age" in best:
        try:
            claim.member_age = int(str(best["age"]))
        except ValueError:
            pass
    if "gender" in best:
        claim.member_gender = {"M": Gender.MALE, "F": Gender.FEMALE, "O": Gender.OTHER}.get(
            str(best["gender"])[:1].upper(), Gender.UNKNOWN)
    claim.hospital_name = str(best["hospital_name"])[:255] if "hospital_name" in best else claim.hospital_name
    claim.hospital_rohini_id = str(best["rohini_id"])[:32] if "rohini_id" in best else claim.hospital_rohini_id
    claim.treating_doctor = str(best["doctor_name"])[:200] if "doctor_name" in best else claim.treating_doctor
    claim.doctor_registration_no = (str(best["doctor_reg_no"])[:64] if "doctor_reg_no" in best
                                    else claim.doctor_registration_no)
    claim.admission_at = _dt(best.get("admission")) or claim.admission_at
    claim.discharge_at = _dt(best.get("discharge")) or claim.discharge_at
    for attr, key in (("claimed_amount", "claimed_amount"), ("sum_insured", "sum_insured")):
        if key in best:
            try:
                setattr(claim, attr, Decimal(str(best[key])).quantize(Decimal("0.01")))
            except Exception:
                pass
    claim.policy_start_date = _dt(best.get("policy_start")) or claim.policy_start_date