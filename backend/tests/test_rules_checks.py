"""Step 9: every deterministic check, on a small hand-built genuine claim and on a version with one problem.

No PDFs and no database: the claim record is built directly as DocExtract objects, the way Step 8 stores it.
"""

import copy
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfWriter

from app.extraction.model import DocExtract, Value
from app.rules.catalogue import CHECKS
from app.rules.context import ClaimContext, Facts, OriginalFile, Page
from app.rules.engine import AI_CHECKS, REGISTRY, run_checks
from app.rules.reference import HospitalRef, PackageRef, PolicyRef, Reference, TariffRef

# ---------------------------------------------------------------- a genuine claim


def v(text, normalized=None, page="p1", conf=1.0, method="rule"):
    return Value(str(text), normalized if normalized is not None else text, page, [0.1, 0.1, 0.3, 0.12], conf, method)


def row(**cols):
    return {k: v(x, x if not isinstance(x, str) else None) for k, x in cols.items()}


PROC = "Laparoscopic cholecystectomy"
IDENTITY = {"patient_name": "Rajesh Pillai", "uhid": "UH5979061", "rohini_id": "8900080712982",
            "hospital_name": "Ganga Superspeciality Hospital"}


def genuine_docs() -> list[DocExtract]:
    def doc(key, t, page, fields, tables=None):
        d = DocExtract(key, t, [page], "good", {k: v(x, page=page) if not isinstance(x, Value) else x
                                                for k, x in fields.items()}, tables or {})
        return d

    lines = [
        row(date="2026-04-05", category="Investigations", description="Complete Blood Count", qty=1, rate=350,
            amount=350),
        row(date="2026-04-05", category="Investigations", description="USG Abdomen", qty=1, rate=1200,
            amount=1200),
        row(date="2026-04-05", category="Room Rent", description="Room rent - semi-private ward", qty=2,
            rate=2200, amount=4400),
        row(date="2026-04-05", category="Nursing", description="Nursing charges", qty=2, rate=400, amount=800),
        row(date="2026-04-06", category="Professional Fees", description=f"Surgeon fee - {PROC}", qty=1,
            rate=12250, amount=12250),
        row(date="2026-04-06", category="Professional Fees", description="Anaesthetist fee", qty=1, rate=4200,
            amount=4200),
        row(date="2026-04-06", category="OT Charges", description="Operation theatre charges", qty=1, rate=5250,
            amount=5250),
        row(date="2026-04-05", category="Consumables", description="Disposable Syringe 5 ml", qty=8, rate=12,
            amount=96),
        row(date="2026-04-07", category="Pharmacy", description="Pharmacy and medicines (as per pharmacy bill)",
            qty=1, rate=820, amount=820),
    ]
    total = sum(float(str(r["amount"].normalized)) for r in lines)
    pharmacy = [row(drug="Inj Ceftriaxone 1 g", qty=4, mrp=62, rate=62, amount=248),
                row(drug="Inj Pantoprazole 40 mg", qty=2, mrp=48, rate=48, amount=96),
                row(drug="Inj Diclofenac 75 mg", qty=4, mrp=10, rate=10, amount=40),
                row(drug="Inj Ondansetron 4 mg", qty=4, mrp=109, rate=109, amount=436)]
    meds = "Inj Ceftriaxone 1 g; Inj Pantoprazole 40 mg; Inj Diclofenac 75 mg; Inj Ondansetron 4 mg"
    days = [row(date="2026-04-05", location="Ward", meds=meds), row(date="2026-04-06", location="Ward", meds=meds)]
    return [
        doc("claim_form", "claim_form", "p1", {
            **IDENTITY, "policy_no": "SHP/12345678/2025", "member_id": "M1234567", "claimed_amount": total,
            "bill_total": total, "doctor_name": "Dr. Anil Pillai", "doctor_reg_no": "MMC/1999/88069",
            "diagnosis": "Symptomatic cholelithiasis", "icd_code": "K80.20",
            "submitted": v("07/04/2026", "2026-04-07T00:00:00", "p1"),
            "admission": v("05/04/2026 09:00", "2026-04-05T09:00:00", "p1"),
            "discharge": v("07/04/2026 13:00", "2026-04-07T13:00:00", "p1")}),
        doc("preauth_form", "preauth_form", "p2", {**IDENTITY, "preauth_no": "PA12345678", "approved": 32000,
                                                  "preauth_diagnosis": "Symptomatic cholelithiasis"}),
        doc("discharge_summary", "discharge_summary", "p3", {
            **IDENTITY, "age": 45, "gender": "M", "diagnosis": "Symptomatic cholelithiasis", "icd_code": "K80.20",
            "admission": v("05/04/2026 09:00", "2026-04-05T09:00:00", "p3"),
            "discharge": v("07/04/2026 13:00", "2026-04-07T13:00:00", "p3"),
            "procedures": v(PROC, [PROC], "p3"), "doctor_name": "Dr. Anil Pillai",
            "doctor_reg_no": "MMC/1999/88069", "signed": v("yes", True, "p3"),
            "history": "No known comorbidities.", "ward_type": v("Semi-private", "semi_private", "p3")}),
        doc("final_bill", "final_bill", "p4", {**IDENTITY, "bill_no": "IPB/2026/78720", "bill_total": total,
                                              "age": 45, "gender": "M"}, {"lines": lines}),
        doc("pharmacy_bill", "pharmacy_bill", "p5", {**IDENTITY, "pharmacy_total": 820}, {"lines": pharmacy}),
        doc("lab_report:1", "lab_report", "p6", {**IDENTITY, "test": "Complete Blood Count",
                                                 "report_date": v("05/04/2026 11:00", "2026-04-05T11:00:00", "p6")}),
        doc("imaging_report:1", "imaging_report", "p7", {
            **IDENTITY, "test": "USG Abdomen", "report_date": v("05/04/2026 12:00", "2026-04-05T12:00:00", "p7"),
            "impression": "Multiple calculi."}),
        doc("ot_notes", "ot_notes", "p8", {**IDENTITY, "procedures": v(PROC, [PROC], "p8"),
                                          "procedure_date": v("06/04/2026 10:00", "2026-04-06T10:00:00", "p8"),
                                          "surgeon": "Dr. Anil Pillai"}),
        doc("nursing_chart", "nursing_chart", "p9", dict(IDENTITY), {"days": days}),
        doc("policy_copy", "policy_copy", "p10", {
            "policy_no": "SHP/12345678/2025", "patient_name": "Rajesh Pillai", "sum_insured": 500000,
            "used_si": 0, "policy_start": v("01/06/2025", "2025-06-01", "p10"),
            "policy_end": v("31/05/2026", "2026-05-31", "p10"), "inception": v("01/06/2021", "2021-06-01", "p10")}),
        doc("id_proof", "id_proof", "p11", {"patient_name": "Rajesh Pillai"}),
    ]


