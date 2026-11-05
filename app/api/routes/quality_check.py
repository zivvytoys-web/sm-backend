from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.dependencies import get_db
from app.models.user import User
from app.models.brand_profile import BrandProfile
from app.models.media_asset import MediaAsset
from app.models.content import Content
from app.models.quality_report import QualityReport
from app.services.quality_check_engine import QualityCheckEngine, QUALITY_THRESHOLDS
from app.core.permissions import get_current_active_user, require_creator

router = APIRouter(prefix="/quality-check", tags=["Real-Time Quality Check & Duplicate Prevention"])


@router.get("/thresholds")
def get_quality_thresholds():
    """Retrieve active system quality check and duplicate detection thresholds."""
    return QUALITY_THRESHOLDS


@router.get("/reports")
def list_quality_reports(
    asset_type: Optional[str] = Query(None, description="image, video, text, content"),
    decision: Optional[str] = Query(None, description="PASS, REGENERATE, BLOCK, REVIEW_REQUIRED"),
    duplicate_status: Optional[str] = Query(None, description="UNIQUE, POTENTIAL_DUPLICATE, CONFIRMED_DUPLICATE"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List historical audit validation reports and duplicate prevention records."""
    query = db.query(QualityReport).filter(QualityReport.organization_id == current_user.organization_id)
    if asset_type:
        query = query.filter(QualityReport.asset_type == asset_type)
    if decision:
        query = query.filter(QualityReport.decision == decision)
    if duplicate_status:
        query = query.filter(QualityReport.duplicate_status == duplicate_status)

    total = query.count()
    items = query.order_by(QualityReport.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "reports": [
            {
                "id": r.id,
                "asset_type": r.asset_type,
                "media_asset_id": r.media_asset_id,
                "content_id": r.content_id,
                "decision": r.decision,
                "quality_score": r.quality_score,
                "technical_score": r.technical_score,
                "brand_score": r.brand_score,
                "duplicate_status": r.duplicate_status,
                "similarity_score": r.similarity_score,
                "matching_asset_id": r.matching_asset_id,
                "matching_content_id": r.matching_content_id,
                "failed_checks": r.failed_checks or [],
                "issues": r.issues or [],
                "suggestions": r.suggestions or [],
                "match_details": r.match_details or {},
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in items
        ]
    }


@router.get("/report/{report_id}")
def get_quality_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve full audit details for a specific quality report."""
    report = db.query(QualityReport).filter(
        QualityReport.id == report_id,
        QualityReport.organization_id == current_user.organization_id
    ).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quality report not found.")

    return {
        "id": report.id,
        "asset_type": report.asset_type,
        "media_asset_id": report.media_asset_id,
        "content_id": report.content_id,
        "decision": report.decision,
        "quality_score": report.quality_score,
        "technical_score": report.technical_score,
        "brand_score": report.brand_score,
        "content_accuracy_score": report.content_accuracy_score,
        "duplicate_status": report.duplicate_status,
        "similarity_score": report.similarity_score,
        "matching_asset_id": report.matching_asset_id,
        "matching_content_id": report.matching_content_id,
        "failed_checks": report.failed_checks or [],
        "issues": report.issues or [],
        "suggestions": report.suggestions or [],
        "match_details": report.match_details or {},
        "metrics": report.metrics or {},
        "created_at": report.created_at.isoformat() if report.created_at else None
    }


@router.post("/validate-asset/{media_asset_id}")
def validate_single_asset(
    media_asset_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Run real-time technical quality and duplicate prevention validation on a media asset."""
    asset = db.query(MediaAsset).filter(
        MediaAsset.id == media_asset_id,
        MediaAsset.organization_id == current_user.organization_id
    ).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found.")

    brand = db.query(BrandProfile).filter(BrandProfile.organization_id == current_user.organization_id).first()

    if str(asset.media_type).upper().endswith("VIDEO"):
        report = QualityCheckEngine.validate_video(
            video_url=asset.file_url,
            brand=brand,
            organization_id=current_user.organization_id,
            db=db,
            candidate_asset_id=asset.id
        )
    else:
        report = QualityCheckEngine.validate_image(
            image_url=asset.file_url,
            brand=brand,
            organization_id=current_user.organization_id,
            db=db,
            candidate_asset_id=asset.id
        )

    # Persist columns on MediaAsset
    asset.sha256_hash = report.get("sha256")
    asset.perceptual_hash = report.get("perceptual_hash")
    asset.validation_status = report.get("decision", "PASS")
    asset.quality_score = report.get("quality_score", 100.0)
    asset.is_duplicate = (report.get("duplicate_status") == "CONFIRMED_DUPLICATE")
    if report.get("matching_asset_id"):
        asset.duplicate_of_id = report["matching_asset_id"]

    # Record QualityReport
    qr = QualityReport(
        asset_type="video" if str(asset.media_type).upper().endswith("VIDEO") else "image",
        media_asset_id=asset.id,
        organization_id=current_user.organization_id,
        brand_profile_id=brand.id if brand else None,
        decision=report.get("decision", "PASS"),
        quality_score=report.get("quality_score", 100.0),
        technical_score=report.get("technical_score", 100.0),
        brand_score=report.get("brand_score", 100.0),
        content_accuracy_score=100.0,
        duplicate_status=report.get("duplicate_status", "UNIQUE"),
        similarity_score=report.get("similarity_score", 0.0),
        matching_asset_id=report.get("matching_asset_id"),
        failed_checks=report.get("failed_checks", []),
        issues=report.get("issues", []),
        suggestions=report.get("suggestions", []),
        match_details=report.get("match_details", {}),
        metrics=report.get("metrics", {})
    )
    db.add(qr)
    db.commit()
    db.refresh(asset)

    return report


@router.post("/validate-content/{content_id}")
def validate_single_content(
    content_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Run real-time multi-gate quality and duplicate audit on content."""
    content = db.query(Content).join(BrandProfile, Content.brand_profile_id == BrandProfile.id).filter(
        Content.id == content_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found.")

    brand = db.query(BrandProfile).filter(BrandProfile.id == content.brand_profile_id).first()
    report = QualityCheckEngine.audit_content_item(content=content, brand=brand, db=db)
    return report


@router.get("/similar/{asset_id}")
def get_similar_historical_assets(
    asset_id: int,
    asset_type: str = Query("media", description="'media' or 'content'"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve most similar historical assets with visual or semantic similarity distance breakdown."""
    if asset_type == "media":
        target = db.query(MediaAsset).filter(
            MediaAsset.id == asset_id,
            MediaAsset.organization_id == current_user.organization_id
        ).first()
        if not target:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found.")

        # Find historical matches
        all_others = db.query(MediaAsset).filter(
            MediaAsset.organization_id == current_user.organization_id,
            MediaAsset.id != asset_id
        ).all()

        results = []
        for other in all_others:
            sim = 0.0
            method = "none"
            if target.sha256_hash and other.sha256_hash and target.sha256_hash == other.sha256_hash:
                sim = 1.0
                method = "exact_sha256"
            elif target.perceptual_hash and other.perceptual_hash:
                try:
                    import imagehash
                    h1 = imagehash.hex_to_hash(target.perceptual_hash)
                    h2 = imagehash.hex_to_hash(other.perceptual_hash)
                    dist = int(h1 - h2)
                    sim = round(max(0.0, 1.0 - (dist / 64.0)), 4)
                    method = f"phash_dist_{dist}"
                except Exception:
                    pass

            if sim > 0.5:
                results.append({
                    "id": other.id,
                    "file_name": other.file_name,
                    "file_url": other.file_url,
                    "media_type": other.media_type.value if hasattr(other.media_type, "value") else str(other.media_type),
                    "similarity": sim,
                    "method": method,
                    "is_confirmed_duplicate": sim >= 0.90
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return {
            "target_id": asset_id,
            "target_file": target.file_name,
            "sha256": target.sha256_hash,
            "perceptual_hash": target.perceptual_hash,
            "similar_matches": results[:10]
        }
    else:
        target_c = db.query(Content).filter(
            Content.id == asset_id,
            Content.organization_id == current_user.organization_id
        ).first()
        if not target_c:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found.")

        all_c = db.query(Content).filter(
            Content.organization_id == current_user.organization_id,
            Content.id != asset_id
        ).all()

        results = []
        for other in all_c:
            sim, method = QualityCheckEngine.compute_text_similarity(
                f"{target_c.title or ''} {target_c.caption or ''}",
                f"{other.title or ''} {other.caption or ''}"
            )
            if sim > 0.4:
                results.append({
                    "id": other.id,
                    "title": other.title,
                    "caption_preview": (other.caption or "")[:80],
                    "similarity": sim,
                    "method": method,
                    "is_confirmed_duplicate": sim >= QUALITY_THRESHOLDS["DESCRIPTION_EMBEDDING_MIN_COSINE"]
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return {
            "target_id": asset_id,
            "target_title": target_c.title,
            "similar_matches": results[:10]
        }
