from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, field_validator


class CollectionBase(BaseModel):
    name: str
    description: Optional[str] = None
    cover_asset_id: Optional[int] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Collection name cannot be empty.")
        return v.strip()


class CollectionCreate(CollectionBase):
    pass


class CollectionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cover_asset_id: Optional[int] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Collection name cannot be empty.")
        return v.strip() if v is not None else None


class CollectionMediaAssign(BaseModel):
    media_ids: List[int]


class MediaItemBrief(BaseModel):
    id: int
    file_name: str
    file_url: str
    media_type: str
    title: Optional[str] = None
    tags: List[str] = []

    class Config:
        from_attributes = True


class CollectionResponse(CollectionBase):
    id: int
    organization_id: int
    user_id: Optional[int] = None
    media_count: int = 0
    cover_asset_url: Optional[str] = None
    media_assets: List[MediaItemBrief] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
