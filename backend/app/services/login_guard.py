"""Locks an account for a while after repeated failed logins.

Counts live in Redis. If Redis is down, logins still work (fail open) and a
warning is logged, so a Redis outage never locks every user out.
"""

import logging

from app.core.config import get_settings
from app.services.queue import redis_conn

log = logging.getLogger(__name__)


def _key(email: str) -> str:
    return f"login_fail:{email.lower()}"


def is_locked(email: str) -> bool:
    try:
        return int(redis_conn.get(_key(email)) or 0) >= get_settings().login_max_failures
    except Exception:
        log.warning("Login guard unavailable (Redis); skipping lockout check")
        return False


def record_failure(email: str) -> None:
    try:
        pipe = redis_conn.pipeline()
        pipe.incr(_key(email))
        pipe.expire(_key(email), get_settings().login_lockout_seconds)
        pipe.execute()
    except Exception:
        log.warning("Login guard unavailable (Redis); failure not counted")


def clear(email: str) -> None:
    try:
        redis_conn.delete(_key(email))
    except Exception:
        pass