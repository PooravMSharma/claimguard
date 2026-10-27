"""Request dependencies: who is calling, and are they allowed."""

import uuid
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.permissions import Permission, has_permission
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

COOKIE_NAME = "cg_session"

NOT_AUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not signed in",
    headers={"WWW-Authenticate": "Bearer"},
)


def _token_from(request: Request) -> str | None:
    if token := request.cookies.get(COOKIE_NAME):
        return token
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _token_from(request)
    if not token:
        raise NOT_AUTHENTICATED
    payload = decode_access_token(token)
    if not payload:
        raise NOT_AUTHENTICATED
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise NOT_AUTHENTICATED
    # Load the user fresh every request: deactivation and role changes apply at once.
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise NOT_AUTHENTICATED
    return user


def require_permission(permission: Permission) -> Callable[..., User]:
    def checker(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role.name, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this")
        return user

    return checker
