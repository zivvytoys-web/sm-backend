from datetime import datetime
from pydantic import BaseModel, EmailStr
from app.utils.enums import UserRole


class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = None
    role: UserRole = UserRole.CREATOR
    is_active: bool = True
    avatar_url: str | None = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    role: UserRole = UserRole.CREATOR
    organization_id: int | None = None


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    password: str | None = None
    role: UserRole | None = None
    avatar_url: str | None = None
    is_active: bool | None = None


class UserResponse(UserBase):
    id: int
    organization_id: int | None = None
    is_verified: bool
    assigned_businesses: list[dict] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
