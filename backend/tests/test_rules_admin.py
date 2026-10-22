"""Step 12: the rule library (tune, history, dismissal stats) and reference data changes from the admin screen."""

import uuid

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.models.claim import Claim
from app.models.enums import ReviewAction, RoleName, Severity
from app.models.reference import Package, PolicyTerm, ReferenceImport, RegistryDoctor
from app.models.rules import Finding, FindingReview, RuleChange
from app.models.user import AuditLog
from app.rules.catalogue import CHECKS
from app.rules.engine import run_checks
from app.rules.store import sync_rules
from app.services.reference import importers
from app.services.users import create_user
from tests.test_rules_checks import context
from tests.test_rules_engine import process, synthetic


def headers(db, role: RoleName = RoleName.ADMIN) -> dict[str, str]:
    user = create_user(db, email=f"{role.value}-{uuid.uuid4().hex[:6]}@x.claimguard.in", full_name=role.value,
                       role=role, password="Claims#2026secure")
    token, _ = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def patch(client, hdrs, rule_id, **body):
    body.setdefault("reason", "Tuning after the pilot review")
    return client.patch(f"/api/rules/{rule_id}", headers=hdrs, json=body)


# ---------------------------------------------------------------- rule library


def test_everyone_who_reviews_can_read_the_library_only_admins_change_it(client, db):
    adjudicator = headers(db, RoleName.ADJUDICATOR)
    res = client.get("/api/rules", headers=adjudicator)
    assert res.status_code == 200 and len(res.json()) == len(CHECKS)
    b01 = next(r for r in res.json() if r["id"] == "B-01")
    assert b01["default_config"] == {"tolerance_rs": 1.0} and b01["allowed_statuses"] == ["active", "disabled"]
    assert b01["stats"]["findings"] == 0 and b01["stats"]["dismissal_rate"] is None
    m01 = next(r for r in res.json() if r["id"] == "M-01")
    assert m01["status"] == "draft" and "clinician_reviewed" in m01["allowed_statuses"]
    assert patch(client, adjudicator, "B-01", weight=10).status_code == 403


def test_change_bumps_version_keeps_history_and_audits(client, db):
    admin = headers(db)
    res = patch(client, admin, "B-03", weight=25, config={"over_benchmark_pct": 40})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["weight"] == 25 and body["version"] == 2 and body["config"]["over_benchmark_pct"] == 40
    assert body["config"]["skip_categories"]                         # untouched settings are kept
    assert body["history"][0]["changes"]["weight"] == [15, 25]
    assert body["history"][0]["changed_by"] == "admin"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "rule.updated")) is not None
    assert patch(client, admin, "B-03", weight=25).status_code == 422          # nothing changed
    assert db.scalar(select(RuleChange).where(RuleChange.rule_id == "B-03")).version == 2


def test_edits_are_validated(client, db):
    admin = headers(db)
    assert patch(client, admin, "B-01", weight=150).status_code == 422
    assert patch(client, admin, "B-01", weight=10, reason="x").status_code == 422           # reason too short
    assert patch(client, admin, "B-01", status="clinician_reviewed").status_code == 422     # not a medical rule
    assert patch(client, admin, "C-02", max_severity="red").status_code == 422              # designed for amber
    assert patch(client, admin, "B-01", max_severity="amber").status_code == 200            # lowering is fine
    assert patch(client, admin, "B-01", config={"tolerence_rs": 2}).status_code == 422      # typo in the key
    assert patch(client, admin, "B-01", config={"tolerance_rs": "two"}).status_code == 422  # wrong type
    assert patch(client, admin, "B-01", config={"tolerance_rs": -1}).status_code == 422
    res = patch(client, admin, "B-07", config={"per_day": {"syringe": 6, "gloves": 12, "mask": 4}})
    assert res.status_code == 200 and res.json()["config"]["per_day"] == {"syringe": 6, "gloves": 12, "mask": 4}
    res = patch(client, admin, "C-05", config={"accident_words": ["accident", "rta", "accident", " "]})
    assert res.status_code == 422                                                           # blank word
    res = patch(client, admin, "C-05", config={"accident_words": ["accident", "rta", "accident"]})
    assert res.json()["config"]["accident_words"] == ["accident", "rta"]
    assert patch(client, admin, "B-01", source_url="not a link").status_code == 422


