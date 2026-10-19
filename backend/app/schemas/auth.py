import uuid

from pydantic import BaseModel, EmailStr, Field

from app.core.permissions import permissions_for
from app.models.enums import RoleName
from app.models.user import User


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: RoleName
    permissions: list[str]

    @classmethod
    def from_user(cls, user: User) -> "MeResponse":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role.name,
            permissions=sorted(p.value for p in permissions_for(user.role.name)),
        )