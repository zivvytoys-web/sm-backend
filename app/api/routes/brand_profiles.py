from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.models.user import User
from app.schemas.brand import BrandProfileCreate, BrandProfileUpdate, BrandProfileResponse
from app.services.brand_service import BrandService
from app.core.permissions import get_current_active_user, require_creator

router = APIRouter(prefix="/brand-profiles", tags=["Brand Profiles"])


@router.get("/", response_model=List[BrandProfileResponse])
def list_brand_profiles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all brand profiles within current organization for this user."""
    return BrandService.list_brands(db, current_user.organization_id, user_id=current_user.id)


@router.post("/", response_model=BrandProfileResponse, status_code=status.HTTP_201_CREATED)
def create_brand_profile(
    payload: BrandProfileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Create a new brand profile."""
    return BrandService.create_brand(db, current_user.organization_id, payload, user_id=current_user.id)


@router.get("/{brand_id}", response_model=BrandProfileResponse)
def get_brand_profile(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get single brand profile by ID."""
    return BrandService.get_brand(db, brand_id, current_user.organization_id)


@router.put("/{brand_id}", response_model=BrandProfileResponse)
def update_brand_profile(
    brand_id: int,
    payload: BrandProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Update an existing brand profile."""
    return BrandService.update_brand(db, brand_id, current_user.organization_id, payload, user_id=current_user.id)


@router.delete("/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_brand_profile(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Delete a brand profile."""
    BrandService.delete_brand(db, brand_id, current_user.organization_id)
    return None
