from sqlalchemy import Column, String, Boolean, Enum as SQLEnum, ForeignKey, Integer
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.enums import UserRole


class User(Base):
    __tablename__ = "users"

    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    avatar_url = Column(String(512), nullable=True)
    role = Column(SQLEnum(UserRole), default=UserRole.CREATOR, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    organization = relationship("Organization", back_populates="users")
    user_organizations = relationship("UserOrganization", back_populates="user", cascade="all, delete-orphan")
    contents = relationship("Content", back_populates="creator")
    approvals_requested = relationship("Approval", foreign_keys="Approval.requester_id", back_populates="requester")
    approvals_reviewed = relationship("Approval", foreign_keys="Approval.reviewer_id", back_populates="reviewer")
    generation_jobs = relationship("GenerationJob", back_populates="user")
    audit_logs = relationship("AuditLog", back_populates="user")
