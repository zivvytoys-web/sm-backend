from typing import Optional
from sqlalchemy.orm import Session
from src.services.user_service import UserService
from src.models.user import User
from src.validations.user_validation import UserUpdateValidation


class UserController:
    """HTTP Controller orchestrating User operations."""

    @staticmethod
    def get_profile(current_user: User):
        return current_user

    @staticmethod
    def get_user_by_id(db: Session, user_id: int):
        return UserService.get_by_id(db, user_id)

    @staticmethod
    def list_users(db: Session, organization_id: Optional[int] = None):
        return UserService.list_users(db, organization_id)

    @staticmethod
    def update_user(db: Session, user_id: int, data: UserUpdateValidation):
        return UserService.update_user(db, user_id, data)

    @staticmethod
    def delete_user(db: Session, user_id: int):
        UserService.delete_user(db, user_id)
        return {"success": True, "message": f"User #{user_id} deleted successfully"}


__all__ = ["UserController"]
