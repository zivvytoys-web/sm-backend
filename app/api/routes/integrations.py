from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import get_current_active_user, require_manager
from app.core.security import encrypt_token
from app.db.dependencies import get_db
from app.models.user import User
from app.models.workspace_integration import WorkspaceIntegration
from app.schemas.workspace_integration import (
    WorkspaceIntegrationCreate, WorkspaceIntegrationUpdate, WorkspaceIntegrationResponse,
)

router = APIRouter(prefix="/integrations", tags=["Workspace Integrations"])


def _org_id(user: User) -> int:
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="No active workspace is associated with this user.")
    return user.organization_id


def _response(row: WorkspaceIntegration) -> dict:
    return {
        "id": row.id, "organization_id": row.organization_id, "provider": row.provider,
        "client_id": row.client_id, "client_secret_configured": bool(row.client_secret_encrypted),
        "settings": row.settings or {}, "enabled": row.enabled,
        "verification_status": row.verification_status,
        "verification_message": row.verification_message,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


@router.get("/configs", response_model=list[WorkspaceIntegrationResponse])
def list_configs(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return [_response(row) for row in db.query(WorkspaceIntegration).filter(
        WorkspaceIntegration.organization_id == _org_id(user)
    ).order_by(WorkspaceIntegration.provider).all()]


@router.post("/configs", response_model=WorkspaceIntegrationResponse, status_code=201)
def create_config(payload: WorkspaceIntegrationCreate, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    org_id = _org_id(user)
    if db.query(WorkspaceIntegration).filter_by(organization_id=org_id, provider=payload.provider.lower()).first():
        raise HTTPException(status_code=409, detail="A configuration for this provider already exists in the workspace.")
    row = WorkspaceIntegration(
        organization_id=org_id, provider=payload.provider.lower(), client_id=payload.client_id,
        client_secret_encrypted=encrypt_token(payload.client_secret), settings=payload.settings,
        enabled=payload.enabled, created_by=user.id,
    )
    db.add(row); db.commit(); db.refresh(row)
    return _response(row)


@router.put("/configs/{config_id}", response_model=WorkspaceIntegrationResponse)
def update_config(config_id: int, payload: WorkspaceIntegrationUpdate, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    row = db.query(WorkspaceIntegration).filter_by(id=config_id, organization_id=_org_id(user)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Integration configuration not found in this workspace.")
    if payload.client_id is not None: row.client_id = payload.client_id
    if payload.client_secret: row.client_secret_encrypted = encrypt_token(payload.client_secret)
    if payload.settings is not None: row.settings = payload.settings
    if payload.enabled is not None: row.enabled = payload.enabled
    row.verification_status = "untested"
    db.commit(); db.refresh(row)
    return _response(row)


@router.delete("/configs/{config_id}", status_code=204)
def delete_config(config_id: int, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    row = db.query(WorkspaceIntegration).filter_by(id=config_id, organization_id=_org_id(user)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Integration configuration not found in this workspace.")
    db.delete(row); db.commit()


@router.post("/configs/{config_id}/test", response_model=WorkspaceIntegrationResponse)
def test_config(config_id: int, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    row = db.query(WorkspaceIntegration).filter_by(id=config_id, organization_id=_org_id(user)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Integration configuration not found in this workspace.")
    if not row.client_id or not row.client_secret_encrypted:
        row.verification_status = "failed"
        row.verification_message = "Client ID and client secret are required."
    else:
        row.verification_status = "configured"
        row.verification_message = "Credentials are stored securely. Provider OAuth testing occurs when Connect is started."
    db.commit(); db.refresh(row)
    return _response(row)
