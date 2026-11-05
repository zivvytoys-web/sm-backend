import json
import pytz
import re
from datetime import datetime, date, time, timedelta, timezone
from typing import List, Optional, Dict, Any, Union
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.core.logging import logger
from app.db.dependencies import get_db
from app.core.permissions import get_current_active_user
from app.models.user import User
from app.models.brand_profile import BrandProfile
from app.models.social_account import SocialAccount
from app.models.autonomous_pipeline import (
    BusinessSchedule,
    PipelineRun,
    AutonomousPost,
    PostVariant,
    ContentBrief
)
from app.schemas.autonomous_pipeline import (
    BusinessScheduleBase,
    BusinessScheduleUpdate,
    BusinessScheduleResponse,
    AutomationStatusResponse,
    ScheduledJobInfo,
    AutomationLogEntry,
    ContentRescheduleRequest,
    ContentEditRequest,
    AutonomousPostResponse,
    AutonomousPostUpdate,
    PostApproveRequest,
    PostRejectRequest,
    PostRegenerateRequest,
    RunNowRequest,
    RunNowResponse
)
from app.services.pipeline_service import run_pipeline, regenerate_post_part, calculate_utc_scheduled_time
from app.scheduler.autonomous_scheduler import AutonomousScheduler
from app.services.autonomous_publisher import publish_due_posts

router = APIRouter(prefix="/automation", tags=["Autonomous Social AI Automation"])


def _get_user_brand(db: Session, current_user: User, brand_id: Optional[int] = None) -> Optional[BrandProfile]:
    """Resolves the authorized brand profile for the user safely with strict multi-tenant isolation."""
    user_org_id = getattr(current_user, "organization_id", None)
    user_role_str = str(getattr(current_user, "role", "")).lower()
    is_super = "super_admin" in user_role_str

    if brand_id:
        query = db.query(BrandProfile).filter(BrandProfile.id == brand_id)
        if user_org_id and not is_super:
            query = query.filter(BrandProfile.organization_id == user_org_id)
        brand = query.first()
        if brand:
            return brand
        # If client supplied a mismatched or stale brand_id, recover to the tenant's authentic brand

    if user_org_id:
        brand = db.query(BrandProfile).filter(
            BrandProfile.organization_id == user_org_id
        ).first()
        if brand:
            return brand

    # Multi-tenant isolation: Never return another organization's brand profile!
    # If no brand profile exists for this organization, return None (do not seed fake placeholder brands)
    return None


def _enrich_schedule_response(schedule: BusinessSchedule, brand_id: int, db: Session) -> BusinessScheduleResponse:
    resp = BusinessScheduleResponse.model_validate(schedule)
    try:
        scheduler = AutonomousScheduler.get_scheduler()
        job = scheduler.get_job(f"generate_{brand_id}")
        if job and getattr(job, "next_run_time", None):
            resp.next_generation_time = job.next_run_time
    except Exception as e:
        logger.debug(f"[Automation API] Error checking APScheduler job for brand {brand_id}: {e}")

    # Calculate next daily trigger time directly from user's preferred posting_time
    tz = pytz.timezone(schedule.timezone or "Asia/Kolkata")
    now_tz = datetime.now(tz)
    time_str = schedule.posting_time or schedule.generation_time or "18:00"
    try:
        ph, pm = map(int, time_str.split(":"))
    except Exception:
        ph, pm = 18, 0

    today_slot = tz.localize(datetime(now_tz.year, now_tz.month, now_tz.day, ph, pm, 0))
    if today_slot > now_tz:
        next_slot = today_slot
    else:
        tomorrow = now_tz.date() + timedelta(days=1)
        next_slot = tz.localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, ph, pm, 0))

    calculated_utc = next_slot.astimezone(pytz.UTC).replace(tzinfo=None)
    if resp.next_generation_time is None:
        resp.next_generation_time = calculated_utc
    resp.next_posting_time = calculated_utc

    try:
        today = now_tz.date()
        today_run = db.query(PipelineRun).filter(
            PipelineRun.brand_profile_id == brand_id,
            PipelineRun.run_date == today
        ).first()
        if today_run:
            resp.today_run_status = today_run.status

        latest_post = db.query(AutonomousPost).filter(
            AutonomousPost.brand_profile_id == brand_id
        ).order_by(AutonomousPost.created_at.desc()).first()

        if latest_post:
            resp.today_post_id = latest_post.id
            resp.today_post_status = latest_post.status
            for v in latest_post.variants:
                if getattr(v, "platform_url", None):
                    resp.today_post_url = v.platform_url
                    break

        if latest_post and latest_post.status in ("scheduled", "publishing") and latest_post.scheduled_time:
            resp.next_posting_time = latest_post.scheduled_time
    except Exception as e:
        logger.debug(f"[Automation API] Error enriching schedule metadata: {e}")

    resp.engine_status = "active" if schedule.is_active else "paused"
    return resp


