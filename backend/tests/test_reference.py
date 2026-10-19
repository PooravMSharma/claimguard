"""Step 4: reference data loading, lookups and the reference API."""

import csv
import gzip
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token
from app.models.enums import CityTier, RoleName, TariffSource, WardType
from app.models.reference import Icd10Code, NonPayableItem, Package, PolicyTerm, ReferenceImport, TariffRate
from app.services.reference import importers
from app.services.reference.icd10 import lookup, normalise_code
from app.services.reference.importers import ReferenceImportError, SourceInfo
from app.services.reference.non_payables import NonPayableMatcher
from app.services.reference.paths import ICD10_FILE
from app.services.reference.tables import TableError
from app.services.reference.tariffs import TariffError, cghs_price, is_ward_uniform, rates_in_force
from app.services.reference.text import normalise, parse_money
from app.services.users import create_user

# ---------------------------------------------------------------- helpers

# Made-up test rates. Real rates come from the official CGHS annexure.
TARIFF_ROWS = [
    ["Sr No", "CGHS Code", "Procedure Name", "Section", "Non NABH", "NABH", "Super Speciality"],
    ["1", "TS001", "Appendicectomy - open", "General Surgery", "20000", "23529", "27058"],
    ["2", "LB001", "Haemoglobin", "Laboratory investigation", "100", "120", ""],
    ["3", "CN001", "Consultation OPD", "Consultation", "", "350", ""],
]


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    with open(path, "w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return path


def load_tariffs(db, tmp_path, effective=date(2025, 10, 13), rows=TARIFF_ROWS, version="CGHS-TEST"):
    path = write_csv(tmp_path / f"cghs-{effective}.csv", rows)
    info = SourceInfo(version=version, source_name="Test CGHS annexure", effective_from=effective)
    return importers.import_tariffs(db, path, TariffSource.CGHS, info)


def auth_header(db, role: RoleName) -> dict[str, str]:
    user = create_user(db, email=f"{role.value}@ref.claimguard.in", full_name="Ref Tester", role=role,
                       password="Claims#2026secure")
    token, _ = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def defaults(db):
    importers.import_non_payables(db)
    importers.import_policy_terms(db)


# ---------------------------------------------------------------- text helpers

def test_text_helpers():
    assert normalise("  Gloves (Sterile) & Mask!! ") == "gloves sterile and mask"
    assert parse_money("₹ 1,250.5") == Decimal("1250.50")
    assert parse_money("Rs. 900") == Decimal("900.00")
    assert parse_money("-") is None and parse_money("") is None and parse_money(None) is None


# ---------------------------------------------------------------- non-payables

def test_non_payable_lists_load_with_sizes(db, defaults):
    stmt = select(NonPayableItem.list_number, func.count()).group_by(NonPayableItem.list_number)
    counts = dict(db.execute(stmt).all())
    assert counts == {1: 68, 2: 37, 3: 23, 4: 18}
    rec = db.scalar(select(ReferenceImport).where(ReferenceImport.dataset == "non_payables"))
    assert rec.row_count == 146 and rec.file_sha256 and len(rec.file_sha256) == 64


def test_non_payable_reload_replaces_not_duplicates(db, defaults):
    importers.import_non_payables(db)
    assert db.scalar(select(func.count()).select_from(NonPayableItem)) == 146


@pytest.mark.parametrize("line, list_number, item", [
    ("Surgical Gloves 7.5 x 10", 1, "GLOVES"),
    ("Oxygen mask adult", 1, "OXYGEN MASK"),
    ("Face mask", 2, "FACE MASK"),
    ("Admission Kit", 2, "ADMISSION KIT"),
    ("Registration Charges", 4, "ADMISSION/REGISTRATION CHARGES"),
    ("Cotton roll 500g", 3, "COTTON"),
    ("Diapers adult", 1, "DIAPER OF ANY TYPE"),
    ("Urinebag 2000ml", 4, "URINE BAG"),
    ("Glovs", 1, "GLOVES"),
    ("Misc charges", 2, "INCIDENTAL EXPENSES / MISC. CHARGES (NOT EXPLAINED)"),
])
def test_matcher_finds_non_payables(db, defaults, line, list_number, item):
    hit = NonPayableMatcher.from_db(db).match(line)
    assert hit is not None, line
    assert (hit.list_number, hit.item_name) == (list_number, item)


@pytest.mark.parametrize("line", [
    "Laryngeal mask airway size 4",   # anaesthesia device, not a face mask
    "PEEK interbody spacer 10mm",     # implant, not an inhaler spacer
    "Caps. Omez 20mg",                # medicine, not surgical caps
    "Inj. Ceftriaxone 1g",
    "Excision of soft tissue tumour",
    "Foley catheter 16Fr",
    "Room rent - semi private",
    "Doctor visit charges",
    "CT scan brain",
])
def test_matcher_ignores_payable_lines(db, defaults, line):
    assert NonPayableMatcher.from_db(db).match(line) is None


# ---------------------------------------------------------------- policy terms

def test_policy_terms_load_and_reload_upserts(db, defaults):
    importers.import_policy_terms(db)
    terms = {t.key: t.value for t in db.scalars(select(PolicyTerm).where(PolicyTerm.product_code == "STANDARD"))}
    assert terms["ped_waiting_months"]["months"] == 36
    assert terms["initial_waiting_days"]["days"] == 30
    assert terms["moratorium_months"]["months"] == 60
    assert "Excl18" in terms["standard_exclusions"]["codes"]
    assert db.scalar(select(func.count()).select_from(PolicyTerm)) == len(terms)


# ---------------------------------------------------------------- ICD-10

def test_bundled_icd_file_is_complete():
    with gzip.open(ICD10_FILE, "rt") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) > 90000
    codes = {r["code"] for r in rows}
    assert {"A00", "A00.0", "K35.80", "I21.9", "U07.1"} <= codes


