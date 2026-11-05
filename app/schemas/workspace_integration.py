from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class WorkspaceIntegrationCreate(BaseModel):
    provider: str = Field(min_length=2, max_length=50)
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    settings: Dict[str, Any] = {}
    enabled: bool = True


class WorkspaceIntegrationUpdate(BaseModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class WorkspaceIntegrationResponse(BaseModel):
    id: int
    organization_id: int
    provider: str
    client_id: Optional[str]
    client_secret_configured: bool
    settings: Dict[str, Any]
    enabled: bool
    verification_status: str
    verification_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