# =========================================================================
# 1. AUTOMATION SETTINGS APIS
# =========================================================================

@router.get("/settings", response_model=BusinessScheduleResponse)
def get_automation_settings(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    if not brand:
        return BusinessScheduleResponse(
            brand_profile_id=0,
            generation_time="09:00",
            posting_time="18:00",
            timezone="Asia/Kolkata",
            auto_approve=False,
            auto_schedule=False,
            is_active=False,
            target_platforms=["instagram", "facebook", "linkedin", "youtube"]
        )
    schedule = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == brand.id).first()

    if not schedule:
        schedule = BusinessSchedule(
            brand_profile_id=brand.id,
            generation_time="09:00",
            posting_time="18:00",
            timezone="Asia/Kolkata",
            auto_approve=False,
            auto_schedule=True,
            is_active=True,
            target_platforms=["instagram", "facebook", "linkedin", "youtube"]
        )
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
        AutonomousScheduler.reschedule_business_pipeline_job(brand.id, schedule)

    return _enrich_schedule_response(schedule, brand.id, db)


@router.put("/settings", response_model=BusinessScheduleResponse)
def update_automation_settings(
    payload: BusinessScheduleUpdate,
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    schedule = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == brand.id).first()

    if not schedule:
        schedule = BusinessSchedule(brand_profile_id=brand.id)
        db.add(schedule)

    update_data = payload.model_dump(exclude_unset=True)

    # Normalize empty string dates to None
    if "generation_date" in update_data and (update_data["generation_date"] == "" or update_data["generation_date"] is None):
        update_data["generation_date"] = None
    if "posting_date" in update_data and (update_data["posting_date"] == "" or update_data["posting_date"] is None):
        update_data["posting_date"] = None

    # Validation: Timezone
    if "timezone" in update_data and update_data["timezone"]:
        tz_name = update_data["timezone"]
        if tz_name not in pytz.all_timezones:
            raise HTTPException(status_code=400, detail=f"Invalid timezone '{tz_name}'. Must be a valid IANA timezone.")

    tz = pytz.timezone(update_data.get("timezone") or schedule.timezone or "Asia/Kolkata")
    now_tz = datetime.now(tz)

    # Synchronize generation_time with posting_time so daily cycle fires at posting time if not explicitly provided
    if "posting_time" in update_data and update_data["posting_time"]:
        if "generation_time" not in update_data or not update_data["generation_time"]:
            update_data["generation_time"] = update_data["posting_time"]

    # If dates are not provided, ensure they default to None
    if "generation_date" not in update_data:
        update_data["generation_date"] = None
    if "posting_date" not in update_data:
        update_data["posting_date"] = None

    for field, val in update_data.items():
        setattr(schedule, field, val)

    # Update pending/scheduled posts to next eligible slot if posting_time or timezone changed
    if "posting_time" in update_data or "timezone" in update_data:
        new_posting_time = calculate_utc_scheduled_time(
            schedule.posting_time, schedule.timezone
        )
        existing = db.query(AutonomousPost).filter(
            AutonomousPost.brand_profile_id == brand.id,
            AutonomousPost.status.in_(["pending_approval", "scheduled", "ready_to_publish"]),
        ).all()
        for post in existing:
            post.scheduled_time = new_posting_time
            for variant in post.variants:
                if variant.status in {"pending", "pending_approval", "scheduled", "ready_to_publish"}:
                    variant.scheduled_time = new_posting_time

    # If auto_approve and auto_schedule were enabled, promote pending posts
    if schedule.auto_approve and schedule.auto_schedule:
        pending_posts = db.query(AutonomousPost).filter(
            AutonomousPost.brand_profile_id == brand.id,
            AutonomousPost.status.in_(["pending_approval", "ready_to_publish"]),
        ).all()
        for pending in pending_posts:
            pending.status = "scheduled"
            pending.auto_approved = True
            for variant in pending.variants:
                if variant.status in {"pending", "pending_approval", "ready_to_publish"}:
                    variant.status = "scheduled"

    db.commit()
    db.refresh(schedule)

    # Reschedule in APScheduler
    AutonomousScheduler.reschedule_business_pipeline_job(brand.id, schedule)
    AutonomousScheduler.reschedule_business_publications(brand.id)
    logger.info(f"[Automation API] Successfully updated settings for brand {brand.id}.")

    return _enrich_schedule_response(schedule, brand.id, db)


