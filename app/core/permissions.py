from typing import List, Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.dependencies import get_db
from app.models.user import User
from app.utils.enums import UserRole

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login",
    auto_error=False
)

ADMIN_EMAIL = "superadmin@socialai.com"
ADMIN_EMAILS = {"superadmin@socialai.com", "admin@socialai.com"}


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme)
) -> User:
    """
    Reusable FastAPI authentication dependency that extracts the JWT from either:
    1. Authorization: Bearer <token> header, OR
    2. Secure HTTP-only access_token cookie.
    Validates token signature, algorithm, expiration, CSRF for cookie-based state changes, and user identity.
    Rejects malformed, expired, tampered, or missing tokens with HTTP 401.
    """
    resolved_token = token
    from_cookie = False

    if not resolved_token:
        cookie_tok = request.cookies.get("access_token")
        if cookie_tok:
            resolved_token = cookie_tok
            from_cookie = True

    if not resolved_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Authorization Bearer token or session cookie is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # CSRF check for cookie-based state modifications
    if from_cookie and request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        csrf_cookie = request.cookies.get("csrf_token")
        csrf_header = request.headers.get("x-csrf-token")
        if csrf_cookie and csrf_header and csrf_cookie != csrf_header:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed: token mismatch."
            )

    payload = decode_access_token(resolved_token)
    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token validation failed: missing user identifier claim.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_pk = int(user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token validation failed: invalid user identifier format.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_pk).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with this token does not exist or was deleted.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user



def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency verifying that the authenticated user is currently active.
    Rejects inactive or disabled accounts with HTTP 401.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive or disabled.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


class RoleChecker:
    """Dependency factory verifying that the authenticated user has one of the allowed roles."""
    def __init__(self, allowed_roles: List[UserRole]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: User = Depends(get_current_active_user)) -> User:
        user_role_str = user.role.value if hasattr(user.role, 'value') else str(user.role).lower()
        allowed_roles_str = [r.value if hasattr(r, 'value') else str(r).lower() for r in self.allowed_roles]

        # Super admin always has master access across all role-protected endpoints
        if user_role_str == "super_admin":
            return user

        if user_role_str not in allowed_roles_str:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User role '{user_role_str}' lacks required permission for this resource."
            )
        return user


# Shorthand role dependencies
require_super_admin = RoleChecker([UserRole.SUPER_ADMIN])
require_enterprise_admin = RoleChecker([UserRole.ADMIN, UserRole.SUPER_ADMIN])
require_admin = require_enterprise_admin
require_manager = RoleChecker([UserRole.MANAGER, UserRole.ADMIN, UserRole.SUPER_ADMIN])
require_creator = RoleChecker([UserRole.CREATOR, UserRole.MANAGER, UserRole.ADMIN, UserRole.SUPER_ADMIN])

