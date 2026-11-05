from fastapi import APIRouter
from app.api.routes import (
    auth_router,
    users_router,
    organizations_router,
    brand_profiles_router,
    social_accounts_router,
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
    autonomous_pipeline_router,
    facebook_oauth_router,
    meta_router,
    webhooks_router,
    quality_check_router,
)
from app.api.routes.integrations import router as integrations_router

api_router = APIRouter()

# Register all feature routers
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(organizations_router)
api_router.include_router(brand_profiles_router)
api_router.include_router(social_accounts_router)
api_router.include_router(facebook_oauth_router)
api_router.include_router(meta_router)
api_router.include_router(webhooks_router)
api_router.include_router(subscriptions_router)
api_router.include_router(campaigns_router)
api_router.include_router(content_router)
api_router.include_router(media_router)
api_router.include_router(collections_router)
api_router.include_router(approvals_router)
api_router.include_router(generations_router)
api_router.include_router(scheduler_router)
api_router.include_router(publishing_router)
api_router.include_router(analytics_router)
api_router.include_router(admin_router)
api_router.include_router(integrations_router)
api_router.include_router(autonomous_pipeline_router)
api_router.include_router(quality_check_router)
