import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import RoleName
from app.models.user import User


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: RoleName
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def from_user(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role.name,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    role: RoleName
    password: str = Field(min_length=1, max_length=200)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    role: RoleName | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=1, max_length=200)