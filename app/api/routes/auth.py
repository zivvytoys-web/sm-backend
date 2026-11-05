import secrets
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.dependencies import get_db
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    Token,
    PasswordResetRequest,
    PasswordResetConfirm,
    EmailVerificationRequest,
    EmailVerificationConfirm
)
from app.schemas.user import UserResponse, UserUpdate
from app.schemas.business import AvailableBusinessItem
from app.services.auth_service import AuthService
from app.core.permissions import get_current_active_user
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/available-businesses", response_model=List[AvailableBusinessItem])
def get_available_businesses(db: Session = Depends(get_db)):
    """Retrieve list of active predefined businesses for user onboarding/registration."""
    return AuthService.get_available_businesses(db)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user account and assign to selected or default business."""
    user = AuthService.register(db, request)
    user.assigned_businesses = AuthService.get_user_businesses(db, user)
    return user


@router.post("/login", response_model=Token)
def login(request: LoginRequest, req: Request, res: Response, db: Session = Depends(get_db)):
    """Authenticate, obtain JWT access token, and establish secure HTTP-only session cookie."""
    client_ip = req.client.host if req.client else request.email
    tokens = AuthService.login(db, request, client_identifier=client_ip)

    # Set secure HTTP-only cookie for browser session management
    is_prod = getattr(settings, "ENVIRONMENT", "").lower() in ["production", "prod"]
    res.set_cookie(
        key="access_token",
        value=tokens.access_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=tokens.expires_in * 60,
        path="/"
    )
    # Set CSRF token cookie accessible to frontend JavaScript
    csrf_token = secrets.token_hex(16)
    res.set_cookie(
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
def logout(res: Response):
    """Log out user by clearing secure session and CSRF cookies."""
    res.delete_cookie(key="access_token", path="/")
    res.delete_cookie(key="csrf_token", path="/")
    return {"message": "Logged out successfully."}


@router.post("/password-reset/request", status_code=status.HTTP_200_OK)
@router.post("/forgot-password", status_code=status.HTTP_200_OK)
def request_password_reset(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    """Generate a secure password reset token with 1-hour expiration."""
    return AuthService.request_password_reset(db, payload.email)


@router.post("/password-reset/confirm", status_code=status.HTTP_200_OK)
@router.post("/reset-password", status_code=status.HTTP_200_OK)
def confirm_password_reset(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    """Reset account password using validated reset token."""
    return AuthService.reset_password(db, payload.token, payload.new_password)


@router.post("/verify-email/request", status_code=status.HTTP_200_OK)
def request_email_verification(payload: EmailVerificationRequest, db: Session = Depends(get_db)):
    """Generate an email verification token with 24-hour expiration."""
    return AuthService.request_email_verification(db, payload.email)


@router.post("/verify-email/confirm", status_code=status.HTTP_200_OK)
@router.post("/verify-email", status_code=status.HTTP_200_OK)
def confirm_email_verification(payload: EmailVerificationConfirm, db: Session = Depends(get_db)):
    """Verify email address using validated token."""
    return AuthService.verify_email(db, payload.token)



@router.get("/me", response_model=UserResponse)
def get_current_user_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve profile of current authenticated user with their assigned businesses."""
    current_user.assigned_businesses = AuthService.get_user_businesses(db, current_user)
    return current_user


@router.get("/my-businesses")
def get_my_businesses(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve all businesses assigned to the authenticated user (or all businesses if Admin)."""
    return AuthService.get_user_businesses(db, current_user)


@router.post("/switch-business/{business_id}")
def switch_active_business(
    business_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Switch user's active business workspace with server-side authorization check."""
    return AuthService.switch_user_business(db, current_user, business_id)


@router.put("/me", response_model=UserResponse)
def update_current_user_profile(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update profile details of current authenticated user."""
    update_data = payload.model_dump(exclude_unset=True)
    if "password" in update_data and update_data["password"]:
        from app.core.security import get_password_hash
        current_user.hashed_password = get_password_hash(update_data.pop("password"))
    for k, v in update_data.items():
        setattr(current_user, k, v)
    db.commit()
    db.refresh(current_user)
    current_user.assigned_businesses = AuthService.get_user_businesses(db, current_user)
    return current_user
