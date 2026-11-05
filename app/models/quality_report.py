from sqlalchemy import Column, String, Integer, Float, ForeignKey, JSON, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.base import Base


class QualityReport(Base):
    __tablename__ = "quality_reports"

    asset_type = Column(String(32), nullable=False)  # "image", "video", "text", "bundle", "content"
    media_asset_id = Column(Integer, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True, index=True)
    content_id = Column(Integer, ForeignKey("contents.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id", ondelete="SET NULL"), nullable=True)

    decision = Column(String(32), default="PENDING", nullable=False, index=True)  # "PASS", "REGENERATE", "BLOCK", "REVIEW_REQUIRED"
    quality_score = Column(Float, default=100.0, nullable=False)
    technical_score = Column(Float, default=100.0, nullable=False)
    brand_score = Column(Float, default=100.0, nullable=False)
    content_accuracy_score = Column(Float, default=100.0, nullable=False)

    duplicate_status = Column(String(32), default="UNIQUE", nullable=False, index=True)  # "UNIQUE", "POTENTIAL_DUPLICATE", "CONFIRMED_DUPLICATE"
    similarity_score = Column(Float, default=0.0, nullable=False)
    matching_asset_id = Column(Integer, nullable=True)
    matching_content_id = Column(Integer, nullable=True)

    failed_checks = Column(JSON, default=list, nullable=True)
    issues = Column(JSON, default=list, nullable=True)
    suggestions = Column(JSON, default=list, nullable=True)
    match_details = Column(JSON, default=dict, nullable=True)
    metrics = Column(JSON, default=dict, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)

    # Relationships
    media_asset = relationship("MediaAsset", foreign_keys=[media_asset_id])
    content = relationship("Content", foreign_keys=[content_id])
    organization = relationship("Organization", foreign_keys=[organization_id])
    brand_profile = relationship("BrandProfile", foreign_keys=[brand_profile_id])
