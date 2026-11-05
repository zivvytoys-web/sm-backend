from sqlalchemy import Column, String, Integer, Enum as SQLEnum, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.enums import SubscriptionTier, SubscriptionStatus


class Subscription(Base):
    __tablename__ = "subscriptions"

    tier = Column(SQLEnum(SubscriptionTier), default=SubscriptionTier.FREE, nullable=False)
    status = Column(SQLEnum(SubscriptionStatus), default=SubscriptionStatus.ACTIVE, nullable=False)
    stripe_customer_id = Column(String(255), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    monthly_generation_limit = Column(Integer, default=50, nullable=False)
    monthly_posts_limit = Column(Integer, default=100, nullable=False)

    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Relationships
    organization = relationship("Organization", back_populates="subscription")
