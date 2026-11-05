from app.api.routes.auth import router as auth_router
from app.api.routes.users import router as users_router
from app.api.routes.organizations import router as organizations_router
from app.api.routes.brand_profiles import router as brand_profiles_router
from app.api.routes.social_accounts import router as social_accounts_router
from app.api.routes.subscriptions import router as subscriptions_router
from app.api.routes.campaigns import router as campaigns_router
from app.api.routes.content import router as content_router
from app.api.routes.media import router as media_router
from app.api.routes.collections import router as collections_router
from app.api.routes.approvals import router as approvals_router
from app.api.routes.generations import router as generations_router
from app.api.routes.scheduler import router as scheduler_router
from app.api.routes.publishing import router as publishing_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.admin import router as admin_router
from app.api.routes.superadmin import router as superadmin_router
from app.api.routes.autonomous_pipeline import router as autonomous_pipeline_router
from app.api.routes.automation import router as automation_router
from app.api.routes.facebook_oauth import router as facebook_oauth_router, meta_router
from app.api.routes.webhooks import router as webhooks_router
from app.api.routes.audit_logs import router as audit_logs_router
from app.api.routes.quality_check import router as quality_check_router
from app.api.routes.notifications import router as notifications_router

__all__ = [
    "auth_router",
    "users_router",
    "organizations_router",
    "brand_profiles_router",
    "social_accounts_router",
    "subscriptions_router",
    "campaigns_router",
    "content_router",
    "media_router",
    "collections_router",
    "approvals_router",
    "generations_router",
    "scheduler_router",
    "publishing_router",
    "analytics_router",
    "admin_router",
    "superadmin_router",
    "autonomous_pipeline_router",
    "automation_router",
    "facebook_oauth_router",
    "meta_router",
    "webhooks_router",
    "audit_logs_router",
    "quality_check_router",
    "notifications_router",
]

