from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field


class LoginValidation(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)


class RegisterValidation(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    full_name: str = Field(..., min_length=2)
    organization_name: Optional[str] = None
    business_id: Optional[int] = None
    role: Optional[str] = "creator"


class SwitchBusinessValidation(BaseModel):
    business_id: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class PasswordResetRequestValidation(BaseModel):
    email: EmailStr


class PasswordResetConfirmValidation(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class EmailVerificationRequestValidation(BaseModel):
    email: EmailStr


class EmailVerificationConfirmValidation(BaseModel):
    token: str


__all__ = [
    "LoginValidation",
    "RegisterValidation",
    "SwitchBusinessValidation",
    "TokenResponse",
    "PasswordResetRequestValidation",
    "PasswordResetConfirmValidation",
    "EmailVerificationRequestValidation",
    "EmailVerificationConfirmValidation"
]

