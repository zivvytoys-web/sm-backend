"""
SuperAdmin Master Management & Governance Router.
Provides platform-level executive oversight, full admin & user detail management,
comparative analytics between admin and user activities, and master system controls.
"""
import re
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from app.db.dependencies import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.subscription import Subscription
from app.models.brand_profile import BrandProfile
from app.models.user_organization import UserOrganization
from app.models.content import Content
from app.models.campaign import Campaign
from app.models.scheduled_post import ScheduledPost
from app.models.generation_job import GenerationJob
from app.models.social_account import SocialAccount
from app.models.audit_log import AuditLog
from app.core.permissions import require_super_admin
from app.core.security import get_password_hash
from app.utils.enums import UserRole, ContentStatus, PlatformType, SubscriptionTier, SubscriptionStatus

router = APIRouter(prefix="/superadmin", tags=["SuperAdmin Executive Portal"])


# ---------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------

class SuperAdminStatsResponse(BaseModel):
    total_superadmins: int
    total_admins: int
    total_users: int
    active_admins: int
    inactive_admins: int
    active_users: int
    inactive_users: int
    total_organizations: int
    total_posts: int
    total_published_posts: int
    total_pending_posts: int
    total_scheduled_posts: int
    total_draft_posts: int
    total_failed_posts: int
    total_generation_jobs: int
    total_social_accounts: int
    new_users_last_7_days: int
    new_users_last_30_days: int
    new_admins_last_30_days: int


class AssignedBusinessInfo(BaseModel):
    organization_id: int
    name: str
    slug: Optional[str] = None
    role: str
    is_default: bool


class AdminDetailResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    role: str
    is_active: bool
    is_verified: bool
    organization_id: Optional[int] = None
    organization_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    assigned_businesses: List[AssignedBusinessInfo]
    total_posts_created: int
    total_approvals_reviewed: int
    total_generations_run: int
    subscription_plan: Optional[str] = None
    subscription_status: Optional[str] = None
    subscription_price: Optional[str] = None
    has_brand_profile: bool = False


class UserDetailResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    role: str
    is_active: bool
    is_verified: bool
    organization_id: Optional[int] = None
    organization_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    assigned_businesses: List[AssignedBusinessInfo]
    total_posts_created: int
    total_generations_run: int


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: UserRole = UserRole.CREATOR
    organization_id: Optional[int] = None
    organization_name: Optional[str] = None
    business_name: Optional[str] = None
    is_active: bool = True


class UpdateUserRoleRequest(BaseModel):
    role: UserRole


class UpdateUserStatusRequest(BaseModel):
    is_active: bool


class SuperAdminControls(BaseModel):
    maintenance_mode: bool = False
    open_registration: bool = True
    default_role: str = "creator"
    autonomous_scheduler_active: bool = True
    publishing_worker_active: bool = True
    active_ai_provider: str = "Google Gemini 2.5 (Veo & Imagen 3)"
    max_upload_size_mb: int = 50
    auto_approval_enabled: bool = False


class AuditLogItem(BaseModel):
    id: int
    action: str
    entity_type: str
    entity_id: Optional[int] = None
    user_id: Optional[int] = None
    user_email: Optional[str] = None
    user_role: Optional[str] = None
    ip_address: Optional[str] = None
    details: Dict[str, Any] = {}
    created_at: Optional[datetime] = None


# Persistent runtime controls state
_RUNTIME_CONTROLS = {
    "maintenance_mode": False,
    "open_registration": True,
    "default_role": "creator",
    "autonomous_scheduler_active": True,
    "publishing_worker_active": True,
    "active_ai_provider": "Google Gemini 2.5 (Veo & Imagen 3)",
    "max_upload_size_mb": 50,
    "auto_approval_enabled": False,
}


# ---------------------------------------------------------
# 1. Executive Platform Stats
# ---------------------------------------------------------

