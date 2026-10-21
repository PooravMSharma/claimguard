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
    page_image_scale: float = 2.0  # page images at ~144 dpi for the viewer

    # Page reading (Step 7)
    ocr_languages: str = "eng+hin"   # Tesseract languages; missing ones are skipped
    ocr_render_scale: float = 3.0    # PDF pages are rendered at ~216 dpi for OCR
    ocr_min_width: int = 1800        # smaller photos/scans are enlarged to this width before OCR
    ocr_workers: int = 3             # pages read in parallel

    cors_origins: list[str] = ["http://localhost:5173"]

    # AI (Step 8). "ollama" (local model) or "anthropic" (with a key) turns it on; blank runs rules only.
    ai_provider: str = ""
    ai_api_key: str = ""
    ai_model: str = "claude-sonnet-5-5"
    ai_extraction: str = "gaps"          # off | gaps (scans and incomplete documents) | all
    ai_max_calls_per_claim: int = 25
    ai_timeout_seconds: int = 120
    ai_parallel_calls: int = 4
    ai_image_max_side: int = 1600        # page images are scaled down to this before sending
    # Ollama only
    ai_base_url: str = ""                # default http://host.docker.internal:11434 (Ollama on the host)
    ai_vision: bool = True               # false for text-only models: send OCR text without page images
    ai_context_tokens: int = 24576       # model context window; images + OCR text must fit
    ai_keep_alive: str = "30m"           # keep the model loaded between calls
    job_timeout_seconds: int = 10800     # longest a claim may take in the worker (local AI is slow)
    # Estimated prices in USD per million tokens, for the cost log. Set them from your Anthropic plan.
    ai_price_input_per_mtok: float = 3.0
    ai_price_output_per_mtok: float = 15.0

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()