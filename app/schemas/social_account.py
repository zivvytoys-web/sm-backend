from datetime import datetime
from typing import Dict, Any
from pydantic import BaseModel, field_validator
from app.utils.enums import PlatformType


class SocialAccountBase(BaseModel):
    platform: PlatformType
    account_name: str
    account_id: str | None = None
    profile_url: str | None = None
    meta_info: Dict[str, Any] = {}

    @field_validator('platform', mode='before')
    @classmethod
    def parse_platform(cls, v):
        if isinstance(v, str):
            v_lower = v.lower()
            return PlatformType(v_lower)
        return v


class SocialAccountCreate(SocialAccountBase):
    brand_profile_id: int | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None


class SocialAccountUpdate(BaseModel):
    account_name: str | None = None
    profile_url: str | None = None
    is_connected: bool | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    meta_info: Dict[str, Any] | None = None


class SocialAccountResponse(SocialAccountBase):
    id: int
    brand_profile_id: int
    is_connected: bool
    token_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

