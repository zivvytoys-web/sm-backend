from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.campaign import Campaign
from app.models.brand_profile import BrandProfile
from app.schemas.campaign import CampaignCreate, CampaignUpdate


class CampaignService:
    @staticmethod
    def list_campaigns(db: Session, organization_id: Optional[int]) -> List[Campaign]:
        if not organization_id:
            return []
        return db.query(Campaign).filter(Campaign.organization_id == organization_id).order_by(Campaign.id.desc()).all()

    @staticmethod
    def get_campaign(db: Session, campaign_id: int, organization_id: Optional[int]) -> Campaign:
        if not organization_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")
        campaign = db.query(Campaign).filter(
            Campaign.id == campaign_id,
            Campaign.organization_id == organization_id
        ).first()
        if not campaign:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")
        return campaign

    @staticmethod
    def create_campaign(db: Session, organization_id: int, payload: CampaignCreate) -> Campaign:
        brand_id = payload.brand_profile_id
        if not brand_id:
            brand = db.query(BrandProfile).filter(BrandProfile.organization_id == organization_id).first()
            if not brand:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No Brand Profile found for organization.")
            brand_id = brand.id
        else:
            # Verify brand exists for this organization
            brand = db.query(BrandProfile).filter(
                BrandProfile.id == brand_id,
                BrandProfile.organization_id == organization_id
            ).first()
            if not brand:
                fallback_brand = db.query(BrandProfile).filter(BrandProfile.organization_id == organization_id).first()
                if fallback_brand:
                    brand_id = fallback_brand.id
                else:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Brand Profile for organization.")

        campaign = Campaign(
            organization_id=organization_id,
            brand_profile_id=brand_id,
            title=payload.title,
            objective=payload.objective,
            start_date=payload.start_date,
            end_date=payload.end_date,
            tags=payload.tags,
            status=payload.status
        )
        db.add(campaign)
        db.commit()
        db.refresh(campaign)
        return campaign

    @staticmethod
    def update_campaign(db: Session, campaign_id: int, organization_id: int, payload: CampaignUpdate) -> Campaign:
        campaign = CampaignService.get_campaign(db, campaign_id, organization_id)
        update_data = payload.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(campaign, field, value)
        db.commit()
        db.refresh(campaign)
        return campaign

    @staticmethod
    def delete_campaign(db: Session, campaign_id: int, organization_id: int) -> bool:
        campaign = CampaignService.get_campaign(db, campaign_id, organization_id)
        db.delete(campaign)
        db.commit()
        return True
