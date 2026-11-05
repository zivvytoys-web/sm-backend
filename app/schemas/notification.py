from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    organization_id: Optional[int] = None
    title: str
    message: str
    event_type: str
    severity: str
    link: Optional[str] = None
    metadata_json: Optional[Dict[str, Any]] = None
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationListResponse(BaseModel):
    items: List[NotificationResponse]
    unread_count: int
    total: int


class MarkReadRequest(BaseModel):
    notification_ids: Optional[List[int]] = None
