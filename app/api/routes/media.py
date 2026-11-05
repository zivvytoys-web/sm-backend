import json
from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, status
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.models.user import User
from app.schemas.media import MediaAssetResponse, CloudinarySignatureResponse, MediaAssetUpdate, DirectMediaUploadRequest
from app.services.media_service import MediaService
from app.core.permissions import get_current_active_user, require_creator

router = APIRouter(prefix="/media", tags=["Media Assets"])


@router.get("/", response_model=List[MediaAssetResponse])
def list_media(
    media_type: Optional[str] = Query(None, description="Filter by 'image' or 'video'"),
    collection_id: Optional[int] = Query(None, description="Filter by collection ID"),
    search: Optional[str] = Query(None, description="Search by file name, title, description"),
    sort_by: Optional[str] = Query("newest", description="Sort by 'newest', 'oldest', or 'name'"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List uploaded media assets belonging to active organization."""
    return MediaService.list_media(
        db,
        current_user.organization_id,
        media_type=media_type,
        collection_id=collection_id,
        search=search,
        sort_by=sort_by
    )


@router.get("/signature", response_model=CloudinarySignatureResponse)
def get_upload_signature(
    current_user: User = Depends(require_creator)
):
    """Generate secure Cloudinary upload signature for client-side direct uploads."""
    return MediaService.get_upload_signature(current_user)


@router.get("/{asset_id}", response_model=MediaAssetResponse)
def get_media_asset(
    asset_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve details for a single media asset with organization access check."""
    return MediaService.get_media_asset(db, asset_id, current_user)


@router.post("/upload", response_model=MediaAssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    alt_text: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    collection_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Upload a new image or video asset to Cloudinary with metadata and collection assignment."""
    parsed_tags = []
    if tags:
        try:
            parsed_tags = json.loads(tags)
        except Exception:
            parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]

    return await MediaService.upload_asset(
        db=db,
        user=current_user,
        file=file,
        title=title,
        description=description,
        alt_text=alt_text,
        tags=parsed_tags,
        collection_id=collection_id
    )

@router.post("/complete-upload", response_model=MediaAssetResponse, status_code=status.HTTP_201_CREATED)
def complete_direct_upload(
    payload: DirectMediaUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Register a file already uploaded directly from the browser to Cloudinary."""
    return MediaService.register_direct_asset(db, current_user, payload)


@router.post("/bulk-upload", response_model=List[MediaAssetResponse], status_code=status.HTTP_201_CREATED)
async def bulk_upload_media(
    files: List[UploadFile] = File(...),
    collection_id: Optional[int] = Form(None),
    tags: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Bulk upload multiple media assets at once."""
    parsed_tags = []
    if tags:
        try:
            parsed_tags = json.loads(tags)
        except Exception:
            parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]

    uploaded_assets = []
    for file in files:
        asset = await MediaService.upload_asset(
            db=db,
            user=current_user,
            file=file,
            title=file.filename,
            tags=parsed_tags,
            collection_id=collection_id
        )
        uploaded_assets.append(asset)
    return uploaded_assets


@router.patch("/{asset_id}", response_model=MediaAssetResponse)
def update_media(
    asset_id: int,
    data: MediaAssetUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Update title, description, alt text, tags, or collections for a media asset."""
    return MediaService.update_asset(db, asset_id, current_user, data)


@router.delete("/{asset_id}", status_code=status.HTTP_200_OK)
def delete_media(
    asset_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Delete a media asset from Cloudinary and database with ownership check."""
    MediaService.delete_asset(db, asset_id, current_user)
    return {"message": "Media asset deleted successfully"}
