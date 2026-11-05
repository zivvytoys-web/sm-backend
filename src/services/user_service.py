from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from src.models.user import User
from src.validations.user_validation import UserUpdateValidation


class UserService:
    """Business logic for User operations."""

    @staticmethod
    def get_by_id(db: Session, user_id: int) -> User:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found."
            )
        return user

    @staticmethod
    def get_by_email(db: Session, email: str) -> Optional[User]:
        return db.query(User).filter(User.email == email).first()

    @staticmethod
    def list_users(db: Session, organization_id: Optional[int] = None) -> List[User]:
        query = db.query(User)
        if organization_id is not None:
            query = query.filter(User.organization_id == organization_id)
        return query.order_by(User.created_at.desc()).all()

    @staticmethod
    def update_user(db: Session, user_id: int, update_data: UserUpdateValidation) -> User:
        user = UserService.get_by_id(db, user_id)
        update_dict = update_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(user, key, value)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def delete_user(db: Session, user_id: int) -> bool:
        user = UserService.get_by_id(db, user_id)
        db.delete(user)
        db.commit()
        return True


__all__ = ["UserService"]
