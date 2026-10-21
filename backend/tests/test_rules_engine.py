"""Step 9 end to end: the checks step in the pipeline, findings API, reviews, re-runs, registries, rules list."""

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token
from app.models.enums import ClaimStatus, RoleName, TariffSource
from app.models.extraction import ExtractedField
from app.models.reference import PolicyRecord, RegistryDoctor, RegistryHospital, RegistryWatchlist
from app.models.rules import CheckRun, Finding, FindingReview, Rule
from app.models.user import AuditLog
from app.pipeline import runner
from app.rules.catalogue import CHECKS
from app.rules.reference import Reference
from app.rules.store import run_for_claim, sync_rules
from app.services import intake
from app.services.reference import importers
from app.services.reference.registries import RegistryImportError, import_registries
from app.services.storage import LocalStorage
from app.services.users import create_user
from app.synthetic.generate import Generator, Options, write_reference
from app.synthetic.render import render_case


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    local = LocalStorage(str(tmp_path / "storage"))
    monkeypatch.setattr(intake, "get_storage", lambda: local)
    monkeypatch.setattr(intake, "enqueue_processing", lambda claim_id: "job")
    return local


@pytest.fixture
def defaults(db):
    importers.load_defaults(db, include_icd=False)
    db.flush()


def headers(db, role: RoleName = RoleName.ADJUDICATOR) -> dict[str, str]:
    user = create_user(db, email=f"{role.value}-{uuid.uuid4().hex[:6]}@x.claimguard.in", full_name=f"{role.value}",
                       role=role, password="Claims#2026secure")
    token, _ = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def synthetic(checks: list[str], seed: int = 31):
    gen = Generator(Options(out_dir=None, count=1, seed=seed))  # type: ignore[arg-type]
    case = gen.build(0, checks)
    return render_case(case).pdf, case


def process(client, db, hdrs, pdf: bytes, stage: str = "final_cashless") -> uuid.UUID:
    res = client.post("/api/claims", headers=hdrs, data={"stage": stage},
                      files=[("files", ("claim.pdf", pdf, "application/pdf"))])
    assert res.status_code == 201, res.text
    claim_id = uuid.UUID(res.json()["claim"]["id"])
    claim = runner.run_pipeline(db, claim_id)
    assert claim.status == ClaimStatus.READY_FOR_REVIEW, claim.processing_error
    return claim_id


def test_rules_are_seeded_from_the_catalogue(db, client):
    views = sync_rules(db)
    assert set(views) == set(CHECKS)
    rows = {r.id: r for r in db.scalars(select(Rule))}
    assert rows["M-05"].status.value == "draft" and rows["B-01"].status.value == "active"
    assert rows["B-01"].weight == 30 and rows["C-01"].weight == 15 and rows["B-05"].weight == 5
    assert rows["B-03"].config["over_benchmark_pct"] == 25
    rows["B-03"].config = {"over_benchmark_pct": 40}            # tuned values are kept on the next sync
    db.flush()
    assert sync_rules(db)["B-03"].config["over_benchmark_pct"] == 40
    res = client.get("/api/rules", headers=headers(db))
    assert res.status_code == 200 and len(res.json()) == len(CHECKS)


def test_pipeline_runs_checks_and_finds_seeded_issues(client, db, defaults):
    hdrs = headers(db)
    pdf, case = synthetic(["B-10", "X-04"])
    claim_id = process(client, db, hdrs, pdf, case.stage)
    runs = {r.rule_id: r.status for r in db.scalars(select(CheckRun).where(CheckRun.claim_id == claim_id))}
    assert set(runs) == set(CHECKS)
    assert runs["B-10"] == "flagged" and runs["X-04"] == "flagged"
    assert runs["M-01"] == "pending_ai" and runs["R-01"] == "not_evaluable"     # registries not loaded here
    res = client.get(f"/api/claims/{claim_id}/findings", headers=hdrs).json()
    by_rule = {f["rule_id"]: f for f in res["findings"]}
    mrp = by_rule["B-10"]
    assert mrp["severity"] == "red" and mrp["status"] == "open" and float(mrp["amount_at_stake"]) > 0
    assert mrp["evidence"] and mrp["evidence"][0]["page_id"] and mrp["evidence"][0]["bbox"]   # show on page
    assert res["summary"]["red"] >= 1 and float(res["summary"]["amount_at_stake"]) > 0
    checks = client.get(f"/api/claims/{claim_id}/checks", headers=hdrs).json()
    assert len(checks["checks"]) == len(CHECKS) and checks["counts"]["flagged"] >= 2
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "claim.checks_run"))


def test_genuine_claim_has_no_red(client, db, defaults):
    hdrs = headers(db)
    pdf, case = synthetic([], seed=32)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    findings = db.scalars(select(Finding).where(Finding.claim_id == claim_id)).all()
    assert not [f for f in findings if f.severity.value == "red"], [(f.rule_id, f.title) for f in findings]


