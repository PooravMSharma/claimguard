"""The 10 packages the synthetic set covers (PRD: test data).

Rates are illustrative synthetic values in rupees, not official tariffs: the
benchmark checks read the loaded CGHS / PM-JAY tables, never these numbers.
Clinical content follows common treatment patterns and is for test data only.
"""

from dataclasses import dataclass, field
from random import Random


@dataclass(frozen=True)
class Med:
    name: str
    strength: str
    form: str            # Tab / Inj / Syp / Cap / Drops
    mrp: float           # per unit
    per_day: int


@dataclass(frozen=True)
class Test:
    name: str
    kind: str            # lab | imaging
    rate: float


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    package_code: str
    specialty: str
    icd_code: str
    diagnosis: str
    procedure: str | None
    surgical: bool
    day_care: bool
    los: tuple[int, int]           # usual length of stay in days (min, max)
    los_norm: int                  # upper norm used by M-05
    icu_days: tuple[int, int]
    package_rate: float
    gender: str | None             # None = any
    age: tuple[int, int]
    tests: tuple[Test, ...]
    meds: tuple[Med, ...]
    implant: tuple[str, str, str, float] | None = None   # (type, brand, model, price)
    accident: bool = False
    specific_waiting: bool = False  # listed under specific-disease waiting period
    complaints: str = ""
    course: str = ""
    advice: str = ""
    upcoded_procedure: str | None = None
    extra_surgery: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


CBC = Test("Complete Blood Count", "lab", 350)
LFT = Test("Liver Function Test", "lab", 650)
RFT = Test("Renal Function Test", "lab", 600)
ELEC = Test("Serum Electrolytes", "lab", 500)
CRP = Test("C-Reactive Protein", "lab", 450)

