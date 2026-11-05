from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, ConfigDict


class PlatformIntegrationBase(BaseModel):
    platform: str
    display_name: str
    is_enabled: bool = False
    environment: str = "production"
    client_id: Optional[str] = None
    auth_url: Optional[str] = None
    token_url: Optional[str] = None
    api_base_url: Optional[str] = None
    api_version: Optional[str] = None
    redirect_uri: Optional[str] = None
    scopes: List[str] = []
    webhook_url: Optional[str] = None
    config_notes: Optional[str] = None
    extra_settings: Dict[str, Any] = {}


class PlatformIntegrationCreate(PlatformIntegrationBase):
    client_secret: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


class PlatformIntegrationUpdate(BaseModel):
    display_name: Optional[str] = None
    is_enabled: Optional[bool] = None
    environment: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    auth_url: Optional[str] = None
    token_url: Optional[str] = None
    api_base_url: Optional[str] = None
    api_version: Optional[str] = None
    redirect_uri: Optional[str] = None
    scopes: Optional[List[str]] = None
    webhook_url: Optional[str] = None
    config_notes: Optional[str] = None
    extra_settings: Optional[Dict[str, Any]] = None


class PlatformIntegrationResponse(PlatformIntegrationBase):
    id: int
    client_secret_configured: bool = False
    client_secret_masked: str = "Not Configured"
    api_key_configured: bool = False
    api_key_masked: str = "Not Configured"
    api_secret_configured: bool = False
    api_secret_masked: str = "Not Configured"
    last_tested_at: Optional[datetime] = None
    test_status: str = "untested"
    test_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestIntegrationRequest(BaseModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


class TestIntegrationResponse(BaseModel):
    platform: str
    success: bool
    test_status: str
    message: str
    details: Dict[str, Any] = {}
    tested_at: str
