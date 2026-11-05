from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.models.user import User
from app.schemas.campaign import (
    CampaignCreate,
    CampaignUpdate,
    CampaignResponse,
    GeneratePlanRequest,
    SavePlanRequest,
    UpdateIdeaRequest
)
from app.services.campaign_service import CampaignService
from app.services.content_planner_service import ContentPlannerService
from app.core.permissions import get_current_active_user, require_creator

router = APIRouter(prefix="/campaigns", tags=["Campaigns & Content Planner"])


# =========================================================================
# AI CONTENT PLANNER ENDPOINTS
# =========================================================================

@router.post("/generate-plan")
async def generate_content_plan(
    payload: GeneratePlanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Generate a structured content plan using verified Brand Profile and Gemini/OpenAI."""
    return await ContentPlannerService.generate_plan(
        db=db,
        organization_id=current_user.organization_id,
        brand_profile_id=payload.brand_profile_id,
        duration_days=payload.duration_days,
        platforms=payload.platforms,
        posting_frequency=payload.posting_frequency,
        campaign_goal=payload.campaign_goal,
        custom_focus=payload.custom_focus,
        start_date=payload.start_date,
        media_format_preference=payload.media_format_preference
    )


@router.post("/save-plan")
def save_content_plan(
    payload: SavePlanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Save or update a campaign plan and its planned content ideas in database."""
    return ContentPlannerService.save_plan_to_database(
        db=db,
        organization_id=current_user.organization_id,
        current_user=current_user,
        plan_data=payload.model_dump()
    )


@router.get("/{campaign_id}/ideas")
def get_campaign_ideas(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Fetch saved campaign plan and all its Content ideas."""
    return ContentPlannerService.get_campaign_ideas(
        db=db,
        organization_id=current_user.organization_id,
        campaign_id=campaign_id
    )


@router.put("/ideas/{content_id}")
def update_planned_idea(
    content_id: int,
    payload: UpdateIdeaRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Update title, hook, brief, format, date, or plan status of a single planned idea."""
    return ContentPlannerService.update_idea(
        db=db,
        organization_id=current_user.organization_id,
        content_id=content_id,
        payload=payload.model_dump(exclude_unset=True)
    )


@router.delete("/ideas/{content_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_planned_idea(
    content_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Delete a planned idea from the plan."""
    ContentPlannerService.delete_idea(
        db=db,
        organization_id=current_user.organization_id,
        content_id=content_id
    )
    return None


@router.post("/ideas/{content_id}/regenerate")
async def regenerate_single_idea(
    content_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Regenerate a single planned idea using AI while preserving date slot."""
    return await ContentPlannerService.regenerate_single_idea(
        db=db,
        organization_id=current_user.organization_id,
        content_id=content_id
    )


# =========================================================================
# STANDARD CAMPAIGN CRUD
# =========================================================================

@router.get("", response_model=List[CampaignResponse], include_in_schema=False)
@router.get("/", response_model=List[CampaignResponse])
def list_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List marketing campaigns."""
    return CampaignService.list_campaigns(db, current_user.organization_id)


@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post("/", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
def create_campaign(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Create a new campaign."""
    return CampaignService.create_campaign(db, current_user.organization_id, payload)


@router.get("/{campaign_id}", response_model=CampaignResponse)
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get single campaign details."""
    return CampaignService.get_campaign(db, campaign_id, current_user.organization_id)


@router.put("/{campaign_id}", response_model=CampaignResponse)
def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Update campaign information."""
    return CampaignService.update_campaign(db, campaign_id, current_user.organization_id, payload)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Delete campaign."""
    CampaignService.delete_campaign(db, campaign_id, current_user.organization_id)
    return None
