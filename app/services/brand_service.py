from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.brand_profile import BrandProfile
from app.schemas.brand import BrandProfileCreate, BrandProfileUpdate


from datetime import datetime, timezone


def is_brand_details_configured(brand: Optional[BrandProfile]) -> bool:
    """
    Returns True ONLY if authentic brand details have been provided by the user.
    If brand is missing, or brand name is empty/default placeholder, or industry is missing/empty, returns False.
    """
    if not brand:
        return False

    name = (getattr(brand, "name", "") or "").strip()
    industry = (getattr(brand, "industry", "") or "").strip()

    if not name or not industry:
        return False

    lower_name = name.lower()
    lower_ind = industry.lower()

    is_placeholder_name = (
        lower_name in ["brand", "default brand", "brand workspace"]
        or lower_name.endswith("'s brand")
        or lower_name.endswith("'s workspace")
    )
    is_placeholder_industry = lower_ind in [
        "retail & lifestyle",
        "retail & creative studio",
        "commercial",
        "lifestyle",
        "other"
    ]

    # If both name and industry look like default auto-generated placeholders, check if user provided custom tagline/products/guidelines
    if is_placeholder_name and is_placeholder_industry:
        tagline = getattr(brand, "tagline", "") or ""
        has_custom_tagline = bool(tagline.strip() and not tagline.strip().startswith("Welcome to"))
        cta = getattr(brand, "cta", "") or ""
        has_custom_cta = bool(cta.strip() and cta.strip() != "Shop the collection today")
        meta = getattr(brand, "meta_info", {}) or {}
        has_products = bool(isinstance(meta, dict) and meta.get("products"))
        if not (has_custom_tagline or has_custom_cta or has_products):
            return False

    return True


class BrandService:
    @staticmethod
    def is_configured(brand: Optional[BrandProfile]) -> bool:
        return is_brand_details_configured(brand)

    @staticmethod
    def list_brands(db: Session, organization_id: int, user_id: Optional[int] = None) -> List[BrandProfile]:
        brands = db.query(BrandProfile).filter(BrandProfile.organization_id == organization_id).order_by(BrandProfile.updated_at.desc(), BrandProfile.id.desc()).all()
        if not brands:
            from app.models.organization import Organization
            org = db.query(Organization).filter(Organization.id == organization_id).first()
            org_name = org.name if org else "Brand Workspace"
            default_bp = BrandProfile(
                organization_id=organization_id,
                name=org_name,
                industry="Retail & Lifestyle",
                tagline=f"Welcome to {org_name}",
                tone_of_voice="Modern & Engaging",
                target_audience="Target Customers",
                brand_guidelines="Clean visuals and cohesive brand voice.",
                key_themes=["Brand Launch", "Product Highlights", "Storytelling"],
                hashtags=["#BrandLaunch"],
                color_palette=["#59171b", "#fed7b8", "#ec4899"],
                meta_info={}
            )
            db.add(default_bp)
            db.commit()
            db.refresh(default_bp)
            brands = [default_bp]
        return brands

    @staticmethod
    def get_brand(db: Session, brand_id: int, organization_id: int) -> BrandProfile:
        # Check if the requested brand profile belongs to another tenant
        target = db.query(BrandProfile).filter(BrandProfile.id == brand_id).first()
        if target:
            if target.organization_id != organization_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Cross-tenant access to brand profile is strictly prohibited."
                )
            return target

        # If brand_id does not exist, check fallback for this organization
        brand = db.query(BrandProfile).filter(BrandProfile.organization_id == organization_id).first()
        if not brand:
            brands = BrandService.list_brands(db, organization_id)
            if brands:
                return brands[0]
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand profile not found.")
        return brand

    @staticmethod
    def create_brand(db: Session, organization_id: int, payload: BrandProfileCreate, user_id: Optional[int] = None) -> BrandProfile:
        import json
        data = payload.model_dump()
        data["organization_id"] = organization_id
        if user_id:
            data["user_id"] = user_id
        if isinstance(data.get("target_audience"), (dict, list)):
            data["target_audience"] = json.dumps(data["target_audience"])
        brand = BrandProfile(**data)
        db.add(brand)
        db.commit()
        db.refresh(brand)
        return brand

    @staticmethod
    def update_brand(db: Session, brand_id: int, organization_id: int, payload: BrandProfileUpdate, user_id: Optional[int] = None) -> BrandProfile:
        import json
        target = db.query(BrandProfile).filter(BrandProfile.id == brand_id).first()
        if target and target.organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cross-tenant modification of brand profile is strictly prohibited."
            )

        brand = target
        if not brand:
            # Fallback to existing org brand or auto-create if missing
            brand = db.query(BrandProfile).filter(BrandProfile.organization_id == organization_id).first()
        if not brand:
            from app.models.organization import Organization
            org = db.query(Organization).filter(Organization.id == organization_id).first()
            org_name = org.name if org else "Brand Workspace"
            brand = BrandProfile(
                organization_id=organization_id,
                name=org_name,
                industry="Retail & Lifestyle",
                tone_of_voice="Modern & Engaging",
                target_audience="Target Customers",
                brand_guidelines="Clean visuals and cohesive brand voice.",
                key_themes=["Brand Launch", "Product Highlights", "Storytelling"],
                hashtags=["#BrandLaunch"],
                color_palette=["#59171b", "#fed7b8", "#ec4899"],
                meta_info={}
            )
            db.add(brand)
            db.flush()
        if user_id and not brand.user_id:
            brand.user_id = user_id
        update_data = payload.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if field == "target_audience" and isinstance(value, (dict, list)):
                value = json.dumps(value)
            if field == "meta_info" and isinstance(value, dict) and isinstance(brand.meta_info, dict):
                merged = dict(brand.meta_info)
                merged.update(value)
                setattr(brand, "meta_info", merged)
            else:
                setattr(brand, field, value)
        brand.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(brand)
        return brand

    @staticmethod
    def delete_brand(db: Session, brand_id: int, organization_id: int) -> bool:
        brand = BrandService.get_brand(db, brand_id, organization_id)
        db.delete(brand)
        db.commit()
        return True
