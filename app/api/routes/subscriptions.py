from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.models.user import User
from app.schemas.subscription import SubscriptionResponse
from app.services.subscription_service import SubscriptionService
from app.core.permissions import get_current_active_user
from app.utils.enums import SubscriptionTier

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions & Billing"])


@router.get("/current", response_model=SubscriptionResponse)
def get_current_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get active plan tier and usage quota limits."""
    return SubscriptionService.get_subscription(db, current_user.organization_id)


@router.post("/upgrade/{tier}", response_model=SubscriptionResponse)
def upgrade_plan(
    tier: SubscriptionTier,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Upgrade or switch organization subscription tier."""
    return SubscriptionService.upgrade_tier(db, current_user.organization_id, tier)
