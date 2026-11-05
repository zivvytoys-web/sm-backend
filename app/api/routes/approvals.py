from typing import List, Optional
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.models.user import User
from app.schemas.approval import ApprovalCreate, ApprovalReview, ApprovalResponse
from app.services.approval_service import ApprovalService
from app.core.permissions import get_current_active_user, require_manager, require_creator
from app.utils.enums import ApprovalStatus

router = APIRouter(prefix="/approvals", tags=["Content Approvals"])


@router.get("/", response_model=List[ApprovalResponse])
def list_approvals(
    status_filter: Optional[ApprovalStatus] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List pending or reviewed content approvals for organization."""
    return ApprovalService.list_approvals(db, current_user.organization_id, status_filter)


@router.post("/request", response_model=ApprovalResponse, status_code=status.HTTP_201_CREATED)
def request_approval(
    payload: ApprovalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Submit post content for manager/admin approval."""
    return ApprovalService.request_approval(db, current_user, payload)


@router.post("/{approval_id}/review", response_model=ApprovalResponse)
def review_approval(
    approval_id: int,
    payload: ApprovalReview,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Approve or reject a content submission."""
    return ApprovalService.review_approval(db, approval_id, current_user, payload)
