"""Password hashing and session tokens."""

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_BYTES = 72  # bcrypt only uses the first 72 bytes


def password_problem(password: str) -> str | None:
    """Return why a password is not acceptable, or None if it is fine."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return "Password is too long"
    if password.isalpha() or password.isdigit():
        return "Password must mix letters with numbers or symbols"
    return None


def hash_password(password: str) -> str:
    rounds = get_settings().bcrypt_rounds
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# Checked against when the email does not exist, so a wrong email takes as long
# as a wrong password and response time does not reveal which accounts exist.
DUMMY_HASH = bcrypt.hashpw(
    b"timing-equaliser-not-a-real-password", bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)
).decode()


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """Return (token, lifetime in seconds)."""
    settings = get_settings()
    lifetime = settings.access_token_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(seconds=lifetime),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM), lifetime


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