@router.get("/stats", response_model=SuperAdminStatsResponse)
def get_superadmin_stats(
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Retrieve top-level platform statistics covering Superadmins, Admins, and Users."""
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    # SuperAdmin counts
    total_superadmins = db.query(User).filter(User.role == UserRole.SUPER_ADMIN).count()

    # Admin counts
    total_admins = db.query(User).filter(User.role == UserRole.ADMIN).count()
    active_admins = db.query(User).filter(User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN]), User.is_active == True).count()
    inactive_admins = (total_admins + total_superadmins) - active_admins

    # Regular user counts (Creator, Manager, Viewer)
    user_roles = [UserRole.CREATOR, UserRole.MANAGER, UserRole.VIEWER]
    total_users = db.query(User).filter(User.role.in_(user_roles)).count()
    active_users = db.query(User).filter(User.role.in_(user_roles), User.is_active == True).count()
    inactive_users = total_users - active_users

    total_orgs = db.query(Organization).count()

    # Content post breakdowns
    total_posts = db.query(Content).count()
    total_published = db.query(Content).filter(Content.status == ContentStatus.PUBLISHED).count()
    total_pending = db.query(Content).filter(Content.status == ContentStatus.PENDING_APPROVAL).count()
    total_scheduled = db.query(Content).filter(Content.status == ContentStatus.SCHEDULED).count()
    total_drafts = db.query(Content).filter(Content.status == ContentStatus.DRAFT).count()
    total_failed = db.query(Content).filter(Content.status == ContentStatus.FAILED).count()

    total_jobs = db.query(GenerationJob).count()
    allowed_superadmin_platforms = [PlatformType.YOUTUBE, PlatformType.FACEBOOK, PlatformType.INSTAGRAM]
    total_social_accounts = db.query(SocialAccount).filter(SocialAccount.platform.in_(allowed_superadmin_platforms)).count()

    new_users_7d = db.query(User).filter(User.role.in_(user_roles), User.created_at >= seven_days_ago).count()
    new_users_30d = db.query(User).filter(User.role.in_(user_roles), User.created_at >= thirty_days_ago).count()
    new_admins_30d = db.query(User).filter(User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN]), User.created_at >= thirty_days_ago).count()

    return SuperAdminStatsResponse(
        total_superadmins=total_superadmins,
        total_admins=total_admins,
        total_users=total_users,
        active_admins=active_admins,
        inactive_admins=inactive_admins,
        active_users=active_users,
        inactive_users=inactive_users,
        total_organizations=total_orgs,
        total_posts=total_posts,
        total_published_posts=total_published,
        total_pending_posts=total_pending,
        total_scheduled_posts=total_scheduled,
        total_draft_posts=total_drafts,
        total_failed_posts=total_failed,
        total_generation_jobs=total_jobs,
        total_social_accounts=total_social_accounts,
        new_users_last_7_days=new_users_7d,
        new_users_last_30_days=new_users_30d,
        new_admins_last_30_days=new_admins_30d
    )


# ---------------------------------------------------------
# 2. Admin Details Management
# ---------------------------------------------------------

@router.get("/admins", response_model=List[AdminDetailResponse])
def get_all_admins(
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Retrieve full details of all Administrators and SuperAdmins."""
    query = db.query(User).filter(User.role.in_([UserRole.SUPER_ADMIN, UserRole.ADMIN]))

    if search:
        st = f"%{search.strip()}%"
        query = query.filter(or_(User.full_name.ilike(st), User.email.ilike(st)))

    if status_filter == "active":
        query = query.filter(User.is_active == True)
    elif status_filter == "inactive":
        query = query.filter(User.is_active == False)

    admins = query.order_by(User.role.asc(), User.created_at.desc()).all()

    # Pre-fetch organizations for fast mapping
    all_orgs = {o.id: o for o in db.query(Organization).all()}

    results = []
    for adm in admins:
        # Assigned businesses
        assignments = db.query(UserOrganization).filter(UserOrganization.user_id == adm.id).all()
        assigned_list = []
        for a in assignments:
            org = all_orgs.get(a.organization_id)
            if org:
                assigned_list.append(AssignedBusinessInfo(
                    organization_id=org.id,
                    name=org.name,
                    slug=org.slug,
                    role=a.role.value if hasattr(a.role, 'value') else str(a.role),
                    is_default=bool(a.is_default)
                ))

        # Calculate posts created by admin or inside admin's organization brand profile
        if adm.organization_id:
            bp_ids = [b[0] for b in db.query(BrandProfile.id).filter(BrandProfile.organization_id == adm.organization_id).all()]
            if bp_ids:
                posts_count = db.query(Content).filter(
                    or_(Content.creator_id == adm.id, Content.brand_profile_id.in_(bp_ids))
                ).count()
            else:
                posts_count = db.query(Content).filter(Content.creator_id == adm.id).count()
        else:
            posts_count = db.query(Content).filter(Content.creator_id == adm.id).count()

        approvals_reviewed = db.query(AuditLog).filter(
            AuditLog.user_id == adm.id,
            AuditLog.action.in_(["approve_post", "reject_post", "content_approved", "content_rejected"])
        ).count()
        gen_count = db.query(GenerationJob).filter(GenerationJob.user_id == adm.id).count()

        # Subscription details
        sub_plan = "Pro Growth Plan"
        sub_status = "Active"
        sub_price = "$79 / month"
        has_bp = False
        org_name = None

        if adm.organization_id:
            org = all_orgs.get(adm.organization_id)
            if org:
                org_name = org.name
                sub = db.query(Subscription).filter(Subscription.organization_id == org.id).first()
                if sub:
                    tier_str = sub.tier.value if hasattr(sub.tier, 'value') else str(sub.tier)
                    tier_map = {
                        "FREE": ("Starter Boutique", "$29 / month"),
                        "STARTER": ("Growth Starter Plan", "$29 / month"),
                        "PRO": ("Pro Growth Plan", "$79 / month"),
                        "ENTERPRISE": ("Enterprise Scale Plan", "$199 / month")
                    }
                    p_name, p_price = tier_map.get(tier_str.upper(), (f"{tier_str.title()} Plan", "$79 / month"))
                    sub_plan = p_name
                    sub_price = p_price
                    sub_status = sub.status.value if hasattr(sub.status, 'value') else str(sub.status).capitalize()

                bp = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
                has_bp = bp is not None

        results.append(AdminDetailResponse(
            id=adm.id,
            email=adm.email,
            full_name=adm.full_name,
            role=adm.role.value if hasattr(adm.role, 'value') else str(adm.role),
            is_active=adm.is_active,
            is_verified=adm.is_verified,
            organization_id=adm.organization_id,
            organization_name=org_name,
            created_at=adm.created_at,
            updated_at=adm.updated_at,
            assigned_businesses=assigned_list,
            total_posts_created=posts_count,
            total_approvals_reviewed=approvals_reviewed,
            total_generations_run=gen_count,
            subscription_plan=sub_plan,
            subscription_status=sub_status,
            subscription_price=sub_price,
            has_brand_profile=has_bp
        ))

    return results


# ---------------------------------------------------------
# 3. Regular User Details Management
# ---------------------------------------------------------

@router.get("/users", response_model=List[UserDetailResponse])
def get_all_users(
    search: Optional[str] = None,
    role_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    organization_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Retrieve full details of all Regular Users (Creators, Managers, Viewers)."""
    user_roles = [UserRole.CREATOR, UserRole.MANAGER, UserRole.VIEWER]
    query = db.query(User).filter(User.role.in_(user_roles))

    if search:
        st = f"%{search.strip()}%"
        query = query.filter(or_(User.full_name.ilike(st), User.email.ilike(st)))

    if role_filter and role_filter != "all":
        query = query.filter(User.role == role_filter)

    if status_filter == "active":
        query = query.filter(User.is_active == True)
    elif status_filter == "inactive":
        query = query.filter(User.is_active == False)

    if organization_id:
        query = query.filter(User.organization_id == organization_id)

    users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()

    all_orgs = {o.id: o for o in db.query(Organization).all()}

    results = []
    for u in users:
        assignments = db.query(UserOrganization).filter(UserOrganization.user_id == u.id).all()
        assigned_list = []
        for a in assignments:
            org = all_orgs.get(a.organization_id)
            if org:
                assigned_list.append(AssignedBusinessInfo(
                    organization_id=org.id,
                    name=org.name,
                    slug=org.slug,
                    role=a.role.value if hasattr(a.role, 'value') else str(a.role),
                    is_default=bool(a.is_default)
                ))

        active_org = all_orgs.get(u.organization_id)
        posts_count = db.query(Content).filter(Content.creator_id == u.id).count()
        gen_count = db.query(GenerationJob).filter(GenerationJob.user_id == u.id).count()

        results.append(UserDetailResponse(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=u.role.value if hasattr(u.role, 'value') else str(u.role),
            is_active=u.is_active,
            is_verified=u.is_verified,
            organization_id=u.organization_id,
            organization_name=active_org.name if active_org else None,
            created_at=u.created_at,
            updated_at=u.updated_at,
            assigned_businesses=assigned_list,
            total_posts_created=posts_count,
            total_generations_run=gen_count
        ))

    return results


# ---------------------------------------------------------
# 4. SuperAdmin User / Admin Creation & Role Management
# ---------------------------------------------------------

@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user_or_admin(
    payload: CreateUserRequest,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Directly provision a new Admin or User account with organizational assignment."""
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User with email '{payload.email}' already exists."
        )

    org_id = payload.organization_id
    target_org_name = (payload.organization_name or payload.business_name or "").strip()

    if target_org_name:
        # Match case-insensitively or exact
        matched_org = db.query(Organization).filter(
            func.lower(Organization.name) == target_org_name.lower()
        ).first()
        if not matched_org:
            clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', target_org_name).lower()[:90] or "workspace"
            base_slug = clean_slug
            counter = 1
            while db.query(Organization).filter(Organization.slug == clean_slug).first():
                clean_slug = f"{base_slug}_{counter}"
                counter += 1
            matched_org = Organization(
                name=target_org_name,
                slug=clean_slug,
                is_active=True,
                settings={"category": "Retail & Social AI"}
            )
            db.add(matched_org)
            db.flush()
        org_id = matched_org.id
    elif not org_id:
        # Default to first organization
        first_org = db.query(Organization).filter(Organization.is_active == True).first()
        org_id = first_org.id if first_org else None

    new_user = User(
        email=payload.email,
        hashed_password=get_password_hash(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        organization_id=org_id,
        is_active=payload.is_active,
        is_verified=True
    )
    db.add(new_user)
    db.flush()

    if org_id:
        db.add(UserOrganization(
            user_id=new_user.id,
            organization_id=org_id,
            role=payload.role,
            is_default=True
        ))

    # Log action in AuditLog
    db.add(AuditLog(
        action="superadmin_create_account",
        entity_type="user",
        entity_id=new_user.id,
        user_id=current_superadmin.id,
        details={
            "created_email": new_user.email,
            "created_role": new_user.role.value if hasattr(new_user.role, 'value') else str(new_user.role),
            "created_by": current_superadmin.email
        }
    ))

    db.commit()
    db.refresh(new_user)

    return {
        "message": f"Account '{new_user.email}' created successfully with role '{new_user.role.value}'.",
        "id": new_user.id,
        "email": new_user.email,
        "role": new_user.role.value
    }


@router.put("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    payload: UpdateUserRoleRequest,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Update role for any user (promote user to Admin/SuperAdmin, or demote)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    # Guard: prevent demoting oneself if you are the only super_admin
    if current_superadmin.id == user_id and payload.role != UserRole.SUPER_ADMIN:
        super_count = db.query(User).filter(User.role == UserRole.SUPER_ADMIN, User.is_active == True).count()
        if super_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the only remaining active SuperAdmin."
            )

    old_role = user.role.value if hasattr(user.role, 'value') else str(user.role)
    user.role = payload.role

    # Update assigned organization roles
    db.query(UserOrganization).filter(UserOrganization.user_id == user_id).update({"role": payload.role})

    db.add(AuditLog(
        action="superadmin_update_role",
        entity_type="user",
        entity_id=user.id,
        user_id=current_superadmin.id,
        details={"old_role": old_role, "new_role": payload.role.value, "target_email": user.email}
    ))

    db.commit()
    db.refresh(user)

    return {
        "message": f"User '{user.email}' role updated to '{user.role.value}'.",
        "user_id": user.id,
        "email": user.email,
        "role": user.role.value
    }


@router.put("/users/{user_id}/status")
def update_user_status(
    user_id: int,
    payload: UpdateUserStatusRequest,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Activate or deactivate any user or administrator account."""
    if current_superadmin.id == user_id and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own SuperAdmin account."
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.is_active = payload.is_active

    db.add(AuditLog(
        action="superadmin_update_status",
        entity_type="user",
        entity_id=user.id,
        user_id=current_superadmin.id,
        details={"is_active": payload.is_active, "target_email": user.email}
    ))

    db.commit()
    db.refresh(user)

    return {
        "message": f"User '{user.email}' status updated to {'Active' if user.is_active else 'Deactivated'}.",
        "user_id": user.id,
        "is_active": user.is_active
    }


@router.delete("/users/{user_id}")
def delete_user_or_admin(
    user_id: int,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Permanently delete a user or administrator account."""
    if current_superadmin.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own SuperAdmin account."
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    target_email = user.email
    db.delete(user)

    db.add(AuditLog(
        action="superadmin_delete_user",
        entity_type="user",
        entity_id=user_id,
        user_id=current_superadmin.id,
        details={"deleted_email": target_email}
    ))

    db.commit()
    return {"message": f"Account '{target_email}' (ID #{user_id}) permanently removed."}


# ---------------------------------------------------------
# 5. Comparative Analytics: Admin vs User
# ---------------------------------------------------------

@router.get("/analytics")
def get_superadmin_analytics(
    timeframe: str = Query("30d", pattern="^(7d|30d|90d|all)$"),
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """
    Retrieve comparative analytics contrasting Admin operations against User/Creator activities,
    including content generation rates, post status ratios, platform breakdown, and top performers.
    """
    # 1. Admin operations summary
    admins = db.query(User).filter(User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN])).all()
    admin_ids = [a.id for a in admins]

    admin_post_count = db.query(Content).filter(Content.creator_id.in_(admin_ids)).count() if admin_ids else 0
    admin_gen_count = db.query(GenerationJob).filter(GenerationJob.user_id.in_(admin_ids)).count() if admin_ids else 0

    # 2. User operations summary
    user_roles = [UserRole.CREATOR, UserRole.MANAGER, UserRole.VIEWER]
    users = db.query(User).filter(User.role.in_(user_roles)).all()
    user_ids = [u.id for u in users]

    user_post_count = db.query(Content).filter(Content.creator_id.in_(user_ids)).count() if user_ids else 0
    user_gen_count = db.query(GenerationJob).filter(GenerationJob.user_id.in_(user_ids)).count() if user_ids else 0

    # 3. Post status breakdown
    post_status_dist = {}
    for st in ContentStatus:
        count = db.query(Content).filter(Content.status == st).count()
        post_status_dist[st.value] = count

    # 4. Social platform breakdown (strictly YouTube, Facebook, Instagram)
    allowed_platforms = [PlatformType.YOUTUBE, PlatformType.FACEBOOK, PlatformType.INSTAGRAM]
    platform_dist = {}
    for plat in allowed_platforms:
        count = db.query(SocialAccount).filter(SocialAccount.platform == plat).count()
        platform_dist[plat.value] = count

    # 4b. Timeline trend series for SuperAdmin Line Chart (dynamic data across timeframe)
    days_map = {"7d": 7, "30d": 30, "90d": 90, "all": 30}
    num_days = days_map.get(timeframe, 30)
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=num_days - 1)

    contents_in_range = db.query(Content.created_at, Content.target_platforms).filter(Content.created_at >= start_date).all()
    jobs_in_range = db.query(GenerationJob.created_at).filter(GenerationJob.created_at >= start_date).all()

    timeline_dict = {}
    cur = start_date
    while cur <= today:
        d_str = cur.strftime("%Y-%m-%d")
        timeline_dict[d_str] = {
            "date": d_str,
            "label": cur.strftime("%b %d"),
            "youtube": 0,
            "facebook": 0,
            "instagram": 0,
            "total_posts": 0,
            "generations": 0
        }
        cur += timedelta(days=1)

    for c in contents_in_range:
        if c[0]:
            d_str = c[0].date().strftime("%Y-%m-%d")
            if d_str in timeline_dict:
                timeline_dict[d_str]["total_posts"] += 1
                plats = [str(p).lower() for p in (c[1] or [])]
                if any("youtube" in p for p in plats):
                    timeline_dict[d_str]["youtube"] += 1
                if any("facebook" in p for p in plats):
                    timeline_dict[d_str]["facebook"] += 1
                if any("instagram" in p for p in plats):
                    timeline_dict[d_str]["instagram"] += 1

    for j in jobs_in_range:
        if j[0]:
            d_str = j[0].date().strftime("%Y-%m-%d")
            if d_str in timeline_dict:
                timeline_dict[d_str]["generations"] += 1

    activity_timeline = list(timeline_dict.values())

    # 5. Generation jobs by type
    gen_jobs = db.query(GenerationJob.job_type, func.count(GenerationJob.id)).group_by(GenerationJob.job_type).all()
    gen_by_type = {str(job_type.value if hasattr(job_type, "value") else job_type): count for job_type, count in gen_jobs}

    # 6. Top Creators / Regular Users by output
    top_creators_query = db.query(
        User.id,
        User.full_name,
        User.email,
        func.count(Content.id).label("post_count")
    ).join(Content, Content.creator_id == User.id)\
     .filter(User.role.in_(user_roles))\
     .group_by(User.id, User.full_name, User.email)\
     .order_by(func.count(Content.id).desc())\
     .limit(5).all()

    top_creators = [
        {"user_id": r[0], "name": r[1] or r[2].split('@')[0], "email": r[2], "posts": r[3]}
        for r in top_creators_query
    ]

    # 7. Top Active Admins
    top_admins_query = db.query(
        User.id,
        User.full_name,
        User.email,
        User.role,
        func.count(UserOrganization.id).label("org_count")
    ).outerjoin(UserOrganization, UserOrganization.user_id == User.id)\
     .filter(User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN]))\
     .group_by(User.id, User.full_name, User.email, User.role)\
     .order_by(func.count(UserOrganization.id).desc())\
     .limit(5).all()

    top_admins = [
        {"admin_id": r[0], "name": r[1] or r[2].split('@')[0], "email": r[2], "role": r[3].value if hasattr(r[3], 'value') else str(r[3]), "assigned_organizations": r[4]}
        for r in top_admins_query
    ]

    return {
        "admin_summary": {
            "total_admins": len(admins),
            "posts_created_by_admins": admin_post_count,
            "generations_by_admins": admin_gen_count,
        },
        "user_summary": {
            "total_users": len(users),
            "posts_created_by_users": user_post_count,
            "generations_by_users": user_gen_count,
        },
        "ratio_user_to_admin_posts": round(user_post_count / max(admin_post_count, 1), 2),
        "post_status_distribution": post_status_dist,
        "platform_distribution": platform_dist,
        "activity_timeline": activity_timeline,
        "generation_types_distribution": gen_by_type,
        "top_active_creators": top_creators,
        "top_managing_admins": top_admins,
        "timeframe": timeframe
    }


