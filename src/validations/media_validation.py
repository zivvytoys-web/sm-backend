from datetime import datetime
from typing import Dict, Any, Optional
from pydantic import BaseModel
from app.utils.enums import MediaType


class MediaAssetResponseValidation(BaseModel):
    id: int
    organization_id: int
    user_id: Optional[int] = None
    file_name: str
    file_url: str
    public_id: Optional[str] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    media_type: MediaType = MediaType.IMAGE
    meta_info: Dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MediaSignatureValidation(BaseModel):
    timestamp: int
    signature: str
    api_key: str
    cloud_name: str
    folder: str


__all__ = ["MediaAssetResponseValidation", "MediaSignatureValidation"]