def reference() -> Reference:
    ref = Reference.from_files(None)
    ref.packages = [
        PackageRef("SYN-SG020", PROC, Decimal(35000), 3, False,
                   ("room", "nursing", "consultation", "surgeon_fee", "anaesthesia", "ot_charges", "consumables"),
                   ("K80", "cholelithiasis")),
        PackageRef("SYN-OP009", "Phacoemulsification with foldable IOL", Decimal(25000), 0, True, (), ("H25",)),
    ]
    ref.tariffs = [
        TariffRef("SYN-T-CBC", "Complete Blood Count", "Investigation", "tier_1", "any", Decimal(350)),
        TariffRef("SYN-SF-SG020", f"Surgeon fee - {PROC}", "Professional fee", "tier_1", "any", Decimal(12250)),
        TariffRef("SYN-AF-SG020", f"Anaesthetist fee - {PROC}", "Professional fee", "tier_1", "any", Decimal(4200)),
        TariffRef("SYN-OT-SG020", f"Operation theatre charges - {PROC}", "OT charges", "tier_1", "any",
                  Decimal(5250)),
        TariffRef("SYN-RM-SEMI", "Room rent - semi-private ward", "Room", "tier_1", "semi_private", Decimal(2200)),
        TariffRef("SYN-NURSING", "Nursing charges", "Nursing", "tier_1", "any", Decimal(400)),
    ]
    ref.doctors = {"MMC/1999/88069": "Dr. Anil Pillai"}
    ref.hospitals = {"8900080712982": HospitalRef("8900080712982", "IN1", "Ganga Superspeciality Hospital",
                                                  "Nagpur", "tier_1")}
    ref.watchlist = {}
    ref.policies = {"SHP123456782025": [PolicyRef(
        "SHP/12345678/2025", "M1234567", "Rajesh Pillai", "SHP-FAM", Decimal(500000), Decimal(0),
        date(2025, 6, 1), date(2026, 5, 31), date(2021, 6, 1), "semi_private", None, {"cataract_per_eye": 40000},
        ())]}
    return ref


