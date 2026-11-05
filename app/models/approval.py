from sqlalchemy import Column, String, Text, Enum as SQLEnum, ForeignKey, Integer, DateTime
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.enums import ApprovalStatus


class Approval(Base):
    __tablename__ = "approvals"

    status = Column(SQLEnum(ApprovalStatus), default=ApprovalStatus.PENDING, nullable=False)
    comment = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    content_id = Column(Integer, ForeignKey("contents.id", ondelete="CASCADE"), nullable=False)
    requester_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reviewer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    content = relationship("Content", back_populates="approvals")
    requester = relationship("User", foreign_keys=[requester_id], back_populates="approvals_requested")
    reviewer = relationship("User", foreign_keys=[reviewer_id], back_populates="approvals_reviewed")
