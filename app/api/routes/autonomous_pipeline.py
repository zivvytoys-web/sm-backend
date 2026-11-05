from datetime import datetime, date
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.session import SessionLocal
from app.db.dependencies import get_db
from app.core.permissions import get_current_active_user
from app.models.user import User
from app.models.brand_profile import BrandProfile
from app.models.autonomous_pipeline import (
    BusinessSchedule,
    PipelineRun,
    ContentBrief,
    AutonomousPost,
    PostVariant
)
from app.schemas.autonomous_pipeline import (
    BusinessScheduleResponse,
    BusinessScheduleUpdate,
    AutonomousPostResponse,
    AutonomousPostUpdate,
    PostVariantResponse,
    PostVariantUpdate,
    PostApproveRequest,
    PostRejectRequest,
    PostRegenerateRequest,
    PipelineRunResponse,
    RunNowRequest,
    RunNowResponse
)
from app.services.pipeline_service import run_pipeline, regenerate_post_part, calculate_utc_scheduled_time
from app.scheduler.autonomous_scheduler import AutonomousScheduler


router = APIRouter(prefix="/pipeline", tags=["Autonomous AI Pipeline"])


def _get_user_brand(db: Session, current_user: User, brand_id: Optional[int] = None) -> BrandProfile:
    """
    Helper to resolve the active brand profile for the user with strict multi-tenant isolation.
    """
    user_org_id = getattr(current_user, "organization_id", None)
    if brand_id:
        brand = db.query(BrandProfile).filter(BrandProfile.id == brand_id).first()
        if brand:
            if user_org_id and brand.organization_id == user_org_id:
                return brand
            user_role_str = str(getattr(current_user, "role", "")).lower()
            if "super_admin" in user_role_str:
                return brand
            # If client supplied a stale brand_id from another tenant, recover to tenant's own brand

    if user_org_id:
        brand = db.query(BrandProfile).filter(
            BrandProfile.organization_id == user_org_id
        ).first()
        if brand:
            return brand

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No brand profile found for current organization."
    )


# =========================================================================
# 1. SCHEDULE SETTINGS (GET & PUT)
# =========================================================================

def _enrich_schedule_response(schedule: BusinessSchedule, brand_id: int, db: Session) -> BusinessScheduleResponse:
    resp = BusinessScheduleResponse.model_validate(schedule)
    try:
        scheduler = AutonomousScheduler.get_scheduler()
        job = scheduler.get_job(f"generate_{brand_id}")
        if job and getattr(job, "next_run_time", None):
            resp.next_generation_time = job.next_run_time
    except Exception as e:
        logger.debug(f"[API] Could not query APScheduler job for brand {brand_id}: {e}")

    try:
        import pytz
        tz = pytz.timezone(schedule.timezone or "Asia/Kolkata")
        today = datetime.now(tz).date()
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

        if latest_post and latest_post.status == "scheduled" and latest_post.scheduled_time:
            resp.next_posting_time = latest_post.scheduled_time
        else:
            resp.next_posting_time = calculate_utc_scheduled_time(schedule.posting_time, schedule.timezone)
    except Exception as e:
        logger.debug(f"[API] Error enriching schedule metadata: {e}")

    resp.engine_status = "active" if schedule.is_active else "paused"
    return resp


@router.get("/schedule", response_model=BusinessScheduleResponse)
def get_schedule(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    schedule = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == brand.id).first()

    if not schedule:
        schedule = BusinessSchedule(
            brand_profile_id=brand.id,
            generation_time="09:00",
            posting_time="18:00",
            timezone="Asia/Kolkata",
            auto_approve=False,
            is_active=True,
            target_platforms=["instagram", "facebook", "linkedin", "youtube"]
        )
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
        # Register in scheduler
        AutonomousScheduler.reschedule_business_pipeline_job(brand.id, schedule)

    return _enrich_schedule_response(schedule, brand.id, db)


