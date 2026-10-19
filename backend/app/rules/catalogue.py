"""The v1 check catalogue from the PRD: 45 checks in seven layers.

This is the single list of check IDs the rest of the system uses (synthetic
ground truth now, the rule library and engine from Step 9). Medical-necessity
checks are capped at amber until a clinician has reviewed them.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    id: str
    layer: str
    name: str
    method: str
    max_severity: str  # red | amber | info


_CHECKS = [
    ("C-01", "completeness", "All mandatory documents present for this claim type and package", "rule", "amber"),
    ("C-02", "completeness", "Discharge summary signed, stamped, with doctor registration no.", "ai+rule", "amber"),
    ("C-03", "completeness", "Every billed investigation has a report attached", "rule", "red"),
    ("C-04", "completeness", "Implant billed: invoice and sticker attached", "rule", "red"),
    ("C-05", "completeness", "Accident case: MLC/FIR attached", "rule", "amber"),
    ("F-01", "forensics", "PDF metadata: created or edited after the stated date, editing software traces", "rule",
     "amber"),
    ("F-02", "forensics", "Image tampering: font or spacing mismatch in numbers, pasted regions", "ai", "amber"),
    ("F-03", "forensics", "Same page repeated inside one claim with different patient details", "image_hash", "red"),
    ("F-04", "forensics", "Lab report layout inconsistent with the same lab's other reports", "ai", "amber"),
    ("X-01", "consistency", "Patient name, age, gender, UHID match across all documents", "rule", "red"),
    ("X-02", "consistency", "Dates in logical order: admission, tests, procedure, discharge, claim", "rule", "red"),
    ("X-03", "consistency", "Report dates fall inside the admission period or allowed window", "rule", "amber"),
    ("X-04", "consistency", "Billed medicines appear in the medication chart", "rule", "amber"),
    ("X-05", "consistency", "ICU days billed equal ICU days in nursing notes", "rule", "red"),
    ("X-06", "consistency", "Procedure billed equals procedure in OT notes and discharge summary", "rule+ai", "red"),
    ("X-07", "consistency", "Implant billed equals implant in OT notes and sticker", "rule", "red"),
    ("X-08", "consistency", "Final diagnosis vs pre-auth diagnosis; change explained", "ai", "amber"),
    ("X-09", "consistency", "Hospital name, address, Rohini ID same on all documents", "rule", "amber"),
    ("M-01", "medical", "Admission justified: condition normally treated as outpatient", "ai+stg", "amber"),
    ("M-02", "medical", "Each investigation relevant to the diagnosis", "ai+stg", "amber"),
    ("M-03", "medical", "Repeat imaging or tests without documented reason", "rule+ai", "amber"),
    ("M-04", "medical", "ICU stay supported by vitals and lab values", "ai", "amber"),
    ("M-05", "medical", "Length of stay vs package/STG norm", "benchmark", "amber"),
    ("M-06", "medical", "Severity claimed supported by lab values", "ai", "amber"),
    ("M-07", "medical", "Day-care procedure converted to overnight stay", "rule", "amber"),
    ("B-01", "billing", "Bill arithmetic: line totals, subtotals, taxes, grand total", "rule", "red"),
    ("B-02", "billing", "Bill total equals claimed amount on claim form", "rule", "red"),
    ("B-03", "billing", "Procedure/test rate vs benchmark", "benchmark", "amber"),
    ("B-04", "billing", "Unbundling: items billed separately that the package includes", "rule", "red"),
    ("B-05", "billing", "IRDAI List I non-payable items billed", "rule", "info"),
    ("B-06", "billing", "List II-IV items billed separately", "rule", "amber"),
    ("B-07", "billing", "Consumable quantity per day above norm", "benchmark", "amber"),
    ("B-08", "billing", "Room category billed vs eligibility; proportionate deduction", "rule", "info"),
    ("B-09", "billing", "Multiple surgeries in one admission billed at full rate each", "rule", "amber"),
    ("B-10", "billing", "Pharmacy charged above MRP", "rule", "red"),
    ("B-11", "billing", "Upcoding: billed procedure more complex than notes support", "ai", "amber"),
    ("P-01", "policy", "Policy active on admission date; patient is a covered member", "rule", "red"),
    ("P-02", "policy", "Waiting periods served", "rule", "red"),
    ("P-03", "policy", "Standard exclusions and sub-limits", "rule", "amber"),
    ("P-04", "policy", "Claim within weeks of policy start, or after a lapse", "rule", "amber"),
    ("P-05", "policy", "Moratorium: after 60 months non-disclosure cannot be a ground", "rule", "info"),
    ("P-06", "policy", "Claimed amount within remaining sum insured", "rule", "amber"),
    ("R-01", "registry", "Treating doctor registration exists and matches", "lookup", "amber"),
    ("R-02", "registry", "Hospital registered and verified (HFR / Rohini)", "lookup", "amber"),
    ("R-03", "registry", "Hospital on watchlist or de-empanelled list", "lookup", "amber"),
]

CHECKS: dict[str, Check] = {c[0]: Check(*c) for c in _CHECKS}
SEVERITY_ORDER = {"info": 0, "amber": 1, "red": 2}


def expected_band(check_ids: list[str]) -> str:
    """Band a claim would get from these findings alone (PRD banding rules, ignoring score)."""
    worst = max((SEVERITY_ORDER[CHECKS[c].max_severity] for c in check_ids), default=-1)
    return {2: "red", 1: "amber"}.get(worst, "green")