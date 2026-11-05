from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    title = Column(String(255), nullable=False, index=True)
    objective = Column(Text, nullable=True)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    tags = Column(JSON, default=list, nullable=False)
    status = Column(String(50), default="active", nullable=False)

    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"), nullable=False)

    # Relationships
    organization = relationship("Organization", back_populates="campaigns")
    brand_profile = relationship("BrandProfile", back_populates="campaigns")
    contents = relationship("Content", back_populates="campaign", cascade="all, delete-orphan")
