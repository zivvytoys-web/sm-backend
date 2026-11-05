from sqlalchemy.orm import Session
from src.services.auth_service import AuthService
from src.models.user import User
from src.validations.auth_validation import LoginValidation, RegisterValidation
from src.utils.response import success_response


class AuthController:
    """HTTP Controller orchestrating Authentication operations."""

    @staticmethod
    def login(db: Session, creds: LoginValidation):
        from app.schemas.auth import LoginRequest
        req = LoginRequest(email=creds.email, password=creds.password)
        tokens = AuthService.login(db, req)
        return tokens

    @staticmethod
    def register(db: Session, payload: RegisterValidation):
        # Translate to schema expected by AuthService
        from app.schemas.auth import RegisterRequest
        req = RegisterRequest(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            organization_name=payload.organization_name,
            business_id=payload.business_id,
            role=payload.role
        )
        user = AuthService.register(db, req)
        return user

    @staticmethod
    def get_me(user: User):
        return user

    @staticmethod
    def get_available_businesses(db: Session):
        return AuthService.get_available_businesses(db)

    @staticmethod
    def get_my_businesses(db: Session, current_user: User):
        return AuthService.get_user_businesses(db, current_user)

    @staticmethod
    def switch_business(db: Session, business_id: int, current_user: User):
        return AuthService.switch_user_business(db, current_user, business_id)

    @staticmethod
    def request_password_reset(db: Session, email: str):
        return AuthService.request_password_reset(db, email)

    @staticmethod
    def reset_password(db: Session, token: str, new_password: str):
        return AuthService.reset_password(db, token, new_password)

    @staticmethod
    def request_email_verification(db: Session, email: str):
        return AuthService.request_email_verification(db, email)

    @staticmethod
    def verify_email(db: Session, token: str):
        return AuthService.verify_email(db, token)


__all__ = ["AuthController"]

