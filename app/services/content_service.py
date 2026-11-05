from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.content import Content
from app.models.brand_profile import BrandProfile
from app.models.user import User
from app.schemas.content import ContentCreate, ContentUpdate
from app.utils.enums import ContentStatus


class ContentService:
    @staticmethod
    def list_contents(
        db: Session,
        organization_id: int,
        brand_profile_id: Optional[int] = None,
        status_filter: Optional[ContentStatus] = None
    ) -> List[Content]:
        query = db.query(Content).filter(Content.organization_id == organization_id)
        if brand_profile_id:
            query = query.filter(Content.brand_profile_id == brand_profile_id)
        if status_filter:
            query = query.filter(Content.status == status_filter)
        return query.order_by(Content.created_at.desc()).all()

    @staticmethod
    def get_content(db: Session, content_id: int, organization_id: int) -> Content:
        content = db.query(Content).filter(
            Content.id == content_id,
            Content.organization_id == organization_id
        ).first()
        if not content:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found or unauthorized.")
        return content

    @staticmethod
    def create_content(db: Session, creator: User, payload: ContentCreate) -> Content:
        brand_id = payload.brand_profile_id
        if not brand_id:
            # Fallback to user's first organization brand profile
            default_brand = db.query(BrandProfile).filter(BrandProfile.organization_id == creator.organization_id).first()
            if not default_brand:
                default_brand = BrandProfile(
                    organization_id=creator.organization_id,
                    name="Default Studio",
                    industry="Creative & Media",
                    tone_of_voice="Engaging",
                    key_themes=[],
                    hashtags=[],
                    color_palette=[]
                )
                db.add(default_brand)
                db.flush()
            brand_id = default_brand.id
        else:
            # Verify ownership
            brand = db.query(BrandProfile).filter(
                BrandProfile.id == brand_id,
                BrandProfile.organization_id == creator.organization_id
            ).first()
            if not brand:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brand profile does not belong to your organization.")

        content = Content(
            title=payload.title,
            caption=payload.caption,
            hashtags=payload.hashtags,
            target_platforms=payload.target_platforms,
            status=payload.status,
            meta_info=payload.meta_info,
            organization_id=creator.organization_id,
            brand_profile_id=brand_id,
            campaign_id=payload.campaign_id,
            creator_id=creator.id,
            media_asset_id=payload.media_asset_id
        )
        db.add(content)
        db.commit()
        db.refresh(content)
        return content

    @staticmethod
    def update_content(db: Session, content_id: int, organization_id: int, payload: ContentUpdate) -> Content:
        content = ContentService.get_content(db, content_id, organization_id)
        update_data = payload.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(content, field, value)
        db.commit()
        db.refresh(content)
        return content

    @staticmethod
    def delete_content(db: Session, content_id: int, organization_id: int) -> bool:
        content = ContentService.get_content(db, content_id, organization_id)
        db.delete(content)
        db.commit()
        return True