SCENARIOS: dict[str, Scenario] = {s.key: s for s in [
    Scenario(
        "appendicectomy", "Appendicectomy (laparoscopic)", "SYN-SG039", "General Surgery", "K35.80",
        "Acute appendicitis", "Laparoscopic appendicectomy", True, False, (2, 4), 4, (0, 0), 30000, None, (14, 60),
        (CBC, CRP, Test("USG Abdomen", "imaging", 1200)),
        (Med("Ceftriaxone", "1 g", "Inj", 62.0, 2), Med("Metronidazole", "500 mg/100 ml", "Inj", 28.0, 3),
         Med("Pantoprazole", "40 mg", "Inj", 48.0, 1), Med("Paracetamol", "1 g/100 ml", "Inj", 95.0, 3)),
        complaints="Pain in right lower abdomen for 2 days with fever and vomiting.",
        course="Diagnosed as acute appendicitis on USG. Underwent laparoscopic appendicectomy under GA. "
               "Post-operative period uneventful.",
        advice="Soft diet. Tab Cefuroxime 500 mg BD x 5 days. Review after 7 days for suture removal.",
        upcoded_procedure="Laparoscopic appendicectomy for perforated appendix with peritoneal lavage",
        tags=("upcodable",),
    ),
    Scenario(
        "cataract", "Cataract surgery with IOL (phaco)", "SYN-OP009", "Ophthalmology", "H25.12",
        "Age-related nuclear cataract, left eye", "Phacoemulsification with foldable IOL, left eye", True, True,
        (0, 0), 0, (0, 0), 25000, None, (52, 80),
        (Test("Biometry (A-scan)", "imaging", 900), Test("Random Blood Sugar", "lab", 120)),
        (Med("Moxifloxacin", "0.5%", "Drops", 18.0, 4), Med("Prednisolone acetate", "1%", "Drops", 22.0, 4),
         Med("Tropicamide + Phenylephrine", "0.8%/5%", "Drops", 15.0, 3)),
        implant=("Intraocular lens", "AcrySof-like Foldable IOL (fictional)", "SN60-SYN", 9000.0),
        specific_waiting=True,
        complaints="Gradual painless diminution of vision in left eye for 8 months.",
        course="Underwent uneventful phacoemulsification with foldable IOL implantation, left eye, under local "
               "anaesthesia as a day-care procedure.",
        advice="Eye drops as advised. Avoid rubbing eye. Review after 1 week.",
        tags=("day_care", "implant", "sub_limit"),
    ),
    Scenario(
        "normal_delivery", "Normal vaginal delivery", "SYN-OB001", "Obstetrics", "O80",
        "Single spontaneous delivery", "Normal vaginal delivery with episiotomy", True, False, (1, 3), 3, (0, 0),
        18000, "F", (21, 36),
        (CBC, Test("USG Obstetric", "imaging", 1100), Test("Blood Group and Rh", "lab", 150)),
        (Med("Oxytocin", "10 IU", "Inj", 25.0, 1), Med("Paracetamol", "650 mg", "Tab", 2.0, 3),
         Med("Ferrous ascorbate + Folic acid", "100 mg", "Tab", 9.0, 1)),
        complaints="Term pregnancy (39 weeks) with labour pains.",
        course="Progressed to spontaneous vaginal delivery of a live female baby, 2.9 kg. Mother and baby stable.",
        advice="Breastfeeding. Iron and calcium supplements for 3 months. Review after 6 weeks.",
    ),
    Scenario(
        "dengue", "Dengue fever", "SYN-MG064", "General Medicine", "A90",
        "Dengue fever with thrombocytopenia", None, False, False, (3, 5), 5, (0, 0), 15000, None, (12, 65),
        (CBC, Test("Dengue NS1 Antigen", "lab", 600), Test("Dengue IgM", "lab", 600), LFT),
        (Med("Paracetamol", "650 mg", "Tab", 2.0, 3), Med("Ringer Lactate", "500 ml", "Inj", 45.0, 3),
         Med("Ondansetron", "4 mg", "Inj", 12.0, 2)),
        complaints="High grade fever with body ache and headache for 3 days.",
        course="NS1 positive. Platelet count monitored daily; managed with IV fluids and supportive care. "
               "Platelets recovered.",
        advice="Oral fluids. Tab Paracetamol SOS. Repeat CBC after 3 days.",
        tags=("platelets",),
    ),
    Scenario(
        "pneumonia", "Community acquired pneumonia", "SYN-MG006", "General Medicine", "J18.9",
        "Community acquired pneumonia", None, False, False, (4, 6), 7, (0, 2), 22000, None, (25, 80),
        (CBC, CRP, Test("Chest X-ray PA view", "imaging", 400), RFT),
        (Med("Ceftriaxone", "1 g", "Inj", 62.0, 2), Med("Azithromycin", "500 mg", "Tab", 25.0, 1),
         Med("Salbutamol + Ipratropium", "2.5 ml", "Respules", 22.0, 3), Med("Paracetamol", "650 mg", "Tab", 2.0, 3)),
        complaints="Fever with cough and breathlessness for 4 days.",
        course="Chest X-ray showed right lower lobe consolidation. Treated with IV antibiotics and nebulisation. "
               "Improved.",
        advice="Tab Cefuroxime 500 mg BD x 5 days. Review after 1 week with chest X-ray.",
        tags=("icu_possible",),
    ),
    Scenario(
        "femur_fracture", "Fracture neck of femur: fixation", "SYN-OR018", "Orthopaedics", "S72.001A",
        "Closed fracture of neck of right femur (fall from two-wheeler)", "Closed reduction and internal fixation "
        "with proximal femoral nail, right", True, False, (5, 7), 8, (0, 0), 60000, None, (25, 75),
        (CBC, RFT, Test("X-ray Right Hip AP/Lateral", "imaging", 500), Test("ECG", "lab", 250)),
        (Med("Cefuroxime", "1.5 g", "Inj", 140.0, 2), Med("Enoxaparin", "40 mg", "Inj", 320.0, 1),
         Med("Tramadol", "50 mg", "Inj", 18.0, 2), Med("Pantoprazole", "40 mg", "Inj", 48.0, 1)),
        implant=("Proximal femoral nail", "OrthoFix-like PFN (fictional)", "PFN-240-SYN", 28000.0),
        accident=True,
        complaints="Fall from two-wheeler, pain and inability to bear weight on right lower limb.",
        course="X-ray showed fracture neck of right femur. Underwent CRIF with PFN under spinal anaesthesia. "
               "Mobilised with walker.",
        advice="Non weight bearing walking with walker. Inj Enoxaparin x 10 days. Review after 2 weeks.",
        tags=("implant", "accident"),
    ),
    Scenario(
        "ptca", "PTCA with single stent", "SYN-CV013", "Cardiology", "I21.4",
        "Non-ST elevation myocardial infarction", "Coronary angiography and PTCA with one drug-eluting stent to LAD",
        True, False, (3, 5), 5, (1, 2), 120000, None, (40, 78),
        (CBC, Test("Troponin I", "lab", 900), Test("2D Echocardiography", "imaging", 2000), RFT,
         Test("Lipid Profile", "lab", 600)),
        (Med("Aspirin", "75 mg", "Tab", 1.0, 1), Med("Ticagrelor", "90 mg", "Tab", 38.0, 2),
         Med("Atorvastatin", "80 mg", "Tab", 22.0, 1), Med("Heparin", "5000 IU", "Inj", 95.0, 2)),
        implant=("Drug-eluting coronary stent", "CardioFlex-like DES (fictional)", "DES-3.0x28-SYN", 38000.0),
        complaints="Chest pain radiating to left arm for 6 hours with sweating.",
        course="Troponin raised. Coronary angiography showed 90% LAD stenosis; PTCA with one DES done. "
               "Observed in ICCU, then shifted to ward.",
        advice="Dual antiplatelet therapy for 1 year. Statin. Cardiac rehabilitation. Review after 1 week.",
        tags=("implant", "icu"),
    ),
    Scenario(
        "cholecystectomy", "Laparoscopic cholecystectomy", "SYN-SG020", "General Surgery", "K80.20",
        "Symptomatic cholelithiasis", "Laparoscopic cholecystectomy", True, False, (2, 3), 3, (0, 0), 35000, None,
        (25, 70),
        (CBC, LFT, Test("USG Abdomen", "imaging", 1200)),
        (Med("Ceftriaxone", "1 g", "Inj", 62.0, 2), Med("Pantoprazole", "40 mg", "Inj", 48.0, 1),
         Med("Diclofenac", "75 mg", "Inj", 10.0, 2)),
        specific_waiting=True,
        complaints="Recurrent pain in right upper abdomen after fatty meals for 3 months.",
        course="USG showed multiple gall bladder calculi. Underwent laparoscopic cholecystectomy. Uneventful recovery.",
        advice="Low fat diet. Tab Paracetamol SOS. Review after 7 days.",
        extra_surgery="Open inguinal hernia repair with mesh, right",
        tags=("multi_surgery",),
    ),
    Scenario(
        "gastroenteritis", "Acute gastroenteritis with dehydration", "SYN-MG036", "General Medicine", "A09",
        "Acute gastroenteritis with moderate dehydration", None, False, False, (2, 3), 3, (0, 0), 9000, None,
        (8, 70),
        (CBC, ELEC, RFT),
        (Med("Ringer Lactate", "500 ml", "Inj", 45.0, 4), Med("Ondansetron", "4 mg", "Inj", 12.0, 2),
         Med("Pantoprazole", "40 mg", "Inj", 48.0, 1), Med("ORS", "21 g", "Sachet", 20.0, 3)),
        complaints="Loose stools 10-12 episodes and vomiting since 1 day, unable to take orally.",
        course="Moderate dehydration on admission. Managed with IV fluids and antiemetics. Tolerating orally.",
        advice="ORS. Light diet. Tab Racecadotril 100 mg TDS x 3 days.",
        tags=("outpatient_possible",),
    ),
    Scenario(
        "typhoid", "Enteric fever", "SYN-MG047", "General Medicine", "A01.00",
        "Typhoid fever", None, False, False, (4, 6), 7, (0, 0), 14000, None, (10, 60),
        (CBC, Test("Widal Test", "lab", 250), Test("Blood Culture", "lab", 900), LFT),
        (Med("Ceftriaxone", "2 g", "Inj", 110.0, 1), Med("Paracetamol", "650 mg", "Tab", 2.0, 3),
         Med("Pantoprazole", "40 mg", "Inj", 48.0, 1)),
        complaints="Fever for 7 days, step-ladder pattern, with abdominal discomfort.",
        course="Blood culture grew Salmonella Typhi. Treated with IV ceftriaxone. Afebrile for 48 hours at discharge.",
        advice="Tab Azithromycin 500 mg OD x 7 days. Soft diet. Review after 1 week.",
    ),
]}

IRRELEVANT_TEST = Test("MRI Brain (plain)", "imaging", 6500)
NORMAL_CONSUMABLES = [("IV Cannula 20G", 1, 120.0), ("IV Infusion Set", 1, 90.0), ("Disposable Syringe 5 ml", 4, 12.0)]


def pick(rng: Random) -> Scenario:
    return rng.choice(list(SCENARIOS.values()))