def test_icd_normalise_code():
    assert normalise_code("a000") == "A00.0"
    assert normalise_code(" K35.80 ") == "K35.80"
    assert normalise_code("I21") == "I21"
    assert normalise_code("appendix") is None and normalise_code("123") is None


def test_icd_lookup_exact_category_and_unknown(db, tmp_path):
    path = write_csv(tmp_path / "icd.csv", [
        ["code", "description", "chapter", "category", "is_billable"],
        ["K35", "Acute appendicitis", "11 Digestive", "K35", "0"],
        ["K35.80", "Unspecified acute appendicitis", "11 Digestive", "K35", "1"],
    ])
    importers.import_icd10(db, path, SourceInfo(version="test", source_name="test"))
    exact = lookup(db, "k3580")
    assert (exact.match_level, exact.is_billable, exact.code) == ("exact", True, "K35.80")
    who_only = lookup(db, "K35.9")  # a WHO code not in this set: falls back to its category
    assert who_only.match_level == "category" and who_only.category_description == "Acute appendicitis"
    assert lookup(db, "Q99.9").match_level == "none"
    assert lookup(db, "nonsense").valid_format is False


# ---------------------------------------------------------------- tariffs and the CGHS calculator

def test_ward_uniform_classification():
    assert is_ward_uniform("Laboratory investigation", "Haemoglobin")
    assert is_ward_uniform("Consultation", "Consultation OPD")
    assert not is_ward_uniform("General Surgery", "Appendicectomy - open")


def test_tariff_import_creates_one_row_per_grade(db, tmp_path):
    rec = load_tariffs(db, tmp_path)
    assert rec.row_count == 3 + 2 + 1
    rows = rates_in_force(db, TariffSource.CGHS, "ts001", date(2025, 11, 1))
    assert sorted(r.rate for r in rows) == [Decimal("20000.00"), Decimal("23529.00"), Decimal("27058.00")]
    assert all(r.city_tier == CityTier.TIER_1 and r.ward_type == WardType.SEMI_PRIVATE for r in rows)
    assert rates_in_force(db, TariffSource.CGHS, "TS001", date(2025, 10, 1)) == []  # before it started