def context(docs=None, ref=None, stage="final_cashless", pages=None, files=None) -> ClaimContext:
    docs = docs if docs is not None else genuine_docs()
    pages = pages if pages is not None else [Page(pid, d.doc_type, "text_layer", "good", None, "f1")
                                             for d in docs for pid in d.page_ids]
    return ClaimContext(Facts("CG-TEST-1", stage), docs, pages, ref or reference(), files or [])


def outcome(ctx: ClaimContext, rule_id: str):
    return next(o for o in run_checks(ctx, {}) if o.rule_id == rule_id)


def d(docs, doc_type, n=0) -> DocExtract:
    return [x for x in docs if x.doc_type == doc_type][n]


def line(docs, start: str) -> dict:
    return next(r for r in d(docs, "final_bill").tables["lines"] if r["description"].text.startswith(start))


def set_amounts(docs, delta: float):
    """Keep the bill, claim form and totals consistent after adding money."""
    for doc_type, name in (("final_bill", "bill_total"), ("claim_form", "bill_total"), ("claim_form",
                                                                                         "claimed_amount")):
        f = d(docs, doc_type).fields[name]
        f.normalized = float(str(f.normalized)) + delta
        f.text = str(f.normalized)


def add_line(docs, **cols):
    d(docs, "final_bill").tables["lines"].append(row(**cols))
    set_amounts(docs, cols["amount"])


# ---------------------------------------------------------------- the genuine claim raises nothing


def test_genuine_claim_raises_no_findings():
    outcomes = run_checks(context(), {})
    assert len(outcomes) == len(CHECKS)
    found = {o.rule_id: o.findings[0].title for o in outcomes if o.findings}
    assert found == {}
    status = {o.rule_id: o.status for o in outcomes}
    # without an AI reviewer the judgement checks wait for it; X-08 needs no AI when the diagnosis is unchanged
    assert {status[c] for c in AI_CHECKS if c != "X-08"} == {"pending_ai"}
    assert status["X-08"] == "passed"
    assert status["B-01"] == status["X-01"] == status["P-01"] == status["R-01"] == "passed"
    assert status["C-05"] == "not_applicable"                    # not an accident case
    assert set(REGISTRY) == set(CHECKS)                           # every check is implemented


# ---------------------------------------------------------------- one problem at a time

def mutate_c01(docs):
    docs[:] = [x for x in docs if x.doc_type != "id_proof"]


def mutate_c02(docs):
    s = d(docs, "discharge_summary")
    s.fields["signed"] = v("no", False, "p3")
    del s.fields["doctor_reg_no"]


def mutate_c03(docs):
    add_line(docs, date="2026-04-05", category="Investigations", description="Serum Amylase", qty=1, rate=550,
             amount=550)


def mutate_c04(docs):
    add_line(docs, date="2026-04-06", category="Implants", description="Intraocular lens - Foldable SN60-SYN",
             qty=1, rate=8000, amount=8000)


def mutate_c05(docs):
    d(docs, "discharge_summary").fields["diagnosis"] = v("Closed fracture of neck of femur (fall from two-wheeler)")


def mutate_x01(docs):
    d(docs, "pharmacy_bill").fields["patient_name"] = v("Suresh Pillai", page="p5")


