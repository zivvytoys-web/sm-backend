from sqlalchemy import Column, String, Text, Enum as SQLEnum, ForeignKey, Integer, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.enums import PublishAttemptStatus


class PublishAttempt(Base):
    __tablename__ = "publish_attempts"

    attempt_number = Column(Integer, default=1, nullable=False)
    status = Column(SQLEnum(PublishAttemptStatus), default=PublishAttemptStatus.PENDING, nullable=False)
    response_payload = Column(JSON, default=dict, nullable=False)
    error_message = Column(Text, nullable=True)

    scheduled_post_id = Column(Integer, ForeignKey("scheduled_posts.id", ondelete="CASCADE"), nullable=False)

    # Relationships
    scheduled_post = relationship("ScheduledPost", back_populates="publish_attempts")
