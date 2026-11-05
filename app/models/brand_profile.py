from sqlalchemy import Column, String, Text, JSON, ForeignKey, Integer
from sqlalchemy.orm import relationship
from app.db.base import Base


class BrandProfile(Base):
    __tablename__ = "brand_profiles"

    name = Column(String(255), nullable=False, index=True)
    industry = Column(String(100), nullable=True)
    tagline = Column(String(255), nullable=True)
    location = Column(String(255), nullable=True)
    timings = Column(String(255), nullable=True)
    logo_url = Column(Text, nullable=True)
    tone_of_voice = Column(String(100), default="Professional & Engaging", nullable=False)
    target_audience = Column(Text, nullable=True)
    brand_guidelines = Column(Text, nullable=True)
    cta = Column(Text, nullable=True)
    avoid = Column(Text, nullable=True)
    key_themes = Column(JSON, default=list, nullable=False)
    hashtags = Column(JSON, default=list, nullable=False)
    color_palette = Column(JSON, default=list, nullable=False)
    meta_info = Column(JSON, default=dict, nullable=False)

    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Relationships
    organization = relationship("Organization", back_populates="brand_profiles")
    creator = relationship("User", foreign_keys=[user_id])
    social_accounts = relationship("SocialAccount", back_populates="brand_profile", cascade="all, delete-orphan")
    campaigns = relationship("Campaign", back_populates="brand_profile", cascade="all, delete-orphan")
    contents = relationship("Content", back_populates="brand_profile", cascade="all, delete-orphan")
    business_schedule = relationship("BusinessSchedule", back_populates="brand_profile", uselist=False, cascade="all, delete-orphan")
