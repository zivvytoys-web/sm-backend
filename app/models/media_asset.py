from sqlalchemy import Column, String, Integer, BigInteger, Enum as SQLEnum, ForeignKey, JSON, Boolean, Float
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.enums import MediaType


class MediaAsset(Base):
    __tablename__ = "media_assets"

    file_name = Column(String(255), nullable=False)
    file_url = Column(String(1024), nullable=False)
    public_id = Column(String(255), nullable=True, index=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    mime_type = Column(String(100), nullable=True)
    media_type = Column(SQLEnum(MediaType), default=MediaType.IMAGE, nullable=False)
    meta_info = Column(JSON, default=dict, nullable=False)
    title = Column(String(255), nullable=True)
    description = Column(String(1000), nullable=True)
    alt_text = Column(String(500), nullable=True)
    tags = Column(JSON, default=list, nullable=False)

    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Quality Check & Duplicate Prevention fields
    sha256_hash = Column(String(64), nullable=True, index=True)
    perceptual_hash = Column(String(64), nullable=True, index=True)
    validation_status = Column(String(32), default="PENDING", nullable=False, index=True)  # PASS, REGENERATE, BLOCK, REVIEW_REQUIRED, PENDING
    quality_score = Column(Float, default=100.0, nullable=False)
    is_duplicate = Column(Boolean, default=False, nullable=False)
    duplicate_of_id = Column(Integer, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    contents = relationship("Content", back_populates="media_asset")
    collections = relationship("Collection", secondary="collection_media", back_populates="media_assets")
    user = relationship("User", foreign_keys=[user_id])
    quality_reports = relationship("QualityReport", back_populates="media_asset", cascade="all, delete-orphan")
