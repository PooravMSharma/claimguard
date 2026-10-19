from fastapi import APIRouter

from app.api.routes import audit, auth, claims, health, reference, users

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(audit.router)
api_router.include_router(reference.router)
api_router.include_router(claims.router)