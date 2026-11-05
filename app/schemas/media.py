from datetime import datetime
from typing import Dict, Any, Optional, List
from pydantic import BaseModel
from app.utils.enums import MediaType


class MediaAssetBase(BaseModel):
    file_name: str
    file_url: str
    public_id: Optional[str] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    media_type: MediaType = MediaType.IMAGE
    title: Optional[str] = None
    description: Optional[str] = None
    alt_text: Optional[str] = None
    tags: List[str] = []
    meta_info: Dict[str, Any] = {}
    sha256_hash: Optional[str] = None
    perceptual_hash: Optional[str] = None
    validation_status: Optional[str] = "PENDING"
    quality_score: Optional[float] = 100.0
    is_duplicate: Optional[bool] = False
    duplicate_of_id: Optional[int] = None


class MediaAssetCreate(MediaAssetBase):
    organization_id: int
    user_id: Optional[int] = None
    collection_id: Optional[int] = None


class MediaAssetUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    alt_text: Optional[str] = None
    tags: Optional[List[str]] = None
    collection_ids: Optional[List[int]] = None


class MediaAssetResponse(MediaAssetBase):
    id: int
    organization_id: int
    user_id: Optional[int] = None
    collections: List[Dict[str, Any]] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CloudinarySignatureResponse(BaseModel):
    timestamp: int
    signature: str
    api_key: str
    cloud_name: str
    folder: str

class DirectMediaUploadRequest(BaseModel):
    file_name: str
    file_url: str
    public_id: Optional[str] = None
    file_size_bytes: Optional[int] = 0
    mime_type: Optional[str] = None
    media_type: MediaType = MediaType.IMAGE
    title: Optional[str] = None
    description: Optional[str] = None
    alt_text: Optional[str] = None
    tags: List[str] = []
    meta_info: Dict[str, Any] = {}