@router.put("/schedule", response_model=BusinessScheduleResponse)
def update_schedule(
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
    for field, val in update_data.items():
        setattr(schedule, field, val)

    # Move already-generated, not-yet-published packages to the newly selected
    # posting time. Otherwise editing the setting only affected future runs.
    if "posting_time" in update_data or "timezone" in update_data:
        new_posting_time = calculate_utc_scheduled_time(
            schedule.posting_time, schedule.timezone, target_date=None
        )
        existing = db.query(AutonomousPost).filter(
            AutonomousPost.brand_profile_id == brand.id,
            AutonomousPost.status.in_(["pending_approval", "scheduled"]),
        ).all()
        for post in existing:
            post.scheduled_time = new_posting_time
            for variant in post.variants:
                if variant.status in {"pending", "pending_approval", "scheduled"}:
                    variant.scheduled_time = new_posting_time

    # Enabling Auto-Schedule also releases already-generated, unreviewed
    # packages. Without this, the UI toggle only affected future runs and old
    # pending cards continued asking for manual approval.
    if schedule.auto_approve:
        pending_posts = db.query(AutonomousPost).filter(
            AutonomousPost.brand_profile_id == brand.id,
            AutonomousPost.status == "pending_approval",
        ).all()
        for pending in pending_posts:
            pending.status = "scheduled"
            pending.auto_approved = True
            for variant in pending.variants:
                if variant.status in {"pending", "pending_approval"}:
                    variant.status = "scheduled"

    db.commit()
    db.refresh(schedule)

    # Dynamically reschedule job in APScheduler
    AutonomousScheduler.reschedule_business_pipeline_job(brand.id, schedule)
    AutonomousScheduler.reschedule_business_publications(brand.id)
    logger.info(f"[API] Updated schedule for brand {brand.id}. Rescheduled in APScheduler.")

    return _enrich_schedule_response(schedule, brand.id, db)


# =========================================================================
# 2. AUTONOMOUS POSTS & APPROVAL WORKFLOW
# =========================================================================

@router.get("/posts", response_model=List[AutonomousPostResponse])
def list_autonomous_posts(
    status: Optional[str] = Query(None, description="Filter by status e.g. pending_approval, scheduled, posted, draft"),
    brand_id: Optional[int] = None,
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    query = db.query(AutonomousPost).filter(AutonomousPost.brand_profile_id == brand.id)
    
    if status:
        query = query.filter(AutonomousPost.status == status)

    posts = query.order_by(AutonomousPost.created_at.desc()).limit(limit).all()
    return posts


@router.get("/posts/{post_id}", response_model=AutonomousPostResponse)
def get_autonomous_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Autonomous post not found.")
    return post


@router.patch("/posts/{post_id}", response_model=AutonomousPostResponse)
def update_autonomous_post(
    post_id: int,
    payload: AutonomousPostUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Autonomous post not found.")

    if payload.editor_notes is not None:
        post.editor_notes = payload.editor_notes
    if payload.scheduled_time is not None:
        tz_str = getattr(payload, "timezone", None)
        if not tz_str:
            sched = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == post.brand_profile_id).first()
            tz_str = sched.timezone if sched and sched.timezone else "Asia/Kolkata"
        from app.api.routes.automation import _normalize_to_utc
        utc_sched = _normalize_to_utc(payload.scheduled_time, tz_str)
        post.scheduled_time = utc_sched
        for v in post.variants:
            v.scheduled_time = utc_sched

    db.commit()
    db.refresh(post)
    if post.status == "scheduled" and payload.scheduled_time is not None:
        AutonomousScheduler.schedule_post_publication(post.id, post.scheduled_time)
    return post


@router.patch("/variants/{variant_id}", response_model=PostVariantResponse)
def update_post_variant(
    variant_id: int,
    payload: PostVariantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    variant = db.query(PostVariant).filter(PostVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Post variant not found.")

    update_dict = payload.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        setattr(variant, field, val)

    db.commit()
    db.refresh(variant)
    return variant


@router.post("/posts/{post_id}/approve", response_model=AutonomousPostResponse)
def approve_autonomous_post(
    post_id: int,
    payload: Optional[PostApproveRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Autonomous post not found.")

    post.status = "scheduled"
    if payload and payload.custom_scheduled_time:
        post.scheduled_time = payload.custom_scheduled_time
    if payload and payload.notes:
        post.editor_notes = payload.notes

    for variant in post.variants:
        variant.status = "scheduled"
        if payload and payload.custom_scheduled_time:
            variant.scheduled_time = payload.custom_scheduled_time

    db.commit()
    db.refresh(post)
    AutonomousScheduler.schedule_post_publication(post.id, post.scheduled_time)
    logger.info(f"[API] Post {post_id} approved and marked scheduled for {post.scheduled_time}")
    return post


@router.post("/posts/{post_id}/reject", response_model=AutonomousPostResponse)
def reject_autonomous_post(
    post_id: int,
    payload: PostRejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Autonomous post not found.")

    post.status = "rejected"
    post.rejection_reason = payload.reason
    for variant in post.variants:
        variant.status = "skipped"

    db.commit()
    db.refresh(post)
    AutonomousScheduler.cancel_post_publication(post.id)
    logger.info(f"[API] Post {post_id} rejected. Reason: {payload.reason}")
    return post


@router.post("/posts/{post_id}/save-draft", response_model=AutonomousPostResponse)
def save_post_as_draft(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Autonomous post not found.")

    if post.status == "posted" or any(v.status == "posted" for v in post.variants):
        raise HTTPException(status_code=400, detail="Cannot convert an already published live post to draft.")

    post.status = "draft"
    for variant in post.variants:
        variant.status = "draft"

    db.commit()
    db.refresh(post)
    return post


@router.post("/posts/{post_id}/regenerate")
async def regenerate_post_component(
    post_id: int,
    payload: PostRegenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    post = db.query(AutonomousPost).filter(AutonomousPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Autonomous post not found.")

    res = await regenerate_post_part(post_id, payload.part)
    return res


# =========================================================================
# 3. RUN NOW & PIPELINE RUN LOGS
# =========================================================================

@router.post("/run-now", response_model=RunNowResponse)
async def trigger_run_now(
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
            detail="Brand details are required: Please configure your Brand Name and Industry in Brand Profile before generating autonomous content."
        )
    force = payload.force if payload else True

    # If force is true, clear today's completed run if present so test execution proceeds
    if force:
        # PipelineRun is keyed by the business-local calendar date.
        import pytz
        schedule = db.query(BusinessSchedule).filter(BusinessSchedule.brand_profile_id == brand.id).first()
        today = datetime.now(pytz.timezone(schedule.timezone if schedule else "Asia/Kolkata")).date()
        existing_run = db.query(PipelineRun).filter(
            PipelineRun.brand_profile_id == brand.id,
            PipelineRun.run_date == today
        ).first()
        if existing_run:
            db.delete(existing_run)
            db.commit()

    logger.info(f"[API] Triggering immediate autonomous pipeline run for brand {brand.id} ({brand.name})...")
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


@router.get("/runs", response_model=List[PipelineRunResponse])
def list_pipeline_runs(
    brand_id: Optional[int] = None,
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    brand = _get_user_brand(db, current_user, brand_id)
    runs = db.query(PipelineRun).filter(
        PipelineRun.brand_profile_id == brand.id
    ).order_by(PipelineRun.created_at.desc()).limit(limit).all()
    return runs
