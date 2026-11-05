import os
import uuid
import json
import asyncio
import pytz
from datetime import datetime, date, time as dt_time, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.logging import logger
from app.db.session import SessionLocal
from app.models.brand_profile import BrandProfile
from app.models.social_account import SocialAccount
from app.models.content import Content
from app.models.autonomous_pipeline import (
    BusinessSchedule,
    PipelineRun,
    ContentBrief,
    GeneratedAsset,
    AutonomousPost,
    PostVariant
)
from app.services.pipeline_gemini_service import PipelineGeminiService, CreativeBriefSchema, PlatformCaptionsSchema
from app.services.quality_service import QualityService


def calculate_utc_scheduled_time(posting_time_str: str, timezone_str: str, target_date: Optional[Any] = None) -> datetime:
    """
    Converts a business local posting time (e.g. '18:00' Asia/Kolkata) to a UTC datetime.
    Supports either datetime.date or 'YYYY-MM-DD' string for target_date.
    """
    local_tz = pytz.timezone(timezone_str or "Asia/Kolkata")
    explicit_date = False
    if isinstance(target_date, str) and target_date.strip():
        try:
            target_d = datetime.strptime(target_date.strip(), "%Y-%m-%d").date()
            explicit_date = True
        except Exception:
            target_d = datetime.now(local_tz).date()
    elif isinstance(target_date, date):
        target_d = target_date
        explicit_date = True
    else:
        target_d = datetime.now(local_tz).date()

    try:
        hour, minute = map(int, posting_time_str.split(":"))
    except Exception:
        hour, minute = 18, 0

    local_dt = local_tz.localize(datetime(target_d.year, target_d.month, target_d.day, hour, minute, 0))
    utc_dt = local_dt.astimezone(pytz.UTC).replace(tzinfo=None)

    now_local = datetime.now(local_tz)
    # If target_date is today, do NOT roll over to tomorrow if generation just completed at posting time.
    # Keep today's date so it publishes immediately. Only roll over if scheduled time was for a past calendar day.
    if not explicit_date and target_d < now_local.date():
        next_local = local_dt + timedelta(days=1)
        utc_dt = next_local.astimezone(pytz.UTC).replace(tzinfo=None)

    return utc_dt


