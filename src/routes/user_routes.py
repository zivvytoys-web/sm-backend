from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from src.config.db import get_db
from src.controllers.user_controller import UserController
from src.middleware.auth_middleware import get_current_user, require_admin
from src.models.user import User
from src.validations.user_validation import UserResponseValidation, UserUpdateValidation

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserResponseValidation)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Retrieve current authenticated user."""
    return UserController.get_profile(current_user)


@router.get("/", response_model=List[UserResponseValidation])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Admin endpoint to list all system users."""
    return UserController.list_users(db)


@router.get("/{user_id}", response_model=UserResponseValidation)
def get_user_by_id(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retrieve user by ID."""
    return UserController.get_user_by_id(db, user_id)


@router.put("/{user_id}", response_model=UserResponseValidation)
def update_user(
    user_id: int,
    data: UserUpdateValidation,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update user information."""
    return UserController.update_user(db, user_id, data)


@router.delete("/{user_id}", status_code=status.HTTP_200_OK)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Admin endpoint to delete a user."""
    return UserController.delete_user(db, user_id)


__all__ = ["router"]
