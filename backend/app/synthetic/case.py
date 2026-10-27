"""A synthetic claim: the facts, the documents that show them, and the seeded issues.

`build_genuine` makes a clean, internally consistent claim. Seeders (seeders.py)
then change facts or one document's overrides to plant a known issue.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from random import Random

from app.synthetic import data
from app.synthetic.scenarios import NORMAL_CONSUMABLES, Med, Scenario, Test

TIER_FACTOR = {"tier_1": 1.0, "tier_2": 0.9, "tier_3": 0.8}
ROOM_RATE = {"general": 1200, "semi_private": 2200, "private": 3800}
ICU_RATE = 5500  # within the 2%-of-sum-insured ICU cap on the smallest policy
WINDOW_START = date(2025, 11, 1)
WINDOW_DAYS = 330


@dataclass
class Patient:
    name: str
    age: int
    gender: str
    uhid: str
    phone: str
    id_last4: str
    address: str


@dataclass
class Hospital:
    name: str
    address: str
    city: str
    state: str
    tier: str
    rohini_id: str
    hfr_id: str
    phone: str
    nabh: bool


@dataclass
class Doctor:
    name: str
    qualification: str
    reg_no: str
    council: str


@dataclass
class Policy:
    policy_no: str
    member_id: str
    insurer: str
    tpa: str
    product_code: str
    product_name: str
    sum_insured: int
    used_si: int
    start: date
    end: date
    inception: date
    room_eligibility: str
    room_rent_cap_pct: float | None
    sub_limits: dict
    members: list[str]
    declared_peds: list[str]


@dataclass
class LabReport:
    test: str
    kind: str
    report_dt: datetime
    lab_name: str
    rows: list[tuple[str, str, str, str]]   # parameter, value, unit, reference
    impression: str
    signatory: str
    layout: str = "standard"


@dataclass
class BillLine:
    category: str
    description: str
    day: date
    qty: float
    rate: float
    amount: float
    font: str | None = None   # set by F-02 to render this amount in a different font


@dataclass
class PharmacyLine:
    drug: str
    batch: str
    expiry: str
    qty: int
    mrp: float
    rate: float
    amount: float
    day: date


@dataclass
class NursingDay:
    day: date
    location: str
    temp_f: float
    pulse: int
    bp: str
    rr: int
    spo2: int
    meds: list[str]


@dataclass
class Implant:
    type: str
    brand: str
    model: str
    serial: str
    price: float


@dataclass
class OtNote:
    procedures: list[str]
    start: datetime
    end: datetime
    surgeon: str
    anaesthetist: str
    anaesthesia: str
    findings: str
    implants: list[Implant]


@dataclass
class PreAuth:
    number: str
    requested_on: datetime
    diagnosis: str
    procedure: str
    estimate: float
    approved: float


@dataclass
class Document:
    type: str
    title: str
    overrides: dict = field(default_factory=dict)


@dataclass
class Issue:
    check_id: str
    description: str
    documents: list[str]
    field_name: str | None = None
    amount_at_stake: float | None = None
    also: list[str] = field(default_factory=list)


@dataclass
class Case:
    claim_id: str
    scenario: Scenario
    stage: str
    patient: Patient
    hospital: Hospital
    doctor: Doctor
    anaesthetist: Doctor | None
    policy: Policy
    admission: datetime
    discharge: datetime
    ward_type: str
    icu_days: int
    final_diagnosis: str
    icd_code: str
    procedures: list[str]
    discharge_procedures: list[str]
    complaints: str
    course: str
    advice: str
    history: str
    reports: list[LabReport]
    bill: list[BillLine]
    pharmacy: list[PharmacyLine]
    nursing: list[NursingDay]
    implants: list[Implant]
    ot: OtNote | None
    preauth: PreAuth | None
    claim_submitted: datetime
    claimed_amount: float
    bill_no: str
    documents: list[Document]
    issues: list[Issue] = field(default_factory=list)
    pdf_meta: dict = field(default_factory=dict)
    mlc: dict | None = None
    sticker_model: str | None = None
    signed_summary: bool = True

    # ---- derived numbers -------------------------------------------------
    @property
    def bill_total(self) -> float:
        return round(sum(line.amount for line in self.bill), 2)

    @property
    def pharmacy_total(self) -> float:
        return round(sum(line.amount for line in self.pharmacy), 2)

    @property
    def los_days(self) -> int:
        return (self.discharge.date() - self.admission.date()).days

    def doc(self, doc_type: str) -> Document | None:
        return next((d for d in self.documents if d.type == doc_type), None)

    def sync_pharmacy_line(self) -> None:
        """Keep the 'Pharmacy as per bill' line on the final bill equal to the pharmacy bill."""
        for line in self.bill:
            if line.category == "Pharmacy":
                line.rate = line.amount = self.pharmacy_total

    def sync_claim_amount(self) -> None:
        self.claimed_amount = self.bill_total
        if self.preauth:
            self.preauth.approved = max(self.preauth.approved, round(self.claimed_amount, -2))


# ---------------------------------------------------------------- people and places

def make_person_name(rng: Random, gender: str) -> str:
    first = rng.choice(data.MALE_FIRST if gender == "M" else data.FEMALE_FIRST)
    return f"{first} {rng.choice(data.LAST)}"


def make_hospital(rng: Random, city: tuple[str, str, str] | None = None) -> Hospital:
    city_name, state, tier = city or rng.choice(data.CITIES)
    name = f"{rng.choice(data.HOSPITAL_PREFIX)} {rng.choice(data.HOSPITAL_SUFFIX)}"
    return Hospital(
        name=name, address=f"{rng.randint(1, 250)}, {rng.choice(data.STREETS)}", city=city_name, state=state,
        tier=tier, rohini_id=f"8900080{rng.randint(100000, 999999)}", hfr_id=f"IN{rng.randint(10**9, 10**10 - 1)}",
        phone=f"0{rng.randint(100, 999)}-{rng.randint(2000000, 9999999)}", nabh=rng.random() < 0.6,
    )


def make_doctor(rng: Random, qualification: str) -> Doctor:
    code, council = rng.choice(data.COUNCILS)
    gender = rng.choice("MF")
    return Doctor(name=f"Dr. {make_person_name(rng, gender)}", qualification=qualification,
                  reg_no=f"{code}/{rng.randint(1995, 2018)}/{rng.randint(10000, 99999)}", council=council)


QUALIFICATION = {"General Surgery": "MS (General Surgery)", "Ophthalmology": "MS (Ophthalmology)",
                 "Obstetrics": "MS (Obstetrics & Gynaecology)", "General Medicine": "MD (Medicine)",
                 "Orthopaedics": "MS (Orthopaedics)", "Cardiology": "DM (Cardiology)"}


# ---------------------------------------------------------------- lab results

def _num(rng: Random, lo: float, hi: float, digits: int = 1) -> str:
    return f"{rng.uniform(lo, hi):.{digits}f}"


def lab_rows(test: Test, scenario: Scenario, rng: Random, *, severe: bool = True, normal: bool = False,
             day_index: int = 0) -> tuple[list[tuple[str, str, str, str]], str]:
    """Report rows and impression for one test. `normal=True` gives unremarkable values."""
    key = scenario.key
    n = test.name
    if n == "Complete Blood Count":
        wbc = (rng.uniform(4.5, 9.5) if normal else
               rng.uniform(12.5, 18.0) if key in ("appendicectomy", "pneumonia") else
               rng.uniform(2.8, 4.2) if key == "dengue" else rng.uniform(5.0, 10.5))
        if key == "dengue" and not normal and severe:
            platelets = [38, 52, 71, 96, 140][min(day_index, 4)] * 1000 + rng.randint(-3000, 3000)
        else:
            platelets = rng.randint(170, 380) * 1000
        hb = rng.uniform(10.2, 11.5) if key == "normal_delivery" else rng.uniform(11.8, 15.2)
        return ([("Haemoglobin", f"{hb:.1f}", "g/dL", "12.0 - 16.0"),
                 ("Total Leucocyte Count", f"{wbc * 1000:,.0f}", "/cumm", "4,000 - 11,000"),
                 ("Platelet Count", f"{platelets:,}", "/cumm", "1,50,000 - 4,10,000"),
                 ("Haematocrit", _num(rng, 36, 44), "%", "36 - 46")], "")
    if n == "C-Reactive Protein":
        v = _num(rng, 1, 5) if normal else _num(rng, 35, 120)
        return ([("CRP (quantitative)", v, "mg/L", "< 6.0")], "")
    if n == "Liver Function Test":
        bili = _num(rng, 0.4, 1.0) if normal or key != "cholecystectomy" else _num(rng, 1.1, 1.8)
        return ([("Total Bilirubin", bili, "mg/dL", "0.3 - 1.2"), ("SGOT (AST)", _num(rng, 18, 38, 0), "U/L", "< 40"),
                 ("SGPT (ALT)", _num(rng, 15, 40, 0), "U/L", "< 41"),
                 ("Alkaline Phosphatase", _num(rng, 60, 120, 0), "U/L", "40 - 130")], "")
    if n == "Renal Function Test":
        return ([("Blood Urea", _num(rng, 18, 38, 0), "mg/dL", "15 - 45"),
                 ("Serum Creatinine", _num(rng, 0.6, 1.1, 2), "mg/dL", "0.6 - 1.3")], "")
    if n == "Serum Electrolytes":
        na = _num(rng, 136, 144, 0) if normal or key != "gastroenteritis" else _num(rng, 129, 133, 0)
        k = _num(rng, 3.6, 4.8) if normal or key != "gastroenteritis" else _num(rng, 3.0, 3.4)
        return ([("Sodium", na, "mmol/L", "135 - 145"), ("Potassium", k, "mmol/L", "3.5 - 5.1"),
                 ("Chloride", _num(rng, 98, 106, 0), "mmol/L", "98 - 107")], "")
    if n == "Dengue NS1 Antigen":
        return ([("Dengue NS1 Antigen (ELISA)", "Positive" if not normal else "Negative", "", "Negative")], "")
    if n == "Dengue IgM":
        return ([("Dengue IgM Antibody", "Reactive" if not normal else "Non-reactive", "", "Non-reactive")], "")
    if n == "Widal Test":
        return ([("S. Typhi O", "1:320" if not normal else "1:40", "titre", "< 1:80"),
                 ("S. Typhi H", "1:160" if not normal else "1:40", "titre", "< 1:160")], "")
    if n == "Blood Culture":
        return ([("Organism isolated", "Salmonella Typhi" if not normal else "No growth after 5 days", "", ""),
                 ("Sensitive to", "Ceftriaxone, Azithromycin" if not normal else "-", "", "")], "")
    if n == "Troponin I":
        return ([("Troponin I (hs)", _num(rng, 1.8, 6.5, 2) if not normal else "0.01", "ng/mL", "< 0.04")], "")
    if n == "Lipid Profile":
        return ([("Total Cholesterol", _num(rng, 190, 260, 0), "mg/dL", "< 200"),
                 ("LDL Cholesterol", _num(rng, 120, 180, 0), "mg/dL", "< 100"),
                 ("HDL Cholesterol", _num(rng, 32, 45, 0), "mg/dL", "> 40")], "")
    if n == "Random Blood Sugar":
        return ([("Random Blood Sugar", _num(rng, 90, 140, 0), "mg/dL", "70 - 140")], "")
    if n == "Blood Group and Rh":
        return ([("Blood Group", rng.choice(["A", "B", "O", "AB"]), "", ""), ("Rh Factor", "Positive", "", "")], "")
    if n == "ECG":
        return ([("Rhythm", "Sinus rhythm", "", ""), ("Rate", _num(rng, 68, 92, 0), "bpm", "60 - 100")],
                "Normal ECG.")
    # imaging
    impressions = {
        "USG Abdomen": {"appendicectomy": "Inflamed, non-compressible appendix (9 mm) with periappendiceal fluid. "
                                          "Features suggestive of acute appendicitis.",
                        "cholecystectomy": "Gall bladder distended with multiple calculi, largest 12 mm. "
                                           "No CBD dilatation."},
        "Chest X-ray PA view": {"pneumonia": "Homogeneous opacity in right lower zone suggestive of consolidation."},
        "X-ray Right Hip AP/Lateral": {"femur_fracture": "Displaced fracture of neck of right femur."},
        "2D Echocardiography": {"ptca": "Regional wall motion abnormality in LAD territory. LVEF 48%."},
        "Biometry (A-scan)": {"cataract": "Axial length 23.12 mm. Calculated IOL power +21.0 D."},
        "USG Obstetric": {"normal_delivery": "Single live intrauterine fetus, cephalic, 38-39 weeks. Liquor adequate."},
    }
    text = "No significant abnormality detected." if normal else impressions.get(n, {}).get(
        key, "No significant abnormality detected.")
    return ([("Findings", "See impression", "", "")], text)


# ---------------------------------------------------------------- the genuine claim

def _rand_dt(rng: Random, day: date, start_h: int = 8, end_h: int = 20) -> datetime:
    return datetime.combine(day, time(rng.randint(start_h, end_h), rng.choice([0, 10, 15, 20, 30, 40, 45, 50])))


def _vitals(rng: Random, sick: bool) -> tuple[float, int, str, int, int]:
    if sick:
        return (round(rng.uniform(99.5, 102.2), 1), rng.randint(96, 118),
                f"{rng.randint(100, 118)}/{rng.randint(62, 76)}", rng.randint(20, 26), rng.randint(93, 96))
    return (round(rng.uniform(97.8, 98.9), 1), rng.randint(70, 88),
            f"{rng.randint(112, 128)}/{rng.randint(72, 84)}", rng.randint(14, 18), rng.randint(97, 99))


def build_genuine(claim_id: str, scenario: Scenario, rng: Random, hospital: Hospital, doctor: Doctor,
                  stage: str | None = None) -> Case:
    # patient
    gender = scenario.gender or rng.choice("MF")
    patient = Patient(
        name=make_person_name(rng, gender), age=rng.randint(*scenario.age), gender=gender,
        uhid=f"UH{rng.randint(10**6, 10**7 - 1)}", phone=f"9{rng.randint(10**8, 10**9 - 1)}",
        id_last4=f"{rng.randint(1000, 9999)}", address=f"{rng.randint(1, 400)}, {rng.choice(data.STREETS)}, "
                                                      f"{hospital.city}",
    )

    # dates
    adm_day = WINDOW_START + timedelta(days=rng.randrange(WINDOW_DAYS))
    if scenario.day_care:
        admission = datetime.combine(adm_day, time(rng.randint(7, 9), 30))
        discharge = admission + timedelta(hours=rng.randint(7, 9))
    else:
        admission = _rand_dt(rng, adm_day)
        los = rng.randint(*scenario.los)
        discharge = datetime.combine(adm_day + timedelta(days=los), time(rng.randint(10, 17), 0))
    icu_days = rng.randint(*scenario.icu_days)

    # policy: comfortably active, waiting periods long served
    si = rng.choice([300000, 500000, 500000, 1000000])
    start = adm_day - timedelta(days=rng.randint(20, 330))
    inception = start - timedelta(days=365 * rng.randint(3, 4))
    cap = 1.0 if rng.random() < 0.35 else None
    insurer = rng.choice(data.INSURERS)
    product_code, product_name = rng.choice(data.PRODUCTS)
    sub_limits = {"cataract_per_eye": 40000} if rng.random() < 0.5 else {}
    if scenario.key == "normal_delivery":  # maternity is excluded (Excl18) unless the product covers it
        product_code, product_name = data.PRODUCTS[2]
        sub_limits["maternity"] = 50000
    policy = Policy(
        policy_no=f"{product_code[:3]}/{rng.randint(10**7, 10**8 - 1)}/{start.year}",
        member_id=f"M{rng.randint(10**6, 10**7 - 1)}",
        insurer=insurer, tpa=rng.choice(data.TPAS), product_code=product_code, product_name=product_name,
        sum_insured=si, used_si=rng.choice([0, 0, 0, 15000, 32000]), start=start,
        end=start + timedelta(days=364), inception=inception, room_eligibility="semi_private",
        room_rent_cap_pct=cap, sub_limits=sub_limits,
        members=[patient.name], declared_peds=[],
    )

    tier_f = TIER_FACTOR[hospital.tier]
    ward = "semi_private"
    days = list(_days(admission, discharge))
    ward_days = 0 if scenario.day_care else max(1, len(days) - 1 - icu_days)

    # investigations and reports
    reports: list[LabReport] = []
    bill: list[BillLine] = []
    lab_name = data.LABS_IN_HOUSE
    pathologist = f"Dr. {make_person_name(rng, rng.choice('MF'))}, MD (Pathology)"
    radiologist = f"Dr. {make_person_name(rng, rng.choice('MF'))}, MD (Radiodiagnosis)"
    for i, test in enumerate(scenario.tests):
        when = admission + timedelta(hours=rng.randint(1, 5) + (0 if i < 2 else rng.randint(0, 12)))
        when = min(when, discharge - timedelta(hours=1))
        rows, impression = lab_rows(test, scenario, rng, day_index=0)
        reports.append(LabReport(test.name, test.kind, when, lab_name, rows, impression,
                                 radiologist if test.kind == "imaging" else pathologist))
        bill.append(BillLine("Investigations", test.name, when.date(), 1, round(test.rate * tier_f), 0))
    if scenario.key == "dengue":  # daily platelet monitoring
        cbc = next(t for t in scenario.tests if t.name == "Complete Blood Count")
        for d in range(1, len(days) - 1):
            when = datetime.combine(days[d], time(7, 30))
            rows, _ = lab_rows(cbc, scenario, rng, day_index=d)
            reports.append(LabReport(cbc.name, "lab", when, lab_name, rows, "", pathologist))
            bill.append(BillLine("Investigations", cbc.name, when.date(), 1, round(cbc.rate * tier_f), 0))

    # stay and professional charges
    if ward_days:
        bill.append(BillLine("Room Rent", f"Room rent - {ward.replace('_', '-')} ward", admission.date(), ward_days,
                             round(ROOM_RATE[ward] * tier_f), 0))
    if icu_days:
        bill.append(BillLine("ICU", "ICU / ICCU charges", admission.date(), icu_days, round(ICU_RATE * tier_f), 0))
    stay_days = max(1, ward_days + icu_days)
    bill.append(BillLine("Nursing", "Nursing charges", admission.date(), stay_days, round(400 * tier_f), 0))
    bill.append(BillLine("Professional Fees", f"Consultant visit charges ({doctor.name})", admission.date(),
                         stay_days, round(700 * tier_f), 0))

    procedure_dt = None
    ot = None
    anaesthetist = None
    implants: list[Implant] = []
    if scenario.surgical:
        procedure_dt = admission + timedelta(hours=rng.randint(3, 10)) if scenario.day_care or len(days) < 3 \
            else datetime.combine(days[1], time(rng.randint(9, 14), 0))
        procedure_dt = min(procedure_dt, discharge - timedelta(hours=3))
        anaesthetist = make_doctor(rng, "MD (Anaesthesiology)")
        rate = scenario.package_rate * tier_f
        bill += [BillLine("Professional Fees", f"Surgeon fee - {scenario.procedure}", procedure_dt.date(), 1,
                          round(rate * 0.35), 0),
                 BillLine("Professional Fees", "Anaesthetist fee", procedure_dt.date(), 1, round(rate * 0.12), 0),
                 BillLine("OT Charges", "Operation theatre charges", procedure_dt.date(), 1, round(rate * 0.15), 0)]
        if scenario.implant:
            itype, brand, model, price = scenario.implant
            implants.append(Implant(itype, brand, model, f"SN{rng.randint(10**7, 10**8 - 1)}", price))
            bill.append(BillLine("Implants", f"{itype} - {brand} {model}", procedure_dt.date(), 1, price, 0))
        anaesthesia = {"cataract": "Topical / peribulbar block", "femur_fracture": "Spinal anaesthesia",
                       "normal_delivery": "Local infiltration", "ptca": "Local anaesthesia (radial access)"}
        assert scenario.procedure, f"surgical scenario {scenario.key} needs a procedure"
        ot = OtNote([scenario.procedure], procedure_dt, procedure_dt + timedelta(minutes=rng.randint(35, 110)),
                    doctor.name, anaesthetist.name, anaesthesia.get(scenario.key, "General anaesthesia"),
                    scenario.course.split(".")[0] + ".", list(implants))

    # consumables
    for name, per_day, rate in NORMAL_CONSUMABLES:
        bill.append(BillLine("Consumables", name, admission.date(), per_day * stay_days, rate, 0))

    # pharmacy and medication chart
    pharmacy: list[PharmacyLine] = []
    for med in scenario.meds:
        qty = med.per_day * max(1, len(days) - 1 if not scenario.day_care else 1)
        pharmacy.append(_pharmacy_line(rng, med, qty, admission.date()))
    bill.append(BillLine("Pharmacy", "Pharmacy and medicines (as per pharmacy bill)", discharge.date(), 1, 0, 0))

    nursing = []
    for i, d in enumerate(days[:-1] if len(days) > 1 else days):
        loc = "ICU" if i < icu_days else ("Day care" if scenario.day_care else "Ward")
        sick = i == 0 and scenario.key not in ("cataract", "normal_delivery")
        nursing.append(NursingDay(d, loc, *_vitals(rng, sick), meds=[_med_label(m) for m in scenario.meds]))

    for line in bill:
        line.amount = round(line.qty * line.rate, 2)

    stage = stage or ("final_cashless" if rng.random() < 0.6 else "reimbursement")
    submitted = discharge + (timedelta(hours=2) if stage == "final_cashless" else timedelta(days=rng.randint(5, 20)))
    case = Case(
        claim_id=claim_id, scenario=scenario, stage=stage, patient=patient, hospital=hospital, doctor=doctor,
        anaesthetist=anaesthetist, policy=policy, admission=admission, discharge=discharge, ward_type=ward,
        icu_days=icu_days, final_diagnosis=scenario.diagnosis, icd_code=scenario.icd_code,
        procedures=[scenario.procedure] if scenario.procedure else [],
        discharge_procedures=[scenario.procedure] if scenario.procedure else [],
        complaints=scenario.complaints, course=scenario.course, advice=scenario.advice,
        history="No known comorbidities.", reports=reports, bill=bill, pharmacy=pharmacy, nursing=nursing,
        implants=implants, ot=ot, preauth=None, claim_submitted=submitted, claimed_amount=0,
        bill_no=f"IPB/{admission.year}/{rng.randint(10000, 99999)}", documents=[],
        sticker_model=implants[0].model if implants else None,
    )
    case.sync_pharmacy_line()
    case.sync_claim_amount()
    if stage == "final_cashless":
        estimate = round(case.claimed_amount * rng.uniform(0.9, 1.1), -2)
        case.preauth = PreAuth(f"PA{rng.randint(10**7, 10**8 - 1)}", admission + timedelta(hours=1),
                               scenario.diagnosis, scenario.procedure or "Medical management", estimate,
                               max(estimate, round(case.claimed_amount, -2) + 1000))
    if scenario.accident:
        case.mlc = {"number": f"MLC/{admission.year}/{rng.randint(100, 9999)}", "date": admission,
                    "station": rng.choice(data.POLICE_STATIONS)}
    if cap:  # keep the genuine room within the 1% cap
        assert ROOM_RATE[ward] * tier_f <= si * cap / 100
    case.documents = default_documents(case)
    return case


def _days(admission: datetime, discharge: datetime):
    d = admission.date()
    while d <= discharge.date():
        yield d
        d += timedelta(days=1)


def _med_label(med: Med) -> str:
    return f"{med.form} {med.name} {med.strength}"


def _pharmacy_line(rng: Random, med: Med, qty: int, day: date) -> PharmacyLine:
    rate = med.mrp if rng.random() < 0.7 else round(med.mrp * 0.95, 2)
    return PharmacyLine(_med_label(med), f"B{rng.randint(1000, 9999)}{rng.choice('ABCDEFGH')}",
                        f"{rng.randint(1, 12):02d}/{day.year + 2}", qty, med.mrp, rate, round(rate * qty, 2), day)


def default_documents(case: Case) -> list[Document]:
    docs = [Document("claim_form", "Claim Form (Part A and Part B)")]
    if case.preauth:
        docs.append(Document("preauth", "Pre-authorisation Request and Approval"))
    docs += [Document("discharge_summary", "Discharge Summary"), Document("final_bill", "Final Bill"),
             Document("pharmacy_bill", "Pharmacy Bill")]
    docs += [Document("lab_report" if r.kind == "lab" else "imaging_report", r.test, {"report_index": i})
             for i, r in enumerate(case.reports)]
    if case.ot:
        docs.append(Document("ot_note", "Operation and Anaesthesia Notes"))
    if case.implants:
        docs += [Document("implant_invoice", "Implant Invoice"), Document("implant_sticker", "Implant Sticker")]
    docs.append(Document("nursing_chart", "Nursing / TPR Chart and Medication Record"))
    if case.mlc:
        docs.append(Document("mlc", "Medico-Legal Case Report"))
    docs += [Document("policy_ecard", "Health Card / Policy Copy"), Document("id_proof", "Photo ID Proof")]
    return docs
