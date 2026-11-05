from typing import Dict, Any, List, Optional
from datetime import datetime

from pydantic import BaseModel, Field
from app.utils.enums import PublishAttemptStatus


class ScheduledPostBase(BaseModel):
    title: Optional[str] = None
    caption: Optional[str] = None
    hashtags: List[str] = Field(default_factory=list)
    internal_tags: List[str] = Field(default_factory=list)
    platform: str = "instagram"
    post_format: str = "post"
    content_id: Optional[int] = None
    social_account_id: Optional[int] = None
    media_asset_id: Optional[int] = None
    schedule_rule_id: Optional[int] = None
    scheduled_time: datetime
    timezone: str = "Asia/Kolkata"
    status: str = "scheduled"


class ScheduledPostCreate(ScheduledPostBase):
    post_to_all: bool = False
    social_account_ids: Optional[List[int]] = None


class ScheduledPostUpdate(BaseModel):
    title: Optional[str] = None
    caption: Optional[str] = None
    hashtags: Optional[List[str]] = None
    internal_tags: Optional[List[str]] = None
    platform: Optional[str] = None
    post_format: Optional[str] = None
    content_id: Optional[int] = None
    media_asset_id: Optional[int] = None
    schedule_rule_id: Optional[int] = None
    social_account_id: Optional[int] = None
    social_account_ids: Optional[List[int]] = None
    scheduled_time: Optional[datetime] = None
    timezone: Optional[str] = None
    status: Optional[str] = None


class PublishAttemptResponse(BaseModel):
    id: int
    attempt_number: int
    status: PublishAttemptStatus
    response_payload: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MediaAssetBrief(BaseModel):
    id: int
    file_name: str
    file_url: str
    media_type: str
    title: Optional[str] = None

    class Config:
        from_attributes = True


class ScheduledPostResponse(ScheduledPostBase):
    id: int
    published_time: Optional[datetime] = None
    platform_post_id: Optional[str] = None
    publish_metadata: Dict[str, Any] = Field(default_factory=dict)
    publish_attempts: List[PublishAttemptResponse] = Field(default_factory=list)
    error_message: Optional[str] = None
    media_asset: Optional[MediaAssetBrief] = None
    social_account_name: Optional[str] = None
    account_name: Optional[str] = None
    content_title: Optional[str] = None

    class Config:
        from_attributes = True


