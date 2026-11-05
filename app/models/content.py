from sqlalchemy import Column, String, Text, Enum as SQLEnum, ForeignKey, Integer, JSON, Boolean, Float
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.enums import ContentStatus, PlatformType


class Content(Base):
    __tablename__ = "contents"

    title = Column(String(255), nullable=True)
    caption = Column(Text, nullable=False)
    hashtags = Column(JSON, default=list, nullable=False)
    target_platforms = Column(JSON, default=list, nullable=False)  # List of PlatformType
    status = Column(SQLEnum(ContentStatus), default=ContentStatus.DRAFT, nullable=False)
    meta_info = Column(JSON, default=dict, nullable=False)

    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    media_asset_id = Column(Integer, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)

    # Quality Check & Duplicate Prevention fields
    sha256_hash = Column(String(64), nullable=True, index=True)
    validation_status = Column(String(32), default="PENDING", nullable=False, index=True)  # PASS, REGENERATE, BLOCK, REVIEW_REQUIRED, PENDING
    quality_score = Column(Float, default=100.0, nullable=False)
    is_duplicate = Column(Boolean, default=False, nullable=False)

    # Relationships
    brand_profile = relationship("BrandProfile", back_populates="contents")
    organization = relationship("Organization", foreign_keys=[organization_id])
    campaign = relationship("Campaign", back_populates="contents")
    creator = relationship("User", back_populates="contents")
    media_asset = relationship("MediaAsset", back_populates="contents")
    approvals = relationship("Approval", back_populates="content", cascade="all, delete-orphan")
    scheduled_posts = relationship("ScheduledPost", back_populates="content", cascade="all, delete-orphan")
    quality_reports = relationship("QualityReport", back_populates="content", cascade="all, delete-orphan")
