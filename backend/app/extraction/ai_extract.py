"""Read a document with the AI model: page images + OCR text + the rules' draft -> corrected values.

Used for scanned documents and for documents where the rules left required items empty. The model
copies values as printed; we normalise them with the same code as the rules, then place each value on
the page by finding its words, so the evidence link still works.
"""

import json
import logging
from difflib import SequenceMatcher

from app.ai.client import AIProvider, AIResult, image_block, text_block
from app.extraction.documents import (
    BILL,
    INVOICE,
    LAB,
    NURSING,
    OT_IMPLANTS,
    PHARMACY,
    normalize,
    normalize_row,
    tidy,
)
from app.extraction.layout import W
from app.extraction.model import SPEC, DocExtract, Value
from app.extraction.values import norm_text

log = logging.getLogger(__name__)

SYSTEM = (
    "You read Indian hospital insurance claim documents for a claims adjudicator. Copy each value exactly as "
    "printed on the page image (same spelling, digits and date format). The OCR text and the draft values are "
    "machine readings that may contain errors: trust the image. If a value is not on the page, or you cannot "
    "read it with confidence, return null for it and list the field in `uncertain`. Never guess or infer a "
    "value that is not printed. Amounts: digits as printed, e.g. 12,320.00. Dates: as printed, e.g. 05/08/2026 13:50."
)

FIELD_HELP = {
    "patient_name": "patient's full name", "uhid": "hospital patient ID (UHID / MRD no.)", "age": "age in years",
    "gender": "M, F or O", "policy_no": "insurance policy number", "member_id": "insurer member ID",
    "hospital_name": "hospital name from the letterhead or form", "rohini_id": "hospital Rohini ID (13 digits)",
    "doctor_name": "treating doctor's name with the Dr. prefix, e.g. Dr. A. Kumar (no qualifications)",
    "doctor_reg_no": "doctor's registration number",
    "admission": "admission date and time", "discharge": "discharge date and time",
    "diagnosis": "final diagnosis text, without the ICD code", "icd_code": "ICD-10 code if printed",
    "procedures": "procedures performed, one per item (empty list for medical management)",
    "claimed_amount": "amount claimed", "bill_total": "bill grand total", "bill_no": "bill number",
    "submitted": "date the claim form was signed/submitted", "claim_type": "cashless or reimbursement",
    "preauth_no": "pre-authorisation number", "preauth_diagnosis": "provisional diagnosis on the pre-auth",
    "proposed_treatment": "proposed treatment", "estimate": "estimated cost", "approved": "approved amount",
    "requested_on": "date/time the pre-auth was requested", "history": "past history text",
    "signed": "'yes' if the summary carries a doctor's signature with registration number, else 'no'",
    "ward_type": "ward / room category", "course": "course in hospital", "complaints": "presenting complaints",
    "room_category": "room category billed", "bill_date": "bill date", "pharmacy_total": "pharmacy bill total",
    "test": "name of the test or scan", "report_date": "report / collection / study date and time",
    "impression": "the complete impression / conclusion text, every sentence, without the doctor's signature line",
    "procedure_date": "date and start time of the operation",
    "surgeon": "operating surgeon", "anaesthetist": "anaesthetist", "anaesthesia": "type of anaesthesia",
    "invoice_no": "invoice number", "model": "implant model / REF", "brand": "implant brand / manufacturer",
    "serial": "implant serial / LOT", "mlc_no": "MLC or FIR number", "mlc_date": "MLC date and time",
    "police_station": "police station", "product": "insurance product name", "sum_insured": "sum insured",
    "policy_start": "policy period start date", "policy_end": "policy period end date",
    "inception": "first inception date", "room_eligibility": "room eligibility text",
    "sub_limits": "sub-limits text as printed",
    "used_si": "amount printed as 'Claims paid this year' (claims already paid this policy year)",
    "balance_si": "balance sum insured", "id_last4": "last 4 digits of the ID number", "birth_year": "year of birth",
}
TABLES = {
    "final_bill": ("lines", BILL.columns), "pharmacy_bill": ("lines", PHARMACY.columns),
    "lab_report": ("results", LAB.columns), "nursing_chart": ("days", NURSING.columns),
    "ot_notes": ("implants", OT_IMPLANTS.columns), "implant_invoice": ("implants", INVOICE.columns),
}


def schema_for(doc_type: str) -> dict:
    spec = SPEC.get(doc_type, {})
    names = list(dict.fromkeys(spec.get("required", []) + spec.get("optional", [])))
    props: dict = {}
    for name in names:
        if name == "procedures":
            props[name] = {"type": ["array", "null"], "items": {"type": "string"},
                           "description": FIELD_HELP[name]}
        else:
            props[name] = {"type": ["string", "null"], "description": FIELD_HELP.get(name, name.replace("_", " "))}
    if doc_type in TABLES:
        table, columns = TABLES[doc_type]
        props[table] = {"type": "array", "description": f"every row of the {table.replace('_', ' ')} table, in order",
                        "items": {"type": "object",
                                  "properties": {c: {"type": ["string", "null"]} for c in columns}}}
    props["uncertain"] = {"type": "array", "items": {"type": "string"},
                          "description": "fields you could not read with confidence"}
    return {"type": "object", "properties": props, "required": list(props)}