def test_medical_rule_review_names_the_clinician(client, db):
    admin = headers(db)
    assert patch(client, admin, "M-05", status="clinician_reviewed").status_code == 422
    res = patch(client, admin, "M-05", status="clinician_reviewed", clinician="Dr. S. Rao, MD (Medicine)",
                reason="Reviewed against the STG for LOS norms")
    assert res.status_code == 200 and res.json()["status"] == "clinician_reviewed"
    assert res.json()["history"][0]["clinician"] == "Dr. S. Rao, MD (Medicine)"
    assert patch(client, admin, "M-05", status="active").status_code == 422                 # not for medical


def test_settings_reach_the_checks(client, db):
    admin = headers(db)
    assert patch(client, admin, "C-05", status="disabled").status_code == 200
    rules = sync_rules(db)
    out = {o.rule_id: o for o in run_checks(context(), rules)}
    assert out["C-05"].status == "disabled"
    assert patch(client, admin, "B-01", config={"tolerance_rs": 5.5}).status_code == 200
    assert sync_rules(db)["B-01"].config["tolerance_rs"] == 5.5


@pytest.fixture
def claims_store(tmp_path, monkeypatch):
    from app.services import intake
    from app.services.storage import LocalStorage

    local = LocalStorage(str(tmp_path / "storage"))
    monkeypatch.setattr(intake, "get_storage", lambda: local)
    monkeypatch.setattr(intake, "enqueue_processing", lambda claim_id: "job")


def test_dismissal_rate_flags_rules_for_retuning(client, db, claims_store):
    adjudicator = headers(db, RoleName.ADJUDICATOR)
    claim_id = process(client, db, adjudicator, synthetic(["B-10"], seed=61)[0])
    for n in range(6):                       # six dismissed B-10 findings
        f = Finding(claim_id=claim_id, rule_id="B-10", rule_version=1, severity=Severity.RED, title="x",
                    explanation="x", evidence=[], fingerprint=f"dismissed-{n}", is_current=False)
        db.add(f)
        db.flush()
        db.add(FindingReview(finding_id=f.id, action=ReviewAction.DISMISSED, reason="MRP strip is old"))
    db.commit()
    res = client.get("/api/rules/B-10", headers=adjudicator).json()
    assert res["stats"]["dismissed"] == 6 and res["stats"]["needs_retuning"]
    assert res["stats"]["findings"] >= 7 and res["dismissals"][0]["reason"] == "MRP strip is old"


def test_weight_and_status_changes_rescore_open_claims(client, db, claims_store):
    importers.load_defaults(db, include_icd=False)
    adjudicator = headers(db, RoleName.ADJUDICATOR)
    claim_id = process(client, db, adjudicator, synthetic(["B-05"], seed=62)[0])
    claim = db.get(Claim, claim_id)
    assert db.scalar(select(Finding).where(Finding.claim_id == claim_id, Finding.rule_id == "B-05")) is not None
    before = claim.risk_score
    assert before is not None
    admin = headers(db)
    assert patch(client, admin, "B-05", weight=40).status_code == 200
    db.refresh(claim)
    assert claim.risk_score == min(100, before + 35)                       # B-05 now adds 40 instead of 5
    assert patch(client, admin, "B-05", status="disabled").status_code == 200
    db.refresh(claim)
    assert claim.risk_score == before - 5                                  # a disabled rule stops counting


# ---------------------------------------------------------------- reference data


