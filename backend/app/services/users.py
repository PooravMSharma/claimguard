from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password, password_problem
from app.models.enums import RoleName
from app.models.user import Role, User


class UserError(ValueError):
    pass


def get_role(db: Session, name: RoleName) -> Role:
    role = db.scalar(select(Role).where(Role.name == name))
    if role is None:
        raise UserError(f"Role {name.value} does not exist (have migrations run?)")
    return role


def email_taken(db: Session, email: str) -> bool:
    return (db.scalar(select(func.count()).select_from(User).where(User.email == email.lower())) or 0) > 0


def create_user(db: Session, *, email: str, full_name: str, role: RoleName, password: str) -> User:
    email = email.strip().lower()
    if email_taken(db, email):
        raise UserError("A user with this email already exists")
    if problem := password_problem(password):
        raise UserError(problem)
    user = User(
        email=email,
        full_name=full_name.strip(),
        password_hash=hash_password(password),
        role_id=get_role(db, role).id,
    )
    db.add(user)
    db.flush()
    return user


def count_active_admins(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(User).join(Role).where(Role.name == RoleName.ADMIN, User.is_active)
    ) or 0