def mutate_x02(docs):
    d(docs, "ot_notes").fields["procedure_date"] = v("09/04/2026 10:00", "2026-04-09T10:00:00", "p8")


def mutate_x03(docs):
    d(docs, "lab_report").fields["report_date"] = v("10/02/2026 10:00", "2026-02-10T10:00:00", "p6")


def mutate_x04(docs):
    d(docs, "pharmacy_bill").tables["lines"].append(row(drug="Inj Meropenem 1 g", qty=4, mrp=1450, rate=1450,
                                                        amount=5800))


def mutate_x05(docs):
    add_line(docs, date="2026-04-05", category="ICU", description="ICU charges", qty=2, rate=5500, amount=11000)


def mutate_x06(docs):
    d(docs, "discharge_summary").fields["procedures"] = v("Open cholecystectomy", ["Open cholecystectomy"], "p3")


def mutate_x07(docs):
    add_line(docs, date="2026-04-06", category="Implants", description="Drug-eluting stent - DES-3.0x28-SYN",
             qty=1, rate=30000, amount=30000)
    docs.append(DocExtract("implant_sticker", "implant_sticker", ["p12"], "good",
                           {"model": v("DES-3.0x28-LITE", page="p12")}))
    docs.append(DocExtract("implant_invoice", "implant_invoice", ["p13"], "good", {}))


def mutate_x09(docs):
    d(docs, "pharmacy_bill").fields["rohini_id"] = v("8900080111111", page="p5")


def mutate_m03(docs):
    docs.append(DocExtract("imaging_report:2", "imaging_report", ["p14"], "good", {
        "test": v("USG Abdomen", page="p14"),
        "report_date": v("05/04/2026 22:00", "2026-04-05T22:00:00", "p14")}))
    add_line(docs, date="2026-04-05", category="Investigations", description="USG Abdomen", qty=1, rate=1200,
             amount=1200)


def mutate_m05(docs):
    for t in ("claim_form", "discharge_summary"):
        d(docs, t).fields["discharge"] = v("13/04/2026 13:00", "2026-04-13T13:00:00")


def mutate_m07(docs):
    for t in ("claim_form", "discharge_summary"):
        d(docs, t).fields["diagnosis"] = v("Age-related nuclear cataract")
        d(docs, t).fields["icd_code"] = v("H25.1")
    for t in ("discharge_summary", "ot_notes"):
        d(docs, t).fields["procedures"] = v("Phacoemulsification with foldable IOL",
                                            ["Phacoemulsification with foldable IOL"])
    line(docs, "Surgeon fee")["description"] = v("Surgeon fee - Phacoemulsification with foldable IOL")


def mutate_b01(docs):
    line(docs, "Nursing")["amount"] = v("1800", 1800.0)
    set_amounts(docs, 1000)


def mutate_b02(docs):
    f = d(docs, "claim_form").fields["claimed_amount"]
    f.normalized = float(str(f.normalized)) + 5000


def mutate_b03(docs):
    r = line(docs, "Surgeon fee")
    r["rate"], r["amount"] = v("36750", 36750.0), v("36750", 36750.0)
    set_amounts(docs, 24500)


def mutate_b04(docs):
    add_line(docs, date="2026-04-06", category="Package", description=f"Package charges - {PROC} (SYN-SG020)",
             qty=1, rate=35000, amount=35000)


def mutate_b05(docs):
    add_line(docs, date="2026-04-05", category="Miscellaneous", description="Attendant charges", qty=1, rate=500,
             amount=500)


def mutate_b06(docs):
    add_line(docs, date="2026-04-05", category="Miscellaneous", description="Admission kit", qty=1, rate=750,
             amount=750)


def mutate_b07(docs):
    r = line(docs, "Disposable Syringe")
    r["qty"], r["amount"] = v("60", 60.0), v("720", 720.0)
    set_amounts(docs, 624)


def mutate_b08(docs):
    r = line(docs, "Room rent")
    r["description"], r["rate"], r["amount"] = v("Room rent - private room"), v("3800", 3800.0), v("7600", 7600.0)
    set_amounts(docs, 3200)


