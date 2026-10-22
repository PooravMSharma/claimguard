from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import get_settings
from app.core.hardening import check_or_exit
from app.core.logging import setup_logging
from app.core.middleware import install

settings = get_settings()
setup_logging(debug=settings.is_development)
check_or_exit(settings, "The API")

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
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Request-ID"],
)
install(app, production=not settings.is_development)

app.include_router(api_router, prefix="/api")


@app.get("/api")
def root() -> dict:
    return {"app": settings.app_name, "version": __version__}
