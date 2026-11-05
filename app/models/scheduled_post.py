from sqlalchemy import Column, String, Text, DateTime, Enum as SQLEnum, ForeignKey, Integer, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.enums import ContentStatus


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    title = Column(String(255), nullable=True)
    caption = Column(Text, nullable=True)
    hashtags = Column(JSON, default=list, nullable=False)
    internal_tags = Column(JSON, default=list, nullable=False)
    platform = Column(String(50), default="instagram", nullable=False)
    post_format = Column(String(50), default="post", nullable=False)  # "post", "reel", "story", "carousel"

    scheduled_time = Column(DateTime, nullable=False, index=True)
    published_time = Column(DateTime, nullable=True)
    status = Column(String(50), default="scheduled", nullable=False)
    timezone = Column(String(50), default="Asia/Kolkata", nullable=False)
    platform_post_id = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    publish_metadata = Column(JSON, default=dict, nullable=False)

    content_id = Column(Integer, ForeignKey("contents.id", ondelete="SET NULL"), nullable=True)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id", ondelete="SET NULL"), nullable=True)
    media_asset_id = Column(Integer, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    schedule_rule_id = Column(Integer, ForeignKey("schedule_rules.id", ondelete="SET NULL"), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    organization = relationship("Organization")
    user = relationship("User")
    content = relationship("Content", back_populates="scheduled_posts")
    social_account = relationship("SocialAccount", back_populates="scheduled_posts")
    media_asset = relationship("MediaAsset")
    schedule_rule = relationship("ScheduleRule", back_populates="scheduled_posts")
    publish_attempts = relationship("PublishAttempt", back_populates="scheduled_post", cascade="all, delete-orphan")
