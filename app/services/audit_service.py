"""
Audit Service Module.
Records and retrieves tenant-scoped and platform-wide audit logs.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog
from app.models.user import User


class AuditService:
    @staticmethod
    def log(
        db: Session,
        action: str,
        entity_type: str,
        entity_id: Optional[int] = None,
        user_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        commit: bool = True
    ) -> AuditLog:
        """
        Records an audit log entry associated with tenant and user context.
        """
        # Fallback to user organization if not provided
        if user_id and not organization_id:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                organization_id = user.organization_id

        entry = AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            organization_id=organization_id,
            ip_address=ip_address,
            details=details or {}
        )
        db.add(entry)
        if commit:
            db.commit()
            db.refresh(entry)
        return entry

    @staticmethod
    def list_tenant_logs(
        db: Session,
        organization_id: int,
        limit: int = 50,
        action: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieves chronological audit logs strictly isolated to the specified tenant.
        """
        query = db.query(AuditLog).filter(AuditLog.organization_id == organization_id)
        if action:
            query = query.filter(AuditLog.action.ilike(f"%{action.strip()}%"))
        logs = query.order_by(AuditLog.id.desc()).limit(limit).all()

        user_ids = [l.user_id for l in logs if l.user_id]
        users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

        results = []
        for log in logs:
            u = users.get(log.user_id)
            results.append({
                "id": log.id,
                "action": log.action,
                "entity_type": log.entity_type,
                "entity_id": log.entity_id,
                "user_id": log.user_id,
                "user_email": u.email if u else None,
                "user_role": u.role.value if u and hasattr(u.role, 'value') else (str(u.role) if u else None),
                "organization_id": log.organization_id,
                "ip_address": log.ip_address,
                "details": log.details or {},
                "created_at": log.created_at
            })
        return results

    @staticmethod
    def list_platform_logs(
        db: Session,
        limit: int = 50,
        action: Optional[str] = None,
        organization_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Platform-wide audit logs for Super Admin oversight.
        """
        query = db.query(AuditLog)
        if organization_id:
            query = query.filter(AuditLog.organization_id == organization_id)
        if action:
            query = query.filter(AuditLog.action.ilike(f"%{action.strip()}%"))
        logs = query.order_by(AuditLog.id.desc()).limit(limit).all()

        user_ids = [l.user_id for l in logs if l.user_id]
        users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

        results = []
        for log in logs:
            u = users.get(log.user_id)
            results.append({
                "id": log.id,
                "action": log.action,
                "entity_type": log.entity_type,
                "entity_id": log.entity_id,
                "user_id": log.user_id,
                "user_email": u.email if u else None,
                "user_role": u.role.value if u and hasattr(u.role, 'value') else (str(u.role) if u else None),
                "organization_id": log.organization_id,
                "ip_address": log.ip_address,
                "details": log.details or {},
                "created_at": log.created_at
            })
        return results
