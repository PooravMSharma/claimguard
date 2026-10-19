from datetime import datetime, timezone
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.models import (
    BillLineItem,
    Claim,
    ClaimDocument,
    Decision,
    Diagnosis,
    DocumentPage,
    ExtractedField,
    Finding,
    FindingReview,
    Role,
    Rule,
    User,
)
from app.models.enums import (
    BillCategory,
    ClaimStage,
    ClaimStatus,
    DocumentType,
    Recommendation,
    ReviewAction,
    RoleName,
    RuleLayer,
    RuleMethod,
    Severity,
)

EXPECTED_TABLES = {
    "audit_log", "bill_line_items", "claim_documents", "claims", "decisions", "diagnoses",
    "document_pages", "extracted_fields", "finding_reviews", "findings", "icd10_codes",
    "investigations", "medications", "non_payable_items", "packages", "policy_terms",
    "procedures", "roles", "rules", "tariff_rates", "users",
}


# ---------- helpers ----------

def make_user(db, email="adj@example.com") -> User:
    role = db.scalar(select(Role).where(Role.name == RoleName.ADJUDICATOR))
    user = User(email=email, full_name="Test Adjudicator", password_hash="x", role_id=role.id)
    db.add(user)
    db.flush()
    return user


def make_rule(db, rule_id="C-03") -> Rule:
    rule = Rule(
        id=rule_id,
        name="Billed investigation has a report",
        layer=RuleLayer.COMPLETENESS,
        description="Every billed test must have its report in the file.",
        method=RuleMethod.RULE,
        max_severity=Severity.RED,
        weight=30,
    )
    db.add(rule)
    db.flush()
    return rule


def make_claim(db, number="CLM-0001") -> Claim:
    claim = Claim(
        claim_number=number,
        stage=ClaimStage.FINAL_CASHLESS,
        member_name="Ravi Kumar",
        hospital_name="City Care Hospital",
        admission_at=datetime(2026, 9, 1, 10, tzinfo=timezone.utc),
        discharge_at=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
        claimed_amount=Decimal("84500.00"),
    )
    db.add(claim)
    db.flush()
    return claim


# ---------- migrations ----------

def test_migrations_are_at_head(test_engine):
    with test_engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"


def test_models_match_migrations(test_engine):
    """If someone changes a model without a migration, this fails."""
    with test_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_all_tables_exist(test_engine):
    tables = set(inspect(test_engine).get_table_names())
    assert EXPECTED_TABLES <= tables


def test_roles_are_seeded(db):
    names = set(db.scalars(select(Role.name)))
    assert names == set(RoleName)


# ---------- relationships ----------

def test_full_claim_record_and_cascade_delete(db):
    user = make_user(db)
    rule = make_rule(db)
    claim = make_claim(db)

    doc = ClaimDocument(
        claim=claim, filename="bill.pdf", content_type="application/pdf",
        storage_key=f"claims/{claim.id}/bill.pdf", size_bytes=1024, sha256="a" * 64, uploaded_by_id=user.id,
    )
    page = DocumentPage(document=doc, page_number=1, doc_type=DocumentType.FINAL_BILL, doc_type_confidence=0.97)
    db.add_all([doc, page])
    db.flush()

    db.add_all([
        ExtractedField(claim=claim, section="encounter", field_name="admission_date",
                       value_text="01/09/2026", source_page_id=page.id, confidence=0.95),
        BillLineItem(claim=claim, description="CT Abdomen", category=BillCategory.INVESTIGATION,
                     quantity=Decimal("1"), unit_rate=Decimal("6500"), amount=Decimal("6500"),
                     source_page_id=page.id),
        Diagnosis(claim=claim, description="Acute appendicitis", icd10_code="K35.2", is_primary=True),
    ])
    finding = Finding(
        claim=claim, rule=rule, rule_version=1, severity=Severity.RED,
        title="CT abdomen billed but no report in file",
        explanation="Bill line 14 charges ₹6,500 for CT abdomen; no CT report was found.",
        evidence=[{"page_id": str(page.id), "quote": "CT Abdomen 6,500"}],
        amount_at_stake=Decimal("6500"), confidence=0.98,
    )
    db.add(finding)
    db.flush()
    db.add_all([
        FindingReview(finding=finding, reviewer_id=user.id, action=ReviewAction.CONFIRMED),
        Decision(claim=claim, decided_by_id=user.id, recommendation=Recommendation.APPROVE_WITH_DEDUCTIONS,
                 approved_amount=Decimal("78000"), reason="CT not supported by report",
                 deductions=[{"description": "CT abdomen", "amount": 6500}]),
    ])
    db.flush()
    db.expire_all()

    loaded = db.get(Claim, claim.id)
    assert loaded.status == ClaimStatus.RECEIVED
    assert len(loaded.documents[0].pages) == 1
    assert len(loaded.bill_line_items) == 1
    assert loaded.findings[0].reviews[0].action == ReviewAction.CONFIRMED
    assert loaded.decisions[0].deductions[0]["amount"] == 6500

    claim_id = claim.id
    db.delete(loaded)
    db.flush()
    for table in ("claim_documents", "extracted_fields", "bill_line_items", "diagnoses",
                  "findings", "decisions"):
        count = db.execute(text(f"SELECT count(*) FROM {table} WHERE claim_id = :id"), {"id": claim_id}).scalar()
        assert count == 0, f"{table} rows left after claim delete"
    assert db.execute(text("SELECT count(*) FROM document_pages")).scalar() == 0
    assert db.execute(text("SELECT count(*) FROM finding_reviews")).scalar() == 0
    # The rule and the user are not part of the claim and must survive.
    assert db.get(Rule, "C-03") is not None
    assert db.get(User, user.id) is not None


# ---------- constraints enforced by the database ----------

def test_claim_number_is_unique(db):
    make_claim(db, "CLM-DUP")
    with pytest.raises(IntegrityError):
        make_claim(db, "CLM-DUP")


def test_database_rejects_unknown_stage(db):
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO claims (id, claim_number, stage, status) "
                        "VALUES (gen_random_uuid(), 'CLM-BAD', 'bogus', 'received')"))


def test_risk_score_must_be_0_to_100(db):
    claim = make_claim(db)
    claim.risk_score = 150
    with pytest.raises(IntegrityError):
        db.flush()


def test_page_numbers_unique_within_document(db):
    claim = make_claim(db)
    doc = ClaimDocument(claim=claim, filename="a.pdf", content_type="application/pdf",
                        storage_key="claims/x/a.pdf", size_bytes=1, sha256="b" * 64)
    db.add_all([doc, DocumentPage(document=doc, page_number=1)])
    db.flush()
    db.add(DocumentPage(document=doc, page_number=1))
    with pytest.raises(IntegrityError):
        db.flush()


def test_rule_with_findings_cannot_be_deleted(db):
    rule = make_rule(db, "X-01")
    claim = make_claim(db)
    db.add(Finding(claim=claim, rule=rule, rule_version=1, severity=Severity.AMBER,
                   title="t", explanation="e"))
    db.flush()
    with pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM rules WHERE id = 'X-01'"))