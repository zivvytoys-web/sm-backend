from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from app.db.dependencies import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.content import Content
from app.models.campaign import Campaign
from app.models.scheduled_post import ScheduledPost
from app.models.generation_job import GenerationJob
from app.models.social_account import SocialAccount
from app.schemas.user import UserResponse
from app.core.permissions import require_admin
from app.utils.enums import UserRole, ContentStatus, PlatformType

router = APIRouter(prefix="/admin", tags=["Admin Portal"])


class AdminStatsResponse(BaseModel):
    total_users: int
    active_users: int
    inactive_users: int
    total_organizations: int
    total_posts: int
    total_campaigns: int
    total_scheduled_posts: int
    total_generations: int
    total_social_accounts: int
    new_users_last_7_days: int
    new_users_last_30_days: int


class UserStatusUpdate(BaseModel):
    is_active: bool


class UserRoleUpdate(BaseModel):
    role: UserRole


class AdminAnalyticsResponse(BaseModel):
    posts_by_status: dict
    posts_by_platform: dict
    total_published: int
    total_pending_approval: int
    generations_by_type: dict


class AdminSettingsResponse(BaseModel):
    environment: str
    debug_mode: bool
    database_connected: bool
    storage_type: str
    default_role: str
    open_registration: bool
    active_ai_providers: List[str]


class AdminSettingsUpdate(BaseModel):
    open_registration: Optional[bool] = None
    default_role: Optional[str] = None


class AdminIntegrationSettingsResponse(BaseModel):
    meta_app_id: str
    meta_app_secret_configured: bool
    meta_app_secret_masked: str
    google_client_id: str
    google_client_secret_configured: bool
    google_client_secret_masked: str
    twitter_client_id: str
    twitter_client_secret_configured: bool
    twitter_client_secret_masked: str
    encryption_key_configured: bool


class AdminIntegrationSettingsUpdate(BaseModel):
    meta_app_id: Optional[str] = None
    meta_app_secret: Optional[str] = None
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    twitter_client_id: Optional[str] = None
    twitter_client_secret: Optional[str] = None



@router.get("/stats", response_model=AdminStatsResponse)
def get_admin_stats(
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Retrieve platform-wide KPI statistics from MySQL."""
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    total_users = db.query(User).count()
    active_users = db.query(User).filter(User.is_active == True).count()
    inactive_users = total_users - active_users

    total_orgs = db.query(Organization).count()
    total_posts = db.query(Content).count()
    total_campaigns = db.query(Campaign).count()
    total_scheduled = db.query(ScheduledPost).count()
    total_generations = db.query(GenerationJob).count()
    total_accounts = db.query(SocialAccount).count()

    new_7d = db.query(User).filter(User.created_at >= seven_days_ago).count()
    new_30d = db.query(User).filter(User.created_at >= thirty_days_ago).count()

    return AdminStatsResponse(
        total_users=total_users,
        active_users=active_users,
        inactive_users=inactive_users,
        total_organizations=total_orgs,
        total_posts=total_posts,
        total_campaigns=total_campaigns,
        total_scheduled_posts=total_scheduled,
        total_generations=total_generations,
        total_social_accounts=total_accounts,
        new_users_last_7_days=new_7d,
        new_users_last_30_days=new_30d
    )
    

@router.get("/users", response_model=List[UserResponse])
def get_admin_users(
    search: Optional[str] = None,
    role_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """List all registered platform users with search, role, and status filters."""
    query = db.query(User)

    if search:
        search_term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                User.full_name.ilike(search_term),
                User.email.ilike(search_term)
            )
        )

    if role_filter and role_filter != "all":
        query = query.filter(User.role == role_filter)

    if status_filter == "active":
        query = query.filter(User.is_active == True)
    elif status_filter == "inactive":
        query = query.filter(User.is_active == False)

    users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    from app.services.auth_service import AuthService
    for u in users:
        u.assigned_businesses = AuthService.get_user_businesses(db, u)
    return users


@router.put("/users/{user_id}/status", response_model=UserResponse)
def update_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Activate or deactivate a user account."""
    if current_admin.id == user_id and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own administrator account."
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.is_active = payload.is_active
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}/role", response_model=UserResponse)
def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Update a user's permission role."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.role = payload.role
    db.commit()
    db.refresh(user)
    return user


