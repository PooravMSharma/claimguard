from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import setup_logging

settings = get_settings()
setup_logging(debug=settings.is_development)

if not settings.is_development and (settings.secret_key.startswith("change-me") or len(settings.secret_key) < 32):
    raise RuntimeError("SECRET_KEY must be set to a long random value outside development")

app = FastAPI(
    title=f"{settings.app_name} API",
    version=__version__,
    docs_url="/docs" if settings.is_development else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/api")
def root() -> dict:
    return {"app": settings.app_name, "version": __version__}