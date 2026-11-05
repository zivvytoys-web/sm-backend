from sqlalchemy import Column, String, Integer, BigInteger, Float, Date, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base


class Analytics(Base):
    __tablename__ = "analytics"

    date = Column(Date, nullable=False, index=True)
    impressions = Column(BigInteger, default=0, nullable=False)
    reach = Column(BigInteger, default=0, nullable=False)
    views = Column(BigInteger, default=0, nullable=False)
    engagements = Column(BigInteger, default=0, nullable=False)
    likes = Column(Integer, default=0, nullable=False)
    comments = Column(Integer, default=0, nullable=False)
    shares = Column(Integer, default=0, nullable=False)
    clicks = Column(Integer, default=0, nullable=False)
    engagement_rate = Column(Float, default=0.0, nullable=False)
    raw_metrics = Column(JSON, default=dict, nullable=False)

    social_account_id = Column(Integer, ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False)

    # Relationships
    social_account = relationship("SocialAccount", back_populates="analytics_records")
