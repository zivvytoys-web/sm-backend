from datetime import datetime
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field


class ProductItem(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    price: Optional[str] = None
    image_url: Optional[str] = None
    category: Optional[str] = None


class BrandProfileBase(BaseModel):
    name: str
    industry: Optional[str] = None
    tagline: Optional[str] = None
    location: Optional[str] = None
    timings: Optional[str] = None
    logo_url: Optional[str] = None
    tone_of_voice: str = "Professional & Engaging"
    target_audience: Optional[Union[str, Dict[str, Any], List[str]]] = None
    brand_guidelines: Optional[str] = None
    cta: Optional[str] = None
    avoid: Optional[str] = None
    key_themes: Optional[List[str]] = Field(default_factory=list)
    hashtags: Optional[List[str]] = Field(default_factory=list)
    color_palette: Optional[List[str]] = Field(default_factory=list)
    meta_info: Optional[Dict[str, Any]] = Field(default_factory=dict)


class BrandProfileCreate(BrandProfileBase):
    organization_id: Optional[int] = None


class BrandProfileUpdate(BaseModel):
    name: Optional[str] = None
    industry: Optional[str] = None
    tagline: Optional[str] = None
    location: Optional[str] = None
    timings: Optional[str] = None
    logo_url: Optional[str] = None
    tone_of_voice: Optional[str] = None
    target_audience: Optional[Union[str, Dict[str, Any], List[str]]] = None
    brand_guidelines: Optional[str] = None
    cta: Optional[str] = None
    avoid: Optional[str] = None
    key_themes: Optional[List[str]] = None
    hashtags: Optional[List[str]] = None
    color_palette: Optional[List[str]] = None
    meta_info: Optional[Dict[str, Any]] = None


class BrandProfileResponse(BrandProfileBase):
    id: int
    organization_id: int
    user_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