@pytest.mark.parametrize("tier, grade, ward, expected", [
    (CityTier.TIER_1, "nabh", WardType.SEMI_PRIVATE, "23529.00"),       # published base
    (CityTier.TIER_1, "non_nabh", WardType.SEMI_PRIVATE, "20000.00"),   # published column used as-is
    (CityTier.TIER_1, "super_speciality", WardType.SEMI_PRIVATE, "27058.00"),
    (CityTier.TIER_2, "nabh", WardType.SEMI_PRIVATE, "21176.10"),       # 23529 x 0.90
    (CityTier.TIER_3, "nabh", WardType.SEMI_PRIVATE, "18823.20"),       # 23529 x 0.80
    (CityTier.TIER_1, "nabh", WardType.GENERAL, "22352.55"),            # 23529 x 0.95
    (CityTier.TIER_1, "nabh", WardType.PRIVATE, "24705.45"),            # 23529 x 1.05
    (CityTier.TIER_3, "non_nabh", WardType.PRIVATE, "16800.00"),        # 20000 x 0.80 x 1.05
])
def test_cghs_price_for_surgery(db, tmp_path, tier, grade, ward, expected):
    load_tariffs(db, tmp_path)
    result = cghs_price(db, "TS001", city_tier=tier, grade=grade, ward_type=ward, on=date(2025, 11, 1))
    assert result.rate == Decimal(expected)


def test_cghs_price_derives_missing_grade_and_keeps_ward_uniform_items_flat(db, tmp_path):
    load_tariffs(db, tmp_path)
    # Haemoglobin has no super speciality column: NABH x 1.15. Lab tests ignore the ward.
    result = cghs_price(db, "LB001", grade="super_speciality", ward_type=WardType.PRIVATE, on=date(2025, 11, 1))
    assert result.rate == Decimal("138.00")
    assert result.ward_uniform and [a.step for a in result.adjustments] == ["hospital grade", "ward"]
    # Consultation only has NABH: non-NABH is 15% lower.
    assert cghs_price(db, "CN001", grade="non_nabh", on=date(2025, 11, 1)).rate == Decimal("297.50")


def test_cghs_price_errors(db, tmp_path):
    load_tariffs(db, tmp_path)
    with pytest.raises(TariffError):
        cghs_price(db, "NOPE", on=date(2025, 11, 1))
    with pytest.raises(TariffError):
        cghs_price(db, "TS001", grade="gold", on=date(2025, 11, 1))


def test_new_tariff_version_closes_the_old_one(db, tmp_path):
    load_tariffs(db, tmp_path, effective=date(2014, 4, 1), version="CGHS-2014")
    newer = [r[:] for r in TARIFF_ROWS]
    newer[1][5] = "25000"
    load_tariffs(db, tmp_path, effective=date(2025, 10, 13), rows=newer, version="CGHS-2025")

    old = cghs_price(db, "TS001", on=date(2020, 1, 1))
    new = cghs_price(db, "TS001", on=date(2026, 1, 1))
    assert (old.rate, new.rate) == (Decimal("23529.00"), Decimal("25000.00"))
    closed = db.scalars(select(TariffRate.effective_to).where(TariffRate.effective_from == date(2014, 4, 1)))
    assert set(closed) == {date(2025, 10, 12)}

    # Re-loading the same start date replaces, never duplicates.
    load_tariffs(db, tmp_path, effective=date(2025, 10, 13), rows=newer, version="CGHS-2025")
    assert len(rates_in_force(db, TariffSource.CGHS, "TS001", date(2026, 1, 1))) == 3


def test_tariff_import_rejects_bad_files(db, tmp_path):
    with pytest.raises(TableError):
        load_tariffs(db, tmp_path, rows=[["foo", "bar"], ["1", "2"]])
    with pytest.raises(ReferenceImportError):
        load_tariffs(db, tmp_path, rows=[["Code", "Procedure Name"], ["X1", "Something"]])
    with pytest.raises(ReferenceImportError):
        importers.import_tariffs(db, write_csv(tmp_path / "t.csv", TARIFF_ROWS), TariffSource.CGHS,
                                 SourceInfo(version="x", source_name="x"))  # no effective date


# ---------------------------------------------------------------- packages

