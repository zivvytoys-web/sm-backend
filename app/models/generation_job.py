from sqlalchemy import Column, String, Text, Enum as SQLEnum, ForeignKey, Integer, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.enums import GenerationType, JobStatus


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    job_type = Column(SQLEnum(GenerationType), nullable=False)
    status = Column(SQLEnum(JobStatus), default=JobStatus.PENDING, nullable=False)
    prompt = Column(Text, nullable=False)
    model_name = Column(String(100), nullable=True)
    parameters = Column(JSON, default=dict, nullable=False)
    result_data = Column(JSON, default=dict, nullable=False)
    error_message = Column(Text, nullable=True)
    tokens_used = Column(Integer, default=0, nullable=False)

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)

    # Relationships
    user = relationship("User", back_populates="generation_jobs")
    organization = relationship("Organization", foreign_keys=[organization_id])
