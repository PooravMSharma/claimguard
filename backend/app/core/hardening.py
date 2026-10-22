"""Settings that must never reach production, checked when the API and the worker start.

In development (APP_ENV=development) everything here is allowed, so the Mac setup keeps working. Anywhere else
the process refuses to start and lists every problem at once, rather than failing one at a time.
"""

from urllib.parse import urlsplit

from app.core.config import Settings

WEAK_PASSWORDS = {"claimguard", "postgres", "password", "change-me", "changeme", "admin", ""}
EXTERNAL_AI = {"anthropic"}


def external_ai_blocked(settings: Settings) -> bool:
    """True when claims must not go to an AI provider outside the client's machines.

    Guardrail: no real patient data to an external AI without the client's written approval and a DPDP Act check.
    Development uses synthetic claims only, so it is allowed there; elsewhere ALLOW_EXTERNAL_AI=true records that
    the approval exists.
    """
    provider = settings.ai_provider.strip().lower()
    return provider in EXTERNAL_AI and not settings.is_development and not settings.allow_external_ai


def production_problems(settings: Settings) -> list[str]:
    if settings.is_development:
        return []
    problems = []
    if settings.secret_key.startswith("change-me") or len(settings.secret_key) < 32:
        problems.append("SECRET_KEY must be a random value of at least 32 characters "
                        "(e.g. python -c \"import secrets; print(secrets.token_urlsafe(48))\")")
    db_password = urlsplit(settings.database_url.replace("+psycopg", "")).password or ""
    if db_password.lower() in WEAK_PASSWORDS or len(db_password) < 12:
        problems.append("The database password in DATABASE_URL is a default or shorter than 12 characters")
    if any(origin.strip() == "*" for origin in settings.cors_origins):
        problems.append("CORS_ORIGINS must list the dashboard's address, not '*'")
    if any(origin.startswith("http://") and "localhost" not in origin for origin in settings.cors_origins):
        problems.append("CORS_ORIGINS must use https:// outside development")
    if external_ai_blocked(settings):
        problems.append("AI_PROVIDER=anthropic sends claim documents outside the client's machines. Set "
                        "ALLOW_EXTERNAL_AI=true only with the client's written approval and a DPDP Act check, "
                        "or use AI_PROVIDER=ollama")
    if settings.bcrypt_rounds < 12:
        problems.append("BCRYPT_ROUNDS must be at least 12")
    if settings.access_token_minutes > 12 * 60:
        problems.append("ACCESS_TOKEN_MINUTES must be at most 720 (12 hours)")
    return problems


def check_or_exit(settings: Settings, who: str) -> None:
    problems = production_problems(settings)
    if problems:
        lines = "\n".join(f"  - {p}" for p in problems)
        raise RuntimeError(f"{who} will not start with APP_ENV={settings.app_env}:\n{lines}")
