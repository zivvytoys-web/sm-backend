from typing import Optional
from sqlalchemy.orm import Session
from fastapi import UploadFile
from src.services.media_service import MediaService
from src.models.user import User


class MediaController:
    """HTTP Controller orchestrating Cloudinary Media operations."""

    @staticmethod
    def list_media(db: Session, current_user: User, media_type: Optional[str] = None, search: Optional[str] = None):
        return MediaService.list_media(db, current_user.organization_id, media_type=media_type, search=search)

    @staticmethod
    def get_media(db: Session, asset_id: int, current_user: User):
        return MediaService.get_media_asset(db, asset_id, current_user)

    @staticmethod
    async def upload_media(db: Session, current_user: User, file: UploadFile):
        return await MediaService.upload_asset(db, current_user, file)

    @staticmethod
    def delete_media(db: Session, asset_id: int, current_user: User):
        MediaService.delete_asset(db, asset_id, current_user)
        return {"success": True, "message": "Media asset deleted successfully"}

    @staticmethod
    def get_signature(current_user: User):
        return MediaService.get_upload_signature(current_user)


__all__ = ["MediaController"]
