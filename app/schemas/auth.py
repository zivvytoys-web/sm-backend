from pydantic import BaseModel, EmailStr
from app.utils.enums import UserRole


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str | None = None
    user_id: int | None = None
    email: str | None = None
    full_name: str | None = None


class TokenPayload(BaseModel):
    sub: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    business_id: int | None = None
    organization_name: str | None = None
    company_name: str | None = None
    role: UserRole = UserRole.CREATOR


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


class EmailVerificationRequest(BaseModel):
    email: EmailStr


class EmailVerificationConfirm(BaseModel):
    token: str

