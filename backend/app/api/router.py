from fastapi import APIRouter

from app.api.routes import (
    audit,
    auth,
    claims,
    findings,
    health,
    record,
    reference,
    reference_admin,
    review,
    rules,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(audit.router)
api_router.include_router(reference.router)
api_router.include_router(reference_admin.router)
api_router.include_router(claims.router)
api_router.include_router(record.router)
api_router.include_router(findings.router)
api_router.include_router(review.router)
api_router.include_router(rules.router)
