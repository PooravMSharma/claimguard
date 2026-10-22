"""Step 13: matching bill wording to tariff items, and the CGHS-adjusted benchmark rate (B-03)."""

import copy
import csv
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.enums import TariffSource
from app.models.reference import TariffMapping, TariffRate
from app.rules.reference import HospitalRef, Reference, TariffRef
from app.rules.tariff_match import build, core, line_kinds, tariff_kind, words
from app.services.reference import importers
from app.services.reference.importers import ReferenceImportError, SourceInfo
from tests.test_rules_checks import context, d, genuine_docs, outcome, reference, row

# A few real CGHS items (names as printed in the 2025 list). Two MRI rows with different prices make
# "MRI Brain" ambiguous on purpose.
ITEMS = [
    ("Complete Haemogram/CBC, Hb, RBC count and indices, TLC, DLC, Platelet, ESR, Peripheral smear", "LB012",
     "test", 135.0),
    ("Haemoglobin (Hb)", "LB007", "test", 23.0),
    ("Liver Function Tests", "LB124", "test", 225.0),
    ("Renal Function Tests", "LB123", "test", 225.0),
    ("MRI Brain (plain)", "RI089", "test", 2000.0),
    ("MRI Brain (with contrast)", "RI090", "test", 3500.0),
    ("Laparoscopic Cholecystectomy", "AG095", "procedure", 30000.0),
    ("Consultation for Inpatients", "CN002", "consultation", 350.0),
]


@pytest.fixture(scope="module")
def matcher():
    return build(ITEMS)


def code(matcher, text, category):
    kinds = line_kinds(category)
    hit = matcher.best(text, kinds, 0.6) if kinds is not None else None
    return ITEMS[hit.key][1] if hit else None


def test_core_reduces_wording_to_one_vocabulary():
    assert words("Complete Blood Count") == words("CBC") == words("Haemogram") == {"cbc"}
    assert words("USG Whole Abdomen") == words("Ultrasound abdomen") == {"usg", "abdomen"}
    assert "contrast" in core("MRI Brain (with contrast)")
    assert core("Surgeon fee - Laparoscopic cholecystectomy") == "lap cholecystectomy"


@pytest.mark.parametrize("text, category, expected", [
    ("CBC", "investigation", "LB012"),
    ("Complete Blood Count", "investigation", "LB012"),
    ("Hb%", "investigation", "LB007"),
    ("LFT", "investigation", "LB124"),
    ("Kidney function test", "investigation", "LB123"),
    ("MRI Brain with contrast", "investigation", "RI090"),
    ("MRI Brain", "investigation", None),                         # plain or contrast? prices differ: not compared
    ("Consultant visit charges (Dr. Jain)", "consultation", "CN002"),
    ("Laparoscopic cholecystectomy", "procedure", "AG095"),
    ("Laparoscopic cholecystectomy", "investigation", None),      # a test line never matches a procedure
    ("Surgeon fee - Laparoscopic cholecystectomy", "surgeon_fee", None),   # CGHS rate is the whole package
    ("Dressing charges", "procedure", None),
])
def test_bill_wording_matches_the_right_item(matcher, text, category, expected):
    assert code(matcher, text, category) == expected


def test_item_kinds_from_code_and_group():
    assert tariff_kind("LB012", "Laboratory Medicine", None) == "test"
    assert tariff_kind("CN002", "Consultation", None) == "consultation"
    assert tariff_kind("TM010", "Cancer Surgery", None) == "cancer_procedure"
    assert tariff_kind("AG095", "General Surgery", None) == "procedure"
    assert tariff_kind("SYN-SF-SG020", None, "Professional fee") == "professional_fee"
    assert line_kinds("procedure", cancer=True) == {"procedure", "critical_care", "cancer_procedure"}
    assert line_kinds("surgeon_fee") is None


# ---------------------------------------------------------------- B-03 with CGHS rows

def cghs_reference(nabh=None, tier="tier_2"):
    ref = reference()
    ref.tariffs = [
        TariffRef("LB124", "Liver Function Tests", "Laboratory Medicine", "tier_1", "general", Decimal(225),
                  source="cghs", kind="test", nabh=True, ward_uniform=True),
        TariffRef("AG095", "Laparoscopic Cholecystectomy", "General Surgery", "tier_1", "general", Decimal(30000),
                  source="cghs", kind="procedure", nabh=True),
    ]
    hospital = next(iter(ref.hospitals.values()))
    ref.hospitals = {hospital.rohini_id: HospitalRef(hospital.rohini_id, hospital.hfr_id, hospital.name,
                                                     hospital.city, tier, nabh)}
    return ref


def with_lines(*lines):
    docs = copy.deepcopy(genuine_docs())
    d(docs, "final_bill").tables["lines"].extend(lines)
    return docs


