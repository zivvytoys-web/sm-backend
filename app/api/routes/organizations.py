from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.models.organization import Organization
from app.models.user import User
from app.core.permissions import get_current_active_user, require_admin

router = APIRouter(prefix="/organizations", tags=["Organizations"])


@router.get("/current")
def get_current_organization(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve details for current user's workspace/organization."""
    if not current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No organization linked")

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    from app.models.brand_profile import BrandProfile
    bp = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "description": org.description,
        "logo_url": org.logo_url,
        "settings": org.settings or {},
        "brand_profile": {
            "id": bp.id,
            "name": bp.name,
            "industry": bp.industry,
            "tagline": bp.tagline,
            "location": bp.location,
            "timings": bp.timings,
            "tone_of_voice": bp.tone_of_voice,
            "target_audience": bp.target_audience,
            "brand_guidelines": bp.brand_guidelines,
            "cta": bp.cta,
            "avoid": bp.avoid,
            "key_themes": bp.key_themes or [],
            "hashtags": bp.hashtags or [],
            "color_palette": bp.color_palette or [],
            "meta_info": bp.meta_info or {}
        } if bp else None
    }


@router.put("/current")
def update_current_organization(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update current organization details."""
    if not current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No organization linked")

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    if "name" in payload and payload["name"]:
        org.name = payload["name"]
    if "description" in payload:
        org.description = payload["description"]
    if "logo_url" in payload:
        org.logo_url = payload["logo_url"]
    if "settings" in payload and isinstance(payload["settings"], dict):
        org.settings = {**(org.settings or {}), **payload["settings"]}

    db.commit()
    db.refresh(org)
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "description": org.description,
        "logo_url": org.logo_url,
        "settings": org.settings
    }

