from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.models.user import User
from app.schemas.user import UserResponse, UserUpdate
from app.core.permissions import get_current_active_user, require_admin

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/", response_model=List[UserResponse])
def get_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """List all users within current organization (Admin only), excluding super_admin."""
    from app.utils.enums import UserRole
    return (
        db.query(User)
        .filter(
            User.organization_id == current_user.organization_id,
            User.role != UserRole.SUPER_ADMIN
        )
        .all()
    )


@router.get("/{user_id}", response_model=UserResponse)
def get_user_by_id(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get single user profile."""
    user = db.query(User).filter(User.id == user_id, User.organization_id == current_user.organization_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.put("/{user_id}", response_model=UserResponse)
def update_user_profile(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update user profile."""
    user_role_str = current_user.role.value if hasattr(current_user.role, 'value') else str(current_user.role).lower()
    if current_user.id != user_id and user_role_str not in ("super_admin", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot update another user's profile")

    user = db.query(User).filter(User.id == user_id, User.organization_id == current_user.organization_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found or unauthorized")


    update_data = payload.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(user, k, v)

    db.commit()
    db.refresh(user)
    return user