def test_package_import_from_excel_with_title_rows(db, tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    ws = book.active
    assert ws is not None
    ws.append(["Health Benefit Package 2022"])          # title row above the table
    ws.append([])
    ws.append(["Procedure Code", "Procedure Name", "Speciality", "Package Price", "ALOS", "Day Care",
               "Mandatory Documents"])
    ws.append(["SG039A", "Appendicectomy - open", "General Surgery", 17200, "3 days", "No",
               "USG abdomen | Histopathology report"])
    ws.append(["SG039A", "duplicate row is ignored", "General Surgery", 1, None, None, None])
    ws.append(["MG001A", "Acute febrile illness", "General Medicine", "₹ 1,800", None, None, None])
    path = tmp_path / "hbp.xlsx"
    book.save(path)

    info = SourceInfo(version="HBP2022", source_name="Test HBP", effective_from=date(2022, 4, 1))
    rec = importers.import_packages(db, path, info)
    assert rec.row_count == 2
    pkg = db.scalar(select(Package).where(Package.code == "SG039A"))
    assert (pkg.rate, pkg.expected_los_days, pkg.is_day_care) == (Decimal("17200.00"), 3, False)
    assert pkg.mandatory_documents == ["USG abdomen", "Histopathology report"] and pkg.import_id == rec.id

    importers.import_packages(db, path, info)  # same version again: replaced
    assert db.scalar(select(func.count()).select_from(Package)) == 2


# ---------------------------------------------------------------- API

def test_reference_api_requires_login_and_permission(client, db):
    assert client.get("/api/reference/summary").status_code == 401
    # every role has reference.view
    assert client.get("/api/reference/summary", headers=auth_header(db, RoleName.ADJUDICATOR)).status_code == 200


def test_reference_api_endpoints(client, db, defaults, tmp_path):
    load_tariffs(db, tmp_path)
    headers = auth_header(db, RoleName.ADJUDICATOR)

    summary = {d["dataset"]: d for d in client.get("/api/reference/summary", headers=headers).json()}
    assert summary["non_payables"]["rows"] == 146 and summary["tariffs"]["rows"] == 6
    assert summary["packages"]["latest_import"] is None

    res = client.get("/api/reference/non-payables?list_number=3", headers=headers).json()
    assert len(res) == 23

    matches = client.post("/api/reference/non-payables/match", headers=headers,
                          json={"lines": ["Surgical gloves", "Inj. Ceftriaxone 1g"]}).json()
    assert matches[0]["matched"] and matches[0]["list_number"] == 1 and not matches[1]["matched"]

    page = client.get("/api/reference/tariffs?q=append&on=2025-11-01", headers=headers).json()
    assert page["total"] == 3 and page["items"][0]["source"] == "cghs"

    price = client.get("/api/reference/tariffs/cghs/TS001/price",
                       params={"city_tier": "tier_2", "grade": "nabh", "ward_type": "general", "on": "2025-11-01"},
                       headers=headers)
    assert price.status_code == 200
    body = price.json()
    assert Decimal(body["rate"]) == Decimal("20117.30")  # 23529 x 0.90 x 0.95
    assert [a["step"] for a in body["adjustments"]] == ["city tier", "ward"]
    assert body["source_name"] == "Test CGHS annexure"

    assert client.get("/api/reference/tariffs/cghs/NOPE/price", headers=headers).status_code == 404
    bad = client.get("/api/reference/tariffs/cghs/TS001/price?grade=gold", headers=headers)
    assert bad.status_code == 422

    terms = {t["key"]: t for t in client.get("/api/reference/policy-terms", headers=headers).json()}
    assert terms["claim_settlement_days"]["value"]["days"] == 30

    db.add(Icd10Code(code="K35.80", description="Unspecified acute appendicitis", category="K35"))
    db.add(Icd10Code(code="K35", description="Acute appendicitis", category="K35", is_billable=False))
    db.flush()
    found = client.get("/api/reference/icd10?q=appendicitis", headers=headers).json()
    assert found["total"] == 2 and found["items"][0]["code"] == "K35"
    by_code = client.get("/api/reference/icd10?q=k35&billable_only=true", headers=headers).json()
    assert [i["code"] for i in by_code["items"]] == ["K35.80"]
    assert client.get("/api/reference/icd10/lookup/k3580", headers=headers).json()["match_level"] == "exact"

    imports = client.get("/api/reference/imports", headers=headers).json()
    assert {i["dataset"] for i in imports} >= {"non_payables", "policy_terms", "tariffs"}