from sqlalchemy import Column, String, Boolean, Enum as SQLEnum, ForeignKey, Integer, DateTime, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.enums import PlatformType


class SocialAccount(Base):
    __tablename__ = "social_accounts"

    platform = Column(String(50), nullable=False)
    account_name = Column(String(255), nullable=False)
    account_id = Column(String(255), nullable=False)
    profile_url = Column(String(512), nullable=True)
    access_token = Column(String(1024), nullable=True)
    refresh_token = Column(String(1024), nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    is_connected = Column(Boolean, default=True, nullable=False)
    meta_info = Column(JSON, default=dict, nullable=False)

    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)

    # Relationships
    brand_profile = relationship("BrandProfile", back_populates="social_accounts")
    organization = relationship("Organization", foreign_keys=[organization_id])
    scheduled_posts = relationship("ScheduledPost", back_populates="social_account", cascade="all, delete-orphan")
    analytics_records = relationship("Analytics", back_populates="social_account", cascade="all, delete-orphan")