def mutate_b09(docs):
    add_line(docs, date="2026-04-06", category="Professional Fees",
             description="Surgeon fee - Open inguinal hernia repair with mesh", qty=1, rate=12250, amount=12250)
    for t in ("discharge_summary", "ot_notes"):
        both = [PROC, "Open inguinal hernia repair with mesh"]
        d(docs, t).fields["procedures"] = v("; ".join(both), both)


def mutate_b10(docs):
    r = d(docs, "pharmacy_bill").tables["lines"][0]
    r["rate"], r["amount"] = v("86.8", 86.8), v("347.2", 347.2)


def mutate_p01(docs):
    d(docs, "policy_copy").fields["policy_end"] = v("01/03/2026", "2026-03-01")


def mutate_p02(docs):
    d(docs, "policy_copy").fields["inception"] = v("01/10/2025", "2025-10-01")


def mutate_p03(docs):
    for t in ("claim_form", "discharge_summary"):
        d(docs, t).fields["diagnosis"] = v("Single spontaneous delivery")
        d(docs, t).fields["icd_code"] = v("O80")


def mutate_p04(docs):
    d(docs, "policy_copy").fields["inception"] = v("15/02/2026", "2026-02-15")


def mutate_p05(docs):
    d(docs, "discharge_summary").fields["history"] = v("Known case of hypertension for 8 years.")
    d(docs, "policy_copy").fields["inception"] = v("01/06/2019", "2019-06-01")


def mutate_p06(docs):
    d(docs, "policy_copy").fields["used_si"] = v("490000", 490000.0)


def mutate_r01(docs):
    for t in ("claim_form", "discharge_summary"):
        d(docs, t).fields["doctor_reg_no"] = v("KMC/2010/12345")


def mutate_r02(docs):
    for doc in docs:
        if "rohini_id" in doc.fields:
            doc.fields["rohini_id"] = v("8900080999999")


MUTATIONS = {name[7].upper() + "-" + name[8:]: fn for name, fn in globals().items() if name.startswith("mutate_")}


def _policy_from_copy_only(ref: Reference) -> Reference:
    ref.policies = {}
    return ref


@pytest.mark.parametrize("rule_id", sorted(MUTATIONS))
def test_each_check_flags_its_problem(rule_id):
    docs = genuine_docs()
    MUTATIONS[rule_id](docs)
    ref = reference()
    if rule_id.startswith("P-"):
        ref = _policy_from_copy_only(ref)      # use the policy copy values the mutation changed
    if rule_id == "R-03":
        ref.watchlist = {"8900080712982": "De-empanelled"}
    o = outcome(context(docs, ref), rule_id)
    assert o.status == "flagged" and o.findings, (rule_id, o.status, o.detail)
    f = o.findings[0]
    assert f.title and f.explanation and f.fingerprint


