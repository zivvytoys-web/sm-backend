from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from app.utils.enums import UserRole


class BusinessSettings(BaseModel):
    industry: Optional[str] = None
    category: Optional[str] = None
    flagshipAddr: Optional[str] = None
    branchAddr: Optional[str] = None
    timings: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    gstin: Optional[str] = None
    categories: Optional[str] = None
    website: Optional[str] = None


class BusinessCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    brand_profile: Optional[Dict[str, Any]] = None


class BusinessUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: Optional[bool] = None
    settings: Optional[Dict[str, Any]] = None
    brand_profile: Optional[Dict[str, Any]] = None


class BusinessResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: bool = True
    settings: Dict[str, Any] = {}
    brand_profile: Optional[Dict[str, Any]] = None
    assigned_users_count: int = 0
    assigned_users: List[Dict[str, Any]] = []
    created_at: Optional[str] = None


class UserBusinessAssignmentRequest(BaseModel):
    business_id: int
    role: Optional[UserRole] = UserRole.CREATOR
    is_default: Optional[bool] = False


class AvailableBusinessItem(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    logo_url: Optional[str] = None
    category: Optional[str] = None
    location: Optional[str] = None
    brand_tagline: Optional[str] = None
