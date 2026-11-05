from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Query, status
from sqlalchemy.orm import Session
from src.config.db import get_db
from src.controllers.media_controller import MediaController
from src.middleware.auth_middleware import get_current_user, require_creator
from src.models.user import User
from src.validations.media_validation import MediaAssetResponseValidation, MediaSignatureValidation

router = APIRouter(prefix="/media", tags=["Media Assets"])


@router.get("/", response_model=List[MediaAssetResponseValidation])
def list_media(
    media_type: Optional[str] = Query(None, description="Filter by 'image' or 'video'"),
    search: Optional[str] = Query(None, description="Search by file name"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List uploaded images, videos, and graphics belonging to the active business."""
    return MediaController.list_media(db, current_user, media_type=media_type, search=search)


@router.get("/signature", response_model=MediaSignatureValidation)
def get_upload_signature(
    current_user: User = Depends(require_creator)
):
    """Generate secure Cloudinary upload signature for client-side direct uploads."""
    return MediaController.get_signature(current_user)


@router.get("/{asset_id}", response_model=MediaAssetResponseValidation)
def get_media_asset(
    asset_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retrieve details for a single media asset with organization access check."""
    return MediaController.get_media(db, asset_id, current_user)


@router.post("/upload", response_model=MediaAssetResponseValidation, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Upload a new image or video asset to Cloudinary."""
    return await MediaController.upload_media(db, current_user, file)


@router.delete("/{asset_id}", status_code=status.HTTP_200_OK)
def delete_media(
    asset_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Delete a media asset from Cloudinary and database with ownership check."""
    return MediaController.delete_media(db, asset_id, current_user)


__all__ = ["router"]
