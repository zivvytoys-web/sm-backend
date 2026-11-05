from sqlalchemy import Column, String, Integer, ForeignKey, Date
from sqlalchemy.orm import relationship
from app.db.base import Base


class UsageRecord(Base):
    __tablename__ = "usage_records"

    metric_name = Column(String(100), nullable=False, index=True)  # e.g., 'ai_tokens', 'ai_images', 'posts_published'
    amount = Column(Integer, default=1, nullable=False)
    billing_period = Column(String(20), nullable=False, index=True)  # e.g., '2026-10'
    date = Column(Date, nullable=False)

    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)

    # Relationships
    organization = relationship("Organization", back_populates="usage_records")