@router.post("/enable")
def enable_automation(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    schedule = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == brand.id).first()
    if not schedule:
        schedule = BusinessSchedule(brand_profile_id=brand.id)
        db.add(schedule)

    schedule.is_active = True
    db.commit()
    db.refresh(schedule)
    AutonomousScheduler.reschedule_business_pipeline_job(brand.id, schedule)
    AutonomousScheduler.reschedule_business_publications(brand.id)
    return {"success": True, "message": "Automation scheduler successfully enabled.", "is_active": True}


@router.post("/pause")
def pause_automation(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    schedule = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == brand.id).first()
    if not schedule:
        schedule = BusinessSchedule(brand_profile_id=brand.id)
        db.add(schedule)

    schedule.is_active = False
    db.commit()
    db.refresh(schedule)
    AutonomousScheduler.reschedule_business_pipeline_job(brand.id, schedule)
    return {"success": True, "message": "Automation scheduler paused.", "is_active": False}


@router.post("/run-now", response_model=RunNowResponse)
async def run_pipeline_now(
    payload: Optional[RunNowRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand_id = payload.brand_profile_id if payload else None
    brand = _get_user_brand(db, current_user, brand_id)
    from app.services.brand_service import is_brand_details_configured
    if not is_brand_details_configured(brand):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Brand details are required: Please configure your Brand Name and Industry in Brand Profile before generating the autonomous pipeline."
        )
    force = payload.force if payload else True

    logger.info(f"[Automation API] Triggering immediate run for brand {brand.id} ({brand.name})...")
    result = await run_pipeline(business_id=brand.id, force=force)

    if result.get("status") == "success":
        return RunNowResponse(
            status="success",
            run_id=result.get("run_id"),
            post_id=result.get("post_id"),
            quality_score=result.get("quality_score"),
            auto_approved=result.get("auto_approved"),
            message="Autonomous content generation completed successfully."
        )
    elif result.get("status") == "skipped":
        return RunNowResponse(
            status="skipped",
            run_id=result.get("run_id"),
            message=f"Pipeline run was skipped: {result.get('reason')}"
        )
    else:
        raise HTTPException(
            status_code=500,
            detail=f"Autonomous pipeline execution failed: {result.get('error', 'Unknown error')}"
        )


@router.post("/clear")
def clear_pipeline(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Cleans up all past pipeline runs, briefs, draft/pending autonomous posts,
    and resets the queue so the pipeline is completely clean and ready for a fresh daily schedule.
    """
    brand = _get_user_brand(db, current_user, brand_id)
    if not brand:
        return {"success": True, "message": "Pipeline already clean (no brand profile found)."}

    # 1. Delete all autonomous posts for this brand
    posts = db.query(AutonomousPost).filter(AutonomousPost.brand_profile_id == brand.id).all()
    post_count = len(posts)
    for p in posts:
        try:
            AutonomousScheduler.cancel_post_publication(p.id)
        except Exception:
            pass
        db.delete(p)

    # 2. Delete all pipeline runs for this brand (cascades to briefs and assets)
    runs = db.query(PipelineRun).filter(PipelineRun.brand_profile_id == brand.id).all()
    run_count = len(runs)
    for r in runs:
        db.delete(r)

    db.commit()

    # 3. Reschedule active business pipeline job with clean state
    schedule = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == brand.id).first()
    if schedule and schedule.is_active:
        AutonomousScheduler.reschedule_business_pipeline_job(brand.id, schedule)

    logger.info(f"[Automation API] Cleared {post_count} posts and {run_count} runs for brand #{brand.id} ({brand.name}).")
    return {
        "success": True,
        "message": f"Pipeline cleaned successfully. Removed {post_count} posts and {run_count} run logs.",
        "deleted_posts": post_count,
        "deleted_runs": run_count
    }


@router.get("/status", response_model=AutomationStatusResponse)
def get_automation_status(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    schedule = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == brand.id).first()

    if not schedule:
        schedule = BusinessSchedule(brand_profile_id=brand.id)
        db.add(schedule)
        db.commit()
        db.refresh(schedule)

    enriched = _enrich_schedule_response(schedule, brand.id, db)

    # Query latest run
    latest_run = db.query(PipelineRun).filter(
        PipelineRun.brand_profile_id == brand.id
    ).order_by(PipelineRun.created_at.desc()).first()

    current_stage = getattr(latest_run, "stage", None) if latest_run and latest_run.status == "running" else None
    current_progress = getattr(latest_run, "progress_percent", 0) if latest_run and latest_run.status == "running" else (100 if latest_run and latest_run.status == "completed" else 0)

    jobs = AutonomousScheduler.list_jobs_info(business_id=brand.id)

    return AutomationStatusResponse(
        is_active=schedule.is_active,
        engine_status="running" if (latest_run and latest_run.status == "running") else ("active" if schedule.is_active else "paused"),
        is_leader=getattr(AutonomousScheduler, '_is_leader', True),
        current_stage=current_stage,
        current_progress=current_progress,
        generation_date=schedule.generation_date or None,
        generation_time=schedule.generation_time,
        posting_date=schedule.posting_date or None,
        posting_time=schedule.posting_time,
        timezone=schedule.timezone,
        auto_approve=schedule.auto_approve,
        auto_schedule=getattr(schedule, "auto_schedule", True),
        next_generation_time=enriched.next_generation_time,
        next_posting_time=enriched.next_posting_time,
        last_run_date=latest_run.run_date if latest_run else None,
        last_run_status=latest_run.status if latest_run else None,
        last_run_error=latest_run.error if latest_run else None,
        last_post_id=enriched.today_post_id,
        last_post_status=enriched.today_post_status,
        last_post_url=enriched.today_post_url,
        active_jobs_count=len(jobs)
    )


@router.get("/jobs", response_model=List[ScheduledJobInfo])
def list_automation_jobs(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    jobs = AutonomousScheduler.list_jobs_info(business_id=brand.id)
    return [ScheduledJobInfo(**j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=ScheduledJobInfo)
def get_automation_job(
    job_id: str,
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    jobs = AutonomousScheduler.list_jobs_info(business_id=brand.id)
    for j in jobs:
        if j["job_id"] == job_id:
            return ScheduledJobInfo(**j)
    raise HTTPException(status_code=404, detail=f"Scheduled job '{job_id}' not found.")


@router.get("/logs", response_model=List[AutomationLogEntry])
def list_automation_logs(
    brand_id: Optional[int] = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    runs = db.query(PipelineRun).filter(
        PipelineRun.brand_profile_id == brand.id
    ).order_by(PipelineRun.created_at.desc()).limit(limit).all()

    entries = []
    for r in runs:
        duration = None
        if r.started_at and r.finished_at:
            duration = (r.finished_at - r.started_at).total_seconds()
        topic = (r.stats or {}).get("topic") if isinstance(r.stats, dict) else None
        entries.append(AutomationLogEntry(
            id=r.id,
            brand_profile_id=r.brand_profile_id,
            run_date=r.run_date,
            status=r.status,
            stage=getattr(r, "stage", "Completed" if r.status == "completed" else "Failed" if r.status == "failed" else "Running"),
            progress_percent=getattr(r, "progress_percent", 100 if r.status == "completed" else 0),
            started_at=r.started_at,
            finished_at=r.finished_at,
            duration_seconds=duration,
            quality_score=r.quality_score,
            topic=topic,
            error=r.error,
            stats=r.stats or {}
        ))
    return entries


# =========================================================================

def _ensure_dict(val):
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip():
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}

def _ensure_list(val):
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val.strip():
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [tag.strip() for tag in val.split() if tag.strip()]
    return []

def _normalize_to_utc(scheduled_time: Union[datetime, str], timezone_str: Optional[str] = None) -> datetime:
    """
    Safely converts a requested scheduled time into naive UTC datetime.
    Handles ISO strings with/without Z/offset, and naive datetimes with optional business timezone.
    """
    if isinstance(scheduled_time, str):
        st_clean = scheduled_time.strip()
        if re.search(r'(?:Z|[+-]\d{2}:?\d{2})$', st_clean):
            dt = datetime.fromisoformat(st_clean.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        
        naive_dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                naive_dt = datetime.strptime(st_clean, fmt)
                break
            except ValueError:
                pass
        if not naive_dt:
            naive_dt = datetime.fromisoformat(st_clean)
        
        local_tz = pytz.timezone(timezone_str or "Asia/Kolkata")
        return local_tz.localize(naive_dt).astimezone(pytz.UTC).replace(tzinfo=None)

    elif isinstance(scheduled_time, datetime):
        if scheduled_time.tzinfo is not None:
            return scheduled_time.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            local_tz = pytz.timezone(timezone_str or "Asia/Kolkata")
            return local_tz.localize(scheduled_time).astimezone(pytz.UTC).replace(tzinfo=None)

    raise ValueError(f"Unsupported scheduled_time type: {type(scheduled_time)}")


def _format_utc_dt(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def serialize_autonomous_post(post: AutonomousPost) -> Dict[str, Any]:
    if not post:
        return {}
    
    # Brief
    brief_data = None
    if post.brief:
        b = post.brief
        brief_data = {
            "id": b.id,
            "topic": getattr(b, "topic", "Daily Content Brief") or "Daily Content Brief",
            "angle": getattr(b, "angle", "Commercial Showcase") or "Commercial Showcase",
            "reason": getattr(b, "reason", None),
            "caption_brief": getattr(b, "caption_brief", "") or "",
            "image_prompt": getattr(b, "image_prompt", "") or "",
            "video_prompt": getattr(b, "video_prompt", None),
            "hashtags": _ensure_list(getattr(b, "hashtags", [])),
            "cta": getattr(b, "cta", None),
            "tone": getattr(b, "tone", "Engaging") or "Engaging",
        }

    # Variants
    variants_data = []
    for v in (post.variants or []):
        variants_data.append({
            "id": v.id,
            "post_id": v.post_id,
            "platform": getattr(v, "platform", "instagram") or "instagram",
            "caption": getattr(v, "caption", "") or "",
            "title": getattr(v, "title", None),
            "hashtags": _ensure_list(getattr(v, "hashtags", [])),
            "media_path": getattr(v, "media_path", None),
            "media_type": getattr(v, "media_type", "image") or "image",
            "aspect_ratio": getattr(v, "aspect_ratio", "1:1") or "1:1",
            "scheduled_time": _format_utc_dt(getattr(v, "scheduled_time", None)),
            "status": getattr(v, "status", "scheduled") or "scheduled",
            "platform_post_id": getattr(v, "platform_post_id", None),
            "platform_url": getattr(v, "platform_url", None),
            "error": getattr(v, "error", None),
            "publish_metadata": _ensure_dict(getattr(v, "publish_metadata", {})),
            "retry_after": getattr(v, "retry_after", None),
        })

    # Assets
    assets_data = []
    try:
        raw_assets = post.assets or []
        for a in raw_assets:
            assets_data.append({
                "id": a.id,
                "type": getattr(a, "type", "image") or "image",
                "aspect_ratio": getattr(a, "aspect_ratio", "1:1") or "1:1",
                "file_path": getattr(a, "file_path", None),
                "status": getattr(a, "status", "done") or "done",
            })
    except Exception as asset_err:
        logger.warning(f"Error accessing post assets: {asset_err}")

    return {
        "id": post.id,
        "brief_id": getattr(post, "brief_id", None),
        "brand_profile_id": getattr(post, "brand_profile_id", None),
        "status": "ready_to_publish" if getattr(post, "status", "") in ("pending_approval", "pending") else ("published" if getattr(post, "status", "") == "posted" else (getattr(post, "status", "ready_to_publish") or "ready_to_publish")),
        "scheduled_time": _format_utc_dt(getattr(post, "scheduled_time", None)),
        "quality_report": _ensure_dict(getattr(post, "quality_report", {})),
        "auto_approved": bool(getattr(post, "auto_approved", False)),
        "rejection_reason": getattr(post, "rejection_reason", None),
        "editor_notes": getattr(post, "editor_notes", None),
        "published_at": getattr(post, "published_at", None),
        "created_at": getattr(post, "created_at", None),
        "updated_at": getattr(post, "updated_at", None),
        "brief": brief_data,
        "variants": variants_data,
        "assets": assets_data,
    }

# 2. REVIEW QUEUE & CONTENT WORKFLOW APIS
# =========================================================================

@router.get("/review-queue", response_model=List[AutonomousPostResponse])
def get_review_queue(
    status: Optional[str] = Query(None, description="Filter by status: pending_approval, scheduled, posted, draft, all"),
    brand_id: Optional[int] = None,
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    try:
        brand = _get_user_brand(db, current_user, brand_id)
        if not brand:
            return []

        query = db.query(AutonomousPost).options(
            joinedload(AutonomousPost.brief).joinedload(ContentBrief.assets),
            joinedload(AutonomousPost.variants)
        ).filter(AutonomousPost.brand_profile_id == brand.id)

        if status and status != "all":
            if status in ["ready_to_publish", "pending", "pending_approval"]:
                query = query.filter(AutonomousPost.status.in_(["ready_to_publish", "pending_approval", "pending"]))
            elif status in ["published", "posted"]:
                query = query.filter(AutonomousPost.status.in_(["published", "posted"]))
            elif status == "scheduled":
                query = query.filter(AutonomousPost.status.in_(["scheduled", "approved"]))
            elif status == "publishing":
                query = query.filter(AutonomousPost.status == "publishing")
            elif status == "generating":
                query = query.filter(AutonomousPost.status == "generating")
            elif status == "failed":
                query = query.filter(AutonomousPost.status == "failed")
            else:
                query = query.filter(AutonomousPost.status == status)

        posts = query.order_by(AutonomousPost.created_at.desc()).limit(limit).all()
        return [serialize_autonomous_post(p) for p in (posts or [])]
    except Exception as queue_err:
        logger.error(f"[ReviewQueue] Error loading review queue: {queue_err}", exc_info=True)
        return []


@router.post("/content/{content_id}/approve", response_model=AutonomousPostResponse)
def approve_content(
    content_id: int,
    payload: Optional[PostApproveRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == content_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Content item not found.")
    if current_user.organization_id and post.organization_id and post.organization_id != current_user.organization_id:
        raise HTTPException(status_code=403, detail="Access forbidden: content belongs to a different workspace.")

    schedule = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == post.brand_profile_id).first()
    auto_sched = getattr(schedule, "auto_schedule", True) if schedule else True
    now_utc = datetime.utcnow()

    if payload and payload.custom_scheduled_time:
        post.scheduled_time = payload.custom_scheduled_time
    elif not post.scheduled_time or post.scheduled_time <= now_utc:
        post.scheduled_time = now_utc

    if payload and payload.notes:
        post.editor_notes = payload.notes

    if auto_sched:
        # If post variants list is empty, synthesize variants for all connected accounts
        if not post.variants:
            connected_accounts = db.query(SocialAccount).filter(
                SocialAccount.brand_profile_id == post.brand_profile_id,
                SocialAccount.is_connected == True
            ).all()
            brief = post.brief
            raw_json = brief.raw_json if brief and brief.raw_json else {}
            topic_str = brief.topic if brief else "Featured Update"
            captions_map = raw_json.get("captions") or {}
            
            from app.models.autonomous_pipeline import GeneratedAsset
            assets = db.query(GeneratedAsset).filter(GeneratedAsset.brief_id == post.brief_id).all()
            first_img = next((a for a in assets if a.type == "image"), None)
            first_vid = next((a for a in assets if a.type == "video"), None)

            for sa in connected_accounts:
                m_path = first_vid.file_path if (sa.platform in {"youtube", "instagram", "tiktok"} and first_vid) else (first_img.file_path if first_img else None)
                m_type = "video" if (sa.platform in {"youtube", "instagram", "tiktok"} and first_vid) else "image"
                var = PostVariant(
                    post_id=post.id,
                    platform=sa.platform,
                    caption=captions_map.get(sa.platform, topic_str),
                    title=topic_str if sa.platform == "youtube" else None,
                    hashtags=brief.hashtags if brief else [],
                    media_path=m_path,
                    media_type=m_type,
                    aspect_ratio="9:16" if m_type == "video" else "1:1",
                    scheduled_time=post.scheduled_time,
                    status="scheduled"
                )
                db.add(var)
            db.commit()
            db.refresh(post)

        if post.scheduled_time <= now_utc + timedelta(minutes=1):
            post.status = "publishing"
            for variant in post.variants:
                if variant.status != "draft":
                    variant.status = "scheduled"
                    variant.scheduled_time = now_utc
            db.commit()
            db.refresh(post)
            from app.services.autonomous_publisher import publish_due_posts
            asyncio.create_task(publish_due_posts())
            logger.info(f"[Automation API] Content {content_id} approved and dispatched for immediate publishing.")
        else:
            post.status = "scheduled"
            for variant in post.variants:
                if variant.status != "draft":
                    variant.status = "scheduled"
                    variant.scheduled_time = post.scheduled_time
            db.commit()
            db.refresh(post)
            AutonomousScheduler.schedule_post_publication(post.id, post.scheduled_time)
            logger.info(f"[Automation API] Content {content_id} approved and scheduled for {post.scheduled_time}.")
    else:
        post.status = "approved"
        for variant in post.variants:
            variant.status = "approved"
        db.commit()
        db.refresh(post)
        logger.info(f"[Automation API] Content {content_id} approved without auto-schedule.")

    return serialize_autonomous_post(post)


@router.post("/content/{content_id}/reject", response_model=AutonomousPostResponse)
def reject_content(
    content_id: int,
    payload: PostRejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == content_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Content item not found.")
    if current_user.organization_id and post.organization_id and post.organization_id != current_user.organization_id:
        raise HTTPException(status_code=403, detail="Access forbidden: content belongs to a different workspace.")

    post.status = "rejected"
    post.rejection_reason = payload.reason
    for variant in post.variants:
        variant.status = "skipped"

    db.commit()
    db.refresh(post)
    AutonomousScheduler.cancel_post_publication(post.id)
    logger.info(f"[Automation API] Content {content_id} rejected. Reason: {payload.reason}")
    return serialize_autonomous_post(post)


@router.patch("/content/{content_id}", response_model=AutonomousPostResponse)
def edit_content(
    content_id: int,
    payload: ContentEditRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == content_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Content item not found.")
    if current_user.organization_id and post.organization_id and post.organization_id != current_user.organization_id:
        raise HTTPException(status_code=403, detail="Access forbidden: content belongs to a different workspace.")

    if payload.editor_notes is not None:
        post.editor_notes = payload.editor_notes
    if payload.scheduled_time is not None:
        tz_str = payload.timezone
        if not tz_str:
            sched = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == post.brand_profile_id).first()
            tz_str = sched.timezone if sched and sched.timezone else "Asia/Kolkata"
        utc_sched = _normalize_to_utc(payload.scheduled_time, tz_str)
        post.scheduled_time = utc_sched
        for v in post.variants:
            v.scheduled_time = utc_sched

    for v in post.variants:
        if payload.caption is not None:
            v.caption = payload.caption
        if payload.title is not None:
            v.title = payload.title
        if payload.hashtags is not None:
            v.hashtags = payload.hashtags

    db.commit()
    db.refresh(post)

    if post.status == "scheduled" and payload.scheduled_time is not None:
        AutonomousScheduler.schedule_post_publication(post.id, post.scheduled_time)

    return serialize_autonomous_post(post)


@router.post("/content/{content_id}/reschedule", response_model=AutonomousPostResponse)
def reschedule_content(
    content_id: int,
    payload: ContentRescheduleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == content_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Content item not found.")
    if current_user.organization_id and post.organization_id and post.organization_id != current_user.organization_id:
        raise HTTPException(status_code=403, detail="Access forbidden: content belongs to a different workspace.")

    tz_str = payload.timezone
    if not tz_str:
        sched = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == post.brand_profile_id).first()
        tz_str = sched.timezone if sched and sched.timezone else "Asia/Kolkata"

    utc_sched = _normalize_to_utc(payload.scheduled_time, tz_str)

    post.scheduled_time = utc_sched
    post.status = "scheduled"
    for v in post.variants:
        v.scheduled_time = utc_sched
        v.status = "scheduled"

    db.commit()
    db.refresh(post)
    AutonomousScheduler.schedule_post_publication(post.id, post.scheduled_time)
    logger.info(f"[Automation API] Content {content_id} rescheduled to {post.scheduled_time} UTC (target tz: {tz_str}).")
    return serialize_autonomous_post(post)


@router.post("/content/{content_id}/retry", response_model=Dict[str, Any])
async def retry_content_publication(
    content_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == content_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Content item not found.")
    if current_user.organization_id and post.organization_id and post.organization_id != current_user.organization_id:
        raise HTTPException(status_code=403, detail="Access forbidden: content belongs to a different workspace.")

    failed_variants = [v for v in post.variants if v.status == "failed"]
    if not failed_variants:
        # If none marked failed, re-mark variants that haven't posted yet
        failed_variants = [v for v in post.variants if v.status != "posted"]

    for v in failed_variants:
        v.status = "scheduled"
        v.scheduled_time = datetime.utcnow()
    post.status = "scheduled"
    db.commit()

    # Trigger publisher directly
    res = await publish_due_posts()
    db.refresh(post)
    return {
        "success": True,
        "message": f"Retry execution initiated for content {content_id}.",
        "status": post.status,
        "post_status": post.status,
        "publisher_result": res
    }
