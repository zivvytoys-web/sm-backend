from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class CampaignBase(BaseModel):
    title: str
    objective: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    tags: Any = []
    status: str = "active"


class CampaignCreate(CampaignBase):
    brand_profile_id: int
    organization_id: Optional[int] = None


class CampaignUpdate(BaseModel):
    title: Optional[str] = None
    objective: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    tags: Any = None
    status: Optional[str] = None


class CampaignResponse(CampaignBase):
    id: int
    organization_id: Optional[int] = None
    brand_profile_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class GeneratePlanRequest(BaseModel):
    brand_profile_id: Optional[int] = None
    duration_days: int = 7
    platforms: List[str] = ["instagram", "facebook", "linkedin"]
    posting_frequency: str = "1_per_day"
    campaign_goal: str = "product_launch"
    media_format_preference: Optional[str] = "both"  # 'images', 'videos', or 'both'
    custom_focus: Optional[str] = None
    start_date: Optional[datetime] = None


class SavePlanRequest(BaseModel):
    campaign_id: Optional[int] = None
    brand_profile_id: Optional[int] = None
    title: str
    summary: Optional[str] = None
    duration_days: int = 7
    posting_frequency: str = "1_per_day"
    campaign_goal: str = "product_launch"
    media_format_preference: Optional[str] = "both"
    platforms: List[str] = ["instagram", "facebook", "linkedin"]
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    ideas: List[Dict[str, Any]] = []


class UpdateIdeaRequest(BaseModel):
    title: Optional[str] = None
    hook: Optional[str] = None
    caption_brief: Optional[str] = None
    content_format: Optional[str] = None
    platforms: Optional[List[str]] = None
    pillar: Optional[str] = None
    cta: Optional[str] = None
    hashtags: Optional[List[str]] = None
    suggested_date: Optional[str] = None
    suggested_time: Optional[str] = None
    plan_status: Optional[str] = None