def test_review_rules_and_audit(client, db, defaults):
    hdrs = headers(db)
    pdf, case = synthetic(["B-10"], seed=33)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    finding = db.scalars(select(Finding).where(Finding.claim_id == claim_id, Finding.rule_id == "B-10")).first()
    assert finding is not None
    url = f"/api/findings/{finding.id}/review"
    assert client.post(url, headers=hdrs, json={"action": "dismissed"}).status_code == 422   # needs a reason
    assert client.post(url, headers=headers(db, RoleName.TEAM_LEAD), json={"action": "confirmed"}).status_code == 403
    res = client.post(url, headers=headers(db, RoleName.FRAUD_INVESTIGATOR), json={"action": "confirmed"})
    assert res.status_code == 200 and res.json()["status"] == "confirmed"
    res = client.post(url, headers=hdrs, json={"action": "dismissed", "reason": "MRP printed is old; new MRP higher"})
    assert res.json()["status"] == "dismissed" and len(res.json()["reviews"]) == 2
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "finding.dismissed")) == 1


def test_reviewed_findings_survive_a_rerun(client, db, defaults):
    hdrs = headers(db)
    pdf, case = synthetic(["B-10"], seed=34)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    finding = db.scalars(select(Finding).where(Finding.claim_id == claim_id, Finding.rule_id == "B-10")).one()
    client.post(f"/api/findings/{finding.id}/review", headers=hdrs, json={"action": "confirmed"})
    claim = finding.claim
    run_for_claim(db, claim)
    db.commit()
    again = db.scalars(select(Finding).where(Finding.claim_id == claim_id, Finding.rule_id == "B-10")).all()
    assert [f.id for f in again] == [finding.id] and again[0].is_current       # kept, not duplicated
    finding.fingerprint = "an-older-version-of-this-finding"
    db.commit()
    run_for_claim(db, claim)
    db.commit()
    rows = db.scalars(select(Finding).where(Finding.claim_id == claim_id, Finding.rule_id == "B-10")).all()
    old = next(f for f in rows if f.id == finding.id)
    assert not old.is_current and len(rows) == 2                                # history kept
    assert db.scalar(select(func.count()).select_from(FindingReview)) == 1


def test_correction_then_rerun_clears_the_finding(client, db, defaults):
    hdrs = headers(db)
    pdf, case = synthetic(["B-02"], seed=35)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    assert db.scalars(select(Finding).where(Finding.claim_id == claim_id, Finding.rule_id == "B-02")).first()
    claimed = db.scalars(select(ExtractedField).where(ExtractedField.claim_id == claim_id,
                                                      ExtractedField.field_name == "claimed_amount")).first()
    assert claimed is not None
    res = client.patch(f"/api/claims/{claim_id}/fields/{claimed.id}", headers=hdrs,
                       json={"value": f"{case.bill_total:.2f}"})
    assert res.status_code == 200
    res = client.post(f"/api/claims/{claim_id}/run-checks", headers=hdrs)
    assert res.status_code == 200
    status = {c["rule_id"]: c["status"] for c in res.json()["checks"]}
    assert status["B-02"] == "passed"


def test_registries_import_and_lookup(db, tmp_path):
    gen = Generator(Options(out_dir=tmp_path, count=3, seed=36))
    gen.run()
    rec, counts = import_registries(db, tmp_path / "registries", source_name="Synthetic", version="T1")
    assert counts.doctors and counts.hospitals and counts.policies == 3 and rec.dataset == "registries"
    assert db.scalar(select(func.count()).select_from(RegistryDoctor)) == counts.doctors
    assert db.scalar(select(func.count()).select_from(RegistryHospital)) == counts.hospitals
    assert db.scalar(select(func.count()).select_from(PolicyRecord)) == 3
    import_registries(db, tmp_path / "registries", source_name="Synthetic", version="T2")      # a full refresh
    assert db.scalar(select(func.count()).select_from(PolicyRecord)) == 3
    assert db.scalar(select(func.count()).select_from(RegistryWatchlist)) == (counts.watchlist or 0)
    with pytest.raises(RegistryImportError):
        import_registries(db, tmp_path / "nothing-here", source_name="x", version="x")


def test_synthetic_benchmarks_load_through_the_normal_importers(db, tmp_path):
    write_reference(tmp_path)
    info = importers.SourceInfo(version="SYN-T", source_name="Synthetic", source_url=None,
                                effective_from=date(2025, 1, 1))
    pk = importers.import_packages(db, tmp_path / "packages.csv", info)
    tf = importers.import_tariffs(db, tmp_path / "tariffs.csv", TariffSource.CLIENT, info)
    assert pk.row_count == 10 and tf.row_count > 100
    ref = Reference.from_db(db)
    chole = ref.package("SYN-SG020")
    assert chole and chole.los_days and "surgeon_fee" in chole.inclusions and "K80" in chole.keywords
