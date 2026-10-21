"""What extraction produces: values with their evidence, grouped by document."""

from dataclasses import dataclass, field


@dataclass
class Value:
    text: str                           # as printed (cleaned)
    normalized: object = None           # date ISO string, number, list... (JSON-safe)
    page_id: str | None = None
    bbox: list[float] | None = None     # [x0, y0, x1, y1] as fractions of the page
    confidence: float = 1.0
    method: str = "rule"                # rule | ai

    def as_json(self) -> dict:
        return {"text": self.text, "normalized": self.normalized, "page_id": self.page_id, "bbox": self.bbox,
                "confidence": round(self.confidence, 3), "method": self.method}


@dataclass
class DocExtract:
    key: str                            # e.g. "final_bill", "lab_report:2"
    doc_type: str
    page_ids: list[str]
    quality: str                        # worst page quality: good | fair | poor | unreadable | blank
    fields: dict[str, Value] = field(default_factory=dict)
    tables: dict[str, list[dict[str, Value]]] = field(default_factory=dict)
    ai_used: bool = False

    def get(self, name: str):
        v = self.fields.get(name)
        return v.normalized if v and v.normalized is not None else (v.text if v else None)


# What each document type should yield. Missing required fields (or an empty required table)
# send the document to the AI reader when it is enabled.
SPEC: dict[str, dict] = {
    "claim_form": {"required": ["policy_no", "patient_name", "claimed_amount", "hospital_name", "rohini_id",
                                "doctor_name", "doctor_reg_no", "admission", "discharge", "diagnosis"],
                   "optional": ["member_id", "age", "gender", "bill_no", "bill_total", "icd_code", "procedures",
                                "claim_type", "submitted"]},
    "preauth_form": {"required": ["preauth_no", "preauth_diagnosis", "approved"],
                     "optional": ["patient_name", "uhid", "rohini_id", "estimate", "requested_on", "proposed_treatment",
                                  "policy_no"]},
    "discharge_summary": {"required": ["patient_name", "admission", "discharge", "diagnosis"],
                          "optional": ["uhid", "age", "gender", "rohini_id", "icd_code", "procedures", "history",
                                       "doctor_name", "doctor_reg_no", "signed", "ward_type", "course", "complaints"]},
    "final_bill": {"required": ["bill_no", "bill_total", "patient_name"], "tables": {"lines": True},
                   "optional": ["uhid", "rohini_id", "admission", "discharge", "room_category", "bill_date"]},
    "pharmacy_bill": {"required": ["pharmacy_total"], "tables": {"lines": True},
                      "optional": ["patient_name", "uhid", "rohini_id"]},
    "lab_report": {"required": ["test", "report_date"], "tables": {"results": True},
                   "optional": ["patient_name", "uhid", "rohini_id", "impression"]},
    "imaging_report": {"required": ["test", "report_date", "impression"],
                       "optional": ["patient_name", "uhid", "rohini_id"]},
    "ot_notes": {"required": ["procedures", "procedure_date", "surgeon"], "tables": {"implants": False},
                 "optional": ["patient_name", "uhid", "rohini_id", "anaesthetist", "anaesthesia"]},
    "implant_invoice": {"required": [], "tables": {"implants": True}, "optional": ["invoice_no", "patient_name"]},
    "implant_sticker": {"required": ["model"], "optional": ["brand", "serial", "patient_name", "rohini_id"]},
    "nursing_chart": {"required": [], "tables": {"days": True}, "optional": ["patient_name", "uhid", "rohini_id"]},
    "mlc_fir": {"required": ["mlc_no"], "optional": ["mlc_date", "police_station", "patient_name", "rohini_id"]},
    "policy_copy": {"required": ["policy_no", "sum_insured", "policy_start", "policy_end"],
                    "optional": ["patient_name", "member_id", "product", "inception", "room_eligibility",
                                 "sub_limits", "used_si", "balance_si"]},
    "id_proof": {"required": ["patient_name"], "optional": ["id_last4", "birth_year", "gender"]},
}


def missing(doc: DocExtract) -> list[str]:
    spec = SPEC.get(doc.doc_type, {})
    gaps = [f for f in spec.get("required", []) if f not in doc.fields]
    for table, required in spec.get("tables", {}).items():
        if required and not doc.tables.get(table):
            gaps.append(table)
    return gaps