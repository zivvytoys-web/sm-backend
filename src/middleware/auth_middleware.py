from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from src.config.db import get_db
from src.models.user import User
from src.models.role import UserRole
from src.utils.helper import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """Authentication middleware: validates JWT token from Bearer header or HTTP-only cookie and returns active user."""
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
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"}
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
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"}
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject identity",
            headers={"WWW-Authenticate": "Bearer"}
        )

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account no longer exists",
            headers={"WWW-Authenticate": "Bearer"}
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated"
        )

    return user



def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    """Authorization middleware: checks for SuperAdmin privileges."""
    user_role_str = current_user.role.value if hasattr(current_user.role, 'value') else str(current_user.role).lower()
    if user_role_str != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SuperAdmin privileges required for this action."
        )
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Authorization middleware: checks for Admin privileges."""
    user_role_str = current_user.role.value if hasattr(current_user.role, 'value') else str(current_user.role).lower()
    if user_role_str not in ["super_admin", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required for this action."
        )
    return current_user


def require_creator(current_user: User = Depends(get_current_user)) -> User:
    """Authorization middleware: checks for Creator/Manager/Admin role."""
    user_role_str = current_user.role.value if hasattr(current_user.role, 'value') else str(current_user.role).lower()
    allowed = ["super_admin", "admin", "manager", "creator"]
    if user_role_str not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator or Manager role required for this action."
        )
    return current_user


__all__ = [
    "oauth2_scheme",
    "get_current_user",
    "require_super_admin",
    "require_admin",
    "require_creator"
]