def _pdf(producer: str, created: str, modified: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.add_metadata({"/Producer": producer, "/CreationDate": created, "/ModDate": modified})
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_watchlisted_hospital_and_f01_metadata():
    ref = reference()
    ref.watchlist = {"8900080712982": "De-empanelled after audit"}
    assert outcome(context(ref=ref), "R-03").status == "flagged"
    edited = _pdf("Adobe Photoshop 25.4", "D:20260407150000", "D:20260420150000")
    assert outcome(context(files=[OriginalFile("f1", "claim.pdf", "application/pdf", lambda: edited)]),
                   "F-01").status == "flagged"
    clean = _pdf("MedSoft HIS", "D:20260407150000", "D:20260407150000")
    assert outcome(context(files=[OriginalFile("f1", "claim.pdf", "application/pdf", lambda: clean)]),
                   "F-01").status == "passed"
    rescanned = _pdf("Scanner Suite 4", "D:20260420150000", "D:20260420150000")   # scanned after it arrived
    assert outcome(context(files=[OriginalFile("f1", "scan.pdf", "application/pdf", lambda: rescanned)]),
                   "F-01").status == "passed"


def test_f03_same_page_image_with_another_patient():
    docs = genuine_docs()
    copy_doc = copy.deepcopy(d(docs, "lab_report"))
    copy_doc.key, copy_doc.page_ids = "lab_report:2", ["p20"]
    copy_doc.fields["patient_name"] = v("Kavita Sharma", page="p20")
    docs.append(copy_doc)
    pages = [Page(pid, x.doc_type, "text_layer", "good", "ab" * 8, "f1") for x in docs for pid in x.page_ids]
    o = outcome(context(docs, pages=pages), "F-03")
    assert o.status == "flagged" and "patient name" in o.findings[0].title


# ---------------------------------------------------------------- severity policy


def test_red_on_a_scanned_value_is_shown_as_amber():
    docs = genuine_docs()
    mutate_b10(docs)
    ctx = context(docs)
    assert outcome(ctx, "B-10").findings[0].severity == "red"
    pages = [Page(pid, x.doc_type, "ocr" if x.doc_type == "pharmacy_bill" else "text_layer", "good", None, "f1")
             for x in docs for pid in x.page_ids]
    scanned = outcome(context(docs, pages=pages), "B-10").findings[0]
    assert scanned.severity == "amber" and scanned.downgraded and "scan" in scanned.explanation


def test_unclear_scan_values_are_not_evaluated_rather_than_flagged():
    docs = genuine_docs()
    mutate_x01(docs)
    d(docs, "pharmacy_bill").fields["patient_name"].confidence = 0.6      # OCR was unsure
    pages = [Page(pid, x.doc_type, "ocr", "fair", None, "f1") for x in docs for pid in x.page_ids]
    assert not outcome(context(docs, pages=pages), "X-01").findings


def test_medical_checks_are_amber_and_need_review():
    docs = genuine_docs()
    mutate_m05(docs)
    f = outcome(context(docs), "M-05").findings[0]
    assert f.severity == "amber" and f.needs_medical_review


def test_reviewer_correction_beats_the_reading():
    docs = genuine_docs()
    mutate_b02(docs)
    claimed = d(docs, "claim_form").fields["claimed_amount"]
    total = d(docs, "final_bill").fields["bill_total"].normalized
    d(docs, "claim_form").fields["claimed_amount"] = Value(str(total), total, claimed.page_id, claimed.bbox, 1.0,
                                                           "reviewer")
    assert outcome(context(docs), "B-02").status == "passed"


def test_one_failing_check_does_not_stop_the_others(monkeypatch):
    from app.rules import engine

    def boom(ctx):
        raise RuntimeError("bad data")

    monkeypatch.setitem(engine.REGISTRY, "B-01", boom)
    outcomes = {o.rule_id: o for o in run_checks(context(), {})}
    assert outcomes["B-01"].status == "error" and "bad data" in (outcomes["B-01"].detail or "")
    assert outcomes["B-02"].status == "passed" and len(outcomes) == len(CHECKS)


def test_disabled_rule_does_not_run():
    from app.rules.engine import RuleView

    rules = {"B-02": RuleView("B-02", "billing", "red", 1, "disabled", {})}
    docs = genuine_docs()
    mutate_b02(docs)
    o = next(x for x in run_checks(context(docs), rules) if x.rule_id == "B-02")
    assert o.status == "disabled" and not o.findings


def test_money_wording():
    from app.rules.checks.common import rs, same_code, same_person

    assert rs(Decimal("150000")) == "Rs. 1,50,000" and rs(1234.5) == "Rs. 1,234.50" and rs(12) == "Rs. 12"
    assert same_person("Rajesh Pillai", "Mr. Rajesh Pillai", lenient=False)
    assert same_person("Rajesh K Pillai", "Rajesh Kumar Pillai", lenient=False)
    assert not same_person("Suresh Pillai", "Rajesh Pillai", lenient=False)
    assert same_person("Rayesh Pillay", "Rajesh Pillai", lenient=True)
    assert same_code("UHS979061", "UH5979061", lenient=True) and not same_code("UHS979061", "UH5979061",
                                                                                lenient=False)
