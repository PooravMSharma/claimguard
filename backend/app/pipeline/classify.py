"""Decide what kind of document each page is (claim form, discharge summary, bill...).

Rule-based and explainable: each type has weighted phrases. Phrases near the top
of the page (titles) count double. A page with no clear signal that follows a
classified page in the same file is treated as that document's continuation
(e.g. page 2 of a bill), with lower confidence.
"""

import re
from dataclasses import dataclass

from app.models.enums import DocumentType as T

# (phrase, weight). Phrases are matched on lower-cased text with spaces collapsed;
# write them in that form. Strong title phrases weigh 5, supporting phrases 1-2.
SIGNALS: dict[T, list[tuple[str, float]]] = {
    T.CLAIM_FORM: [("claim form", 5), ("to be filled by the insured", 4), ("to be filled by the hospital", 4),
                   ("part a", 1.5), ("part b", 1.5), ("amount claimed", 2), ("declaration", 1), ("claimant", 1.5)],
    T.PREAUTH_FORM: [("pre-authorisation", 5), ("pre-authorization", 5), ("preauthorisation", 5),
                     ("request for cashless", 5), ("authorisation letter", 4), ("authorization letter", 4),
                     ("pre-auth no", 3), ("approved amount", 2), ("estimated cost", 2), ("proposed treatment", 2)],
    T.DISCHARGE_SUMMARY: [("discharge summary", 6), ("course in hospital", 3), ("advice on discharge", 3),
                          ("condition at discharge", 3), ("final diagnosis", 2), ("presenting complaints", 2),
                          ("past history", 1), ("examination on admission", 2)],
    T.FINAL_BILL: [("final bill", 6), ("in-patient bill", 4), ("grand total", 3), ("net payable", 3),
                   ("room rent", 1.5), ("nursing charges", 1.5), ("bill no", 1), ("bill date", 1),
                   ("authorised signatory", 1)],
    T.PHARMACY_BILL: [("pharmacy", 3), ("credit bill", 2), ("drug lic", 3), ("batch", 2), ("mrp", 2),
                      ("exp.", 1), ("pharmacist", 2)],
    T.LAB_REPORT: [("laboratory", 3), ("pathology", 2), ("pathologist", 3), ("reference range", 3),
                   ("investigation result", 2), ("collected", 1), ("reported", 1), ("test report", 3),
                   ("haemoglobin", 1), ("serum", 1)],
    T.IMAGING_REPORT: [("radiology", 4), ("radiologist", 3), ("radiodiagnosis", 3), ("impression", 2),
                       ("findings", 1.5), ("study date", 2), ("modality", 2), ("x-ray", 1), ("usg", 1),
                       ("echocardiography", 1), ("mri", 1), ("ct scan", 1), ("biometry", 1)],
    T.OT_NOTES: [("operation notes", 6), ("anaesthesia record", 4), ("operative findings", 3),
                 ("procedure(s) performed", 3), ("operating surgeon", 2), ("anaesthetist", 1.5),
                 ("post-op instructions", 2), ("implants used", 1.5)],
    T.IMPLANT_INVOICE: [("tax invoice", 5), ("gstin", 2), ("distributor", 2), ("invoice no", 2), ("bill to", 1.5)],
    T.IMPLANT_STICKER: [("implant sticker", 6), ("manufacturer label", 4), ("lot ", 1), ("ref ", 1),
                        ("sterile", 2)],
    T.NURSING_CHART: [("nursing", 2.5), ("tpr chart", 5), ("medication administration", 4),
                      ("medicines administered", 3), ("staff nurse", 2), ("spo2", 1), ("pulse", 1)],
    T.MLC_FIR: [("medico-legal", 6), ("mlc no", 4), ("police station", 3), ("first information report", 6),
                ("fir no", 4), ("intimation", 1)],
    T.POLICY_COPY: [("health card", 5), ("policy schedule", 5), ("sum insured", 2.5), ("policy period", 2.5),
                    ("first inception", 3), ("room eligibility", 2), ("sub-limits", 2), ("member id", 1)],
    T.ID_PROOF: [("photo identity", 5), ("identity card", 4), ("year of birth", 3), ("aadhaar", 4),
                 ("xxxx xxxx", 3), ("government", 1.5), ("pan card", 4), ("driving licence", 4)],
}

CONTINUES = {T.FINAL_BILL, T.PHARMACY_BILL, T.DISCHARGE_SUMMARY, T.NURSING_CHART, T.LAB_REPORT, T.OT_NOTES,
             T.CLAIM_FORM, T.POLICY_COPY}
MIN_SCORE = 4.0


@dataclass
class Classification:
    doc_type: T
    confidence: float
    reasons: list[str]


def _clean(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def score_page(text: str) -> dict[T, tuple[float, list[str]]]:
    clean = _clean(text)
    lines = [ln for ln in clean.splitlines() if ln.strip()]
    head = "\n".join(lines[: max(6, len(lines) // 5)])
    flat = " ".join(clean.split())
    scores: dict[T, tuple[float, list[str]]] = {}
    for doc_type, phrases in SIGNALS.items():
        total, hits = 0.0, []
        for phrase, weight in phrases:
            if phrase in flat:
                bonus = 2.0 if phrase in " ".join(head.split()) else 1.0
                total += weight * bonus
                hits.append(phrase)
        if total:
            scores[doc_type] = (total, hits)
    return scores


def classify(text: str, previous: T | None = None) -> Classification:
    if not text.strip():
        return Classification(T.UNKNOWN, 0.0, ["no text"])
    ranked = sorted(score_page(text).items(), key=lambda kv: kv[1][0], reverse=True)
    if not ranked or ranked[0][1][0] < MIN_SCORE:
        if previous in CONTINUES:
            return Classification(previous, 0.45, [f"continuation of {previous.value}"])
        return Classification(T.UNKNOWN, 0.0, ["no clear signal"])
    top_type, (top, hits) = ranked[0]
    second = ranked[1][1][0] if len(ranked) > 1 else 0.0
    margin = top / (top + second)          # 0.5 = tie, 1.0 = no competitor
    strength = min(1.0, top / 12)          # how much evidence in absolute terms
    confidence = round(min(0.99, 0.35 + 0.4 * margin + 0.25 * strength), 3)
    return Classification(top_type, confidence, hits)