def _draft(doc: DocExtract) -> dict:
    out: dict = {k: v.text for k, v in doc.fields.items()}
    for t, rows in doc.tables.items():
        out[t] = [{c: v.text for c, v in r.items()} for r in rows]
    return out


def locate(text: str, words: list[W]) -> tuple[str, list[float], float] | None:
    """Find a value's words on the page: (page_id, bbox, match score). Fuzzy, to survive OCR slips."""
    target = norm_text(text)
    if len(target) < 2 or not words:
        return None
    n_tokens = max(1, len(str(text).split()))
    best: tuple[float, int, int] | None = None
    for i in range(len(words)):
        acc = ""
        for j in range(i, min(len(words), i + n_tokens + 2)):
            if words[j].page_no != words[i].page_no:
                break
            acc += norm_text(words[j].text)
            if len(acc) > len(target) * 1.6:
                break
            score = SequenceMatcher(None, acc, target).ratio()
            if best is None or score > best[0]:
                best = (score, i, j)
    if best is None or best[0] < 0.75:
        return None
    _, i, j = best
    span = words[i:j + 1]
    bbox = [min(w.x0 for w in span), min(w.y0 for w in span), max(w.x1 for w in span), max(w.y1 for w in span)]
    return span[0].page_id, [round(b, 4) for b in bbox], best[0]


def build_request(doc: DocExtract, images: list[bytes], page_texts: list[str]) -> tuple[list[dict], dict]:
    content = [text_block(
        f"Document type: {doc.doc_type.replace('_', ' ')}. {len(images)} page(s) follow, each as an image "
        "with its OCR text.")]
    for n, (png, text) in enumerate(zip(images, page_texts), 1):
        content.append(text_block(f"Page {n} image:"))
        content.append(image_block(png))
        content.append(text_block(f"Page {n} OCR text (may contain errors):\n{text[:6000]}"))
    content.append(text_block("Draft values read by rules (may be wrong or missing):\n"
                              + json.dumps(_draft(doc), ensure_ascii=False)[:8000]))
    return content, schema_for(doc.doc_type)


def merge(doc: DocExtract, result: dict, words: list[W], digital: bool) -> int:
    """Fold the model's answer into the document. Returns the number of values it supplied or changed.

    Digital text is exact, so rule values from a text layer win; on scans the model's reading wins.
    """
    uncertain = set(result.get("uncertain") or [])
    changed = 0
    for name, raw in result.items():
        if name in ("uncertain",) or name in TABLES.get(doc.doc_type, ("",))[:1]:
            continue
        if raw is None or raw == "" or raw == []:
            continue
        text = "; ".join(raw) if isinstance(raw, list) else str(raw)
        normalized = raw if isinstance(raw, list) else normalize(name, text)
        if normalized is None:
            continue
        current = doc.fields.get(name)
        if current is not None and (digital or norm_text(current.text) == norm_text(text)):
            continue
        place = None
        if isinstance(raw, list):
            place = locate(raw[0], words) if raw else None
        else:
            place = locate(text, words)
        conf = 0.6 if name in uncertain else (0.9 if place and place[2] > 0.9 else 0.8)
        doc.fields[name] = Value(text, normalized, place[0] if place else (doc.page_ids[0] if doc.page_ids else None),
                                 place[1] if place else None, conf, "ai")
        changed += 1
    if doc.doc_type in TABLES:
        table, columns = TABLES[doc.doc_type]
        rows = result.get(table) or []
        if rows and (not digital or not doc.tables.get(table)):
            new_rows = []
            for row in rows:
                values: dict[str, Value] = {}
                anchor = next((row[c] for c in ("description", "drug", "parameter", "date", "model", "item", "type")
                               if row.get(c)), None)
                place = locate(str(anchor), words) if anchor else None
                for col, raw in row.items():
                    if col in columns and raw not in (None, ""):
                        values[col] = Value(str(raw), None, place[0] if place else None,
                                            place[1] if place else None, 0.85, "ai")
                if values:
                    new_rows.append(normalize_row(table, values))
            doc.tables[table] = new_rows
            changed += len(new_rows)
    if changed:
        doc.ai_used = True
    tidy(doc)
    return changed


def needs_ai(doc: DocExtract, mode: str, scanned: bool = False) -> bool:
    """'all': every document. 'gaps': every scanned document (OCR slips are invisible to the rules, so a
    scan that looks complete can still be wrong) and any document where the rules left a required item empty."""
    if mode == "all" or scanned:
        return True
    from app.extraction.model import missing

    return doc.quality in ("fair", "poor", "unreadable") or bool(missing(doc))


def read_with_ai(provider: AIProvider, doc: DocExtract, images: list[bytes], page_texts: list[str],
                 ) -> AIResult:
    content, schema = build_request(doc, images, page_texts)
    return provider.structured(
        system=SYSTEM, content=content, tool_name="record_document",
        tool_description=f"Record the values printed on this {doc.doc_type.replace('_', ' ')}.",
        schema=schema, max_tokens=6000,
    )
