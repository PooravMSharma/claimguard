"""Importing this package registers every table on Base.metadata (needed by Alembic)."""

from app.models.claim import Claim, ClaimDocument, DocumentPage
from app.models.decision import Decision
from app.models.extraction import (
    AiCall,
    AiCheckCache,
    BillLineItem,
    Diagnosis,
    ExtractedDocument,
    ExtractedField,
    Investigation,
    Medication,
    Procedure,
)
from app.models.reference import (
    Icd10Code,
    NonPayableItem,
    Package,
    PolicyRecord,
    PolicyTerm,
    ReferenceImport,
    RegistryDoctor,
    RegistryHospital,
    RegistryWatchlist,
    TariffRate,
)
from app.models.rules import CheckRun, Finding, FindingReview, Rule
from app.models.user import AuditLog, Role, User

__all__ = [
    "AuditLog",
    "AiCall",
    "AiCheckCache",
    "BillLineItem",
    "CheckRun",
    "ExtractedDocument",
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
    "PolicyRecord",
    "PolicyTerm",
    "Procedure",
    "Role",
    "Rule",
    "ReferenceImport",
    "RegistryDoctor",
    "RegistryHospital",
    "RegistryWatchlist",
    "TariffRate",
    "User",
]
