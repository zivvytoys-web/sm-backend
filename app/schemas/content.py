from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, field_validator
from app.utils.enums import ContentStatus, PlatformType


class ContentBase(BaseModel):
    model_config = {"extra": "ignore"}

    title: Optional[str] = None
    caption: str
    hashtags: List[str] = []
    target_platforms: List[PlatformType] = []
    status: ContentStatus = ContentStatus.DRAFT
    meta_info: Dict[str, Any] = {}
    sha256_hash: Optional[str] = None
    validation_status: Optional[str] = "PENDING"
    quality_score: Optional[float] = 100.0
    is_duplicate: Optional[bool] = False

    @field_validator("target_platforms", mode="before")
    @classmethod
    def normalize_platforms(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            v = [v]
        if isinstance(v, list):
            res = []
            for item in v:
                if isinstance(item, PlatformType):
                    res.append(item)
                elif isinstance(item, str):
                    clean = item.strip().lower()
                    if clean in ("all", "all channels"):
                        return list(PlatformType)
                    parts = [p.strip() for p in clean.replace("&", ",").replace("/", ",").split(",") if p.strip()]
                    for p in parts:
                        try:
                            res.append(PlatformType(p))
                        except (ValueError, TypeError):
                            pass
            return res
        return []

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v):
        if isinstance(v, str):
            try:
                return ContentStatus(v.lower().strip())
            except ValueError:
                return ContentStatus.DRAFT
        return v


class ContentCreate(ContentBase):
    brand_profile_id: Optional[int] = None
    campaign_id: Optional[int] = None
    media_asset_id: Optional[int] = None


class ContentUpdate(BaseModel):
    model_config = {"extra": "ignore"}

    title: Optional[str] = None
    caption: Optional[str] = None
    hashtags: Optional[List[str]] = None
    target_platforms: Optional[List[PlatformType]] = None
    status: Optional[ContentStatus] = None
    brand_profile_id: Optional[int] = None
    campaign_id: Optional[int] = None
    media_asset_id: Optional[int] = None
    meta_info: Optional[Dict[str, Any]] = None

    @field_validator("target_platforms", mode="before")
    @classmethod
    def normalize_platforms(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            v = [v]
        if isinstance(v, list):
            res = []
            for item in v:
                if isinstance(item, PlatformType):
                    res.append(item)
                elif isinstance(item, str):
                    clean = item.strip().lower()
                    if clean in ("all", "all channels"):
                        return list(PlatformType)
                    # Support joined formats like "instagram & facebook" or "instagram, facebook"
                    parts = [p.strip() for p in clean.replace("&", ",").replace("/", ",").split(",") if p.strip()]
                    for p in parts:
                        try:
                            res.append(PlatformType(p))
                        except (ValueError, TypeError):
                            pass
            return res
        return []

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v):
        if isinstance(v, str):
            try:
                return ContentStatus(v.lower().strip())
            except ValueError:
                return None
        return v


class ContentResponse(ContentBase):
    id: int
    brand_profile_id: int
    campaign_id: Optional[int] = None
    creator_id: Optional[int] = None
    media_asset_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
