import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.permissions import Permission
from app.core.security import hash_password, password_problem
from app.db.session import get_db
from app.models.enums import RoleName
from app.models.user import User
from app.schemas.users import PasswordReset, UserCreate, UserOut, UserUpdate
from app.services.audit import log_event
from app.services.users import UserError, count_active_admins, create_user, get_role

router = APIRouter(prefix="/users", tags=["users"])
admin_only = require_permission(Permission.USERS_MANAGE)


def _get_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(admin_only)) -> list[UserOut]:
    users = db.scalars(select(User).order_by(User.created_at)).all()
    return [UserOut.from_user(u) for u in users]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def add_user(
    body: UserCreate, request: Request, db: Session = Depends(get_db), admin: User = Depends(admin_only)
) -> UserOut:
    try:
        user = create_user(db, email=body.email, full_name=body.full_name, role=body.role, password=body.password)
    except UserError as exc:
        code = status.HTTP_409_CONFLICT if "already exists" in str(exc) else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(exc))
    log_event(
        db, "user.created", entity_type="user", entity_id=user.id, user_id=admin.id,
        details={"email": user.email, "role": body.role.value}, request=request,
    )
    db.commit()
    db.refresh(user)
    return UserOut.from_user(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
) -> UserOut:
    user = _get_user_or_404(db, user_id)
    changes: dict = {}

    removing_admin = user.role.name == RoleName.ADMIN and (
        (body.role is not None and body.role != RoleName.ADMIN) or body.is_active is False
    )
    if user.id == admin.id and removing_admin:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin access")
    if removing_admin and user.is_active and count_active_admins(db) <= 1:
        raise HTTPException(status_code=400, detail="At least one active admin must remain")

    if body.full_name is not None and body.full_name.strip() != user.full_name:
        changes["full_name"] = {"from": user.full_name, "to": body.full_name.strip()}
        user.full_name = body.full_name.strip()
    if body.role is not None and body.role != user.role.name:
        changes["role"] = {"from": user.role.name.value, "to": body.role.value}
        user.role_id = get_role(db, body.role).id
    if body.is_active is not None and body.is_active != user.is_active:
        changes["is_active"] = {"from": user.is_active, "to": body.is_active}
        user.is_active = body.is_active

    if changes:
        log_event(db, "user.updated", entity_type="user", entity_id=user.id, user_id=admin.id,
                  details=changes, request=request)
        db.commit()
        db.refresh(user)
    return UserOut.from_user(user)


@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: uuid.UUID,
    body: PasswordReset,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
) -> None:
    user = _get_user_or_404(db, user_id)
    if problem := password_problem(body.password):
        raise HTTPException(status_code=422, detail=problem)
    user.password_hash = hash_password(body.password)
    log_event(db, "user.password_reset", entity_type="user", entity_id=user.id, user_id=admin.id, request=request)
    db.commit()