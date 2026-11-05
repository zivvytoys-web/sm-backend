from datetime import datetime
from sqlalchemy import Column, String, Text, ForeignKey, Integer, Boolean, DateTime, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)

    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    event_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(32), default="info", nullable=False)  # "info", "success", "warning", "error"
    link = Column(String(255), nullable=True)
    metadata_json = Column(JSON, default=dict, nullable=False)

    is_read = Column(Boolean, default=False, nullable=False, index=True)
    read_at = Column(DateTime, nullable=True)
    dedup_key = Column(String(191), unique=True, index=True, nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    organization = relationship("Organization", foreign_keys=[organization_id])
