from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.models.user import User
from app.schemas.collections import (
    CollectionCreate,
    CollectionUpdate,
    CollectionResponse,
    CollectionMediaAssign
)
from app.services.collection_service import CollectionService
from app.core.permissions import get_current_active_user, require_creator

router = APIRouter(prefix="/collections", tags=["Media Collections"])


class MoveMediaRequest(BaseModel):
    source_collection_id: int
    target_collection_id: int
    media_ids: List[int]


@router.get("/", response_model=List[CollectionResponse])
def list_collections(
    search: Optional[str] = Query(None, description="Search by name or description"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all media collections for the current user's organization."""
    return CollectionService.list_collections(
        db,
        organization_id=current_user.organization_id,
        search=search
    )


@router.post("/", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
def create_collection(
    data: CollectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Create a new media collection."""
    col = CollectionService.create_collection(db, current_user, data)
    cols = CollectionService.list_collections(db, current_user.organization_id)
    for c in cols:
        if c["id"] == col.id:
            return c
    return {
        "id": col.id,
        "name": col.name,
        "description": col.description,
        "cover_asset_id": col.cover_asset_id,
        "cover_asset_url": None,
        "organization_id": col.organization_id,
        "user_id": col.user_id,
        "media_count": 0,
        "media_assets": [],
        "created_at": col.created_at,
        "updated_at": col.updated_at
    }


@router.get("/{collection_id}", response_model=CollectionResponse)
def get_collection(
    collection_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve details and media assets of a specific collection."""
    cols = CollectionService.list_collections(db, current_user.organization_id)
    for c in cols:
        if c["id"] == collection_id:
            return c
    raise HTTPException(status_code=404, detail="Collection not found.")


@router.patch("/{collection_id}", response_model=CollectionResponse)
def update_collection(
    collection_id: int,
    data: CollectionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Update collection details (name, description, cover image)."""
    CollectionService.update_collection(db, collection_id, current_user.organization_id, data)
    cols = CollectionService.list_collections(db, current_user.organization_id)
    for c in cols:
        if c["id"] == collection_id:
            return c
    raise HTTPException(status_code=404, detail="Collection not found.")


@router.delete("/{collection_id}", status_code=status.HTTP_200_OK)
def delete_collection(
    collection_id: int,
    delete_media: bool = Query(False, description="Set to true if media files in collection should also be deleted"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Delete a collection. Media files are preserved unless delete_media=true is passed."""
    CollectionService.delete_collection(db, collection_id, current_user.organization_id, delete_media=delete_media)
    return {"message": "Collection deleted successfully", "deleted_media": delete_media}


@router.post("/{collection_id}/media", response_model=CollectionResponse)
def add_media_to_collection(
    collection_id: int,
    data: CollectionMediaAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Assign existing media assets to this collection."""
    CollectionService.add_media_to_collection(db, collection_id, current_user.organization_id, data.media_ids)
    cols = CollectionService.list_collections(db, current_user.organization_id)
    for c in cols:
        if c["id"] == collection_id:
            return c
    raise HTTPException(status_code=404, detail="Collection not found.")


@router.delete("/{collection_id}/media/{media_id}", response_model=CollectionResponse)
def remove_media_from_collection(
    collection_id: int,
    media_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Remove a media asset from this collection without deleting the underlying media file."""
    CollectionService.remove_media_from_collection(db, collection_id, current_user.organization_id, media_id)
    cols = CollectionService.list_collections(db, current_user.organization_id)
    for c in cols:
        if c["id"] == collection_id:
            return c
    raise HTTPException(status_code=404, detail="Collection not found.")


@router.post("/move-media", status_code=status.HTTP_200_OK)
def move_media(
    data: MoveMediaRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Move media assets between collections."""
    CollectionService.move_media(
        db,
        source_collection_id=data.source_collection_id,
        target_collection_id=data.target_collection_id,
        organization_id=current_user.organization_id,
        media_ids=data.media_ids
    )
    return {"message": "Media moved successfully"}