PACKAGES = (b"code,name,specialty,rate,expected_los_days,is_day_care,mandatory_documents,inclusions\n"
            b"SG039A,Cholecystectomy - laparoscopic,General Surgery,22800,3,no,,\n"
            b"MG001A,Acute febrile illness,General Medicine,1800,1,no,,\n")


def test_import_packages_from_the_browser(client, db):
    admin = headers(db)
    res = client.post("/api/reference/imports", headers=admin,
                      data={"dataset": "packages", "version": "HBP2022-test", "source_name": "NHA HBP 2022 (test)"},
                      files=[("files", ("hbp.csv", PACKAGES, "text/csv"))])
    assert res.status_code == 201, res.text
    assert res.json()[0]["row_count"] == 2 and res.json()[0]["file_sha256"]
    assert db.scalar(select(Package).where(Package.code == "SG039A")) is not None
    assert db.scalar(select(AuditLog).where(AuditLog.action == "reference.imported")) is not None
    bad = client.post("/api/reference/imports", headers=admin,
                      data={"dataset": "packages", "version": "x", "source_name": "Wrong file"},
                      files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert bad.status_code == 422
    junk = client.post("/api/reference/imports", headers=admin,
                       data={"dataset": "packages", "version": "x", "source_name": "No header row"},
                       files=[("files", ("hbp.csv", b"a,b\n1,2\n", "text/csv"))])
    assert junk.status_code == 422 and db.scalar(select(ReferenceImport).where(ReferenceImport.version == "x")) is None
    lead = headers(db, RoleName.TEAM_LEAD)
    assert client.post("/api/reference/imports", headers=lead,
                       data={"dataset": "packages", "version": "y", "source_name": "Lead upload"},
                       files=[("files", ("hbp.csv", PACKAGES, "text/csv"))]).status_code == 403


def test_tariffs_need_an_effective_date_and_registries_their_file_names(client, db):
    admin = headers(db)
    tariff = b"code,name,category,rate_non_nabh\nT1,Complete Blood Count,Investigation,150\n"
    res = client.post("/api/reference/imports", headers=admin,
                      data={"dataset": "tariffs", "version": "CGHS-test", "source_name": "CGHS OM test"},
                      files=[("files", ("cghs.csv", tariff, "text/csv"))])
    assert res.status_code == 422
    wrong = client.post("/api/reference/imports", headers=admin,
                        data={"dataset": "registries", "version": "r1", "source_name": "Insurer extract"},
                        files=[("files", ("docs.csv", b"x", "text/csv"))])
    assert wrong.status_code == 422
    doctors = b"registration_no,name,council,status\nMMC/2001/1234,Dr. Test Person,MMC,active\n"
    res = client.post("/api/reference/imports", headers=admin,
                      data={"dataset": "registries", "version": "r1", "source_name": "Insurer extract"},
                      files=[("files", ("doctors.csv", doctors, "text/csv"))])
    assert res.status_code == 201, res.text
    assert db.get(RegistryDoctor, "MMC/2001/1234") is not None


def test_policy_term_edit_keeps_the_shape(client, db):
    importers.load_defaults(db, include_icd=False)
    db.commit()
    admin = headers(db)
    url = "/api/reference/policy-terms/ped_waiting_months"
    res = client.put(url, headers=admin, json={"value": {"months": 24, "is_regulatory_max": True},
                                               "reason": "Client product has 24 months PED"})
    assert res.status_code == 200 and res.json()["value"]["months"] == 24
    assert client.put(url, headers=admin, json={"value": {"months": "two years", "is_regulatory_max": True},
                                                "reason": "Wrong type"}).status_code == 422
    assert client.put(url, headers=admin, json={"value": {"months": 24}, "reason": "Missing key"}).status_code == 422
    term = db.scalar(select(PolicyTerm).where(PolicyTerm.key == "ped_waiting_months"))
    db.refresh(term)
    assert term.value["months"] == 24
    log = db.scalar(select(AuditLog).where(AuditLog.action == "reference.policy_term_updated"))
    assert log is not None and log.details["old"]["months"] == 36