# ---------------------------------------------------------
# 6. Platform Governance & Master Controls
# ---------------------------------------------------------

@router.get("/controls", response_model=SuperAdminControls)
def get_system_controls(
    current_superadmin: User = Depends(require_super_admin)
):
    """Retrieve global platform governance toggles and engine states."""
    return SuperAdminControls(**_RUNTIME_CONTROLS)


@router.put("/controls", response_model=SuperAdminControls)
def update_system_controls(
    payload: SuperAdminControls,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Update global system controls (maintenance mode, auto-scheduling, AI provider limits)."""
    global _RUNTIME_CONTROLS
    _RUNTIME_CONTROLS.update(payload.dict())

    db.add(AuditLog(
        action="superadmin_update_system_controls",
        entity_type="system",
        entity_id=0,
        user_id=current_superadmin.id,
        details=payload.dict()
    ))
    db.commit()

    return SuperAdminControls(**_RUNTIME_CONTROLS)


# ---------------------------------------------------------
# 7. Platform Audit Logs
# ---------------------------------------------------------

@router.get("/audit-logs", response_model=List[AuditLogItem])
def get_platform_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    action_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """View chronological platform-wide audit log trail for governance and security."""
    query = db.query(AuditLog)
    if action_filter:
        query = query.filter(AuditLog.action.ilike(f"%{action_filter.strip()}%"))

    logs = query.order_by(AuditLog.id.desc()).limit(limit).all()

    user_ids = [l.user_id for l in logs if l.user_id]
    users_db = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    results = []
    for log in logs:
        u = users_db.get(log.user_id)
        results.append(AuditLogItem(
            id=log.id,
            action=log.action,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            user_id=log.user_id,
            user_email=u.email if u else None,
            user_role=u.role.value if u and hasattr(u.role, 'value') else (str(u.role) if u else None),
            ip_address=log.ip_address,
            details=log.details or {},
            created_at=log.created_at
        ))

    return results


# ---------------------------------------------------------
# 8. Multi-Tenant Enterprise Management & Isolation Control
# ---------------------------------------------------------

class CreateTenantRequest(BaseModel):
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    admin_email: Optional[EmailStr] = None
    admin_password: Optional[str] = None
    admin_full_name: Optional[str] = None
    subscription_tier: Optional[SubscriptionTier] = SubscriptionTier.FREE


class UpdateTenantSubscriptionRequest(BaseModel):
    tier: SubscriptionTier
    status: Optional[SubscriptionStatus] = SubscriptionStatus.ACTIVE
    monthly_generation_limit: Optional[int] = None
    monthly_posts_limit: Optional[int] = None


class TenantDetailResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None
    total_users: int = 0
    total_posts: int = 0
    subscription_tier: Optional[str] = None
    subscription_status: Optional[str] = None
    admins: List[Dict[str, Any]] = []


@router.get("/tenants", response_model=List[TenantDetailResponse])
def get_all_tenants(
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Platform-wide listing of all enterprise tenants with metrics and subscription statuses."""
    query = db.query(Organization)
    if search:
        st = f"%{search.strip()}%"
        query = query.filter(or_(Organization.name.ilike(st), Organization.slug.ilike(st)))
    if status_filter == "active":
        query = query.filter(Organization.is_active == True)
    elif status_filter == "suspended":
        query = query.filter(Organization.is_active == False)

    orgs = query.order_by(Organization.id.asc()).all()
    results = []
    for org in orgs:
        user_count = db.query(User).filter(User.organization_id == org.id).count()
        post_count = db.query(Content).filter(Content.organization_id == org.id).count()
        sub = db.query(Subscription).filter(Subscription.organization_id == org.id).first()
        admin_users = db.query(User).filter(
            User.organization_id == org.id,
            User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN])
        ).all()
        admins_data = [{"id": a.id, "email": a.email, "full_name": a.full_name} for a in admin_users]
        results.append(TenantDetailResponse(
            id=org.id,
            name=org.name,
            slug=org.slug,
            description=org.description,
            logo_url=org.logo_url,
            is_active=org.is_active,
            created_at=org.created_at,
            total_users=user_count,
            total_posts=post_count,
            subscription_tier=sub.tier.value if (sub and hasattr(sub.tier, 'value')) else (str(sub.tier) if sub else None),
            subscription_status=sub.status.value if (sub and hasattr(sub.status, 'value')) else (str(sub.status) if sub else None),
            admins=admins_data
        ))
    return results


