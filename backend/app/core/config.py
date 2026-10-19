from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ClaimGuard"
    app_env: str = "development"
    secret_key: str = "change-me"
    access_token_minutes: int = 480  # one 8-hour shift
    bcrypt_rounds: int = 12
    login_max_failures: int = 5
    login_lockout_seconds: int = 900

    database_url: str = "postgresql+psycopg://claimguard:claimguard@localhost:5432/claimguard"

    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "claimguard"

    storage_backend: str = "local"  # "local" or "s3"
    storage_local_path: str = "./storage"
    s3_endpoint_url: str = ""
    s3_bucket: str = "claimguard"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "ap-south-1"

    # Claim intake limits (PRD: up to 150 pages and 100 MB per claim)
    max_claim_bytes: int = 100 * 1024 * 1024
    max_claim_pages: int = 150
    max_files_per_claim: int = 40
    page_image_scale: float = 2.0  # page images at ~144 dpi for OCR and the viewer

    cors_origins: list[str] = ["http://localhost:5173"]

    ai_provider: str = ""
    ai_api_key: str = ""

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()