def test_cghs_rate_is_adjusted_for_city_tier_and_not_for_ward_on_tests():
    lft = row(date="2026-04-05", category="Investigations", description="LFT", qty=1, rate=600, amount=600)
    o = outcome(context(with_lines(lft), cghs_reference()), "B-03")
    flag = next(f for f in o.findings if "LFT" in f.title)
    assert "Rs. 202.50" in flag.explanation or "202.50" in flag.explanation.replace(",", "")    # 225 x 0.9 (tier 2)
    assert flag.suggested_deduction is None             # CGHS is a reference rate: no automatic deduction
    assert "tier 2 city" in flag.explanation and "ward" not in flag.explanation.split("adjusted for")[1].split(")")[0]
    fair = row(date="2026-04-05", category="Investigations", description="LFT", qty=1, rate=240, amount=240)
    assert not any("LFT" in f.title for f in outcome(context(with_lines(fair), cghs_reference()), "B-03").findings)


def test_unknown_hospital_grade_uses_the_highest_procedure_rate():
    proc = row(date="2026-04-06", category="Procedure Charges", description="Lap cholecystectomy", qty=1,
               rate=70000, amount=70000)
    # flagged only above 2.5 x the CGHS rate (over_cghs_pct 150)
    # tier 1, grade unknown: 30000 x 1.15 (super speciality) x 1.0526 (semi-private vs general) = 36,315.79
    unknown = outcome(context(with_lines(proc), cghs_reference(nabh=None, tier="tier_1")), "B-03")
    assert not any("cholecystectomy" in f.title.lower() for f in unknown.findings)
    # the same bill at a non-NABH hospital: 30000 x 0.85 x 1.0526 = 26,842 -> 70,000 is 161% higher
    non_nabh = outcome(context(with_lines(proc), cghs_reference(nabh=False, tier="tier_1")), "B-03")
    flag = next(f for f in non_nabh.findings if "cholecystectomy" in f.title.lower())
    assert "non nabh hospital" in flag.explanation and "AG095" in flag.explanation


def test_an_agreed_tariff_is_used_before_cghs():
    ref = cghs_reference(tier="tier_1")
    ref.tariffs.append(TariffRef("H-LFT", "Liver function test", "Laboratory", "tier_1", "any", Decimal(300)))
    line = row(date="2026-04-05", category="Investigations", description="LFT", qty=1, rate=400, amount=400)
    flag = next(f for f in outcome(context(with_lines(line), ref), "B-03").findings if "LFT" in f.title)
    assert "H-LFT" in flag.explanation and flag.suggested_deduction == Decimal(100)


def test_a_confirmed_mapping_wins_over_wording():
    ref = cghs_reference(tier="tier_1")
    ref.tariff_mappings = {core("Liver profile panel"): "LB124"}
    line = row(date="2026-04-05", category="Investigations", description="Liver profile panel", qty=1, rate=600,
               amount=600)
    flag = next(f for f in outcome(context(with_lines(line), ref), "B-03").findings if "Liver" in f.title)
    assert "confirmed mapping" in flag.explanation


# ---------------------------------------------------------------- the real CGHS list, when it is present

CGHS_FILE = Path(os.environ.get("CGHS_FILE", Path(__file__).resolve().parents[1] / "reference_data" / "raw"
                                / "cghs.csv"))
REAL_CASES = [
    ("Complete Blood Count", "investigation", "LB012"), ("Hb%", "investigation", "LB007"),
    ("Liver Function Test", "investigation", "LB124"), ("RFT", "investigation", "LB123"),
    ("Lipid Profile", "investigation", "LB125"), ("Troponin I", "investigation", "LB111"),
    ("Dengue NS1 Antigen", "investigation", "LB269"), ("ECG", "investigation", "CI001"),
    ("2D Echo", "investigation", "RI001"), ("USG Whole Abdomen", "investigation", "RI020"),
    ("X-Ray Chest PA", "investigation", "RI034"), ("MRI Brain (plain)", "investigation", "RI089"),
    ("MRI Brain", "investigation", None), ("CT Scan Chest", "investigation", None),
    ("Fasting Blood Sugar", "investigation", "LB055"), ("NICU charges", "icu", "CC007"),
    ("Urine Routine", "investigation", "LB001"), ("Consultation inpatient", "consultation", "CN002"),
    ("Lap Appendectomy package", "package", "AG095"), ("Nebulization", "procedure", "CC012"),
    ("Surgeon fee - Laparoscopic cholecystectomy", "surgeon_fee", None),
]


@pytest.mark.skipif(not CGHS_FILE.exists(), reason="put the CGHS CSV at reference_data/raw/cghs.csv to run this")
def test_matching_against_the_full_cghs_list():
    items = []
    with open(CGHS_FILE, encoding="utf-8-sig", newline="") as handle:
        for r in csv.DictReader(handle):
            try:
                rate = float(r["Rate (₹)"].replace(",", ""))
            except ValueError:
                continue
            items.append((r["Procedure Name"], r["Cghs Code No."],
                          tariff_kind(r["Cghs Code No."], r["Speciality Classification"], None), rate))
    m = build(items)
    wrong = []
    for text, category, expected in REAL_CASES:
        kinds = line_kinds(category)
        hit = m.best(text, kinds, 0.6) if kinds is not None else None
        got = items[hit.key][1] if hit else None
        if got != expected:
            wrong.append((text, expected, got))
    assert not wrong


