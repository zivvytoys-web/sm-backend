from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from fastapi import HTTPException, status
from app.models.collection import Collection, collection_media
from app.models.media_asset import MediaAsset
from app.models.user import User
from app.utils.enums import UserRole
from app.schemas.collections import CollectionCreate, CollectionUpdate, CollectionResponse


class CollectionService:
    @staticmethod
    def list_collections(
        db: Session,
        organization_id: int,
        search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lists all collections for the organization with media count and cover image URL."""
        query = db.query(Collection).filter(Collection.organization_id == organization_id)

        if search:
            query = query.filter(
                (Collection.name.ilike(f"%{search}%")) |
                (Collection.description.ilike(f"%{search}%"))
            )

        collections = query.order_by(Collection.created_at.desc()).all()
        results = []

        for col in collections:
            media_count = len(col.media_assets)
            cover_url = None
            if col.cover_asset:
                cover_url = col.cover_asset.file_url
            elif col.media_assets:
                # Fallback to first media asset as cover
                cover_url = col.media_assets[0].file_url

            results.append({
                "id": col.id,
                "name": col.name,
                "description": col.description,
                "cover_asset_id": col.cover_asset_id,
                "cover_asset_url": cover_url,
                "organization_id": col.organization_id,
                "user_id": col.user_id,
                "media_count": media_count,
                "media_assets": [
                    {
                        "id": m.id,
                        "file_name": m.file_name,
                        "file_url": m.file_url,
                        "media_type": m.media_type.value if hasattr(m.media_type, "value") else str(m.media_type),
                        "title": m.title,
                        "tags": m.tags or []
                    }
                    for m in col.media_assets
                ],
                "created_at": col.created_at,
                "updated_at": col.updated_at
            })

        return results

    @staticmethod
    def get_collection(
        db: Session,
        collection_id: int,
        organization_id: int
    ) -> Collection:
        """Retrieves a single collection with ownership verification."""
        col = db.query(Collection).filter(
            Collection.id == collection_id,
            Collection.organization_id == organization_id
        ).first()

        if not col:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Collection not found or access denied."
            )
        return col

    @staticmethod
    def create_collection(
        db: Session,
        user: User,
        data: CollectionCreate
    ) -> Collection:
        """Creates a new collection within the user's organization."""
        if not user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User must belong to an active organization."
            )

        if not data.name or not data.name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Collection name is required and cannot be empty."
            )

        # Verify cover asset if provided
        if data.cover_asset_id:
            asset = db.query(MediaAsset).filter(
                MediaAsset.id == data.cover_asset_id,
                MediaAsset.organization_id == user.organization_id
            ).first()
            if not asset:
                data.cover_asset_id = None

        col = Collection(
            name=data.name.strip(),
            description=data.description,
            cover_asset_id=data.cover_asset_id,
            organization_id=user.organization_id,
            user_id=user.id
        )

        db.add(col)
        db.commit()
        db.refresh(col)
        return col

    @staticmethod
    def update_collection(
        db: Session,
        collection_id: int,
        organization_id: int,
        data: CollectionUpdate
    ) -> Collection:
        """Updates collection name, description, or cover image."""
        col = CollectionService.get_collection(db, collection_id, organization_id)

        if data.name is not None:
            col.name = data.name.strip()
        if data.description is not None:
            col.description = data.description
        if data.cover_asset_id is not None:
            if data.cover_asset_id > 0:
                asset = db.query(MediaAsset).filter(
                    MediaAsset.id == data.cover_asset_id,
                    MediaAsset.organization_id == organization_id
                ).first()
                if asset:
                    col.cover_asset_id = asset.id
            else:
                col.cover_asset_id = None

        db.commit()
        db.refresh(col)
        return col

    @staticmethod
    def delete_collection(
        db: Session,
        collection_id: int,
        organization_id: int,
        delete_media: bool = False
    ) -> bool:
        """
        Deletes a collection. Never silently deletes underlying media unless
        delete_media=True is explicitly specified.
        """
        col = CollectionService.get_collection(db, collection_id, organization_id)

        if delete_media:
            # Delete media assets that are in this collection
            assets_to_delete = list(col.media_assets)
            for asset in assets_to_delete:
                db.delete(asset)

        db.delete(col)
        db.commit()
        return True

    @staticmethod
    def add_media_to_collection(
        db: Session,
        collection_id: int,
        organization_id: int,
        media_ids: List[int]
    ) -> Collection:
        """Associates one or more existing media assets with a collection."""
        col = CollectionService.get_collection(db, collection_id, organization_id)

        # Query all matching media belonging to the org
        assets = db.query(MediaAsset).filter(
            MediaAsset.id.in_(media_ids),
            MediaAsset.organization_id == organization_id
        ).all()

        existing_ids = {m.id for m in col.media_assets}
        for asset in assets:
            if asset.id not in existing_ids:
                col.media_assets.append(asset)

        # If no cover image set, automatically set cover image to the first asset
        if not col.cover_asset_id and col.media_assets:
            col.cover_asset_id = col.media_assets[0].id

        db.commit()
        db.refresh(col)
        return col

    @staticmethod
    def remove_media_from_collection(
        db: Session,
        collection_id: int,
        organization_id: int,
        media_id: int
    ) -> Collection:
        """Removes a media asset from a collection without deleting the underlying asset."""
        col = CollectionService.get_collection(db, collection_id, organization_id)

        col.media_assets = [m for m in col.media_assets if m.id != media_id]
        if col.cover_asset_id == media_id:
            col.cover_asset_id = col.media_assets[0].id if col.media_assets else None

        db.commit()
        db.refresh(col)
        return col

    @staticmethod
    def move_media(
        db: Session,
        source_collection_id: int,
        target_collection_id: int,
        organization_id: int,
        media_ids: List[int]
    ) -> bool:
        """Moves specified media assets from one collection to another."""
        source = CollectionService.get_collection(db, source_collection_id, organization_id)
        target = CollectionService.get_collection(db, target_collection_id, organization_id)

        assets_to_move = [m for m in source.media_assets if m.id in media_ids]

        # Remove from source
        source.media_assets = [m for m in source.media_assets if m.id not in media_ids]
        if source.cover_asset_id in media_ids:
            source.cover_asset_id = source.media_assets[0].id if source.media_assets else None

        # Add to target
        target_existing_ids = {m.id for m in target.media_assets}
        for asset in assets_to_move:
            if asset.id not in target_existing_ids:
                target.media_assets.append(asset)

        if not target.cover_asset_id and target.media_assets:
            target.cover_asset_id = target.media_assets[0].id

        db.commit()
        return True
