from datetime import datetime
from pydantic import BaseModel
from app.utils.enums import ApprovalStatus


class ApprovalBase(BaseModel):
    content_id: int
    comment: str | None = None


class ApprovalCreate(ApprovalBase):
    pass


class ApprovalReview(BaseModel):
    status: ApprovalStatus
    comment: str | None = None


class ApprovalResponse(ApprovalBase):
    id: int
    status: ApprovalStatus
    requester_id: int
    reviewer_id: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