# ---------------------------------------------------------------- importing (database)

# the layout of the CGHS download (one row per item, tier / facility / ward as columns)
CGHS_ROWS = [
    ["SNo.", "Procedure Name", "Speciality Classification", "Tier", "Facility", "Ward", "Cghs Code No.", "Rate (₹)"],
    ["1", "Consultation for Inpatients", "Consultation", "TIER I", "NABH", "GENERAL WARD", "CN002", "350.00"],
    ["2", "Liver Function Tests", "Laboratory Medicine", "TIER I", "NABH", "GENERAL WARD", "LB124", "225.00"],
    ["3", "PET CT scan", "Nuclear Medicine", "TIER I", "NABH", "GENERAL WARD", "NM010", "38740+GST"],
    ["4", "Implant as per actual", "Orthopaedics", "TIER I", "NABH", "GENERAL WARD", "OR999", "Unlisted"],
]


def write(path: Path, rows) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    return path


def load_cghs(db, tmp_path):
    info = SourceInfo(version="CGHS-2025-10", source_name="CGHS OM 03.10.2025", effective_from=date(2025, 10, 13))
    return importers.import_tariffs(db, write(tmp_path / "cghs.csv", CGHS_ROWS), TariffSource.CGHS, info)


def test_cghs_download_layout_imports(db, tmp_path):
    rec = load_cghs(db, tmp_path)
    assert rec.row_count == 3
    assert "plus GST" in rec.notes and "skipped" in rec.notes
    lft = db.scalar(select(TariffRate).where(TariffRate.code == "LB124"))
    assert (lft.city_tier.value, lft.ward_type.value, lft.nabh_accredited, lft.ward_uniform) == \
        ("tier_1", "general", True, True)
    assert db.scalar(select(TariffRate.rate).where(TariffRate.code == "NM010")) == Decimal("38740")


def test_mapping_import_normalises_wording_and_refuses_unknown_codes(db, tmp_path):
    load_cghs(db, tmp_path)
    info = SourceInfo(version="links-1", source_name="Confirmed by claims lead")
    good = write(tmp_path / "links.csv", [["Bill wording", "Tariff code", "Tariff source"],
                                          ["Liver profile panel", "lb124", "CGHS"], ["", "LB124", ""]])
    rec = importers.import_tariff_mappings(db, good, info)
    assert rec.row_count == 1
    assert db.scalar(select(TariffMapping.bill_text)) == core("Liver profile panel")
    assert Reference.from_db(db).tariff_mappings == {core("Liver profile panel"): "LB124"}
    bad = write(tmp_path / "bad.csv", [["Bill wording", "Tariff code"], ["Some test", "ZZ999"]])
    with pytest.raises(ReferenceImportError, match="not in the loaded tariffs"):
        importers.import_tariff_mappings(db, bad, SourceInfo(version="links-2", source_name="x"))


def test_match_report_lists_each_wording_once(tmp_path):
    from app.rules.tariff_report import HEADER, collect, write_csv

    ref = cghs_reference(tier="tier_1")
    extra = row(date="2026-04-05", category="Investigations", description="LFT", qty=1, rate=250, amount=250)
    ctxs = [context(with_lines(extra), ref), context(with_lines(extra), ref)]
    for c in ctxs:
        c.config["B-03"] = {"skip_categories": ["room", "pharmacy", "package", "other", "non_medical"]}
    rows = collect(ctxs)
    lft = next(r for r in rows if r.text == "lft")
    assert (lft.code, lft.benchmark, len(lft.billed)) == ("LB124", Decimal("225.00"), 2)
    assert len(lft.claims) == 1                      # both contexts are the same test claim
    out = tmp_path / "report.csv"
    write_csv(rows, out)
    with open(out, encoding="utf-8") as handle:
        assert next(csv.reader(handle)) == HEADER


def test_mapping_upload_from_the_admin_screen(client, db, tmp_path):
    from app.models.enums import RoleName
    from tests.test_reference import auth_header

    load_cghs(db, tmp_path)
    headers = auth_header(db, RoleName.ADMIN)
    form = {"dataset": "tariff_mappings", "version": "links-1", "source_name": "Confirmed by claims lead"}

    def upload(text):
        return client.post("/api/reference/imports", data=form, headers=headers,
                           files={"files": ("links.csv", text.encode(), "text/csv")})

    ok = upload("Bill wording,Tariff code\nLFT panel,LB124\n")
    assert ok.status_code == 201, ok.text
    assert ok.json()[0]["row_count"] == 1
    refused = upload("Bill wording,Tariff code\nLFT panel,XX1\n")
    assert refused.status_code == 422 and "not in the loaded tariffs" in refused.json()["detail"]
