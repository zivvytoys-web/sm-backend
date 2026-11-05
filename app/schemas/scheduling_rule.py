from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ScheduleRuleBase(BaseModel):
    name: str
    collection_id: Optional[int] = None
    social_account_id: Optional[int] = None
    platform: str = "instagram"
    recurrence_type: str = "daily"  # "hourly", "daily", "every_n_days", "weekdays", "custom_weekly", "once"
    recurrence_interval: int = 1
    weekdays: List[str] = []
    start_date: Optional[str] = None  # "YYYY-MM-DD"
    end_date: Optional[str] = None    # "YYYY-MM-DD"
    start_time: str = "09:00"
    end_time: str = "19:00"
    timezone: str = "Asia/Kolkata"
    max_posts_per_day: int = 5
    selection_strategy: str = "order"  # "order", "oldest_first", "newest_first", "random"
    on_exhaustion: str = "pause"       # "pause", "skip", "wait"
    status: str = "active"             # "active", "paused", "completed", "cancelled"
    meta_info: Dict[str, Any] = {}


class ScheduleRuleCreate(ScheduleRuleBase):
    pass


class ScheduleRuleUpdate(BaseModel):
    name: Optional[str] = None
    collection_id: Optional[int] = None
    social_account_id: Optional[int] = None
    platform: Optional[str] = None
    recurrence_type: Optional[str] = None
    recurrence_interval: Optional[int] = None
    weekdays: Optional[List[str]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    timezone: Optional[str] = None
    max_posts_per_day: Optional[int] = None
    selection_strategy: Optional[str] = None
    on_exhaustion: Optional[str] = None
    status: Optional[str] = None
    meta_info: Optional[Dict[str, Any]] = None


class ScheduleRuleResponse(ScheduleRuleBase):
    id: int
    organization_id: int
    user_id: Optional[int] = None
    next_run_at: Optional[datetime] = None
    collection_name: Optional[str] = None
    social_account_name: Optional[str] = None
    upcoming_slots: List[str] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