@router.post("/tenants", response_model=TenantDetailResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: CreateTenantRequest,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Create a new enterprise tenant workspace with isolated storage and optional initial Enterprise Admin."""
    clean_slug = (payload.slug or re.sub(r'[^a-zA-Z0-9_-]', '_', payload.name.lower())).strip('_')
    existing = db.query(Organization).filter(or_(Organization.name == payload.name, Organization.slug == clean_slug)).first()
    if existing:
        clean_slug = f"{clean_slug}_{int(datetime.utcnow().timestamp())}"

    org = Organization(
        name=payload.name,
        slug=clean_slug,
        description=payload.description,
        is_active=True,
        settings={"category": "Enterprise Workspace"}
    )
    db.add(org)
    db.flush()

    # Create default subscription
    limits = {
        SubscriptionTier.FREE: (50, 100),
        SubscriptionTier.STARTER: (200, 500),
        SubscriptionTier.PRO: (1000, 2500),
        SubscriptionTier.ENTERPRISE: (10000, 50000)
    }
    gen_lim, post_lim = limits.get(payload.subscription_tier, (50, 100))
    sub = Subscription(
        organization_id=org.id,
        tier=payload.subscription_tier,
        status=SubscriptionStatus.ACTIVE,
        monthly_generation_limit=gen_lim,
        monthly_posts_limit=post_lim
    )
    db.add(sub)

    # Optional initial Enterprise Admin
    admins_data = []
    if payload.admin_email and payload.admin_password:
        existing_user = db.query(User).filter(User.email == payload.admin_email).first()
        if existing_user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"User with email '{payload.admin_email}' already exists.")
        admin_user = User(
            email=payload.admin_email,
            hashed_password=get_password_hash(payload.admin_password),
            full_name=payload.admin_full_name or payload.name + " Admin",
            role=UserRole.ADMIN,
            organization_id=org.id,
            is_active=True,
            is_verified=True
        )
        db.add(admin_user)
        db.flush()
        db.add(UserOrganization(
            user_id=admin_user.id,
            organization_id=org.id,
            role=UserRole.ADMIN,
            is_default=True
        ))
        admins_data.append({"id": admin_user.id, "email": admin_user.email, "full_name": admin_user.full_name})

    db.add(AuditLog(
        action="superadmin_create_tenant",
        entity_type="tenant",
        entity_id=org.id,
        organization_id=org.id,
        user_id=current_superadmin.id,
        details={"tenant_name": org.name, "slug": org.slug, "created_by": current_superadmin.email}
    ))
    db.commit()
    db.refresh(org)

    return TenantDetailResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        description=org.description,
        logo_url=org.logo_url,
        is_active=org.is_active,
        created_at=org.created_at,
        total_users=len(admins_data),
        total_posts=0,
        subscription_tier=sub.tier.value if hasattr(sub.tier, 'value') else str(sub.tier),
        subscription_status=sub.status.value if hasattr(sub.status, 'value') else str(sub.status),
        admins=admins_data
    )


@router.get("/tenants/{tenant_id}", response_model=TenantDetailResponse)
def get_tenant_details(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Retrieve complete metadata and resource statistics for a single enterprise tenant."""
    org = db.query(Organization).filter(Organization.id == tenant_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant #{tenant_id} not found.")

    user_count = db.query(User).filter(User.organization_id == org.id).count()
    post_count = db.query(Content).filter(Content.organization_id == org.id).count()
    sub = db.query(Subscription).filter(Subscription.organization_id == org.id).first()
    admin_users = db.query(User).filter(
        User.organization_id == org.id,
        User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN])
    ).all()
    admins_data = [{"id": a.id, "email": a.email, "full_name": a.full_name} for a in admin_users]

    return TenantDetailResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        description=org.description,
        logo_url=org.logo_url,
        is_active=org.is_active,
        created_at=org.created_at,
        total_users=user_count,
        total_posts=post_count,
        subscription_tier=sub.tier.value if (sub and hasattr(sub.tier, 'value')) else (str(sub.tier) if sub else None),
        subscription_status=sub.status.value if (sub and hasattr(sub.status, 'value')) else (str(sub.status) if sub else None),
        admins=admins_data
    )


