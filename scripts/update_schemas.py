import os

path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../app/schemas/autonomous_pipeline.py'))

content = '''from datetime import datetime, date
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class BusinessScheduleBase(BaseModel):
    generation_date: Optional[str] = Field(default=None, description="Optional future generation date (YYYY-MM-DD)")
    generation_time: str = Field(default="09:00", description="Daily generation time (HH:MM)")
    posting_date: Optional[str] = Field(default=None, description="Optional target posting date (YYYY-MM-DD)")
    posting_time: str = Field(default="18:00", description="Daily target posting time (HH:MM)")
    timezone: str = Field(default="Asia/Kolkata", description="IANA timezone e.g. Asia/Kolkata")
    auto_approve: bool = Field(default=False, description="Whether to auto-approve verified posts without manual review")
    auto_schedule: bool = Field(default=True, description="Whether to auto-schedule posts for publishing")
    is_active: bool = Field(default=True, description="Master toggle for automation scheduler")
    target_platforms: List[str] = Field(default=["instagram", "facebook", "linkedin", "youtube"])
    selected_social_account_ids: Optional[List[int]] = Field(default=None, description="Specific social accounts to publish to")
    notification_settings: Dict[str, bool] = Field(default={"in_app": True, "email": False, "whatsapp": False})


class BusinessScheduleUpdate(BaseModel):
    generation_date: Optional[str] = None
    generation_time: Optional[str] = None
    posting_date: Optional[str] = None
    posting_time: Optional[str] = None
    timezone: Optional[str] = None
    auto_approve: Optional[bool] = None
    auto_schedule: Optional[bool] = None
    is_active: Optional[bool] = None
    target_platforms: Optional[List[str]] = None
    selected_social_account_ids: Optional[List[int]] = None
    notification_settings: Optional[Dict[str, bool]] = None


class BusinessScheduleResponse(BusinessScheduleBase):
    id: int
    brand_profile_id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Live Automation Engine Status fields
    engine_status: str = "active"
    next_generation_time: Optional[datetime] = None
    next_posting_time: Optional[datetime] = None
    today_run_status: Optional[str] = None
    today_post_id: Optional[int] = None
    today_post_status: Optional[str] = None
    today_post_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AutomationStatusResponse(BaseModel):
    is_active: bool
    engine_status: str
    current_stage: Optional[str] = None
    current_progress: int = 0
    generation_date: Optional[str] = None
    generation_time: str
    posting_date: Optional[str] = None
    posting_time: str
    timezone: str
    auto_approve: bool
    auto_schedule: bool
    next_generation_time: Optional[datetime] = None
    next_posting_time: Optional[datetime] = None
    last_run_date: Optional[date] = None
    last_run_status: Optional[str] = None
    last_run_error: Optional[str] = None
    last_post_id: Optional[int] = None
    last_post_status: Optional[str] = None
    last_post_url: Optional[str] = None
    active_jobs_count: int = 0


class ScheduledJobInfo(BaseModel):
    job_id: str
    name: str
    job_type: str
    next_run_time: Optional[datetime] = None
    target_date: Optional[str] = None
    status: str
    details: Dict[str, Any] = {}


class AutomationLogEntry(BaseModel):
    id: int
    brand_profile_id: int
    run_date: date
    status: str
    stage: str
    progress_percent: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    quality_score: Optional[float] = None
    topic: Optional[str] = None
    error: Optional[str] = None
    stats: Dict[str, Any] = {}

    model_config = ConfigDict(from_attributes=True)


class ContentRescheduleRequest(BaseModel):
    scheduled_time: datetime = Field(description="New target scheduled posting timestamp (UTC)")


class ContentEditRequest(BaseModel):
    caption: Optional[str] = None
    title: Optional[str] = None
    hashtags: Optional[List[str]] = None
    scheduled_time: Optional[datetime] = None
    editor_notes: Optional[str] = None


class PostVariantResponse(BaseModel):
    id: int
    post_id: int
    platform: str
    caption: str
    title: Optional[str] = None
    hashtags: List[str] = []
    media_path: Optional[str] = None
    media_type: str = "image"
    aspect_ratio: str = "1:1"
    scheduled_time: datetime
    status: str
    platform_post_id: Optional[str] = None
    platform_url: Optional[str] = None
    error: Optional[str] = None
    publish_metadata: Dict[str, Any] = {}

    model_config = ConfigDict(from_attributes=True)


class PostVariantUpdate(BaseModel):
    caption: Optional[str] = None
    title: Optional[str] = None
    hashtags: Optional[List[str]] = None
    media_path: Optional[str] = None
    scheduled_time: Optional[datetime] = None


class ContentBriefSummary(BaseModel):
    id: int
    topic: str
    angle: str
    reason: Optional[str] = None
    caption_brief: str
    image_prompt: str
    video_prompt: Optional[str] = None
    hashtags: List[str] = []
    cta: Optional[str] = None
    tone: str

    model_config = ConfigDict(from_attributes=True)


class AutonomousPostResponse(BaseModel):
    id: int
    brief_id: int
    brand_profile_id: int
    status: str
    scheduled_time: Optional[datetime] = None
    quality_report: Dict[str, Any] = {}
    auto_approved: bool
    rejection_reason: Optional[str] = None
    editor_notes: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    brief: Optional[ContentBriefSummary] = None
    variants: List[PostVariantResponse] = []

    model_config = ConfigDict(from_attributes=True)


class AutonomousPostUpdate(BaseModel):
    editor_notes: Optional[str] = None
    scheduled_time: Optional[datetime] = None


class PostApproveRequest(BaseModel):
    custom_scheduled_time: Optional[datetime] = None
    notes: Optional[str] = None


class PostRejectRequest(BaseModel):
    reason: str = Field(description="Reason for rejecting post")


class PostRegenerateRequest(BaseModel):
    part: str = Field(description="Component to regenerate: 'caption' | 'image' | 'video'")


class PipelineRunResponse(BaseModel):
    id: int
    brand_profile_id: int
    run_date: date
    status: str
    stage: str = "Initializing"
    progress_percent: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    quality_score: Optional[float] = None
    stats: Dict[str, Any] = {}
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class RunNowRequest(BaseModel):
    brand_profile_id: Optional[int] = None
    force: bool = Field(default=True, description="Force re-run even if already executed today")


class RunNowResponse(BaseModel):
    status: str
    run_id: Optional[int] = None
    post_id: Optional[int] = None
    stage: Optional[str] = "Initializing"
    progress_percent: int = 0
    quality_score: Optional[float] = None
    auto_approved: Optional[bool] = None
    message: str
'''

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated app/schemas/autonomous_pipeline.py with complete schema set!")
