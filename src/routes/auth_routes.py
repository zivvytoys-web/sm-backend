import secrets
from typing import List, Any
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session
from src.config.db import get_db
from src.config.env import settings
from src.controllers.auth_controller import AuthController
from src.validations.auth_validation import (
    LoginValidation,
    RegisterValidation,
    PasswordResetRequestValidation,
    PasswordResetConfirmValidation,
    EmailVerificationRequestValidation,
    EmailVerificationConfirmValidation
)
from src.middleware.auth_middleware import get_current_user
from src.models.user import User

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", status_code=status.HTTP_200_OK)
def login(payload: LoginValidation, request: Request, response: Response, db: Session = Depends(get_db)):
    """Authenticate user with email and password, issuing access token and setting secure HTTP-only session cookie."""
    client_ip = request.client.host if request.client else payload.email
    from app.services.auth_service import AuthService
    tokens = AuthService.login(db, payload, client_identifier=client_ip)

    # Set secure HTTP-only cookie for session management
    is_prod = getattr(settings, "ENVIRONMENT", "").lower() in ["production", "prod"]
    response.set_cookie(
        key="access_token",
        value=tokens.access_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=tokens.expires_in * 60,
        path="/"
    )
    # Set CSRF token cookie accessible to frontend JavaScript for state modification headers
    csrf_token = secrets.token_hex(16)
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=is_prod,
        samesite="lax",
        max_age=tokens.expires_in * 60,
        path="/"
    )
    return tokens


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(response: Response):
    """Log out user by clearing secure session and CSRF cookies."""
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="csrf_token", path="/")
    return {"message": "Logged out successfully."}


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterValidation, db: Session = Depends(get_db)):
    """Register a new user account with selected business profile assignment."""
    return AuthController.register(db, payload)


@router.post("/password-reset/request", status_code=status.HTTP_200_OK)
@router.post("/forgot-password", status_code=status.HTTP_200_OK)
def request_password_reset(payload: PasswordResetRequestValidation, db: Session = Depends(get_db)):
    """Generate a secure password reset token with 1-hour expiration."""
    return AuthController.request_password_reset(db, payload.email)


@router.post("/password-reset/confirm", status_code=status.HTTP_200_OK)
@router.post("/reset-password", status_code=status.HTTP_200_OK)
def confirm_password_reset(payload: PasswordResetConfirmValidation, db: Session = Depends(get_db)):
    """Reset account password using validated reset token."""
    return AuthController.reset_password(db, payload.token, payload.new_password)


@router.post("/verify-email/request", status_code=status.HTTP_200_OK)
def request_email_verification(payload: EmailVerificationRequestValidation, db: Session = Depends(get_db)):
    """Generate an email verification token with 24-hour expiration."""
    return AuthController.request_email_verification(db, payload.email)


@router.post("/verify-email/confirm", status_code=status.HTTP_200_OK)
@router.post("/verify-email", status_code=status.HTTP_200_OK)
def confirm_email_verification(payload: EmailVerificationConfirmValidation, db: Session = Depends(get_db)):
    """Verify email address using validated token."""
    return AuthController.verify_email(db, payload.token)



@router.get("/me", status_code=status.HTTP_200_OK)
def get_current_user_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retrieve profile and assigned businesses for the authenticated user."""
    from app.services.auth_service import AuthService
    assigned = AuthService.get_user_businesses(db, current_user)
    data = {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "organization_id": current_user.organization_id,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at,
        "updated_at": current_user.updated_at,
        "assigned_businesses": assigned
    }
    return data


@router.get("/available-businesses")
def get_available_businesses(db: Session = Depends(get_db)):
    """Retrieve active predefined businesses for user registration selection."""
    return AuthController.get_available_businesses(db)


@router.get("/my-businesses")
def get_my_businesses(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retrieve all business profiles assigned to the authenticated user."""
    return AuthController.get_my_businesses(db, current_user)


@router.post("/switch-business/{business_id}")
def switch_active_business(
    business_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Switch user's active business workspace with server-side authorization check."""
    return AuthController.switch_business(db, business_id, current_user)


__all__ = ["router"]
