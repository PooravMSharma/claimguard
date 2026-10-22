"""One seeder per catalogue check: each plants exactly one known issue in a genuine claim.

A seeder declares which claims it can work on (`fits`) and returns the Issue
it planted: the check that should fire, a plain description, the documents
that show it, and the money at stake where there is one. `also` lists other
checks that will legitimately fire as a side effect.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from random import Random

from app.synthetic.case import (
    ICU_RATE,
    ROOM_RATE,
    TIER_FACTOR,
    BillLine,
    Case,
    Document,
    Issue,
    LabReport,
    NursingDay,
    PharmacyLine,
    lab_rows,
    make_doctor,
    make_hospital,
    make_person_name,
)
from app.synthetic.scenarios import IRRELEVANT_TEST, Scenario, Test


@dataclass
class Context:
    """Shared registries the R-layer seeders write into."""
    unregistered_doctors: set[str] = field(default_factory=set)
    unregistered_hospitals: set[str] = field(default_factory=set)
    watchlist: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Seeder:
    check_id: str
    fits: Callable[[Scenario], bool]
    apply: Callable[[Case, Random, Context], Issue]
    stage: str | None = None          # force a claim stage
    combinable: bool = False          # safe to add as a second issue


SEEDERS: dict[str, Seeder] = {}


def seeder(check_id: str, fits: Callable[[Scenario], bool] = lambda s: True, stage: str | None = None,
           combinable: bool = False):
    def register(fn):
        SEEDERS[check_id] = Seeder(check_id, fits, fn, stage, combinable)
        return fn
    return register


# ---------------------------------------------------------------- helpers

def tier(case: Case) -> float:
    return TIER_FACTOR[case.hospital.tier]


def add_line(case: Case, category: str, description: str, qty: float, rate: float, day=None) -> BillLine:
    line = BillLine(category, description, day or case.admission.date(), qty, rate, round(qty * rate, 2))
    pharmacy_at = next((i for i, x in enumerate(case.bill) if x.category == "Pharmacy"), len(case.bill))
    case.bill.insert(pharmacy_at, line)
    return line


def finish(case: Case) -> None:
    case.sync_pharmacy_line()
    case.sync_claim_amount()


def surgical(s: Scenario) -> bool:
    return s.surgical


def overnight(s: Scenario) -> bool:
    return not s.day_care


def add_report(case: Case, test: Test, when: datetime, rng: Random, *, normal: bool = False) -> LabReport:
    rows, impression = lab_rows(test, case.scenario, rng, normal=normal)
    template = case.reports[0]
    report = LabReport(test.name, test.kind, when, template.lab_name, rows, impression, template.signatory)
    case.reports.append(report)
    doc_type = "lab_report" if test.kind == "lab" else "imaging_report"
    at = max(i for i, d in enumerate(case.documents) if d.type in ("lab_report", "imaging_report")) + 1
    case.documents.insert(at, Document(doc_type, test.name, {"report_index": len(case.reports) - 1}))
    return report


# ---------------------------------------------------------------- C: completeness

@seeder("C-01", combinable=True)
def missing_id_proof(case, rng, ctx):
    case.documents = [d for d in case.documents if d.type != "id_proof"]
    return Issue("C-01", "Photo ID proof of the patient is missing from the file.", [], "id_proof")


@seeder("C-02", combinable=True)
def unsigned_summary(case, rng, ctx):
    case.signed_summary = False
    return Issue("C-02", "Discharge summary has no doctor signature, stamp or registration number.",
                 ["discharge_summary"], "signature")


@seeder("C-03")
def test_without_report(case, rng, ctx):
    test = Test("Serum Amylase", "lab", 550) if case.scenario.key != "dengue" else Test("Chest X-ray PA view",
                                                                                        "imaging", 400)
    line = add_line(case, "Investigations", test.name, 1, round(test.rate * tier(case)),
                    case.admission.date())
    finish(case)
    return Issue("C-03", f"{test.name} billed (Rs. {line.amount:,.0f}) but no report is attached.",
                 ["final_bill"], "investigation", line.amount)


@seeder("C-04", fits=lambda s: s.implant is not None)
def implant_without_sticker(case, rng, ctx):
    case.documents = [d for d in case.documents if d.type not in ("implant_invoice", "implant_sticker")]
    return Issue("C-04", f"Implant billed ({case.implants[0].type}, Rs. {case.implants[0].price:,.0f}) without "
                         "implant invoice or sticker.", ["final_bill"], "implant", case.implants[0].price)


@seeder("C-05", fits=lambda s: s.accident)
def accident_without_mlc(case, rng, ctx):
    case.documents = [d for d in case.documents if d.type != "mlc"]
    return Issue("C-05", "Road traffic accident case without MLC/FIR copy.", ["discharge_summary"], "mlc")


# ---------------------------------------------------------------- F: forensics

@seeder("F-01", combinable=True)
def edited_pdf(case, rng, ctx):
    edited = case.claim_submitted + timedelta(days=rng.randint(3, 15))
    case.pdf_meta = {"producer": "Adobe Photoshop 25.4 (Macintosh)", "creator": "Adobe Acrobat Pro 2024",
                     "modified": edited}
    return Issue("F-01", f"PDF was edited with image-editing software on {edited:%d %b %Y}, after the claim "
                         f"was submitted ({case.claim_submitted:%d %b %Y}).", ["claim_form"], "pdf_metadata")


@seeder("F-02")
def tampered_amount(case, rng, ctx):
    line = max((x for x in case.bill if x.category in ("Professional Fees", "OT Charges", "Room Rent")),
               key=lambda x: x.amount)
    old = line.amount
    line.rate = round(line.rate * rng.choice([1.5, 1.8, 2.0]))
    line.amount = round(line.qty * line.rate, 2)
    line.font = "Times-Bold"
    finish(case)
    also = ["B-03"] if line.category != "Room Rent" else []
    cap = case.policy.room_rent_cap_pct
    if line.category == "Room Rent" and cap and line.rate > case.policy.sum_insured * cap / 100:
        also.append("B-08")  # the inflated room rent now breaks the policy's room-rent limit too
    return Issue("F-02", f"Amount for '{line.description}' printed in a different font from the rest of the bill "
                         f"(changed from Rs. {old:,.0f} to Rs. {line.amount:,.0f}).", ["final_bill"],
                 line.description, round(line.amount - old, 2), also=also)


@seeder("F-03")
def duplicated_report(case, rng, ctx):
    idx = next(i for i, r in enumerate(case.reports) if r.kind == "lab")
    other = make_person_name(rng, case.patient.gender)
    pos = next(i for i, d in enumerate(case.documents) if d.overrides.get("report_index") == idx)
    case.documents.insert(pos + 1, Document("lab_report", case.reports[idx].test,
                                            {"report_index": idx, "patient_name": other,
                                             "uhid": f"UH{rng.randint(10**6, 10**7 - 1)}"}))
    return Issue("F-03", f"{case.reports[idx].test} report appears twice; the copy carries another patient's "
                         f"name ({other}).", ["lab_report"], "duplicate_page", also=["X-01"])


@seeder("F-04")
def odd_layout_report(case, rng, ctx):
    labs = [i for i, r in enumerate(case.reports) if r.kind == "lab"]
    idx = labs[-1]
    case.reports[idx].layout = "alt"
    return Issue("F-04", f"{case.reports[idx].test} report layout differs from the same lab's other reports.",
                 ["lab_report"], "layout")


# ---------------------------------------------------------------- X: consistency

@seeder("X-01", combinable=True)
def name_mismatch(case, rng, ctx):
    first, last = case.patient.name.split(" ", 1)
    alt_first = make_person_name(rng, case.patient.gender).split(" ")[0]
    while alt_first == first:
        alt_first = make_person_name(rng, case.patient.gender).split(" ")[0]
    wrong = f"{alt_first} {last}"
    case.doc("pharmacy_bill").overrides["patient_name"] = wrong
    return Issue("X-01", f"Pharmacy bill is in the name '{wrong}', other documents say '{case.patient.name}'.",
                 ["pharmacy_bill"], "patient_name")


@seeder("X-02", fits=surgical)
def procedure_after_discharge(case, rng, ctx):
    shift = case.discharge - case.ot.start + timedelta(days=1, hours=2)
    case.ot.start += shift
    case.ot.end += shift
    return Issue("X-02", f"OT notes date the procedure {case.ot.start:%d %b %Y}, after discharge on "
                         f"{case.discharge:%d %b %Y}.", ["ot_note"], "procedure_date")


@seeder("X-03")
def report_outside_stay(case, rng, ctx):
    report = case.reports[-1]
    report.report_dt = case.admission - timedelta(days=rng.randint(40, 60))
    return Issue("X-03", f"{report.test} report dated {report.report_dt:%d %b %Y}, outside the admission period and "
                         "the 30-day pre-hospitalisation window.", [report.kind + "_report"], "report_date")


@seeder("X-04", combinable=True)
def medicine_not_charted(case, rng, ctx):
    qty = rng.randint(4, 8)
    case.pharmacy.append(PharmacyLine("Inj Meropenem 1 g", f"B{rng.randint(1000, 9999)}K", "06/2028", qty, 1450.0,
                                      1450.0, round(1450.0 * qty, 2), case.admission.date()))
    finish(case)
    return Issue("X-04", f"Inj Meropenem 1 g x {qty} billed by pharmacy but never given per the medication chart.",
                 ["pharmacy_bill", "nursing_chart"], "Inj Meropenem 1 g", 1450.0 * qty)


@seeder("X-05", fits=lambda s: overnight(s) and s.icu_days == (0, 0) and s.los[0] >= 2)
def icu_not_charted(case, rng, ctx):
    days = 2
    rate = round(ICU_RATE * tier(case))
    add_line(case, "ICU", "ICU charges", days, rate)
    finish(case)
    return Issue("X-05", f"{days} ICU days billed (Rs. {days * rate:,.0f}) but nursing chart shows the patient in "
                         "the ward throughout.", ["final_bill", "nursing_chart"], "icu_days", days * rate)


@seeder("X-06", fits=lambda s: s.key in ("cholecystectomy", "appendicectomy"))
def procedure_mismatch(case, rng, ctx):
    open_name = case.procedures[0].replace("Laparoscopic", "Open").replace("laparoscopic", "open")
    case.discharge_procedures = [open_name]
    return Issue("X-06", f"Discharge summary says '{open_name}', bill and OT notes say '{case.procedures[0]}'.",
                 ["discharge_summary", "ot_note", "final_bill"], "procedure")


@seeder("X-07", fits=lambda s: s.implant is not None)
def implant_mismatch(case, rng, ctx):
    case.sticker_model = case.implants[0].model.replace("SYN", "LITE")
    return Issue("X-07", f"Implant billed as model {case.implants[0].model}; the sticker shows "
                         f"{case.sticker_model}.", ["final_bill", "implant_sticker"], "implant_model",
                 case.implants[0].price)


@seeder("X-08", fits=lambda s: s.key in ("appendicectomy", "pneumonia", "typhoid", "dengue"), stage="final_cashless")
def diagnosis_changed(case, rng, ctx):
    alt = {"appendicectomy": "Right ureteric colic", "pneumonia": "Acute exacerbation of bronchial asthma",
           "typhoid": "Acute viral fever", "dengue": "Malaria (P. vivax)"}[case.scenario.key]
    case.preauth.diagnosis = alt
    return Issue("X-08", f"Pre-auth diagnosis '{alt}' changed to '{case.final_diagnosis}' with no explanation.",
                 ["preauth", "discharge_summary"], "diagnosis")


@seeder("X-09", combinable=True)
def hospital_id_mismatch(case, rng, ctx):
    wrong = f"8900080{rng.randint(100000, 999999)}"
    case.doc("pharmacy_bill").overrides["rohini_id"] = wrong
    return Issue("X-09", f"Pharmacy bill shows Rohini ID {wrong}; other documents show {case.hospital.rohini_id}.",
                 ["pharmacy_bill"], "rohini_id")


# ---------------------------------------------------------------- M: medical necessity

@seeder("M-01", fits=lambda s: s.key == "gastroenteritis")
def outpatient_admission(case, rng, ctx):
    case.complaints = "Loose stools 2-3 episodes since morning. Tolerating oral fluids."
    case.course = "Vitals stable, no dehydration. Observed and given oral rehydration."
    case.final_diagnosis = "Acute gastroenteritis without dehydration"
    if case.preauth:
        case.preauth.diagnosis = case.final_diagnosis
    for r in case.reports:
        test = next((t for t in case.scenario.tests if t.name == r.test), None)
        if test:
            r.rows, r.impression = lab_rows(test, case.scenario, rng, normal=True)
    for n in case.nursing:
        n.temp_f, n.pulse, n.bp, n.rr, n.spo2 = 98.4, 78, "120/80", 16, 98
    return Issue("M-01", "Admitted for mild gastroenteritis without dehydration, normal vitals and labs: "
                         "usually treated as outpatient.", ["discharge_summary", "nursing_chart"], "admission",
                 case.claimed_amount)


@seeder("M-02", fits=lambda s: s.key not in ("ptca",))
def irrelevant_test(case, rng, ctx):
    when = case.admission + timedelta(hours=6)
    add_report(case, IRRELEVANT_TEST, when, rng, normal=True)
    line = add_line(case, "Investigations", IRRELEVANT_TEST.name, 1, round(IRRELEVANT_TEST.rate * tier(case)),
                    when.date())
    finish(case)
    return Issue("M-02", f"MRI Brain billed (Rs. {line.amount:,.0f}) with no neurological indication for "
                         f"{case.final_diagnosis.lower()}.", ["final_bill", "imaging_report"], "MRI Brain (plain)",
                 line.amount)


@seeder("M-03", fits=lambda s: any(t.kind == "imaging" for t in s.tests) and s.los[0] >= 1)
def repeat_imaging(case, rng, ctx):
    test = next(t for t in case.scenario.tests if t.kind == "imaging")
    when = next(r.report_dt for r in case.reports if r.test == test.name) + timedelta(hours=22)
    when = min(when, case.discharge - timedelta(hours=1))
    add_report(case, test, when, rng)
    line = add_line(case, "Investigations", test.name, 1, round(test.rate * tier(case)), when.date())
    finish(case)
    return Issue("M-03", f"{test.name} repeated within 24 hours with no documented reason.",
                 ["final_bill", "imaging_report"], test.name, line.amount)


@seeder("M-04", fits=lambda s: s.key in ("typhoid", "dengue", "gastroenteritis"))
def icu_not_needed(case, rng, ctx):
    days = 2
    for n in case.nursing[:days]:
        n.location = "ICU"
        n.temp_f, n.pulse, n.bp, n.rr, n.spo2 = 98.6, 80, "122/78", 16, 98
    rate = round(ICU_RATE * tier(case))
    # move two ward days into ICU on the bill
    for line in case.bill:
        if line.category == "Room Rent":
            line.qty = max(1, line.qty - days)
            line.amount = round(line.qty * line.rate, 2)
    add_line(case, "ICU", "ICU charges", days, rate)
    case.icu_days = days
    finish(case)
    return Issue("M-04", f"{days} days in ICU with normal vitals and no ICU-level treatment recorded.",
                 ["nursing_chart", "final_bill"], "icu_days", days * rate)


@seeder("M-05", fits=lambda s: overnight(s) and s.los_norm > 0)
def long_stay(case, rng, ctx):
    extra = rng.randint(4, 6)
    # long enough to be over the norm plus M-05's default allowance of 2 days, whatever the case's own stay was
    extra = max(extra, case.scenario.los_norm + 3 - case.los_days)
    case.discharge += timedelta(days=extra)
    rate = round(ROOM_RATE[case.ward_type] * tier(case))
    for line in case.bill:
        if line.category == "Room Rent":
            line.qty += extra
            line.amount = round(line.qty * line.rate, 2)
        if line.category in ("Nursing", "Professional Fees") and line.qty > 1:
            line.qty += extra
            line.amount = round(line.qty * line.rate, 2)
    last = case.nursing[-1]
    for i in range(extra):
        d = last.day + timedelta(days=i + 1)
        case.nursing.append(NursingDay(d, "Ward", 98.4, 76, "118/78", 16, 98, list(last.meds)))
    case.claim_submitted += timedelta(days=extra)
    finish(case)
    return Issue("M-05", f"Stay of {case.los_days} days against a usual {case.scenario.los_norm} days for "
                         f"{case.scenario.name.lower()}, with no complication recorded.",
                 ["discharge_summary", "final_bill"], "length_of_stay", extra * rate)


@seeder("M-06", fits=lambda s: s.key == "dengue")
def platelets_normal(case, rng, ctx):
    cbc = next(t for t in case.scenario.tests if t.name == "Complete Blood Count")
    for r in case.reports:
        if r.test == cbc.name:
            r.rows, _ = lab_rows(cbc, case.scenario, rng, severe=False)
    case.final_diagnosis = "Dengue fever with severe thrombocytopenia"
    if case.preauth:
        case.preauth.diagnosis = case.final_diagnosis
    return Issue("M-06", "Diagnosis says severe thrombocytopenia but every platelet count is above 1,50,000.",
                 ["discharge_summary", "lab_report"], "platelet_count")


@seeder("M-07", fits=lambda s: s.day_care)
def daycare_overnight(case, rng, ctx):
    nights = rng.randint(1, 2)
    case.discharge = datetime.combine(case.admission.date() + timedelta(days=nights), time(11, 0))
    rate = round(ROOM_RATE[case.ward_type] * tier(case))
    add_line(case, "Room Rent", "Room rent - semi-private ward", nights, rate)
    case.nursing.append(NursingDay(case.admission.date() + timedelta(days=1), "Ward", 98.2, 74, "126/80", 16, 98,
                                   list(case.nursing[0].meds)))
    case.claim_submitted += timedelta(days=nights)
    finish(case)
    return Issue("M-07", f"Day-care procedure ({case.scenario.procedure}) billed with {nights} night(s) stay.",
                 ["final_bill", "discharge_summary"], "length_of_stay", nights * rate)


# ---------------------------------------------------------------- B: billing

@seeder("B-01", combinable=True)
def arithmetic_error(case, rng, ctx):
    line = rng.choice([x for x in case.bill if x.qty > 1] or case.bill)
    right = line.amount
    line.amount = round(right + rng.choice([1000, 1500, 2000, 900]), 2)
    finish(case)
    return Issue("B-01", f"'{line.description}': {line.qty:g} x Rs. {line.rate:,.0f} = Rs. {right:,.0f}, "
                         f"but billed Rs. {line.amount:,.0f}.", ["final_bill"], line.description,
                 round(line.amount - right, 2))


@seeder("B-02", combinable=True)
def claim_above_bill(case, rng, ctx):
    extra = rng.choice([2500, 4000, 6000, 10000])
    case.claimed_amount = case.bill_total + extra
    if case.preauth:
        case.preauth.approved = max(case.preauth.approved, round(case.claimed_amount, -2))
    return Issue("B-02", f"Claim form amount Rs. {case.claimed_amount:,.0f} is Rs. {extra:,.0f} more than the bill "
                         f"total Rs. {case.bill_total:,.0f}.", ["claim_form", "final_bill"], "claimed_amount", extra)


@seeder("B-03", fits=surgical)
def surgeon_fee_inflated(case, rng, ctx):
    line = next(x for x in case.bill if x.description.startswith("Surgeon fee"))
    old = line.amount
    line.rate = round(line.rate * rng.choice([2.5, 3.0]))
    line.amount = round(line.qty * line.rate, 2)
    finish(case)
    return Issue("B-03", f"Surgeon fee Rs. {line.amount:,.0f} is far above the benchmark for "
                         f"{case.scenario.procedure.lower()}.", ["final_bill"], "Surgeon fee",
                 round(line.amount - old, 2))


@seeder("B-04", fits=lambda s: surgical(s) and not s.day_care and s.icu_days == (0, 0))
def unbundled(case, rng, ctx):
    pkg = round(case.scenario.package_rate * tier(case))
    keep = ("Investigations", "Pharmacy", "Implants")
    removed = [x for x in case.bill if x.category not in keep]
    case.bill = [x for x in case.bill if x.category in keep]
    add_line(case, "Package", f"Package charges - {case.scenario.name} ({case.scenario.package_code})", 1, pkg)
    again = [x for x in removed if x.category in ("OT Charges",) or x.description.startswith("Surgeon fee")]
    for x in again:
        add_line(case, x.category, x.description, x.qty, x.rate, x.day)
    finish(case)
    return Issue("B-04", "Package rate billed, and OT charges and surgeon fee billed again separately although the "
                         "package includes them.", ["final_bill"], "unbundling", sum(x.amount for x in again))


@seeder("B-05", combinable=True)
def list_one_items(case, rng, ctx):
    items = rng.sample([("Attendant charges", 1, 500), ("Diapers adult", 6, 45), ("Crepe bandage 10 cm", 2, 180),
                        ("Mineral water", 10, 20), ("Thermometer digital", 1, 250)], 2)
    total = 0.0
    for name, qty, rate in items:
        total += add_line(case, "Miscellaneous", name, qty, rate).amount
    finish(case)
    return Issue("B-05", "IRDAI List I non-payable items billed: " + ", ".join(i[0] for i in items) + ".",
                 ["final_bill"], "non_payable", total)


@seeder("B-06", combinable=True)
def subsumed_items(case, rng, ctx):
    items = rng.sample([("Admission kit", 1, 750), ("Registration charges", 1, 300),
                        ("Documentation charges", 1, 400), ("Housekeeping charges", 2, 250)], 2)
    total = 0.0
    for name, qty, rate in items:
        total += add_line(case, "Miscellaneous", name, qty, rate).amount
    finish(case)
    return Issue("B-06", "Items that are part of room or treatment charges billed separately: "
                         + ", ".join(i[0] for i in items) + ".", ["final_bill"], "subsumed_items", total)


@seeder("B-07", fits=overnight)
def too_many_consumables(case, rng, ctx):
    line = next(x for x in case.bill if x.description.startswith("Disposable Syringe"))
    days = max(1, line.qty / 4)
    normal_amount = line.amount
    line.qty = round(days * rng.choice([25, 30, 40]))
    line.amount = round(line.qty * line.rate, 2)
    finish(case)
    return Issue("B-07", f"{line.qty:g} syringes billed over {days:g} day(s): far above the usual 4 per day.",
                 ["final_bill"], line.description, round(line.amount - normal_amount, 2))


@seeder("B-08", fits=overnight)
def private_room(case, rng, ctx):
    room = next(x for x in case.bill if x.category == "Room Rent")
    old_rate = room.rate
    room.rate = round(ROOM_RATE["private"] * tier(case))
    room.description = "Room rent - private room"
    room.amount = round(room.qty * room.rate, 2)
    case.ward_type = "private"
    finish(case)
    return Issue("B-08", "Private room billed; the policy allows a semi-private room. Proportionate deduction "
                         "applies.", ["final_bill"], "room_category", round((room.rate - old_rate) * room.qty, 2))


@seeder("B-09", fits=lambda s: s.extra_surgery is not None)
def second_surgery_full(case, rng, ctx):
    extra = case.scenario.extra_surgery
    fee = next(x for x in case.bill if x.description.startswith("Surgeon fee"))
    ot = next(x for x in case.bill if x.category == "OT Charges")
    a = add_line(case, "Professional Fees", f"Surgeon fee - {extra}", 1, fee.rate, fee.day)
    b = add_line(case, "OT Charges", f"Operation theatre charges - {extra}", 1, ot.rate, ot.day)
    case.procedures.append(extra)
    case.discharge_procedures.append(extra)
    case.ot.procedures.append(extra)
    finish(case)
    return Issue("B-09", f"Second surgery ({extra}) in the same sitting billed at full surgeon fee and OT charges "
                         "(usually 50%).", ["final_bill"], "multiple_surgery", round((a.amount + b.amount) / 2, 2))


@seeder("B-10", combinable=True)
def above_mrp(case, rng, ctx):
    line = max(case.pharmacy, key=lambda x: x.mrp)
    line.rate = round(line.mrp * rng.choice([1.25, 1.4, 1.6]), 2)
    over = round((line.rate - line.mrp) * line.qty, 2)
    line.amount = round(line.rate * line.qty, 2)
    finish(case)
    return Issue("B-10", f"{line.drug} charged Rs. {line.rate:,.2f} per unit against MRP Rs. {line.mrp:,.2f}.",
                 ["pharmacy_bill"], line.drug, over)


@seeder("B-11", fits=lambda s: s.upcoded_procedure is not None)
def upcoded(case, rng, ctx):
    fee = next(x for x in case.bill if x.description.startswith("Surgeon fee"))
    old = fee.amount
    fee.description = f"Surgeon fee - {case.scenario.upcoded_procedure}"
    fee.rate = round(fee.rate * 1.6)
    fee.amount = round(fee.qty * fee.rate, 2)
    case.procedures = [case.scenario.upcoded_procedure]
    finish(case)
    return Issue("B-11", f"Billed '{case.scenario.upcoded_procedure}', but OT notes and discharge summary describe an "
                         "uncomplicated procedure.", ["final_bill", "ot_note"], "procedure",
                 round(fee.amount - old, 2), also=["X-06"])


# ---------------------------------------------------------------- P: policy

@seeder("P-01")
def policy_lapsed(case, rng, ctx):
    gap = rng.randint(5, 40)
    case.policy.end = case.admission.date() - timedelta(days=gap)
    case.policy.start = case.policy.end - timedelta(days=364)
    return Issue("P-01", f"Policy ended on {case.policy.end:%d %b %Y}, {gap} days before admission.",
                 ["policy_ecard"], "policy_period", case.claimed_amount)


@seeder("P-02", fits=lambda s: s.specific_waiting)
def waiting_not_served(case, rng, ctx):
    months = rng.randint(6, 11)  # first policy year, so the policy is still active
    case.policy.inception = case.admission.date() - timedelta(days=months * 30)
    case.policy.start = case.policy.inception
    case.policy.end = case.policy.start + timedelta(days=364)
    return Issue("P-02", f"{case.scenario.name} has a 24-month waiting period; cover started only {months} months "
                         "before admission.", ["policy_ecard"], "waiting_period", case.claimed_amount,
                 also=["P-04"] if months < 3 else [])


@seeder("P-03", fits=lambda s: s.key in ("cataract", "normal_delivery"))
def exclusion_or_sublimit(case, rng, ctx):
    if case.scenario.key == "cataract":
        case.policy.sub_limits = {"cataract_per_eye": 20000}
        excess = round(case.claimed_amount - 20000, 2)
        return Issue("P-03", f"Cataract sub-limit is Rs. 20,000 per eye; Rs. {case.claimed_amount:,.0f} claimed.",
                     ["policy_ecard", "claim_form"], "sub_limit", excess)
    case.policy.product_code, case.policy.product_name = "SHP-IND", "Swasthya Individual Plan"
    case.policy.sub_limits.pop("maternity", None)
    return Issue("P-03", "Maternity is excluded under this individual policy (standard exclusion Excl18).",
                 ["policy_ecard", "discharge_summary"], "exclusion", case.claimed_amount)


@seeder("P-04", fits=lambda s: not s.specific_waiting and not s.accident, combinable=True)
def early_claim(case, rng, ctx):
    days = rng.randint(32, 55)
    case.policy.inception = case.admission.date() - timedelta(days=days)
    case.policy.start = case.policy.inception
    case.policy.end = case.policy.start + timedelta(days=364)
    return Issue("P-04", f"Claim {days} days after the first policy started.", ["policy_ecard"], "policy_start")


@seeder("P-05")
def moratorium(case, rng, ctx):
    case.policy.inception = case.policy.start - timedelta(days=365 * rng.randint(6, 8))
    years = rng.randint(6, 12)
    case.history = f"Known case of hypertension for {years} years, on Tab Amlodipine 5 mg OD."
    return Issue("P-05", f"Undeclared hypertension ({years} years) noted, but cover is continuous for more than "
                         "60 months: non-disclosure cannot be used to deny.", ["discharge_summary", "policy_ecard"],
                 "moratorium")


@seeder("P-06")
def sum_insured_exhausted(case, rng, ctx):
    case.policy.sum_insured = 300000
    remaining = max(1000, round(case.claimed_amount * rng.uniform(0.3, 0.7), -2))
    case.policy.used_si = int(300000 - remaining)
    return Issue("P-06", f"Rs. {case.claimed_amount:,.0f} claimed; only Rs. {remaining:,.0f} of the sum insured "
                         "remains.", ["policy_ecard", "claim_form"], "sum_insured",
                 round(case.claimed_amount - remaining, 2))


# ---------------------------------------------------------------- R: registry

@seeder("R-01", combinable=True)
def unregistered_doctor(case, rng, ctx):
    old = case.doctor
    case.doctor = make_doctor(rng, old.qualification)
    ctx.unregistered_doctors.add(case.doctor.reg_no)
    if case.ot:
        case.ot.surgeon = case.doctor.name
    for line in case.bill:
        line.description = line.description.replace(old.name, case.doctor.name)
    return Issue("R-01", f"Treating doctor's registration {case.doctor.reg_no} is not in the medical register.",
                 ["discharge_summary"], "doctor_registration")


def _swap_hospital(case: Case, rng: Random):
    h = case.hospital
    case.hospital = make_hospital(rng, (h.city, h.state, h.tier))
    return case.hospital


@seeder("R-02")
def unregistered_hospital(case, rng, ctx):
    h = _swap_hospital(case, rng)
    ctx.unregistered_hospitals.add(h.rohini_id)
    return Issue("R-02", f"Hospital Rohini ID {h.rohini_id} is not in the hospital registry.", ["claim_form"],
                 "rohini_id")


@seeder("R-03")
def watchlisted_hospital(case, rng, ctx):
    h = _swap_hospital(case, rng)
    ctx.watchlist[h.rohini_id] = rng.choice(["De-empanelled after audit", "Under investigation for inflated bills",
                                             "Suspended by insurer network team"])
    return Issue("R-03", f"{h.name}, {h.city} is on the watchlist: {ctx.watchlist[h.rohini_id]}.", ["claim_form"],
                 "hospital")
