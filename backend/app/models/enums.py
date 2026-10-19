"""Fixed value lists used across the database.

Stored as text with a check constraint (not native Postgres enums), so adding a
value later is a simple migration.
"""

import enum

from sqlalchemy import Enum


def db_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=40,
        validate_strings=True,
        values_callable=lambda members: [m.value for m in members],
    )


class RoleName(str, enum.Enum):
    ADJUDICATOR = "adjudicator"
    SENIOR_REVIEWER = "senior_reviewer"
    FRAUD_INVESTIGATOR = "fraud_investigator"
    TEAM_LEAD = "team_lead"
    ADMIN = "admin"


class ClaimStage(str, enum.Enum):
    PREAUTH = "preauth"
    FINAL_CASHLESS = "final_cashless"
    REIMBURSEMENT = "reimbursement"


class ClaimStatus(str, enum.Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    READY_FOR_REVIEW = "ready_for_review"
    IN_REVIEW = "in_review"
    DECIDED = "decided"
    FAILED = "failed"


class RiskBand(str, enum.Enum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class Gender(str, enum.Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    UNKNOWN = "unknown"


class DocumentType(str, enum.Enum):
    CLAIM_FORM = "claim_form"
    PREAUTH_FORM = "preauth_form"
    PREAUTH_APPROVAL = "preauth_approval"
    DISCHARGE_SUMMARY = "discharge_summary"
    FINAL_BILL = "final_bill"
    BILL_BREAKUP = "bill_breakup"
    PHARMACY_BILL = "pharmacy_bill"
    LAB_REPORT = "lab_report"
    IMAGING_REPORT = "imaging_report"
    OT_NOTES = "ot_notes"
    ANAESTHESIA_NOTES = "anaesthesia_notes"
    IMPLANT_INVOICE = "implant_invoice"
    IMPLANT_STICKER = "implant_sticker"
    CASE_SHEET = "case_sheet"
    NURSING_CHART = "nursing_chart"
    POLICY_COPY = "policy_copy"
    ID_PROOF = "id_proof"
    MLC_FIR = "mlc_fir"
    OTHER = "other"
    UNKNOWN = "unknown"


class BillCategory(str, enum.Enum):
    ROOM = "room"
    ICU = "icu"
    NURSING = "nursing"
    CONSULTATION = "consultation"
    PROCEDURE = "procedure"
    SURGEON_FEE = "surgeon_fee"
    ANAESTHESIA = "anaesthesia"
    OT_CHARGES = "ot_charges"
    INVESTIGATION = "investigation"
    PHARMACY = "pharmacy"
    CONSUMABLES = "consumables"
    IMPLANT = "implant"
    PACKAGE = "package"
    NON_MEDICAL = "non_medical"
    OTHER = "other"


class RuleLayer(str, enum.Enum):
    COMPLETENESS = "completeness"
    FORENSICS = "forensics"
    CONSISTENCY = "consistency"
    MEDICAL_NECESSITY = "medical_necessity"
    BILLING = "billing"
    POLICY = "policy"
    REGISTRY = "registry"


class RuleMethod(str, enum.Enum):
    RULE = "rule"
    BENCHMARK = "benchmark"
    AI = "ai"
    LOOKUP = "lookup"


class Severity(str, enum.Enum):
    RED = "red"
    AMBER = "amber"
    INFO = "info"


class RuleStatus(str, enum.Enum):
    DRAFT = "draft"
    CLINICIAN_REVIEWED = "clinician_reviewed"
    ACTIVE = "active"
    DISABLED = "disabled"


class ReviewAction(str, enum.Enum):
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    NOTED = "noted"


class Recommendation(str, enum.Enum):
    APPROVE = "approve"
    APPROVE_WITH_DEDUCTIONS = "approve_with_deductions"
    QUERY_HOSPITAL = "query_hospital"
    ESCALATE = "escalate"
    REFER_SIU = "refer_siu"


class TariffSource(str, enum.Enum):
    PMJAY = "pmjay"
    CGHS = "cghs"
    CLIENT = "client"


class CityTier(str, enum.Enum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"
    ANY = "any"


class WardType(str, enum.Enum):
    GENERAL = "general"
    SEMI_PRIVATE = "semi_private"
    PRIVATE = "private"
    ANY = "any"