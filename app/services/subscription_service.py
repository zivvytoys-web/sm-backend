from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.subscription import Subscription
from app.schemas.subscription import SubscriptionUpdate
from app.utils.enums import SubscriptionTier, SubscriptionStatus


class SubscriptionService:
    TIER_LIMITS = {
        SubscriptionTier.FREE: {"generations": 50, "posts": 100},
        SubscriptionTier.STARTER: {"generations": 300, "posts": 500},
        SubscriptionTier.PRO: {"generations": 2000, "posts": 5000},
        SubscriptionTier.ENTERPRISE: {"generations": 10000, "posts": 50000},
    }

    @staticmethod
    def get_subscription(db: Session, organization_id: int) -> Subscription:
        sub = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
        if not sub:
            sub = Subscription(
                organization_id=organization_id,
                tier=SubscriptionTier.FREE,
                status=SubscriptionStatus.ACTIVE,
                monthly_generation_limit=50,
                monthly_posts_limit=100
            )
            db.add(sub)
            db.commit()
            db.refresh(sub)
        return sub

    @staticmethod
    def upgrade_tier(db: Session, organization_id: int, new_tier: SubscriptionTier) -> Subscription:
        sub = SubscriptionService.get_subscription(db, organization_id)
        limits = SubscriptionService.TIER_LIMITS.get(new_tier, {"generations": 50, "posts": 100})
        
        sub.tier = new_tier
        sub.status = SubscriptionStatus.ACTIVE
        sub.monthly_generation_limit = limits["generations"]
        sub.monthly_posts_limit = limits["posts"]
        sub.current_period_start = datetime.utcnow()
        sub.current_period_end = datetime.utcnow() + timedelta(days=30)

        db.commit()
        db.refresh(sub)
        return sub