@router.get("/analytics", response_model=AdminAnalyticsResponse)
def get_admin_analytics(
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Retrieve platform-wide content, platform distribution, and AI usage metrics."""
    # Group posts by status
    status_counts = {}
    for st in ContentStatus:
        count = db.query(Content).filter(Content.status == st).count()
        status_counts[st.value] = count

    # Platform distribution across social accounts
    platform_counts = {}
    for plat in PlatformType:
        count = db.query(SocialAccount).filter(SocialAccount.platform == plat).count()
        platform_counts[plat.value] = count

    # Generation jobs by type
    gen_jobs = db.query(GenerationJob.job_type, func.count(GenerationJob.id)).group_by(GenerationJob.job_type).all()
    gen_by_type = {str(job_type.value if hasattr(job_type, "value") else job_type): count for job_type, count in gen_jobs}

    return AdminAnalyticsResponse(
        posts_by_status=status_counts,
        posts_by_platform=platform_counts,
        total_published=status_counts.get("published", 0),
        total_pending_approval=status_counts.get("pending_approval", 0),
        generations_by_type=gen_by_type
    )


@router.get("/settings", response_model=AdminSettingsResponse)
def get_admin_settings(
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Get system and platform governance configuration."""
    from app.core.config import settings
    
    active_ai = []
    if settings.OPENAI_API_KEY:
        active_ai.append("OpenAI (GPT-4o & DALL-E)")
    if settings.GEMINI_API_KEY:
        active_ai.append("Google Gemini (Unified Text, Image & Veo Video Engine)")
    if settings.STABILITY_API_KEY:
        active_ai.append("Stability AI (SDXL)")
    if not active_ai:
        active_ai.append("Local Context Synthesizer (Built-in)")

    return AdminSettingsResponse(
        environment=settings.ENVIRONMENT,
        debug_mode=settings.DEBUG,
        database_connected=True,
        storage_type=settings.STORAGE_TYPE,
        default_role="creator",
        open_registration=True,
        active_ai_providers=active_ai
    )


@router.get("/integrations", response_model=AdminIntegrationSettingsResponse)
def get_admin_integrations(
    current_admin: User = Depends(require_admin)
):
    """Retrieve masked developer credentials and integration statuses (Admin only)."""
    from app.core.config import settings
    from app.core.security import mask_secret

    meta_id = settings.META_APP_ID or settings.FACEBOOK_CLIENT_ID or settings.INSTAGRAM_CLIENT_ID or ""
    meta_secret = settings.META_APP_SECRET or settings.FACEBOOK_CLIENT_SECRET or settings.INSTAGRAM_CLIENT_SECRET or ""
    
    yt_id = settings.YOUTUBE_CLIENT_ID or ""
    yt_secret = settings.YOUTUBE_CLIENT_SECRET or ""

    tw_id = settings.TWITTER_CLIENT_ID or ""
    tw_secret = settings.TWITTER_CLIENT_SECRET or ""

    return AdminIntegrationSettingsResponse(
        meta_app_id=meta_id,
        meta_app_secret_configured=bool(meta_secret),
        meta_app_secret_masked=mask_secret(meta_secret),
        google_client_id=yt_id,
        google_client_secret_configured=bool(yt_secret),
        google_client_secret_masked=mask_secret(yt_secret),
        twitter_client_id=tw_id,
        twitter_client_secret_configured=bool(tw_secret),
        twitter_client_secret_masked=mask_secret(tw_secret),
        encryption_key_configured=bool(settings.ENCRYPTION_KEY or settings.SECRET_KEY)
    )


@router.put("/integrations", response_model=AdminIntegrationSettingsResponse)
def update_admin_integrations(
    payload: AdminIntegrationSettingsUpdate,
    current_admin: User = Depends(require_admin)
):
    """Securely update developer credentials for OAuth integrations (Admin only)."""
    from app.core.config import settings
    from app.core.security import mask_secret

    if payload.meta_app_id is not None:
        settings.META_APP_ID = payload.meta_app_id
        settings.FACEBOOK_CLIENT_ID = payload.meta_app_id
        settings.INSTAGRAM_CLIENT_ID = payload.meta_app_id
    if payload.meta_app_secret is not None and payload.meta_app_secret != "":
        settings.META_APP_SECRET = payload.meta_app_secret
        settings.FACEBOOK_CLIENT_SECRET = payload.meta_app_secret
        settings.INSTAGRAM_CLIENT_SECRET = payload.meta_app_secret

    if payload.google_client_id is not None:
        settings.YOUTUBE_CLIENT_ID = payload.google_client_id
    if payload.google_client_secret is not None and payload.google_client_secret != "":
        settings.YOUTUBE_CLIENT_SECRET = payload.google_client_secret

    if payload.twitter_client_id is not None:
        settings.TWITTER_CLIENT_ID = payload.twitter_client_id
    if payload.twitter_client_secret is not None and payload.twitter_client_secret != "":
        settings.TWITTER_CLIENT_SECRET = payload.twitter_client_secret

    meta_id = settings.META_APP_ID or settings.FACEBOOK_CLIENT_ID or ""
    meta_secret = settings.META_APP_SECRET or settings.FACEBOOK_CLIENT_SECRET or ""
    yt_id = settings.YOUTUBE_CLIENT_ID or ""
    yt_secret = settings.YOUTUBE_CLIENT_SECRET or ""
    tw_id = settings.TWITTER_CLIENT_ID or ""
    tw_secret = settings.TWITTER_CLIENT_SECRET or ""

    return AdminIntegrationSettingsResponse(
        meta_app_id=meta_id,
        meta_app_secret_configured=bool(meta_secret),
        meta_app_secret_masked=mask_secret(meta_secret),
        google_client_id=yt_id,
        google_client_secret_configured=bool(yt_secret),
        google_client_secret_masked=mask_secret(yt_secret),
        twitter_client_id=tw_id,
        twitter_client_secret_configured=bool(tw_secret),
        twitter_client_secret_masked=mask_secret(tw_secret),
        encryption_key_configured=bool(settings.ENCRYPTION_KEY or settings.SECRET_KEY)
    )


# ----------------------------------------------------------------------
# Multi-Platform Social Media Integration Management (Admin Only)
# ----------------------------------------------------------------------
from app.schemas.platform_integration import (
    PlatformIntegrationResponse,
    PlatformIntegrationUpdate,
    TestIntegrationRequest,
    TestIntegrationResponse
)
from app.services.platform_integration_service import PlatformIntegrationService


@router.get("/integrations/platforms", response_model=List[PlatformIntegrationResponse])
def list_platform_integrations(
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """List all supported social media platform configurations with masked secrets (Admin only)."""
    return PlatformIntegrationService.get_all_platforms(db)


@router.get("/integrations/platforms/{platform}", response_model=PlatformIntegrationResponse)
def get_platform_integration(
    platform: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Retrieve detailed configuration for a specific social media platform (Admin only)."""
    item = PlatformIntegrationService.get_platform(db, platform)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Platform '{platform}' not found.")
    
    from app.core.security import decrypt_token, mask_secret
    raw_secret = decrypt_token(item.client_secret_encrypted)
    raw_api_key = decrypt_token(item.api_key_encrypted)
    raw_api_secret = decrypt_token(item.api_secret_encrypted)

    return PlatformIntegrationResponse(
        id=item.id,
        platform=item.platform,
        display_name=item.display_name,
        is_enabled=item.is_enabled,
        environment=item.environment,
        client_id=item.client_id,
        client_secret_configured=bool(raw_secret),
        client_secret_masked=mask_secret(raw_secret),
        api_key_configured=bool(raw_api_key),
        api_key_masked=mask_secret(raw_api_key),
        api_secret_configured=bool(raw_api_secret),
        api_secret_masked=mask_secret(raw_api_secret),
        auth_url=item.auth_url,
        token_url=item.token_url,
        api_base_url=item.api_base_url,
        api_version=item.api_version,
        redirect_uri=item.redirect_uri,
        scopes=item.scopes or [],
        webhook_url=item.webhook_url,
        config_notes=item.config_notes,
        extra_settings=item.extra_settings or {},
        last_tested_at=item.last_tested_at,
        test_status=item.test_status,
        test_message=item.test_message,
        created_at=item.created_at,
        updated_at=item.updated_at
    )


@router.put("/integrations/platforms/{platform}", response_model=PlatformIntegrationResponse)
def update_platform_integration(
    platform: str,
    payload: PlatformIntegrationUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Update API credentials, endpoints, scopes and settings for a social platform (Admin only)."""
    try:
        return PlatformIntegrationService.update_platform(
            db=db,
            platform_name=platform,
            payload=payload,
            admin_user_id=current_admin.id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


class ToggleIntegrationRequest(BaseModel):
    is_enabled: bool


@router.post("/integrations/platforms/{platform}/toggle", response_model=PlatformIntegrationResponse)
def toggle_platform_integration(
    platform: str,
    payload: ToggleIntegrationRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Enable or disable integration for a social media platform (Admin only)."""
    try:
        return PlatformIntegrationService.toggle_platform(
            db=db,
            platform_name=platform,
            enabled=payload.is_enabled,
            admin_user_id=current_admin.id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/integrations/platforms/{platform}/test", response_model=TestIntegrationResponse)
async def test_platform_integration(
    platform: str,
    payload: Optional[TestIntegrationRequest] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Test credentials, format and connectivity against official provider APIs (Admin only)."""
    try:
        return await PlatformIntegrationService.test_platform_config(
            db=db,
            platform_name=platform,
            test_payload=payload,
            admin_user_id=current_admin.id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ----------------------------------------------------------------------
# Multi-Business & Brand Profile Management (Admin Only)
# ----------------------------------------------------------------------
import uuid
import re
from app.models.brand_profile import BrandProfile
from app.models.user_organization import UserOrganization
from app.models.subscription import Subscription
from app.schemas.business import (
    BusinessCreate,
    BusinessUpdate,
    BusinessResponse,
    UserBusinessAssignmentRequest
)
from app.utils.enums import SubscriptionTier, SubscriptionStatus


def _format_business_response(db: Session, org: Organization) -> dict:
    bp = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
    assignments = db.query(UserOrganization).filter(UserOrganization.organization_id == org.id).all()
    user_ids = [a.user_id for a in assignments]
    users_db = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    user_dict = {u.id: u for u in users_db}

    assigned_users = []
    for a in assignments:
        u = user_dict.get(a.user_id)
        if u:
            assigned_users.append({
                "user_id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": a.role.value if hasattr(a.role, 'value') else str(a.role),
                "is_default": a.is_default,
                "is_active": u.is_active
            })

    brand_profile_dict = None
    if bp:
        brand_profile_dict = {
            "id": bp.id,
            "name": bp.name,
            "industry": bp.industry,
            "tagline": bp.tagline,
            "location": bp.location,
            "timings": bp.timings,
            "logo_url": bp.logo_url,
            "tone_of_voice": bp.tone_of_voice,
            "target_audience": bp.target_audience,
            "brand_guidelines": bp.brand_guidelines,
            "cta": bp.cta,
            "avoid": bp.avoid,
            "key_themes": bp.key_themes or [],
            "hashtags": bp.hashtags or [],
            "color_palette": bp.color_palette or [],
            "meta_info": bp.meta_info or {}
        }

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "description": org.description,
        "logo_url": org.logo_url,
        "is_active": org.is_active,
        "settings": org.settings or {},
        "brand_profile": brand_profile_dict,
        "assigned_users_count": len(assigned_users),
        "assigned_users": assigned_users,
        "created_at": org.created_at.isoformat() if org.created_at else None
    }


@router.get("/businesses", response_model=List[BusinessResponse])
def get_admin_businesses(
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Retrieve all platform businesses, settings, brand profiles and assigned users (Admin only)."""
    query = db.query(Organization)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(or_(Organization.name.ilike(term), Organization.slug.ilike(term)))
    orgs = query.order_by(Organization.id.asc()).all()
    return [_format_business_response(db, org) for org in orgs]


@router.post("/businesses", response_model=BusinessResponse, status_code=status.HTTP_201_CREATED)
def create_admin_business(
    payload: BusinessCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Create a new business with organization settings and linked brand profile (Admin only)."""
    slug = payload.slug
    if not slug:
        slug_base = re.sub(r'[^a-zA-Z0-9]', '-', payload.name.lower()).strip('-')[:50] or 'biz'
        slug = f"{slug_base}-{uuid.uuid4().hex[:6]}"

    existing = db.query(Organization).filter(Organization.slug == slug).first()
    if existing:
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"

    org = Organization(
        name=payload.name,
        slug=slug,
        description=payload.description,
        logo_url=payload.logo_url,
        is_active=True,
        settings=payload.settings or {}
    )
    db.add(org)
    db.flush()

    sub = Subscription(
        organization_id=org.id,
        tier=SubscriptionTier.PRO,
        status=SubscriptionStatus.ACTIVE
    )
    db.add(sub)

    bp_data = payload.brand_profile or {}
    brand = BrandProfile(
        organization_id=org.id,
        name=bp_data.get("name") or payload.name,
        industry=bp_data.get("industry") or (payload.settings or {}).get("industry") or "Retail",
        tagline=bp_data.get("tagline"),
        location=bp_data.get("location") or (payload.settings or {}).get("flagshipAddr"),
        timings=bp_data.get("timings") or (payload.settings or {}).get("timings"),
        logo_url=bp_data.get("logo_url") or payload.logo_url,
        tone_of_voice=bp_data.get("tone_of_voice", "Modern & Engaging"),
        target_audience=bp_data.get("target_audience"),
        brand_guidelines=bp_data.get("brand_guidelines"),
        cta=bp_data.get("cta"),
        avoid=bp_data.get("avoid"),
        key_themes=bp_data.get("key_themes") or ["Product Highlights", "Storytelling"],
        hashtags=bp_data.get("hashtags") or ["#BrandGrowth"],
        color_palette=bp_data.get("color_palette") or ["#4f46e5", "#06b6d4"],
        meta_info=bp_data.get("meta_info") or payload.settings or {}
    )
    db.add(brand)
    db.flush()

    # Assign current admin to this new business
    admin_uo = db.query(UserOrganization).filter(
        UserOrganization.user_id == current_admin.id,
        UserOrganization.organization_id == org.id
    ).first()
    if not admin_uo:
        db.add(UserOrganization(
            user_id=current_admin.id,
            organization_id=org.id,
            role=UserRole.SUPER_ADMIN,
            is_default=False
        ))

    db.commit()
    db.refresh(org)
    return _format_business_response(db, org)


@router.put("/businesses/{business_id}", response_model=BusinessResponse)
def update_admin_business(
    business_id: int,
    payload: BusinessUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Update business details, locations, contact points, and linked brand profile (Admin only)."""
    org = db.query(Organization).filter(Organization.id == business_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Business #{business_id} not found.")

    if payload.name is not None:
        org.name = payload.name
    if payload.slug is not None:
        org.slug = payload.slug
    if payload.description is not None:
        org.description = payload.description
    if payload.logo_url is not None:
        org.logo_url = payload.logo_url
    if payload.is_active is not None:
        org.is_active = payload.is_active
    if payload.settings is not None:
        org.settings = {**(org.settings or {}), **payload.settings}

    if payload.brand_profile is not None:
        bp = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
        bp_data = payload.brand_profile
        if not bp:
            bp = BrandProfile(organization_id=org.id, name=bp_data.get("name") or org.name)
            db.add(bp)
        
        for k, v in bp_data.items():
            if hasattr(bp, k) and k != "id" and k != "organization_id":
                setattr(bp, k, v)
        if "meta_info" in bp_data and isinstance(bp_data["meta_info"], dict):
            bp.meta_info = {**(bp.meta_info or {}), **bp_data["meta_info"]}

    db.commit()
    db.refresh(org)
    return _format_business_response(db, org)


@router.delete("/businesses/{business_id}")
def delete_admin_business(
    business_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Delete a business and its associated brand profile data (Admin only)."""
    active_count = db.query(Organization).filter(Organization.is_active == True).count()
    if active_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the only remaining active business workspace."
        )

    org = db.query(Organization).filter(Organization.id == business_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Business #{business_id} not found.")

    # Reassign users whose active organization is this one to another organization
    other_org = db.query(Organization).filter(Organization.id != business_id, Organization.is_active == True).first()
    if other_org:
        db.query(User).filter(User.organization_id == business_id).update({"organization_id": other_org.id})

    db.delete(org)
    db.commit()
    return {"message": f"Business #{business_id} deleted successfully."}


@router.post("/users/{user_id}/assign-business")
def assign_user_to_business(
    user_id: int,
    payload: UserBusinessAssignmentRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Assign a user to a specific business workspace with an assigned role (Admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User #{user_id} not found.")

    org = db.query(Organization).filter(Organization.id == payload.business_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Business #{payload.business_id} not found.")

    uo = db.query(UserOrganization).filter(
        UserOrganization.user_id == user_id,
        UserOrganization.organization_id == payload.business_id
    ).first()

    assigned_role = payload.role or UserRole.CREATOR

    if uo:
        uo.role = assigned_role
        if payload.is_default:
            db.query(UserOrganization).filter(UserOrganization.user_id == user_id).update({"is_default": False})
            uo.is_default = True
            user.organization_id = payload.business_id
    else:
        is_def = payload.is_default or (user.organization_id is None)
        if is_def:
            db.query(UserOrganization).filter(UserOrganization.user_id == user_id).update({"is_default": False})
            user.organization_id = payload.business_id

        uo = UserOrganization(
            user_id=user_id,
            organization_id=payload.business_id,
            role=assigned_role,
            is_default=is_def
        )
        db.add(uo)

    db.commit()
    db.refresh(user)

    from app.services.auth_service import AuthService
    return {
        "user_id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "assigned_businesses": AuthService.get_user_businesses(db, user)
    }


@router.delete("/users/{user_id}/unassign-business/{business_id}")
def unassign_user_from_business(
    user_id: int,
    business_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """Remove a user's access assignment from a business workspace (Admin only)."""
    uo = db.query(UserOrganization).filter(
        UserOrganization.user_id == user_id,
        UserOrganization.organization_id == business_id
    ).first()
    if not uo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found.")

    user = db.query(User).filter(User.id == user_id).first()
    db.delete(uo)
    db.flush()

    # If this was user's active organization, fallback to another assigned organization
    if user and user.organization_id == business_id:
        remaining = db.query(UserOrganization).filter(UserOrganization.user_id == user_id).first()
        user.organization_id = remaining.organization_id if remaining else None
        if remaining:
            remaining.is_default = True

    db.commit()
    if user:
        db.refresh(user)
        from app.services.auth_service import AuthService
        return {
            "user_id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "assigned_businesses": AuthService.get_user_businesses(db, user)
        }
    return {"message": "Unassigned successfully."}


