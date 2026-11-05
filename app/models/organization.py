from sqlalchemy import Column, String, Boolean, JSON, Text
from sqlalchemy.orm import relationship
from app.db.base import Base


class Organization(Base):
    __tablename__ = "organizations"

    name = Column(String(255), nullable=False, index=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    logo_url = Column(String(512), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    settings = Column(JSON, default=dict, nullable=False)

    # Relationships
    users = relationship("User", back_populates="organization", foreign_keys="User.organization_id")
    user_organizations = relationship("UserOrganization", back_populates="organization", cascade="all, delete-orphan")
    brand_profiles = relationship("BrandProfile", back_populates="organization", cascade="all, delete-orphan")
    subscription = relationship("Subscription", back_populates="organization", uselist=False, cascade="all, delete-orphan")
    usage_records = relationship("UsageRecord", back_populates="organization", cascade="all, delete-orphan")
    campaigns = relationship("Campaign", back_populates="organization", cascade="all, delete-orphan")
