from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.models.user import User
from app.core.permissions import get_current_active_user
from app.services.notification_service import NotificationService
from app.schemas.notification import (
    NotificationResponse,
    NotificationListResponse,
)

router = APIRouter(prefix="/notifications", tags=["Notifications & Alerts"])


@router.get("", response_model=NotificationListResponse)
def get_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    unread_only: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve notifications scoped strictly to the authenticated user."""
    items = NotificationService.get_user_notifications(
        db=db,
        user=current_user,
        skip=skip,
        limit=limit,
        unread_only=unread_only
    )
    unread_count = NotificationService.get_unread_count(db=db, user=current_user)
    total_count = NotificationService.get_total_count(db=db, user=current_user)

    return NotificationListResponse(
        items=items,
        unread_count=unread_count,
        total=total_count
    )


@router.get("/unread-count")
def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Lightweight real-time check for unread notification count badge."""
    unread_count = NotificationService.get_unread_count(db=db, user=current_user)
    return {"unread_count": unread_count}


@router.post("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_as_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Mark an individual notification as read."""
    notification = NotificationService.mark_as_read(
        db=db,
        user=current_user,
        notification_id=notification_id
    )
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or access denied."
        )
    return notification


@router.post("/read-all")
def mark_all_notifications_as_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Mark all unread notifications for the user as read."""
    marked_count = NotificationService.mark_all_as_read(db=db, user=current_user)
    return {"marked_count": marked_count, "status": "success"}


@router.delete("/{notification_id}")
def delete_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete a notification belonging to the current user."""
    success = NotificationService.delete_notification(
        db=db,
        user=current_user,
        notification_id=notification_id
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or access denied."
        )
    return {"status": "deleted", "id": notification_id}
