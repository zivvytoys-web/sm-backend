"""
Multi-Tenancy Isolation and Context Core Module.
Enforces strict tenant scoping, suspension checks, and role authorization.
"""
from typing import Optional, Any
from fastapi import Depends, HTTPException, Header, status
from sqlalchemy.orm import Session, Query

from app.db.dependencies import get_db
from app.core.permissions import get_current_active_user
from app.models.user import User
from app.models.organization import Organization
from app.models.user_organization import UserOrganization
from app.utils.enums import UserRole


class TenantContext:
    """
    Encapsulates the active tenant workspace context for the current request.
    Provides query filtering and resource access validation helpers.
    """
    def __init__(
        self,
        tenant_id: int,
        organization: Organization,
        user: User,
        is_super_admin: bool,
        is_enterprise_admin: bool
    ):
        self.tenant_id = tenant_id
        self.organization = organization
        self.user = user
        self.is_super_admin = is_super_admin
        self.is_enterprise_admin = is_enterprise_admin

    def filter_query(self, query: Query, model_cls: Any) -> Query:
        """
        Applies tenant isolation to a SQLAlchemy query.
        Enterprise Admins are strictly scoped to their tenant_id.
        Super Admins are scoped if a specific tenant context is active, or left global.
        """
        if hasattr(model_cls, "organization_id"):
            if not self.is_super_admin or self.tenant_id:
                return query.filter(model_cls.organization_id == self.tenant_id)
        return query

    def validate_access(self, resource_org_id: Optional[int]) -> bool:
        """
        Validates that a resource belongs to the current tenant workspace.
        Raises HTTP 403 if an Enterprise Admin attempts cross-tenant access.
        """
        if self.is_super_admin:
            return True
        if resource_org_id is None or resource_org_id != self.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cross-tenant access is strictly prohibited."
            )
        return True

    def assign_tenant(self, instance: Any) -> Any:
        """
        Automatically associates an entity with the active tenant_id.
        """
        if hasattr(instance, "organization_id"):
            setattr(instance, "organization_id", self.tenant_id)
        return instance


def get_tenant_context(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id")
) -> TenantContext:
    """
    FastAPI dependency that resolves, validates, and scopes the current request to a verified tenant.
    Enforces:
    1. Active status check: Suspended tenants cannot perform any operations (HTTP 403).
    2. Tenant isolation: Enterprise Admins can only access their authorized workspace.
    3. Cross-tenant switching protection: Prevents unauthorized tenant switching via headers.
    4. Super Admin platform-wide oversight.
    """
    is_super = current_user.role == UserRole.SUPER_ADMIN
    is_enterprise = not is_super

    target_tenant_id: Optional[int] = None

    if x_tenant_id:
        try:
            target_tenant_id = int(x_tenant_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Tenant-Id header format. Must be an integer."
            )

    if is_super:
        # Super Admin: Target specified tenant or fallback to user's assigned organization
        effective_tenant_id = target_tenant_id or current_user.organization_id
        if not effective_tenant_id:
            first_org = db.query(Organization).order_by(Organization.id.asc()).first()
            effective_tenant_id = first_org.id if first_org else 1

        org = db.query(Organization).filter(Organization.id == effective_tenant_id).first()
        if not org:
            # Fallback if specific org not found
            org = db.query(Organization).first()
            if not org:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No organization exists.")
            effective_tenant_id = org.id

        return TenantContext(
            tenant_id=effective_tenant_id,
            organization=org,
            user=current_user,
            is_super_admin=True,
            is_enterprise_admin=False
        )

    # Enterprise Admin
    effective_tenant_id = current_user.organization_id
    if not effective_tenant_id:
        # Attempt to resolve from user_organizations
        user_org = db.query(UserOrganization).filter(UserOrganization.user_id == current_user.id).first()
        if user_org:
            effective_tenant_id = user_org.organization_id
            current_user.organization_id = effective_tenant_id
            db.commit()

    if not effective_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not assigned to any enterprise tenant workspace."
        )

    # If caller supplied X-Tenant-Id header, verify user has legitimate membership
    if target_tenant_id and target_tenant_id != effective_tenant_id:
        membership = db.query(UserOrganization).filter(
            UserOrganization.user_id == current_user.id,
            UserOrganization.organization_id == target_tenant_id
        ).first()
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cross-tenant access or switching without authorization is prohibited."
            )
        effective_tenant_id = target_tenant_id

    # Retrieve organization record
    org = db.query(Organization).filter(Organization.id == effective_tenant_id).first()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Enterprise tenant #{effective_tenant_id} not found."
        )

    # Tenant suspension enforcement
    if not org.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Enterprise tenant workspace is suspended. Contact Super Administrator."
        )

    return TenantContext(
        tenant_id=effective_tenant_id,
        organization=org,
        user=current_user,
        is_super_admin=False,
        is_enterprise_admin=True
    )
