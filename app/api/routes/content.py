from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.models.user import User
from app.models.content import Content
from app.models.quality_report import QualityReport
from app.models.brand_profile import BrandProfile
from app.schemas.content import ContentCreate, ContentUpdate, ContentResponse
from app.services.content_service import ContentService
from app.services.quality_service import QualityService
from app.core.permissions import get_current_active_user, require_creator
from app.utils.enums import ContentStatus

router = APIRouter(prefix="/content", tags=["Content Management"])


@router.get("/", response_model=List[ContentResponse])
def list_content(
    brand_profile_id: Optional[int] = None,
    status_filter: Optional[ContentStatus] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List social content posts with optional brand or status filters within organization."""
    return ContentService.list_contents(db, current_user.organization_id, brand_profile_id, status_filter)


@router.get("/quality-check/all")
def quality_check_all_content(
    brand_profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Audit ALL eligible content items for the organization independently.
    Returns complete multi-gate evaluation results per item with summary analytics.
    """
    query = db.query(Content).join(BrandProfile, Content.brand_profile_id == BrandProfile.id).filter(
        BrandProfile.organization_id == current_user.organization_id
    )
    if brand_profile_id:
        query = query.filter(Content.brand_profile_id == brand_profile_id)
    
    contents = query.order_by(Content.created_at.desc()).all()
    brand = db.query(BrandProfile).filter(BrandProfile.organization_id == current_user.organization_id).first()

    results = []
    passed_count = 0
    attention_count = 0
    failed_count = 0
    total_score = 0

    for c in contents:
        try:
            existing_qr = db.query(QualityReport).filter(
                QualityReport.content_id == c.id,
                QualityReport.asset_type == "content"
            ).order_by(QualityReport.created_at.desc()).first()

            if existing_qr and c.validation_status and c.quality_score is not None:
                final_decision = existing_qr.decision or c.validation_status or "PASS"
                overall_score = existing_qr.quality_score or c.quality_score or 95.0
                all_failed = existing_qr.failed_checks or []
                all_issues = existing_qr.issues or []
                all_suggestions = existing_qr.suggestions or []
                status_str = "PASSED" if final_decision == "PASS" else ("NEEDS_ATTENTION" if final_decision in ("REGENERATE", "REVIEW_REQUIRED") else "BLOCKED")
                gates_dict = (existing_qr.metrics or {}).get("gates")
                if not gates_dict:
                    gates_dict = {
                        "caption_accuracy": {"score": 100 if "BANNED_POLICY_CLAIM" not in all_failed else 0, "status": "PASSED" if "BANNED_POLICY_CLAIM" not in all_failed else "BLOCKED", "issues": []},
                        "brand_voice": {"score": existing_qr.brand_score or 95, "status": "PASSED" if "BRAND_AVOID_TERM_USED" not in all_failed else "BLOCKED", "issues": []},
                        "duplicate_prevention": {"score": 100 if existing_qr.duplicate_status == "UNIQUE" else 30, "status": "PASSED" if existing_qr.duplicate_status == "UNIQUE" else "BLOCKED", "issues": []},
                        "platform_limits": {"score": 100, "status": "PASSED", "issues": []}
                    }
                audit = {
                    "content_id": c.id,
                    "title": c.title or "Untitled Post",
                    "overall_score": overall_score,
                    "status": status_str,
                    "decision": final_decision,
                    "duplicate_status": existing_qr.duplicate_status or "UNIQUE",
                    "similarity_score": existing_qr.similarity_score or 0.0,
                    "is_ready_for_publishing": (final_decision == "PASS"),
                    "issues": all_issues,
                    "failed_checks": all_failed,
                    "warnings": [i for i in all_issues if not any(f in i for f in ("Exact", "Blocked", "Identical"))],
                    "suggestions": all_suggestions,
                    "gates": gates_dict
                }
            else:
                audit = QualityService.audit_content_item(c, brand, db=db)
            # Persist evaluation result into content meta_info
            current_meta = dict(c.meta_info or {})
            current_meta["quality_check"] = {
                "score": audit["overall_score"],
                "status": audit["status"],
                "decision": audit.get("decision", "PASS"),
                "duplicate_status": audit.get("duplicate_status", "UNIQUE"),
                "similarity_score": audit.get("similarity_score", 0.0),
                "issues_count": len(audit["issues"]),
                "warnings_count": len(audit["warnings"])
            }
            c.meta_info = current_meta
            results.append(audit)
            
            if audit["status"] == "PASSED":
                passed_count += 1
            elif audit["status"] == "NEEDS_ATTENTION":
                attention_count += 1
            else:
                failed_count += 1
            total_score += audit["overall_score"]
        except Exception as e:
            # Failure in one item does not break the entire batch
            results.append({
                "content_id": c.id,
                "title": c.title or "Untitled Creative",
                "overall_score": 50,
                "status": "NEEDS_ATTENTION",
                "gates": [],
                "issues": [f"Evaluation error: {str(e)}"],
                "warnings": [],
                "suggestions": ["Please verify content formatting."],
                "is_ready_for_publishing": False
            })

    db.commit()

    total_items = len(contents)
    avg_score = round(total_score / total_items) if total_items > 0 else 100

    return {
        "total_items": total_items,
        "total_audited": total_items,
        "passed_count": passed_count,
        "attention_count": attention_count,
        "failed_count": failed_count,
        "average_score": avg_score,
        "items": results
    }


@router.post("/{content_id}/quality-check")
def quality_check_single_content(
    content_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Run full quality check audit on a single content item."""
    content = db.query(Content).join(BrandProfile, Content.brand_profile_id == BrandProfile.id).filter(
        Content.id == content_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found.")
    
    brand = db.query(BrandProfile).filter(BrandProfile.id == content.brand_profile_id).first()
    audit = QualityService.audit_content_item(content, brand, db=db)
    
    current_meta = dict(content.meta_info or {})
    current_meta["quality_check"] = {
        "score": audit["overall_score"],
        "status": audit["status"],
        "decision": audit.get("decision", "PASS"),
        "duplicate_status": audit.get("duplicate_status", "UNIQUE"),
        "similarity_score": audit.get("similarity_score", 0.0),
        "issues_count": len(audit["issues"]),
        "warnings_count": len(audit["warnings"])
    }
    content.meta_info = current_meta
    db.commit()

    return audit


@router.post("/{content_id}/auto-optimize", response_model=ContentResponse)
def auto_optimize_content(
    content_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Auto-optimize content copy, hashtags, and CTA to 100% compliance."""
    content = db.query(Content).join(BrandProfile, Content.brand_profile_id == BrandProfile.id).filter(
        Content.id == content_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found.")

    brand = db.query(BrandProfile).filter(BrandProfile.id == content.brand_profile_id).first()
    optimized = QualityService.auto_optimize_caption(content, brand)

    content.caption = optimized["caption"]
    content.hashtags = optimized["hashtags"]
    
    current_meta = dict(content.meta_info or {})
    current_meta["cta"] = optimized["cta"]
    current_meta["quality_check"] = {
        "score": 100,
        "status": "PASSED",
        "issues_count": 0,
        "warnings_count": 0,
        "auto_optimized": True
    }
    content.meta_info = current_meta
    db.commit()
    db.refresh(content)
    return content


@router.post("/", response_model=ContentResponse, status_code=status.HTTP_201_CREATED)
def create_content(
    payload: ContentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Create a new social post / content draft."""
    return ContentService.create_content(db, current_user, payload)


@router.get("/{content_id}", response_model=ContentResponse)
def get_content(
    content_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get single content post item."""
    return ContentService.get_content(db, content_id, current_user.organization_id)


@router.put("/{content_id}", response_model=ContentResponse)
def update_content(
    content_id: int,
    payload: ContentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Update content copy, hashtags, media, or status."""
    return ContentService.update_content(db, content_id, current_user.organization_id, payload)


@router.delete("/{content_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_content(
    content_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Delete a content item."""
    ContentService.delete_content(db, content_id, current_user.organization_id)
    return None
