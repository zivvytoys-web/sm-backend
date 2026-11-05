from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base


class ScheduleRule(Base):
    __tablename__ = "schedule_rules"

    name = Column(String(255), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    collection_id = Column(Integer, ForeignKey("collections.id", ondelete="SET NULL"), nullable=True)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id", ondelete="SET NULL"), nullable=True)
    platform = Column(String(50), default="instagram", nullable=False)

    # Recurrence rules
    recurrence_type = Column(String(50), default="daily", nullable=False)  # "hourly", "daily", "every_n_days", "weekdays", "custom_weekly", "once"
    recurrence_interval = Column(Integer, default=1, nullable=False)       # Every N hours or N days
    weekdays = Column(JSON, default=list, nullable=False)                  # ["mon", "tue", "wed", "thu", "fri"]

    # Active publishing window
    start_date = Column(String(20), nullable=True)   # "YYYY-MM-DD"
    end_date = Column(String(20), nullable=True)     # "YYYY-MM-DD"
    start_time = Column(String(20), default="09:00", nullable=True)  # "09:00"
    end_time = Column(String(20), default="19:00", nullable=True)    # "19:00"
    timezone = Column(String(50), default="Asia/Kolkata", nullable=False)

    # Execution limits & pacing
    max_posts_per_day = Column(Integer, default=5, nullable=False)
    selection_strategy = Column(String(50), default="order", nullable=False)  # "order", "oldest_first", "newest_first", "random"
    on_exhaustion = Column(String(50), default="pause", nullable=False)       # "pause", "skip", "wait"

    # Status: "active", "paused", "completed", "cancelled"
    status = Column(String(50), default="active", nullable=False)
    next_run_at = Column(DateTime, nullable=True)
    meta_info = Column(JSON, default=dict, nullable=False)

    # Relationships
    collection = relationship("Collection")
    social_account = relationship("SocialAccount")
    scheduled_posts = relationship("ScheduledPost", back_populates="schedule_rule", cascade="all, delete-orphan")
