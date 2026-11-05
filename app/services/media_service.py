import json
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from fastapi import UploadFile, HTTPException, status
from app.models.media_asset import MediaAsset
from app.models.collection import Collection
from app.models.user import User
from app.integrations.storage.cloudinary_storage import CloudinaryStorageProvider
from app.utils.enums import MediaType, UserRole
from app.schemas.media import MediaAssetUpdate, DirectMediaUploadRequest


class MediaService:
    @staticmethod
    def register_direct_asset(db: Session, user: User, payload: DirectMediaUploadRequest) -> Dict[str, Any]:
        if not payload.file_url or not payload.file_url.startswith("https://") or "res.cloudinary.com" not in payload.file_url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Media must be uploaded to Cloudinary before it can be saved or scheduled."
            )
        if not user.organization_id:
            raise HTTPException(status_code=400, detail="User must belong to an active organization to upload media.")
        asset = MediaAsset(
            organization_id=user.organization_id, user_id=user.id,
            file_name=payload.file_name, file_url=payload.file_url,
            public_id=payload.public_id, file_size_bytes=payload.file_size_bytes or 0,
            mime_type=payload.mime_type, media_type=payload.media_type,
            meta_info=payload.meta_info or {}, title=payload.title or payload.file_name,
            description=payload.description, alt_text=payload.alt_text, tags=payload.tags or []
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        MediaService._validate_and_save_report(db, asset)
        return MediaService.get_media_asset(db, asset.id, user)

    @staticmethod
    def _validate_and_save_report(db: Session, asset: MediaAsset, file_bytes: Optional[bytes] = None):
        """Automatically runs quality check & duplicate detection on uploaded/registered media asset."""
        try:
            from app.services.quality_check_engine import QualityCheckEngine
            from app.models.quality_report import QualityReport

            m_type = str(asset.media_type.value if hasattr(asset.media_type, "value") else asset.media_type).lower()
            if m_type == "image":
                val = QualityCheckEngine.validate_image(
                    db=db,
                    image_bytes=file_bytes,
                    file_path_or_url=asset.file_url,
                    organization_id=asset.organization_id
                )
            elif m_type == "video":
                val = QualityCheckEngine.validate_video(
                    db=db,
                    video_bytes=file_bytes,
                    file_path_or_url=asset.file_url,
                    organization_id=asset.organization_id
                )
            else:
                return

            asset.sha256_hash = val.get("sha256_hash")
            asset.perceptual_hash = val.get("perceptual_hash")
            asset.validation_status = val.get("decision", "PASS")
            asset.quality_score = val.get("quality_score", 100)
            asset.is_duplicate = val.get("is_duplicate", False)
            asset.duplicate_of_id = val.get("duplicate_of_id")

            report = QualityReport(
                organization_id=asset.organization_id,
                media_asset_id=asset.id,
                asset_type=m_type,
                decision=val.get("decision", "PASS"),
                quality_score=val.get("quality_score", 100),
                is_duplicate=val.get("is_duplicate", False),
                duplicate_asset_id=val.get("duplicate_of_id"),
                similarity_score=val.get("similarity_score", 0.0),
                compliance_status="COMPLIANT" if val.get("decision") == "PASS" else "FLAGGED",
                metrics_json=json.dumps(val.get("metrics", {})),
                issues_json=json.dumps(val.get("issues", [])),
                suggestions_json=json.dumps(val.get("suggestions", []))
            )
            db.add(report)
            db.commit()
            db.refresh(asset)
        except Exception as e:
            import logging
            logging.getLogger("media_service").warning(f"Media validation non-critical error: {e}")

    @staticmethod
    def list_media(
        db: Session,
        organization_id: int,
        media_type: Optional[str] = None,
        collection_id: Optional[int] = None,
        search: Optional[str] = None,
        sort_by: Optional[str] = "newest"
    ) -> List[Dict[str, Any]]:
        """Lists media assets for an organization with collection and tag filtering."""
        query = db.query(MediaAsset).filter(MediaAsset.organization_id == organization_id)

        if collection_id:
            query = query.filter(MediaAsset.collections.any(Collection.id == collection_id))

        if media_type:
            mt_lower = media_type.lower()
            if mt_lower in ["image", "images"]:
                query = query.filter(MediaAsset.media_type == MediaType.IMAGE)
            elif mt_lower in ["video", "videos"]:
                query = query.filter(MediaAsset.media_type == MediaType.VIDEO)

        if search:
            search_term = f"%{search}%"
            query = query.filter(
                (MediaAsset.file_name.ilike(search_term)) |
                (MediaAsset.title.ilike(search_term)) |
                (MediaAsset.description.ilike(search_term))
            )

        if sort_by == "oldest":
            query = query.order_by(MediaAsset.created_at.asc())
        elif sort_by == "name":
            query = query.order_by(MediaAsset.file_name.asc())
        else:  # newest
            query = query.order_by(MediaAsset.created_at.desc())

        assets = query.all()
        results = []
        for asset in assets:
            m_type_str = asset.media_type.value if hasattr(asset.media_type, "value") else str(asset.media_type)
            results.append({
                "id": asset.id,
                "file_name": asset.file_name,
                "file_url": asset.file_url,
                "public_id": asset.public_id,
                "file_size_bytes": asset.file_size_bytes,
                "mime_type": asset.mime_type,
                "media_type": m_type_str,
                "title": asset.title,
                "description": asset.description,
                "alt_text": asset.alt_text,
                "tags": asset.tags or [],
                "meta_info": asset.meta_info or {},
                "organization_id": asset.organization_id,
                "user_id": asset.user_id,
                "collections": [{"id": c.id, "name": c.name} for c in asset.collections],
                "sha256_hash": getattr(asset, "sha256_hash", None),
                "perceptual_hash": getattr(asset, "perceptual_hash", None),
                "validation_status": getattr(asset, "validation_status", "PENDING"),
                "quality_score": getattr(asset, "quality_score", None),
                "is_duplicate": bool(getattr(asset, "is_duplicate", False)),
                "duplicate_of_id": getattr(asset, "duplicate_of_id", None),
                "created_at": asset.created_at,
                "updated_at": asset.updated_at
            })
        return results

    @staticmethod
    def get_media_asset(db: Session, asset_id: int, user: User) -> Dict[str, Any]:
        """Retrieves a single media asset with strict organization/admin authorization."""
        query = db.query(MediaAsset).filter(MediaAsset.id == asset_id)

        if user.role != UserRole.SUPER_ADMIN:
            query = query.filter(MediaAsset.organization_id == user.organization_id)

        asset = query.first()
        if not asset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Media asset not found or access denied."
            )

        m_type_str = asset.media_type.value if hasattr(asset.media_type, "value") else str(asset.media_type)
        return {
            "id": asset.id,
            "file_name": asset.file_name,
            "file_url": asset.file_url,
            "public_id": asset.public_id,
            "file_size_bytes": asset.file_size_bytes,
            "mime_type": asset.mime_type,
            "media_type": m_type_str,
            "title": asset.title,
            "description": asset.description,
            "alt_text": asset.alt_text,
            "tags": asset.tags or [],
            "meta_info": asset.meta_info or {},
            "organization_id": asset.organization_id,
            "user_id": asset.user_id,
            "collections": [{"id": c.id, "name": c.name} for c in asset.collections],
            "sha256_hash": getattr(asset, "sha256_hash", None),
            "perceptual_hash": getattr(asset, "perceptual_hash", None),
            "validation_status": getattr(asset, "validation_status", "PENDING"),
            "quality_score": getattr(asset, "quality_score", None),
            "is_duplicate": bool(getattr(asset, "is_duplicate", False)),
            "duplicate_of_id": getattr(asset, "duplicate_of_id", None),
            "created_at": asset.created_at,
            "updated_at": asset.updated_at
        }

    @staticmethod
    async def upload_asset(
        db: Session,
        user: User,
        file: UploadFile,
        title: Optional[str] = None,
        description: Optional[str] = None,
        alt_text: Optional[str] = None,
        tags: Optional[List[str]] = None,
        collection_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Validates, uploads file to Cloudinary, and saves asset record with metadata in MySQL."""
        if not user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User must belong to an active organization to upload media."
            )

        upload_data = await CloudinaryStorageProvider.upload_file(
            file=file,
            organization_id=user.organization_id
        )

        parsed_tags = tags or []
        if isinstance(tags, str):
            try:
                parsed_tags = json.loads(tags)
            except Exception:
                parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]

        asset = MediaAsset(
            organization_id=user.organization_id,
            user_id=user.id,
            file_name=upload_data["file_name"],
            file_url=upload_data["file_url"],
            public_id=upload_data["public_id"],
            mime_type=upload_data["mime_type"],
            media_type=upload_data["media_type"],
            file_size_bytes=upload_data["file_size_bytes"],
            meta_info=upload_data["meta_info"],
            title=title or upload_data["file_name"],
            description=description,
            alt_text=alt_text,
            tags=parsed_tags
        )

        if collection_id:
            col = db.query(Collection).filter(
                Collection.id == collection_id,
                Collection.organization_id == user.organization_id
            ).first()
            if col:
                asset.collections.append(col)
                if not col.cover_asset_id:
                    col.cover_asset_id = asset.id

        db.add(asset)
        db.commit()
        db.refresh(asset)
        MediaService._validate_and_save_report(db, asset)
        return MediaService.get_media_asset(db, asset.id, user)

    @staticmethod
    def update_asset(
        db: Session,
        asset_id: int,
        user: User,
        data: MediaAssetUpdate
    ) -> Dict[str, Any]:
        """Updates metadata and collection associations of a media asset."""
        asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Media asset not found.")

        if user.role != UserRole.SUPER_ADMIN and asset.organization_id != user.organization_id:
            raise HTTPException(status_code=403, detail="Access denied.")

        if data.title is not None:
            asset.title = data.title
        if data.description is not None:
            asset.description = data.description
        if data.alt_text is not None:
            asset.alt_text = data.alt_text
        if data.tags is not None:
            asset.tags = data.tags

        if data.collection_ids is not None:
            collections = db.query(Collection).filter(
                Collection.id.in_(data.collection_ids),
                Collection.organization_id == user.organization_id
            ).all()
            asset.collections = collections

        db.commit()
        db.refresh(asset)
        return MediaService.get_media_asset(db, asset.id, user)

    @staticmethod
    def delete_asset(db: Session, asset_id: int, user: User) -> bool:
        """Deletes media asset from Cloudinary and database with ownership validation."""
        asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
        if not asset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Media asset not found."
            )

        if user.role != UserRole.SUPER_ADMIN and asset.organization_id != user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Media asset belongs to another organization."
            )

        is_owner = (asset.user_id == user.id)
        is_privileged = user.role in [UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.MANAGER]
        if not (is_owner or is_privileged):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied: You can only delete media you personally uploaded."
            )

        if asset.public_id:
            CloudinaryStorageProvider.delete_file(asset.public_id, asset.media_type)

        db.delete(asset)
        db.commit()
        return True

    @staticmethod
    def get_upload_signature(user: User):
        """Generates a secure upload signature for direct client-to-Cloudinary upload."""
        if not user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User must belong to an active organization."
            )
        return CloudinaryStorageProvider.generate_upload_signature(user.organization_id)
