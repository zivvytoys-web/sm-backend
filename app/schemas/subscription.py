from datetime import datetime
from pydantic import BaseModel
from app.utils.enums import SubscriptionTier, SubscriptionStatus


class SubscriptionBase(BaseModel):
    tier: SubscriptionTier = SubscriptionTier.FREE
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    monthly_generation_limit: int = 50
    monthly_posts_limit: int = 100


class SubscriptionCreate(SubscriptionBase):
    organization_id: int
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None


class SubscriptionUpdate(BaseModel):
    tier: SubscriptionTier | None = None
    status: SubscriptionStatus | None = None
    monthly_generation_limit: int | None = None
    monthly_posts_limit: int | None = None
    current_period_end: datetime | None = None


class SubscriptionResponse(SubscriptionBase):
    id: int
    organization_id: int
    stripe_customer_id: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
