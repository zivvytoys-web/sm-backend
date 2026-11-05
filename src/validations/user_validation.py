from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, EmailStr
from src.models.role import UserRole


class UserCreateValidation(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: UserRole = UserRole.CREATOR
    organization_id: Optional[int] = None


class UserUpdateValidation(BaseModel):
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    organization_id: Optional[int] = None


class UserResponseValidation(BaseModel):
    id: int
    email: str
    full_name: str
    role: UserRole
    organization_id: Optional[int] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    assigned_businesses: Optional[List[Any]] = None

    class Config:
        from_attributes = True


__all__ = [
    "UserCreateValidation",
    "UserUpdateValidation",
    "UserResponseValidation"
]