@router.put("/tenants/{tenant_id}/suspend")
def suspend_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Suspend an enterprise tenant workspace, immediately revoking API access for all its users."""
    org = db.query(Organization).filter(Organization.id == tenant_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant #{tenant_id} not found.")

    org.is_active = False

    db.add(AuditLog(
        action="superadmin_suspend_tenant",
        entity_type="tenant",
        entity_id=org.id,
        organization_id=org.id,
        user_id=current_superadmin.id,
        details={"tenant_name": org.name, "action": "suspend", "suspended_by": current_superadmin.email}
    ))
    db.commit()
    db.refresh(org)

    return {
        "message": f"Enterprise tenant '{org.name}' (ID #{org.id}) has been suspended.",
        "tenant_id": org.id,
        "is_active": org.is_active
    }


@router.put("/tenants/{tenant_id}/activate")
def activate_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Activate / unsuspend an enterprise tenant workspace, restoring API access."""
    org = db.query(Organization).filter(Organization.id == tenant_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant #{tenant_id} not found.")

    org.is_active = True

    db.add(AuditLog(
        action="superadmin_activate_tenant",
        entity_type="tenant",
        entity_id=org.id,
        organization_id=org.id,
        user_id=current_superadmin.id,
        details={"tenant_name": org.name, "action": "activate", "activated_by": current_superadmin.email}
    ))
    db.commit()
    db.refresh(org)

    return {
        "message": f"Enterprise tenant '{org.name}' (ID #{org.id}) has been activated.",
        "tenant_id": org.id,
        "is_active": org.is_active
    }


@router.put("/tenants/{tenant_id}/subscription")
def update_tenant_subscription(
    tenant_id: int,
    payload: UpdateTenantSubscriptionRequest,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Update subscription plan, generation limits, and status for an enterprise tenant."""
    org = db.query(Organization).filter(Organization.id == tenant_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant #{tenant_id} not found.")

    sub = db.query(Subscription).filter(Subscription.organization_id == tenant_id).first()
    if not sub:
        sub = Subscription(organization_id=tenant_id, tier=payload.tier, status=payload.status or SubscriptionStatus.ACTIVE)
        db.add(sub)
    else:
        sub.tier = payload.tier
        if payload.status:
            sub.status = payload.status

    if payload.monthly_generation_limit is not None:
        sub.monthly_generation_limit = payload.monthly_generation_limit
    if payload.monthly_posts_limit is not None:
        sub.monthly_posts_limit = payload.monthly_posts_limit

    db.add(AuditLog(
        action="superadmin_update_subscription",
        entity_type="tenant",
        entity_id=org.id,
        organization_id=org.id,
        user_id=current_superadmin.id,
        details={
            "tenant_name": org.name,
            "new_tier": payload.tier.value if hasattr(payload.tier, 'value') else str(payload.tier),
            "new_status": payload.status.value if hasattr(payload.status, 'value') else str(payload.status)
        }
    ))
    db.commit()
    db.refresh(sub)

    return {
        "message": f"Tenant #{tenant_id} subscription updated to '{sub.tier.value}'.",
        "tenant_id": tenant_id,
        "tier": sub.tier.value,
        "status": sub.status.value,
        "monthly_generation_limit": sub.monthly_generation_limit,
        "monthly_posts_limit": sub.monthly_posts_limit
    }


@router.delete("/tenants/{tenant_id}")
def delete_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_superadmin: User = Depends(require_super_admin)
):
    """Permanently delete an enterprise tenant workspace and all its scoped data."""
    org = db.query(Organization).filter(Organization.id == tenant_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant #{tenant_id} not found.")

    target_name = org.name
    db.delete(org)

    db.add(AuditLog(
        action="superadmin_delete_tenant",
        entity_type="tenant",
        entity_id=tenant_id,
        user_id=current_superadmin.id,
        details={"deleted_tenant_name": target_name, "deleted_by": current_superadmin.email}
    ))
    db.commit()
    return {"message": f"Enterprise tenant '{target_name}' (ID #{tenant_id}) permanently removed."}


__all__ = ["router"]
