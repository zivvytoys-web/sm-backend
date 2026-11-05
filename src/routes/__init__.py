import os
import sys

# Ensure smmai-backend root is in sys.path for both standalone and workspace execution
backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi import APIRouter

try:
    from src.routes.auth_routes import router as auth_router
    from src.routes.user_routes import router as users_router
    from src.routes.employee_routes import router as employees_router
except ImportError:
    from .auth_routes import router as auth_router
    from .user_routes import router as users_router
    from .employee_routes import router as employees_router

# Import existing platform routers for complete backward compatibility
# pyrefly: ignore[missing-import]
from app.api.routes import (
    organizations_router,
    brand_profiles_router,
    social_accounts_router,
    facebook_oauth_router,
    meta_router,
    webhooks_router,
    subscriptions_router,
    campaigns_router,
    content_router,
    media_router,
    collections_router,
    approvals_router,
    generations_router,
    scheduler_router,
    publishing_router,
    analytics_router,
    admin_router,
    superadmin_router,
    autonomous_pipeline_router,
    automation_router,
    audit_logs_router,
    quality_check_router,
    notifications_router,
)

api_router = APIRouter()

# Register core architectural routers
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(employees_router)
api_router.include_router(media_router)
api_router.include_router(collections_router)

# Register Social AI feature routers
api_router.include_router(organizations_router)
api_router.include_router(brand_profiles_router)
api_router.include_router(social_accounts_router)
api_router.include_router(facebook_oauth_router)
api_router.include_router(meta_router)
api_router.include_router(webhooks_router)
api_router.include_router(subscriptions_router)
api_router.include_router(campaigns_router)
api_router.include_router(content_router)
api_router.include_router(approvals_router)
api_router.include_router(generations_router)
api_router.include_router(scheduler_router)
api_router.include_router(publishing_router)
api_router.include_router(analytics_router)
api_router.include_router(admin_router)
api_router.include_router(superadmin_router)
api_router.include_router(autonomous_pipeline_router)
api_router.include_router(automation_router)
api_router.include_router(audit_logs_router)
api_router.include_router(quality_check_router)
api_router.include_router(notifications_router)

__all__ = ["api_router"]