async def run_pipeline(business_id: int, target_date: Optional[date] = None, force: bool = False) -> Dict[str, Any]:
    """
    Main Autonomous Pipeline Function.
    Fully self-contained, thread-safe, and multi-instance protected via DB lock.
    """
    today = target_date or date.today()
    db: Session = SessionLocal()
    run_record: Optional[PipelineRun] = None

    try:
        # 1. Acquire Multi-Instance Lock on (business_id, run_date)
        run_record = db.query(PipelineRun).filter(
            PipelineRun.brand_profile_id == business_id,
            PipelineRun.run_date == today
        ).first()

        if run_record and force:
            logger.info(f"[Pipeline] Forcing re-run for business {business_id} on {today}. Resetting previous run {run_record.id}.")
            db.delete(run_record)
            db.commit()
            run_record = None

        if run_record:
            if run_record.status == "completed":
                # Check if today's post has already been posted/published.
                # If the post has already posted, allow next scheduled cycle to generate fresh daily content
                existing_post = db.query(AutonomousPost).join(
                    ContentBrief, ContentBrief.id == AutonomousPost.brief_id
                ).filter(
                    ContentBrief.run_id == run_record.id
                ).first()
                if existing_post and existing_post.status == "posted":
                    logger.info(f"[Pipeline] Previous post {existing_post.id} for business {business_id} on {today} is published live. Resetting run record for next generation cycle.")
                    db.delete(run_record)
                    db.commit()
                    run_record = None
                else:
                    logger.info(f"[Pipeline] Business {business_id} already has an active completed run for {today}. Skipping.")
                    return {"status": "skipped", "reason": "already_completed", "run_id": run_record.id}
            elif run_record.status == "running":
                # If a run has been stuck in 'running' for more than 15 minutes, recover it
                run_start = run_record.started_at or datetime.utcnow()
                if (datetime.utcnow() - run_start).total_seconds() > 900:
                    logger.warning(f"[Pipeline] Business {business_id} run {run_record.id} timed out (>15m in running). Resetting to fresh run.")
                    db.delete(run_record)
                    db.commit()
                    run_record = None
                else:
                    logger.info(f"[Pipeline] Business {business_id} run for {today} is already running in another worker.")
                    return {"status": "skipped", "reason": "already_running", "run_id": run_record.id}
            else:
                # Retry previous failed/pending run
                run_record.status = "running"
                run_record.started_at = datetime.utcnow()
                db.commit()
        else:
            try:
                run_record = PipelineRun(
                    brand_profile_id=business_id,
                    run_date=today,
                    status="running",
                    started_at=datetime.utcnow()
                )
                db.add(run_record)
                db.commit()
                db.refresh(run_record)
            except IntegrityError:
                db.rollback()
                logger.info(f"[Pipeline] Concurrent lock acquisition detected for business {business_id}. Skipping.")
                return {"status": "skipped", "reason": "lock_contention"}

        # 2. Analyze Business Context & Enforce Required Brand Details
        brand: Optional[BrandProfile] = db.query(BrandProfile).filter(BrandProfile.id == business_id).first()
        from app.services.brand_service import is_brand_details_configured
        if not is_brand_details_configured(brand):
            logger.warning(f"[Pipeline] Business #{business_id} brand details not given or incomplete. Halting pipeline execution.")
            if run_record:
                run_record.status = "failed"
                run_record.error_log = "Brand details are required: Please configure your Brand Name and Industry in Brand Profile before generating autonomous content."
                run_record.completed_at = datetime.utcnow()
                db.commit()
            return {
                "status": "skipped",
                "reason": "missing_brand_details",
                "error": "Brand details are required: Please configure your Brand Name and Industry in Brand Profile before generating autonomous content."
            }

        schedule: Optional[BusinessSchedule] = db.query(BusinessSchedule).filter(
            BusinessSchedule.brand_profile_id == business_id
        ).first()
        if not schedule:
            # Create default schedule
            schedule = BusinessSchedule(
                brand_profile_id=business_id,
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

        # Retrieve connected social accounts (brand-specific or organization-level fallback)
        social_accounts = db.query(SocialAccount).filter(
            SocialAccount.brand_profile_id == business_id,
            SocialAccount.is_connected == True
        ).all()
        if not social_accounts and brand and brand.organization_id:
            social_accounts = db.query(SocialAccount).filter(
                SocialAccount.organization_id == brand.organization_id,
                SocialAccount.is_connected == True
            ).all()
        connected_platforms = [sa.platform.lower() for sa in social_accounts if sa.platform] or schedule.target_platforms or ["instagram", "facebook", "linkedin", "youtube"]

        past_30_days = datetime.utcnow() - timedelta(days=30)
        recent_contents = db.query(Content).filter(
            Content.brand_profile_id == business_id,
            Content.created_at >= past_30_days
        ).order_by(Content.created_at.desc()).limit(30).all()

        past_posts = [{"title": c.title, "caption": c.caption, "platforms": c.target_platforms} for c in recent_contents]
        # Include autonomous briefs too; otherwise the daily engine could
        # repeat yesterday's topic because those records are not Content rows.
        recent_briefs = db.query(ContentBrief).join(
            PipelineRun, ContentBrief.run_id == PipelineRun.id
        ).filter(
            ContentBrief.brand_profile_id == business_id,
            PipelineRun.started_at >= past_30_days
        ).order_by(PipelineRun.started_at.desc()).limit(30).all()
        past_posts.extend(
            {"title": b.topic, "caption": b.angle, "platforms": b.target_platforms}
            for b in recent_briefs
        )

        # 3. Step 2: Auto Topic & Creative Brief
        run_record.stage = "Analyzing Brand"
        run_record.progress_percent = 25
        db.commit()
        content_type = getattr(schedule, "content_type", "both") or "both"
        content_preferences = getattr(schedule, "content_preferences", None)

        logger.info(f"[Pipeline] Synthesizing creative brief for brand '{brand.name}' (type: {content_type})...")
        brief_data: CreativeBriefSchema = await PipelineGeminiService.generate_creative_brief(
            brand_profile=brand,
            past_posts=past_posts,
            target_platforms=connected_platforms,
            content_preferences=content_preferences,
            content_type=content_type
        )

        # A prior attempt can fail after saving its brief. Reuse that one on
        # retry instead of violating the one-brief-per-run database constraint.
        brief_model = db.query(ContentBrief).filter(ContentBrief.run_id == run_record.id).first()
        brief_values = {
            "brand_profile_id": business_id,
            "topic": brief_data.topic,
            "angle": brief_data.angle,
            "reason": brief_data.reason,
            "caption_brief": brief_data.caption_brief,
            "image_prompt": brief_data.image_prompt,
            "video_prompt": brief_data.video_prompt,
            "hashtags": brief_data.hashtags,
            "cta": brief_data.cta,
            "tone": brief_data.tone,
            "target_platforms": brief_data.target_platforms or connected_platforms,
            "raw_json": brief_data.model_dump(),
        }
        if brief_model:
            for field, value in brief_values.items():
                setattr(brief_model, field, value)
        else:
            brief_model = ContentBrief(run_id=run_record.id, **brief_values)
            db.add(brief_model)
        db.commit()
        db.refresh(brief_model)

        # 4. Step 3: Asset Generation (Stage 3A: Captions & Creative Images)
        run_record.stage = "Generating Copy & Images"
        run_record.progress_percent = 45
        db.commit()
        logger.info(f"[Pipeline] Generating creative copy and multi-ratio brand imagery for run {run_record.id}...")

        text_task = PipelineGeminiService.generate_platform_captions(brief=brief_data, brand_profile=brand)
        img_1_1_task = PipelineGeminiService.generate_branded_image(prompt=brief_data.image_prompt, aspect_ratio="1:1", brand_profile=brand)

        # Generate copy and master 1:1 image first
        captions_res, img_1_1_res = await asyncio.gather(
            text_task,
            img_1_1_task,
            return_exceptions=True
        )

        master_image_bytes = None
        if isinstance(img_1_1_res, dict):
            p = img_1_1_res.get("local_path") or img_1_1_res.get("file_path")
            if p and os.path.exists(p):
                try:
                    with open(p, "rb") as mf:
                        master_image_bytes = mf.read()
                except Exception:
                    pass

        # Generate 4:5 vertical and 1.91:1 landscape with master image derivation fallback
        img_4_5_res, img_land_res = await asyncio.gather(
            PipelineGeminiService.generate_branded_image(
                prompt=brief_data.image_prompt,
                aspect_ratio="4:5",
                brand_profile=brand,
                master_image_bytes=master_image_bytes
            ),
            PipelineGeminiService.generate_branded_image(
                prompt=brief_data.image_prompt,
                aspect_ratio="1.91:1",
                brand_profile=brand,
                master_image_bytes=master_image_bytes
            ),
            return_exceptions=True
        )

        # Collect all successfully generated brand images to supply to video engine
        brand_images = []
        for r in (img_4_5_res, img_1_1_res, img_land_res):
            if isinstance(r, dict):
                loc = r.get("local_path")
                if loc and os.path.exists(loc) and loc not in brand_images:
                    brand_images.append(loc)
                fp = r.get("file_path")
                if fp and fp not in brand_images:
                    brand_images.append(fp)

        primary_source = brand_images[0] if brand_images else None

        wants_images = content_type in ("image", "both")
        wants_videos = content_type in ("video", "both")

        video_res = None
        if wants_videos:
            # Stage 3B: Video Reel Generation (Real-Time Stage Update)
            run_record.stage = "Rendering 30s Video Reel"
            run_record.progress_percent = 65
            db.commit()
            logger.info(f"[Pipeline] Dispatching 30s video reel generation with {len(brand_images)} brand assets...")

            try:
                video_res = await asyncio.wait_for(
                    PipelineGeminiService.start_async_video_generation(
                        prompt=brief_data.video_prompt or brief_data.image_prompt,
                        aspect_ratio="9:16",
                        brand_profile=brand,
                        image_url=primary_source,
                        image_urls=brand_images
                    ),
                    timeout=180.0
                )
            except Exception as v_gen_err:
                logger.warning(f"[Pipeline] start_async_video_generation notice: {v_gen_err}")
                video_res = None

        # A pipeline run is not successful when no real image was generated.
        # Do not create an apparently publishable post with missing media.
        image_results = (img_1_1_res, img_4_5_res, img_land_res)
        if all(isinstance(result, Exception) for result in image_results):
            errors = "; ".join(str(result) for result in image_results)
            raise RuntimeError(f"Image generation failed for every requested aspect ratio: {errors}")

        # Fallbacks for any failed task
        if isinstance(captions_res, Exception):
            logger.error(f"[Pipeline] Captions generation exception: {captions_res}")
            captions_res = PlatformCaptionsSchema(
                instagram=f"✨ {brief_data.topic}\n\n{brief_data.caption_brief}\n\n👉 {brief_data.cta}\n\n" + " ".join(brief_data.hashtags),
                facebook=f"{brief_data.topic}\n\n{brief_data.caption_brief}\n\n{brief_data.cta}",
                linkedin=f"📌 {brief_data.topic}\n\n{brief_data.caption_brief}\n\n{brief_data.cta}",
                youtube_title=f"{brand.name} - {brief_data.topic}"[:70],
                youtube_description=f"{brief_data.caption_brief}\n\n{brief_data.cta}"
            )

        # Check if video was generated (either via Google Veo or Gemini Creative Studio reel engine)
        has_real_video = isinstance(video_res, dict) and video_res.get("file_path") and not isinstance(video_res, Exception)
        if not has_real_video:
            from app.services.video_generation_service import VideoGenerationService

            # Collect all available generated brand imagery (both local paths and Cloudinary URLs)
            brand_images = []
            for r in (img_4_5_res, img_1_1_res, img_land_res):
                if isinstance(r, dict):
                    loc = r.get("local_path")
                    if loc and os.path.exists(loc) and loc not in brand_images:
                        brand_images.append(loc)
                    fp = r.get("file_path")
                    if fp and fp not in brand_images:
                        brand_images.append(fp)

            primary_source = brand_images[0] if brand_images else None
            logger.info(f"[Pipeline] Rendering 30s video with Gemini AI Creative Studio multi-scene engine using {len(brand_images)} brand assets...")
            rendered = await asyncio.to_thread(
                VideoGenerationService.render_branded_promotional_video,
                prompt=brief_data.video_prompt or brief_data.image_prompt,
                brand=brand,
                aspect_ratio="9:16",
                duration_seconds=30,
                fps=24,
                voice=getattr(brand, "voice", None),
                image_url=primary_source,
                image_urls=brand_images,
                description=brief_data.caption_brief or brief_data.topic
            )
            video_res = {
                "operation_id": f"pipeline_vid_{uuid.uuid4().hex[:12]}",
                "status": "done",
                "file_path": rendered["video_url"],
                "local_path": rendered.get("local_path"),
                "duration_seconds": rendered.get("duration_seconds", 30),
                "aspect_ratio": "9:16",
                "mime_type": "video/mp4",
                "provider": "gemini-studio-video-engine",
                "voiceover_script": rendered.get("voiceover_script"),
                "has_voiceover": rendered.get("has_voiceover", True)
            }

        # Save Generated Assets to DB
        saved_assets = []
        for res, a_type, ratio in [
            (img_1_1_res, "image", "1:1"),
            (img_4_5_res, "image", "4:5"),
            (img_land_res, "image", "1.91:1"),
            (video_res, "video", "9:16"),
        ]:
            if isinstance(res, dict):
                asset_obj = GeneratedAsset(
                    brief_id=brief_model.id,
                    type=a_type,
                    status=res.get("status", "done"),
                    file_path=res.get("file_path"),
                    aspect_ratio=ratio,
                    operation_id=res.get("operation_id"),
                    attempts=1,
                    metadata_json=res
                )
                db.add(asset_obj)
                saved_assets.append(res)
            elif isinstance(res, Exception):
                logger.warning(f"[Pipeline] Asset generation for {a_type} ({ratio}) encountered error: {res}")

        db.commit()

        # 5. Step 4: Quality & Policy Check
        run_record.stage = "Validating Content"
        run_record.progress_percent = 85
        db.commit()
        platform_copy_dict = captions_res.model_dump()
        quality_audit = QualityService.audit_post(
            brand_profile=brand,
            brief_data=brief_data.model_dump(),
            platform_captions=platform_copy_dict,
            media_assets=saved_assets,
            past_posts=past_posts
        )

        # A duplicate is not merely a warning: create a fresh brief, captions,
        # images and video before the post can progress.  The hard limit avoids
        # an endless loop if a provider repeatedly returns the same response.
        for retry_number in range(2):
            if quality_audit.passed and quality_audit.badges.get("duplicate_free", True):
                break
            logger.warning(
                f"[Pipeline] Quality gate failed or duplicate detected (passed={quality_audit.passed}, "
                f"score={quality_audit.score}) for brand {business_id}; regenerating all assets on the spot (attempt {retry_number + 1}/2)."
            )
            brief_data = await PipelineGeminiService.generate_creative_brief(
                brand_profile=brand,
                past_posts=past_posts,
                target_platforms=connected_platforms,
            )
            captions_res, img_1_1_res = await asyncio.gather(
                PipelineGeminiService.generate_platform_captions(brief=brief_data, brand_profile=brand),
                PipelineGeminiService.generate_branded_image(prompt=brief_data.image_prompt, aspect_ratio="1:1", brand_profile=brand),
                return_exceptions=True
            )
            retry_master_bytes = None
            if isinstance(img_1_1_res, dict):
                p = img_1_1_res.get("local_path") or img_1_1_res.get("file_path")
                if p and os.path.exists(p):
                    try:
                        with open(p, "rb") as mf:
                            retry_master_bytes = mf.read()
                    except Exception:
                        pass
            img_4_5_res, img_land_res = await asyncio.gather(
                PipelineGeminiService.generate_branded_image(prompt=brief_data.image_prompt, aspect_ratio="4:5", brand_profile=brand, master_image_bytes=retry_master_bytes),
                PipelineGeminiService.generate_branded_image(prompt=brief_data.image_prompt, aspect_ratio="1.91:1", brand_profile=brand, master_image_bytes=retry_master_bytes),
                return_exceptions=True
            )
            retry_images = []
            for r in (img_4_5_res, img_1_1_res, img_land_res):
                if isinstance(r, dict):
                    loc = r.get("local_path")
                    if loc and os.path.exists(loc) and loc not in retry_images:
                        retry_images.append(loc)
                    fp = r.get("file_path")
                    if fp and fp not in retry_images:
                        retry_images.append(fp)
            retry_primary = retry_images[0] if retry_images else None
            try:
                video_res = await asyncio.wait_for(
                    PipelineGeminiService.start_async_video_generation(
                        prompt=brief_data.video_prompt or brief_data.image_prompt,
                        aspect_ratio="9:16",
                        brand_profile=brand,
                        image_url=retry_primary,
                        image_urls=retry_images
                    ),
                    timeout=180.0
                )
            except Exception as r_err:
                logger.warning(f"[Pipeline] Retry video generation warning: {r_err}")
                video_res = None
            if any(isinstance(result, Exception) for result in [captions_res, img_1_1_res, img_4_5_res, img_land_res, video_res]):
                raise RuntimeError("Duplicate-content regeneration did not produce a complete set of assets.")

            for field, value in {
                "topic": brief_data.topic, "angle": brief_data.angle, "reason": brief_data.reason,
                "caption_brief": brief_data.caption_brief, "image_prompt": brief_data.image_prompt,
                "video_prompt": brief_data.video_prompt, "hashtags": brief_data.hashtags,
                "cta": brief_data.cta, "tone": brief_data.tone,
                "target_platforms": brief_data.target_platforms or connected_platforms,
                "raw_json": brief_data.model_dump(),
            }.items():
                setattr(brief_model, field, value)

            db.query(GeneratedAsset).filter(GeneratedAsset.brief_id == brief_model.id).delete()
            saved_assets = []
            for result, asset_type, ratio in [
                (img_1_1_res, "image", "1:1"), (img_4_5_res, "image", "4:5"),
                (img_land_res, "image", "1.91:1"), (video_res, "video", "9:16"),
            ]:
                if isinstance(result, dict) and result.get("file_path"):
                    asset_obj = GeneratedAsset(
                        brief_id=brief_model.id, type=asset_type, status=result.get("status", "done"),
                        file_path=result.get("file_path"), aspect_ratio=ratio,
                        operation_id=result.get("operation_id"), attempts=retry_number + 2, metadata_json=result,
                    )
                    db.add(asset_obj)
                    saved_assets.append(result)
            db.commit()
            platform_copy_dict = captions_res.model_dump()
            quality_audit = QualityService.audit_post(
                brand_profile=brand, brief_data=brief_data.model_dump(),
                platform_captions=platform_copy_dict, media_assets=saved_assets, past_posts=past_posts,
            )

        # 6. Step 5: Post & Platform Variants Creation
        utc_sched_time = calculate_utc_scheduled_time(
            posting_time_str=schedule.posting_time,
            timezone_str=schedule.timezone,
            target_date=getattr(schedule, "posting_date", None)
        )

        is_approved = bool(schedule.auto_approve and quality_audit.passed)
        auto_schedule_enabled = getattr(schedule, "auto_schedule", True)

        if auto_schedule_enabled or is_approved:
            now_utc = datetime.utcnow()
            if utc_sched_time <= now_utc + timedelta(minutes=5):
                initial_post_status = "publishing"
            else:
                initial_post_status = "scheduled"
        else:
            initial_post_status = "ready_to_publish" 

        auto_post = AutonomousPost(
            brief_id=brief_model.id,
            brand_profile_id=business_id,
            organization_id=brand.organization_id if brand else None,
            status=initial_post_status,
            scheduled_time=utc_sched_time,
            quality_report=quality_audit.to_dict(),
            auto_approved=is_approved
        )
        db.add(auto_post)
        db.commit()
        db.refresh(auto_post)

        # Helper mapping for platform assets
        # Never attach a placeholder image: approval must show the actual
        # generated asset (or an explicit empty-media state).
        img_1_1_path = img_1_1_res.get("file_path") if isinstance(img_1_1_res, dict) else None
        img_4_5_path = img_4_5_res.get("file_path") if isinstance(img_4_5_res, dict) else img_1_1_path
        img_land_path = img_land_res.get("file_path") if isinstance(img_land_res, dict) else img_1_1_path
        vid_path = video_res.get("file_path") if isinstance(video_res, dict) else None

        # Build variants for all target platforms
        instagram_media_path = vid_path or img_4_5_path
        instagram_media_type = "video" if vid_path else "image"
        instagram_aspect_ratio = "9:16" if vid_path else "4:5"
        platform_configs = [
            ("instagram", platform_copy_dict.get("instagram", ""), None, instagram_media_path, instagram_media_type, instagram_aspect_ratio),
            ("facebook", platform_copy_dict.get("facebook", ""), None, img_1_1_path, "image", "1:1"),
            ("linkedin", platform_copy_dict.get("linkedin", ""), None, img_land_path, "image", "1.91:1"),
            ("youtube", platform_copy_dict.get("youtube_description", ""), platform_copy_dict.get("youtube_title", f"{brand.name} - {brief_data.topic}"), vid_path or img_land_path, "video" if vid_path else "image", "16:9" if not vid_path else "9:16"),
            ("twitter", platform_copy_dict.get("twitter") or platform_copy_dict.get("instagram", "")[:260], None, img_land_path, "image", "1.91:1"),
            ("pinterest", platform_copy_dict.get("pinterest") or platform_copy_dict.get("instagram", ""), f"{brief_data.topic}", img_4_5_path, "image", "4:5"),
        ]

        # Create variants for all major platforms so the user has the complete multi-channel
        # suite of captions, images, and video ready in the Review Queue.
        publish_platforms = set(connected_platforms)
        for plat_name, caption, title, m_path, m_type, ratio in platform_configs:
            # Connected networks are scheduled for automated release;
            # other generated platforms remain as ready drafts in the review queue.
            variant_status = initial_post_status if plat_name in publish_platforms else "draft"
            variant = PostVariant(
                post_id=auto_post.id,
                platform=plat_name,
                caption=caption,
                title=title,
                hashtags=brief_data.hashtags,
                media_path=m_path,
                media_type=m_type,
                aspect_ratio=ratio,
                scheduled_time=utc_sched_time,
                status=variant_status
            )
            db.add(variant)

        # 7. Step 6: Finalize Pipeline Run
        run_record.status = "completed"
        run_record.stage = "Scheduled" if initial_post_status == "scheduled" else "Awaiting Approval" if initial_post_status == "pending_approval" else "Approved"
        run_record.progress_percent = 100
        run_record.finished_at = datetime.utcnow()
        run_record.quality_score = quality_audit.score
        run_record.stats = {
            "post_id": auto_post.id,
            "brief_id": brief_model.id,
            "topic": brief_data.topic,
            "auto_approved": auto_post.auto_approved,
            "platforms_generated": connected_platforms,
            "duration_seconds": (run_record.finished_at - run_record.started_at).total_seconds()
        }
        db.commit()

        # Wait until every platform variant is committed before adding the
        # exact-time job. This prevents a "publish now" schedule from firing
        # before the generated package is complete.
        if initial_post_status == "publishing":
            from app.services.autonomous_publisher import publish_due_posts
            asyncio.create_task(publish_due_posts())
        elif initial_post_status == "scheduled":
            from app.scheduler.autonomous_scheduler import AutonomousScheduler
            AutonomousScheduler.schedule_post_publication(auto_post.id, utc_sched_time)

        logger.info(f"[Pipeline] Autonomous run completed successfully for business {business_id}. Post ID: {auto_post.id} (Status: {auto_post.status})")

        try:
            from app.services.notification_service import NotificationService
            status_desc = "Scheduled for dispatch" if initial_post_status == "scheduled" else "Awaiting your review and approval"
            NotificationService.create_notification(
                db=db,
                user_id=None,
                organization_id=brand.organization_id if brand else None,
                title="Autonomous Content Batch Synthesized",
                message=f"Pipeline generated {len(connected_platforms)} creative variants for '{brief_data.topic}'. {status_desc}.",
                event_type="pipeline_completed",
                severity="success",
                link="/dashboard/autonomous-pipeline",
                metadata={"post_id": auto_post.id, "run_id": run_record.id, "platforms": connected_platforms},
                dedup_key=f"pipeline_run_success_{run_record.id}"
            )
        except Exception as notif_err:
            logger.warning(f"Could not record pipeline notification: {notif_err}")

        return {
            "status": "success",
            "run_id": run_record.id,
            "post_id": auto_post.id,
            "quality_score": quality_audit.score,
            "auto_approved": auto_post.auto_approved
        }

    except Exception as e:
        logger.exception(f"[Pipeline] Autonomous pipeline error for business {business_id}: {e}")
        if run_record:
            run_record.status = "failed"
            run_record.stage = "Failed"
            run_record.error = str(e)
            run_record.finished_at = datetime.utcnow()
            try:
                db.commit()
            except Exception:
                pass

        try:
            from app.services.notification_service import NotificationService
            NotificationService.create_notification(
                db=db,
                user_id=None,
                organization_id=brand.organization_id if (brand and hasattr(brand, "organization_id")) else None,
                title="Autonomous Pipeline Failed",
                message=f"Autonomous generation failed: {str(e)[:130]}",
                event_type="pipeline_failed",
                severity="error",
                link="/dashboard/autonomous-pipeline",
                metadata={"error": str(e), "business_id": business_id},
                dedup_key=f"pipeline_run_fail_{run_record.id if run_record else business_id}_{datetime.utcnow().strftime('%Y%m%d%H%M')}"
            )
        except Exception as notif_err:
            pass

        return {"status": "failed", "error": str(e)}
    finally:
        db.close()


async def regenerate_post_part(post_id: int, part: str) -> Dict[str, Any]:
    """
    Regenerates a specific part of an autonomous post (caption | image | video).
    """
    db: Session = SessionLocal()
    try:
        post: Optional[AutonomousPost] = db.query(AutonomousPost).filter(AutonomousPost.id == post_id).first()
        if not post or not post.brief:
            raise ValueError("Post or brief not found.")

        brand: Optional[BrandProfile] = db.query(BrandProfile).filter(BrandProfile.id == post.brand_profile_id).first()
        brief = post.brief

        if part == "caption":
            brief_schema = CreativeBriefSchema(**brief.raw_json)
            new_captions = await PipelineGeminiService.generate_platform_captions(brief=brief_schema, brand_profile=brand)
            copy_dict = new_captions.model_dump()
            for variant in post.variants:
                if variant.platform in copy_dict:
                    variant.caption = copy_dict[variant.platform]
                if variant.platform == "youtube":
                    variant.title = copy_dict.get("youtube_title")
                    variant.caption = copy_dict.get("youtube_description")
            db.commit()
            return {"status": "success", "part": "caption", "captions": copy_dict}

        elif part == "image":
            new_img = await PipelineGeminiService.generate_branded_image(
                prompt=brief.image_prompt,
                aspect_ratio="1:1",
                brand_profile=brand
            )
            for variant in post.variants:
                if variant.media_type == "image":
                    variant.media_path = new_img["file_path"]
            # Register in generated_assets for the media gallery
            db.add(GeneratedAsset(
                brief_id=brief.id,
                type="image",
                status="done",
                file_path=new_img["file_path"],
                aspect_ratio="1:1",
                operation_id=new_img.get("operation_id"),
                attempts=1,
                metadata_json=new_img
            ))
            db.commit()
            return {"status": "success", "part": "image", "file_path": new_img["file_path"]}

        elif part == "video":
            # Gather existing image assets to weave into dynamic video scenes
            existing_imgs = [a.file_path for a in (brief.assets or []) if a.type == "image" and a.file_path]
            new_vid = await PipelineGeminiService.start_async_video_generation(
                prompt=brief.video_prompt or brief.image_prompt,
                aspect_ratio="9:16",
                brand_profile=brand,
                image_url=existing_imgs[0] if existing_imgs else None,
                image_urls=existing_imgs if existing_imgs else None
            )
            for variant in post.variants:
                if variant.platform in ["youtube", "instagram", "tiktok"] or variant.media_type == "video":
                    variant.media_path = new_vid["file_path"]
                    variant.media_type = "video"
            # Register in generated_assets for the media gallery
            db.add(GeneratedAsset(
                brief_id=brief.id,
                type="video",
                status="done",
                file_path=new_vid["file_path"],
                aspect_ratio="9:16",
                operation_id=new_vid.get("operation_id"),
                attempts=1,
                metadata_json=new_vid
            ))
            db.commit()
            return {"status": "success", "part": "video", "file_path": new_vid["file_path"]}

        else:
            raise ValueError(f"Unsupported regeneration part: {part}")

    finally:
        db.close()
