from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import COOKIE_NAME, get_current_user
from app.core.config import get_settings
from app.core.security import DUMMY_HASH, create_access_token, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, MeResponse
from app.services import login_guard
from app.services.audit import log_event

router = APIRouter(prefix="/auth", tags=["auth"])

BAD_LOGIN = "Incorrect email or password"


@router.post("/login", response_model=MeResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> MeResponse:
    settings = get_settings()
    email = body.email.lower()

    if login_guard.is_locked(email):
        log_event(db, "login.locked", entity_type="user", details={"email": email}, request=request)
        db.commit()
        minutes = settings.login_lockout_seconds // 60
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {minutes} minutes.",
        )

    user = db.scalar(select(User).where(User.email == email))
    # Always run a hash check so response time does not reveal whether the email exists.
    password_ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)

    if user is None or not password_ok or not user.is_active:
        login_guard.record_failure(email)
        reason = "inactive" if (user and password_ok and not user.is_active) else "bad_credentials"
        log_event(
            db, "login.failed", entity_type="user", entity_id=user.id if user else None,
            user_id=user.id if user else None, details={"email": email, "reason": reason}, request=request,
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=BAD_LOGIN)

    login_guard.clear(email)
    user.last_login_at = datetime.now(timezone.utc)
    log_event(db, "login.success", entity_type="user", entity_id=user.id, user_id=user.id, request=request)
    db.commit()

    token, lifetime = create_access_token(user.id)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=lifetime,
        httponly=True,  # not readable by JavaScript
        secure=not settings.is_development,  # HTTPS only outside development
        samesite="strict",  # never sent on requests from other sites
        path="/",
    )
    return MeResponse.from_user(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    try:
        user = get_current_user(request, db)
        log_event(db, "logout", entity_type="user", entity_id=user.id, user_id=user.id, request=request)
        db.commit()
    except HTTPException:
        pass  # already signed out; still clear the cookie
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse.from_user(user)
