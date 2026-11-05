"""
Tenant Audit Logs API Router.
Provides tenant-isolated audit trail access for Enterprise Admins.
"""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.core.tenant import get_tenant_context, TenantContext
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit-logs", tags=["Tenant Audit Logs"])


class AuditLogResponse(BaseModel):
    id: int
    action: str
    entity_type: str
    entity_id: Optional[int] = None
    user_id: Optional[int] = None
    user_email: Optional[str] = None
    user_role: Optional[str] = None
    organization_id: Optional[int] = None
    ip_address: Optional[str] = None
    details: Dict[str, Any] = {}
    created_at: Optional[datetime] = None


@router.get("", response_model=List[AuditLogResponse])
def get_tenant_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    action_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    tenant_context: TenantContext = Depends(get_tenant_context)
):
    """
    Retrieve chronological audit logs for the current enterprise tenant workspace.
    Enterprise Admins can only view audit logs associated with their own tenant_id.
    """
    return AuditService.list_tenant_logs(
        db=db,
        organization_id=tenant_context.tenant_id,
        limit=limit,
        action=action_filter
    )
