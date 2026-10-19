"""Importing this package registers every table on Base.metadata (needed by Alembic)."""

from app.models.claim import Claim, ClaimDocument, DocumentPage
from app.models.decision import Decision
from app.models.extraction import (
    BillLineItem,
    Diagnosis,
    ExtractedField,
    Investigation,
    Medication,
    Procedure,
)
from app.models.reference import (
    Icd10Code,
    NonPayableItem,
    Package,
    PolicyTerm,
    ReferenceImport,
    TariffRate,
)
from app.models.rules import Finding, FindingReview, Rule
from app.models.user import AuditLog, Role, User

__all__ = [
    "AuditLog",
    "BillLineItem",
    "Claim",
    "ClaimDocument",
    "Decision",
    "Diagnosis",
    "DocumentPage",
    "ExtractedField",
    "Finding",
    "FindingReview",
    "Icd10Code",
    "Investigation",
    "Medication",
    "NonPayableItem",
    "Package",
    "PolicyTerm",
    "Procedure",
    "Role",
    "Rule",
    "ReferenceImport",
    "TariffRate",
    "User",
]
