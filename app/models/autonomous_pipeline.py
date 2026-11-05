from datetime import datetime, date
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Text,
    JSON,
    DateTime,
    Date,
    Integer,
    ForeignKey,
    UniqueConstraint,
    Float,
    Index
)
from sqlalchemy.orm import relationship
from app.db.base import Base


class BusinessSchedule(Base):
    """
    Autonomous posting schedule configuration per business/brand.
    """
    __tablename__ = "business_schedules"

    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"), unique=True, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    
    generation_date = Column(String(10), nullable=True)                    # Optional future generation date (YYYY-MM-DD)
    generation_time = Column(String(10), default="09:00", nullable=False)  # HH:MM (e.g. 09:00)
    posting_date = Column(String(10), nullable=True)                       # Optional target posting date (YYYY-MM-DD)
    posting_time = Column(String(10), default="18:00", nullable=False)     # HH:MM (e.g. 18:00)
    timezone = Column(String(50), default="Asia/Kolkata", nullable=False)  # e.g. Asia/Kolkata
    
    auto_approve = Column(Boolean, default=False, nullable=False)          # If true, auto-approves passing content
    auto_schedule = Column(Boolean, default=True, nullable=False)          # Independent toggle: if true, automatically creates publishing jobs
    is_active = Column(Boolean, default=True, nullable=False)              # Master toggle for autonomous pipeline
    
    target_platforms = Column(JSON, default=lambda: ["instagram", "facebook", "linkedin", "youtube"], nullable=False)
    selected_social_account_ids = Column(JSON, default=list, nullable=True)
    content_type = Column(String(20), default="both", nullable=False)      # "image", "video", "both"
    content_preferences = Column(Text, nullable=True)                      # Custom guidelines/instructions from user
    notification_settings = Column(JSON, default=lambda: {"in_app": True, "email": False, "whatsapp": False}, nullable=False)

    # Relationships
    brand_profile = relationship("BrandProfile", back_populates="business_schedule")


class PipelineRun(Base):
    """
    Record of a daily autonomous pipeline execution per business.
    Guarantees idempotency via UNIQUE(brand_profile_id, run_date).
    """
    __tablename__ = "pipeline_runs"

    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    run_date = Column(Date, default=date.today, nullable=False)
    
    status = Column(String(30), default="pending", nullable=False)  # pending, running, completed, failed
    stage = Column(String(50), default="Initializing", nullable=False)  # Initializing, Analyzing Brand, Generating Content, Creating Media, Validating Content, Awaiting Approval, Scheduled, Publishing, Completed, Failed
    progress_percent = Column(Integer, default=0, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    
    quality_score = Column(Float, nullable=True)
    stats = Column(JSON, default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("brand_profile_id", "run_date", name="uq_business_run_date"),
        Index("ix_pipeline_runs_status", "status"),
    )

    # Relationships
    brand_profile = relationship("BrandProfile")
    brief = relationship("ContentBrief", back_populates="run", uselist=False, cascade="all, delete-orphan")


class ContentBrief(Base):
    """
    AI-generated creative brief and angle for the daily post.
    """
    __tablename__ = "content_briefs"

    run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), unique=True, nullable=False)
    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"), nullable=False)
    
    topic = Column(String(255), nullable=False)
    angle = Column(String(255), nullable=False)
    reason = Column(Text, nullable=True)
    caption_brief = Column(Text, nullable=False)
    image_prompt = Column(Text, nullable=False)
    video_prompt = Column(Text, nullable=True)
    hashtags = Column(JSON, default=list, nullable=False)
    cta = Column(String(255), nullable=True)
    tone = Column(String(100), default="Engaging", nullable=False)
    target_platforms = Column(JSON, default=list, nullable=False)
    raw_json = Column(JSON, default=dict, nullable=False)

    # Relationships
    run = relationship("PipelineRun", back_populates="brief")
    assets = relationship("GeneratedAsset", back_populates="brief", cascade="all, delete-orphan")
    post = relationship("AutonomousPost", back_populates="brief", uselist=False, cascade="all, delete-orphan")


class GeneratedAsset(Base):
    """
    Raw media and text artifacts generated during the pipeline.
    """
    __tablename__ = "generated_assets"

    brief_id = Column(Integer, ForeignKey("content_briefs.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(30), nullable=False)  # text, image, video
    status = Column(String(30), default="pending", nullable=False)  # pending, processing, done, failed
    
    file_path = Column(String(1024), nullable=True)
    aspect_ratio = Column(String(20), nullable=True)  # 1:1, 4:5, 1.91:1, 9:16, 16:9
    operation_id = Column(String(255), nullable=True)  # Used for async Veo/Gemini polling
    attempts = Column(Integer, default=0, nullable=False)
    error = Column(Text, nullable=True)
    metadata_json = Column(JSON, default=dict, nullable=False)

    # Relationships
    brief = relationship("ContentBrief", back_populates="assets")


class AutonomousPost(Base):
    """
    Consolidated post ready for review, scheduling, and auto-publishing.
    """
    __tablename__ = "autonomous_posts"

    brief_id = Column(Integer, ForeignKey("content_briefs.id", ondelete="CASCADE"), unique=True, nullable=False)
    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    
    # Status: generating -> draft -> pending_approval -> approved -> scheduled -> posting -> posted | failed | rejected | expired
    status = Column(String(30), default="pending_approval", nullable=False)
    scheduled_time = Column(DateTime, nullable=True)  # UTC timestamp for release
    
    quality_report = Column(JSON, default=dict, nullable=False)
    auto_approved = Column(Boolean, default=False, nullable=False)
    rejection_reason = Column(Text, nullable=True)
    editor_notes = Column(Text, nullable=True)
    
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_autonomous_posts_status_time", "status", "scheduled_time"),
    )

    # Relationships
    brief = relationship("ContentBrief", back_populates="post")
    brand_profile = relationship("BrandProfile")
    variants = relationship("PostVariant", back_populates="post", cascade="all, delete-orphan")

    @property
    def assets(self):
        if self.brief and self.brief.assets:
            return self.brief.assets
        return []


class PostVariant(Base):
    """
    Platform-specific tailored output (Instagram, Facebook, LinkedIn, YouTube, etc.)
    """
    __tablename__ = "post_variants"

    post_id = Column(Integer, ForeignKey("autonomous_posts.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(50), nullable=False)  # instagram, facebook, linkedin, youtube, twitter, pinterest, snapchat
    
    caption = Column(Text, nullable=False)
    title = Column(String(255), nullable=True)      # e.g. for YouTube / LinkedIn article
    hashtags = Column(JSON, default=list, nullable=False)
    
    media_path = Column(String(1024), nullable=True)
    media_type = Column(String(30), default="image", nullable=False)  # image, video
    aspect_ratio = Column(String(20), default="1:1", nullable=False)
    
    scheduled_time = Column(DateTime, nullable=False)  # UTC timestamp
    status = Column(String(30), default="scheduled", nullable=False)  # pending, scheduled, posting, posted, failed, skipped
    
    platform_post_id = Column(String(255), nullable=True)
    platform_url = Column(String(512), nullable=True)
    error = Column(Text, nullable=True)
    publish_metadata = Column(JSON, default=dict, nullable=False)
    # Rate-limit cooldown: UTC timestamp after which this variant may be retried.
    # Set when Instagram/other platforms return a temporary security block (e.g. subcode 2207051).
    retry_after = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_post_variants_status_time", "status", "scheduled_time"),
    )

    # Relationships
    post = relationship("AutonomousPost", back_populates="variants")
