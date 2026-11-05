from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.models.notification import Notification
from app.models.user import User
from app.utils.enums import UserRole


class NotificationService:
    @staticmethod
    async def send_email(to_email: str, subject: str, body: str) -> bool:
        logger.info(f"Dispatching notification email to {to_email}: [{subject}]")
        return True

    @staticmethod
    async def send_slack_alert(webhook_url: str, message: str) -> bool:
        logger.info(f"Dispatching Slack webhook notification: {message}")
        return True

    @staticmethod
    def create_notification(
        db: Session,
        title: str,
        message: str,
        event_type: str,
        user_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        severity: str = "info",
        link: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        dedup_key: Optional[str] = None,
    ) -> Optional[Notification]:
        """
        Create a persistent database notification with deduplication protection.
        Safely commits or rolls back subtransaction to never disrupt caller logic.
        """
        try:
            # 1. Deduplication check
            if dedup_key:
                existing = db.query(Notification).filter(Notification.dedup_key == dedup_key).first()
                if existing:
                    logger.debug(f"[Notification] Duplicate skipped for dedup_key: {dedup_key}")
                    return existing

            # 2. Derive organization_id if user_id is provided and org is missing
            if user_id and not organization_id:
                user = db.query(User).filter(User.id == user_id).first()
                if user and user.organization_id:
                    organization_id = user.organization_id

            notification = Notification(
                user_id=user_id,
                organization_id=organization_id,
                title=title,
                message=message,
                event_type=event_type,
                severity=severity if severity in {"info", "success", "warning", "error"} else "info",
                link=link,
                metadata_json=metadata or {},
                is_read=False,
                read_at=None,
                dedup_key=dedup_key,
            )
            db.add(notification)
            db.commit()
            db.refresh(notification)
            logger.info(f"[Notification] Created #{notification.id} [{event_type}] for user={user_id}/org={organization_id}: {title}")
            return notification
        except Exception as e:
            logger.warning(f"[Notification] Failed to create notification: {e}")
            try:
                db.rollback()
            except Exception:
                pass
            return None

    @classmethod
    def _user_scope_filter(cls, user: User):
        """Construct SQLAlchemy filter ensuring users only see their own notifications."""
        # Superadmin with no org sees all system & global alerts + their own
        user_role_str = str(getattr(user, "role", "")).lower()
        if ("super_admin" in user_role_str or "superadmin" in user_role_str) and not getattr(user, "organization_id", None):
            return or_(Notification.user_id == user.id, Notification.user_id.is_(None))

        # Regular user / Admin / Superadmin inside an organization
        if user.organization_id:
            return or_(
                Notification.user_id == user.id,
                and_(Notification.user_id.is_(None), Notification.organization_id == user.organization_id)
            )

        # Standalone user without organization
        return Notification.user_id == user.id

    @classmethod
    def get_user_notifications(
        cls,
        db: Session,
        user: User,
        skip: int = 0,
        limit: int = 50,
        unread_only: bool = False
    ) -> List[Notification]:
        """Fetch notifications scoped strictly to the requesting user."""
        scope = cls._user_scope_filter(user)
        query = db.query(Notification).filter(scope)
        if unread_only:
            query = query.filter(Notification.is_read.is_(False))
        return query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()

    @classmethod
    def get_unread_count(cls, db: Session, user: User) -> int:
        """Count unread notifications scoped to the requesting user."""
        scope = cls._user_scope_filter(user)
        return db.query(Notification).filter(scope, Notification.is_read.is_(False)).count()

    @classmethod
    def get_total_count(cls, db: Session, user: User) -> int:
        """Count total notifications scoped to the requesting user."""
        scope = cls._user_scope_filter(user)
        return db.query(Notification).filter(scope).count()

    @classmethod
    def mark_as_read(cls, db: Session, user: User, notification_id: int) -> Optional[Notification]:
        """Mark a single notification as read if owned by the user."""
        scope = cls._user_scope_filter(user)
        notification = db.query(Notification).filter(Notification.id == notification_id, scope).first()
        if not notification:
            return None

        if not notification.is_read:
            notification.is_read = True
            notification.read_at = datetime.utcnow()
            db.commit()
            db.refresh(notification)
        return notification

    @classmethod
    def mark_all_as_read(cls, db: Session, user: User) -> int:
        """Mark all unread notifications for this user as read."""
        scope = cls._user_scope_filter(user)
        unread = db.query(Notification).filter(scope, Notification.is_read.is_(False)).all()
        now = datetime.utcnow()
        count = len(unread)
        for item in unread:
            item.is_read = True
            item.read_at = now
        if count > 0:
            db.commit()
        return count

    @classmethod
    def delete_notification(cls, db: Session, user: User, notification_id: int) -> bool:
        """Delete a notification if owned by the user."""
        scope = cls._user_scope_filter(user)
        notification = db.query(Notification).filter(Notification.id == notification_id, scope).first()
        if not notification:
            return False
        db.delete(notification)
        db.commit()
        return True
