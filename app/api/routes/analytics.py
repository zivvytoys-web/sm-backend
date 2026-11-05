from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.models.user import User
from app.models.brand_profile import BrandProfile
from app.schemas.analytics import OverviewAnalyticsSummary, DashboardAnalyticsResponse
from app.services.analytics_service import AnalyticsService
from app.core.permissions import get_current_active_user, require_creator

router = APIRouter(prefix="/analytics", tags=["Performance & Analytics"])


@router.get("/dashboard", response_model=DashboardAnalyticsResponse)
def get_dashboard_metrics(
    period: str = Query("30d", description="Time window: '7d', '30d', '90d', or 'all'"),
    start_date: Optional[str] = Query(None, description="Custom start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Custom end date (YYYY-MM-DD)"),
    brand_id: Optional[int] = Query(None, description="Filter by brand profile ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Retrieve comprehensive, real-data workspace analytics for dashboard.
    Aggregates real ScheduledPost, Content, and connected SocialAccount records.
    """
    return AnalyticsService.get_dashboard_analytics(
        db=db,
        organization_id=current_user.organization_id,
        period=period,
        start_date=start_date,
        end_date=end_date,
        brand_id=brand_id
    )


@router.post("/sync")
async def sync_connected_account_analytics(
    brand_id: Optional[int] = Query(None, description="Filter by brand profile ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Manually triggers an on-demand analytics synchronization for connected social media accounts.
    """
    result = await AnalyticsService.sync_analytics(
        db=db,
        organization_id=current_user.organization_id,
        brand_id=brand_id
    )
    result["metrics_snapshot"] = AnalyticsService.get_dashboard_analytics(
        db=db,
        organization_id=current_user.organization_id,
        period="30d",
        brand_id=brand_id
    )
    return result


@router.post("/post-publish-sync")
async def post_publish_analytics_refresh(
    brand_id: Optional[int] = Query(None, description="Filter by brand profile ID"),
    post_id: Optional[int] = Query(None, description="The ID of the post that was published"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Triggered automatically after a post is successfully published.
    Waits a brief delay for the platform to index the post, then syncs
    fresh analytics from all connected social media account APIs.
    Returns the updated dashboard metrics snapshot.
    """
    import asyncio
    # Give platforms 5 seconds to register the newly published post
    await asyncio.sleep(5)
    sync_result = await AnalyticsService.sync_analytics(
        db=db,
        organization_id=current_user.organization_id,
        brand_id=brand_id
    )
    # Return fresh dashboard snapshot for the default 30d period
    dashboard = AnalyticsService.get_dashboard_analytics(
        db=db,
        organization_id=current_user.organization_id,
        period="30d",
        brand_id=brand_id
    )
    return {
        "sync": sync_result,
        "metrics_snapshot": dashboard,
        "triggered_by_post_id": post_id
    }


@router.get("/overview/{brand_id}", response_model=OverviewAnalyticsSummary)
def get_analytics_overview(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve historical cross-channel analytics metrics for brand."""
    brand = db.query(BrandProfile).filter(
        BrandProfile.id == brand_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not brand:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand profile not found or unauthorized.")

    return AnalyticsService.get_overview_metrics(db, brand_id)
