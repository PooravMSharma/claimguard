"""What each role is allowed to do. This is the single place to change access rules."""

import enum

from app.models.enums import RoleName


class Permission(str, enum.Enum):
    CLAIMS_VIEW = "claims.view"
    CLAIMS_UPLOAD = "claims.upload"
    CLAIMS_REVIEW = "claims.review"  # confirm / dismiss findings
    CLAIMS_DECIDE = "claims.decide"  # record a recommendation
    CLAIMS_REVIEW_ESCALATED = "claims.review_escalated"
    CLAIMS_INVESTIGATE = "claims.investigate"
    CLAIMS_ASSIGN = "claims.assign"
    RULES_VIEW = "rules.view"
    RULES_MANAGE = "rules.manage"
    REFERENCE_VIEW = "reference.view"
    REFERENCE_MANAGE = "reference.manage"
    USERS_MANAGE = "users.manage"
    AUDIT_VIEW = "audit.view"
    METRICS_VIEW = "metrics.view"
    SYSTEM_VIEW = "system.view"


P = Permission

ROLE_PERMISSIONS: dict[RoleName, frozenset[Permission]] = {
    RoleName.ADJUDICATOR: frozenset({
        P.CLAIMS_VIEW, P.CLAIMS_UPLOAD, P.CLAIMS_REVIEW, P.CLAIMS_DECIDE,
        P.RULES_VIEW, P.REFERENCE_VIEW,
    }),
    RoleName.SENIOR_REVIEWER: frozenset({
        P.CLAIMS_VIEW, P.CLAIMS_UPLOAD, P.CLAIMS_REVIEW, P.CLAIMS_DECIDE, P.CLAIMS_REVIEW_ESCALATED,
        P.RULES_VIEW, P.REFERENCE_VIEW, P.METRICS_VIEW,
    }),
    RoleName.FRAUD_INVESTIGATOR: frozenset({
        P.CLAIMS_VIEW, P.CLAIMS_INVESTIGATE, P.RULES_VIEW, P.REFERENCE_VIEW,
    }),
    RoleName.TEAM_LEAD: frozenset({
        P.CLAIMS_VIEW, P.CLAIMS_ASSIGN, P.RULES_VIEW, P.REFERENCE_VIEW,
        P.AUDIT_VIEW, P.METRICS_VIEW, P.SYSTEM_VIEW,
    }),
    RoleName.ADMIN: frozenset({
        P.CLAIMS_VIEW, P.RULES_VIEW, P.RULES_MANAGE, P.REFERENCE_VIEW, P.REFERENCE_MANAGE,
        P.USERS_MANAGE, P.AUDIT_VIEW, P.METRICS_VIEW, P.SYSTEM_VIEW,
    }),
}


def permissions_for(role: RoleName) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: RoleName, permission: Permission) -> bool:
    return permission in permissions_for(role)