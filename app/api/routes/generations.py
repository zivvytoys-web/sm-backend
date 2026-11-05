import asyncio
from typing import List, Optional
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, status
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.models.user import User
from app.models.brand_profile import BrandProfile
from app.models.generation_job import GenerationJob
from app.models.media_asset import MediaAsset
from app.schemas.generation import (
    GenerateTextRequest,
    GenerateImageRequest,
    GeminiTextGenerationRequest,
    GeminiImageGenerationRequest,
    GeminiVideoGenerationRequest,
    AutoPromptRequest,
    AutoPromptResponse,
    DailyTrendingGoalResponse,
    FullBundleGenerationRequest,
    FullBundleResponse,
    GeminiStatusResponse,
    GenerationJobResponse
)
from app.services.gemini_service import GeminiService
from app.services.image_generation_service import ImageGenerationService
from app.services.video_generation_service import VideoGenerationService
from app.services.usage_service import UsageService
from app.core.permissions import get_current_active_user, require_creator
from app.utils.enums import GenerationType, JobStatus, MediaType
from app.core.logging import logger

router = APIRouter(prefix="/generations", tags=["Google Gemini AI Generation Engine"])


# =========================================================================
# 1. API STATUS & CAPABILITIES
# =========================================================================
@router.get("/gemini/status", response_model=GeminiStatusResponse)
def get_gemini_status(
    current_user: User = Depends(get_current_active_user)
):
    """Check backend Gemini API key configuration and available models."""
    status_info = GeminiService.get_status()
    return status_info


# =========================================================================
# 2. AUTO-PROMPT GENERATION FROM BRAND PROFILE
# =========================================================================
@router.get("/gemini/daily-trending-goal", response_model=DailyTrendingGoalResponse)
async def get_daily_trending_goal(
    brand_id: Optional[int] = Query(None, description="Optional brand profile ID"),
    platform: Optional[str] = Query("instagram", description="Target platform"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Synthesize today's high-impact, trending Primary Campaign Goal designed
    to make the brand trend on social media by analyzing real audience telemetry and current platform algorithms.
    """
    brand = None
    if brand_id:
        brand = db.query(BrandProfile).filter(
            BrandProfile.id == brand_id,
            BrandProfile.organization_id == current_user.organization_id
        ).first()
    if not brand:
        brand = db.query(BrandProfile).filter(BrandProfile.organization_id == current_user.organization_id).first()
    if not brand:
        brand = db.query(BrandProfile).first()

    analytics_summary = {}
    try:
        from app.services.analytics_service import AnalyticsService
        org_id = current_user.organization_id or (brand.organization_id if brand else 1)
        dash = AnalyticsService.get_dashboard_analytics(
            db=db,
            organization_id=org_id,
            period="30d",
            brand_id=brand.id if brand else None
        )
        if dash:
            top_plat = dash.platform_breakdown[0].platform if dash.platform_breakdown else platform
            top_fmt = dash.format_breakdown[0].format if dash.format_breakdown else "Video Reel / Short"
            analytics_summary = {
                "total_impressions": dash.total_impressions,
                "total_reach": dash.total_reach,
                "total_engagements": dash.total_engagements,
                "engagement_rate": dash.average_engagement_rate,
                "top_platform": top_plat,
                "top_format": top_fmt,
                "connected_accounts_count": len(dash.connected_accounts or [])
            }
    except Exception as e:
        logger.warning(f"Could not load analytics summary for trending goal: {e}")

    result = await GeminiService.generate_daily_trending_goal(
        brand=brand,
        analytics_summary=analytics_summary,
        target_platform=platform
    )

    return DailyTrendingGoalResponse(
        brand_name=result.get("brand_name", brand.name if brand else "Brand"),
        campaign_goal=result.get("campaign_goal", "Drive Viral Social Reach and Authentic Community Engagement"),
        analytics_rationale=result.get("analytics_rationale", "Optimized using live platform engagement telemetry and daily algorithmic retention signals."),
        target_platform=result.get("target_platform", platform or "instagram"),
        suggested_format=result.get("suggested_format", "Video Reel / Short"),
        trending_hashtags=result.get("trending_hashtags", []),
        creative_angle=result.get("creative_angle", "Viral Velocity & Trend Hook"),
        metrics_summary=analytics_summary
    )


@router.post("/gemini/auto-prompt", response_model=AutoPromptResponse)
async def generate_auto_prompt_from_brand(
    payload: AutoPromptRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Automatically synthesize synchronized Text, Image, and Video prompts based on Brand Profile."""
    brand = None
    if payload.brand_profile_id:
        brand = db.query(BrandProfile).filter(
            BrandProfile.id == payload.brand_profile_id,
            BrandProfile.organization_id == current_user.organization_id
        ).first()
    if not brand:
        brand = db.query(BrandProfile).filter(BrandProfile.organization_id == current_user.organization_id).first()

    target_plat = "instagram"
    if payload.target_platform:
        target_plat = payload.target_platform.value if hasattr(payload.target_platform, "value") else str(payload.target_platform)

    recent_jobs = db.query(GenerationJob).filter(
        GenerationJob.user_id == current_user.id
    ).order_by(GenerationJob.created_at.desc()).limit(6).all()
    recent_parts = []
    for previous in recent_jobs:
        data = previous.result_data if isinstance(previous.result_data, dict) else {}
        item_text = data.get("text") if isinstance(data.get("text"), dict) else data
        headline = item_text.get("headline") if isinstance(item_text, dict) else None
        caption = item_text.get("caption") if isinstance(item_text, dict) else None
        if headline or caption:
            c_preview = (caption or "")[:100]
            recent_parts.append(f"Headline: {headline or ''}; Caption: {c_preview}")

    seed_val = payload.freshness_seed or str(uuid4())[:8]
    uniqueness_context = (
        f"FRESHNESS AND CREATIVE ANGLE REQUIREMENT (Seed: {seed_val}):\n"
        "Generate an original, distinct creative perspective. Avoid repeating the exact headline, visuals, and copy from recent creations.\n"
        + ("Recent outputs to diverge from:\n" + "\n".join(recent_parts) if recent_parts else "")
    )

    analytics_summary = {}
    if not payload.campaign_goal or str(payload.campaign_goal).strip().lower() in ["auto", "trending", "daily", "ai"]:
        try:
            from app.services.analytics_service import AnalyticsService
            org_id = current_user.organization_id or (brand.organization_id if brand else 1)
            dash = AnalyticsService.get_dashboard_analytics(
                db=db,
                organization_id=org_id,
                period="30d",
                brand_id=brand.id if brand else None
            )
            if dash:
                analytics_summary = {
                    "total_impressions": dash.total_impressions,
                    "total_reach": dash.total_reach,
                    "total_engagements": dash.total_engagements,
                    "engagement_rate": dash.average_engagement_rate,
                    "top_platform": dash.platform_breakdown[0].platform if dash.platform_breakdown else target_plat,
                    "top_format": dash.format_breakdown[0].format if dash.format_breakdown else "Video Reel / Short",
                    "connected_accounts_count": len(dash.connected_accounts or [])
                }
        except Exception as err:
            logger.warning(f"Could not load analytics summary for auto-prompt: {err}")

    result = await GeminiService.generate_auto_prompts(
        brand=brand,
        campaign_goal=payload.campaign_goal,
        custom_focus=payload.custom_focus,
        target_platform=target_plat,
        uniqueness_context=uniqueness_context,
        freshness_seed=seed_val,
        creative_angle=payload.creative_angle,
        analytics_summary=analytics_summary
    )
    return result


# =========================================================================
# 3. FULL POST BUNDLE GENERATOR (Text + Description + Image + Video Script)
# =========================================================================
@router.post("/gemini/full-bundle", response_model=FullBundleResponse)
async def generate_full_post_bundle(
    payload: FullBundleGenerationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Generate complete social media campaign bundle with text description, image artwork, and video storyboard in one click."""
    UsageService.check_quota(db, current_user.organization_id, "ai_generations")

    # Strict Multi-Tenant Brand Resolution & Validation
    brand = None
    if payload.brand_profile_id:
        brand = db.query(BrandProfile).filter(
            BrandProfile.id == payload.brand_profile_id,
            BrandProfile.organization_id == current_user.organization_id
        ).first()
        if not brand:
            foreign = db.query(BrandProfile).filter(BrandProfile.id == payload.brand_profile_id).first()
            if foreign:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Cross-tenant brand profile access is strictly forbidden."
                )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Brand profile #{payload.brand_profile_id} was not found in your organization."
            )
    else:
        brand = db.query(BrandProfile).filter(
            BrandProfile.organization_id == current_user.organization_id
        ).order_by(BrandProfile.updated_at.desc(), BrandProfile.id.desc()).first()

    if not brand:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid Brand Profile is required before generating campaigns. Please configure your Brand Profile first."
        )

    VideoGenerationService.validate_brand_profile(brand)
    brand_snapshot = VideoGenerationService.create_brand_snapshot(brand)
    brand_name = brand.name
    brand_tone = payload.tone or (brand.tone_of_voice if brand else "Engaging")

    target_platform_str = "instagram"
    if payload.target_platforms and len(payload.target_platforms) > 0:
        first_plat = payload.target_platforms[0]
        target_platform_str = first_plat.value if hasattr(first_plat, "value") else str(first_plat)

    # Give Gemini recent outputs and a per-click seed so repeated requests are
    # explicitly required to take a new creative angle.
    recent_jobs = db.query(GenerationJob).filter(
        GenerationJob.user_id == current_user.id
    ).order_by(GenerationJob.created_at.desc()).limit(12).all()
    recent_parts = []
    for previous in recent_jobs:
        data = previous.result_data if isinstance(previous.result_data, dict) else {}
        text = data.get("text") if isinstance(data.get("text"), dict) else data
        headline = text.get("headline") if isinstance(text, dict) else None
        caption = text.get("caption") if isinstance(text, dict) else None
        if headline or caption:
            recent_parts.append(f"Headline: {headline or ''}; Caption: {(caption or '')[:180]}")
    uniqueness_seed = payload.variation_seed or str(uuid4())
    uniqueness_context = (
        f"UNIQUENESS REQUIREMENT (seed {uniqueness_seed}): This is a new generation. "
        "Use a substantially different hook, headline, offer framing, CTA, hashtag mix, "
        "and visual/creative angle from every previous output. Do not reuse wording.\n"
        + ("Recent outputs to avoid:\n" + "\n".join(recent_parts) if recent_parts else "No previous output is available.")
    )

    target_platform_list = [p.value if hasattr(p, "value") else str(p) for p in payload.target_platforms]

    # 1. Resolve prompts
    if payload.prompt and len(payload.prompt.strip()) > 5:
        text_prompt = payload.prompt.strip()
        import re
        # Aggressively clean out copywriting commands and directives
        clean_subj = text_prompt
        clean_subj = re.sub(r"(?i)^write\s+(?:an?\s+)?(?:[\w-]+\s*,?\s*)*(?:caption|post|announcement|copy|description)\s*(?:for|about|featuring)?", "", clean_subj).strip()
        clean_subj = re.sub(r"(?i)(?:focus\s+on|use\s+an?\s+[\w-]+\s+tone|highlight|include|ensure|call\s+to\s+action).*$", "", clean_subj).strip()
        clean_subj = re.sub(r"(?i)(?:instagram|facebook|linkedin|tiktok|youtube|twitter)\s+caption:?.*$", "", clean_subj).strip()
        clean_subj = re.sub(r"#\w+", "", clean_subj).strip(" :.-,;")

        # Extract authentic products/industry from brand profile (zero hardcoding)
        brand_ind = (brand.industry if brand and brand.industry else "").strip()
        prods_list = []
        if brand and hasattr(brand, "meta_info") and isinstance(brand.meta_info, dict):
            p_items = (
                brand.meta_info.get("products", [])
                or brand.meta_info.get("offerings", [])
                or brand.meta_info.get("catalog", [])
            )
            if isinstance(p_items, list):
                for p in p_items:
                    if isinstance(p, dict) and p.get("name"):
                        prods_list.append(p.get("name"))
                    elif isinstance(p, str) and p.strip():
                        prods_list.append(p.strip())
        if not prods_list and brand and getattr(brand, "key_themes", None):
            prods_list = [t for t in brand.key_themes[:2] if isinstance(t, str)]

        prods_focus = ", ".join(prods_list[:2]) if prods_list else ""
        if prods_focus and brand_ind:
            visual_target = f"{brand_name} {prods_focus} ({brand_ind})"
        elif prods_focus:
            visual_target = f"{brand_name} {prods_focus}"
        elif brand_ind:
            visual_target = f"{brand_name} {brand_ind}"
        elif clean_subj and len(clean_subj) > 3:
            visual_target = f"{brand_name}: {clean_subj[:60]}"
        else:
            visual_target = brand_name

        concept_detail = f", inspired by creative concept '{clean_subj[:80]}'" if clean_subj and len(clean_subj) > 3 else ""
        import random
        lighting_styles = [
            "dramatic directional studio rim lighting, pristine pedestal showcase",
            "soft diffused natural daylight, contemporary organic styling",
            "striking high-contrast chiaroscuro lighting, subtle reflections",
            "warm golden hour side illumination, atmospheric depth of field"
        ]
        chosen_lighting = random.choice(lighting_styles)
        unique_token = uuid4().hex[:6]

        image_prompt = (
            f"Commercial high-end photoshoot of {visual_target}{concept_detail}. "
            f"{chosen_lighting}, sharp macro details, crisp material textures, 8k resolution, "
            f"product only, no humans, no models, no chairs, clean visual, no watermark, no text (shot {unique_token})"
        )
        video_prompt = (
            f"Cinematic dynamic video commercial reel showcasing {visual_target}{concept_detail}: "
            f"30s camera motion gliding across authentic product craftsmanship, macro texture reveals, cinematic studio lighting, "
            f"sharp focus, photorealistic product only, no humans, no models"
        )
    else:
        prompts_blueprint = await GeminiService.generate_auto_prompts(
            brand=brand,
            campaign_goal=payload.campaign_goal,
            custom_focus=payload.prompt,
            target_platform=target_platform_str,
            uniqueness_context=uniqueness_context
        )
        text_prompt = prompts_blueprint.get("text_prompt", f"Post for {brand_name}")
        image_prompt = prompts_blueprint.get("image_prompt", f"Commercial product photoshoot for {brand_name}")
        video_prompt = prompts_blueprint.get("video_prompt", f"Cinematic video reel for {brand_name}")

    # 2. Run Text and Image Generation in Parallel
    async def _run_text():
        return await GeminiService.generate_text(
            prompt=text_prompt,
            brand_name=brand_name,
            brand_tone=brand_tone,
            target_platforms=target_platform_list,
            content_type="caption",
            tone=brand_tone,
            language=payload.language,
            additional_instructions=uniqueness_context,
            brand=brand
        )

    async def _run_image():
        try:
            image_job = await ImageGenerationService.generate_image(
                GeminiImageGenerationRequest(
                    prompt=image_prompt,
                    brand_profile_id=brand.id if brand else payload.brand_profile_id,
                    aspect_ratio=payload.aspect_ratio,
                    style_preset="modern_product",
                ),
                db,
                current_user,
            )
            img_data = dict(image_job.result_data or {})
            if image_job and image_job.result_data and image_job.result_data.get("media_asset_id"):
                img_data["media_asset_id"] = image_job.result_data["media_asset_id"]
                img_data["quality_check"] = image_job.result_data.get("quality_check")
            return img_data
        except Exception as img_err:
            logger.warning(f"Bundle image generation fallback ({img_err})")
            return {
                "prompt": image_prompt,
                "style_preset": "modern_product",
                "aspect_ratio": payload.aspect_ratio or "1:1",
                "image_url": None
            }

    text_data, image_data = await asyncio.gather(_run_text(), _run_image())

    # Validate Bundle Text Description
    from app.services.quality_check_engine import QualityCheckEngine
    text_val = QualityCheckEngine.validate_description(
        caption=text_data.get("caption") or text_data.get("headline") or text_prompt,
        title=text_data.get("headline"),
        hashtags=text_data.get("hashtags", []),
        brand=brand,
        target_platforms=[target_platform_str],
        organization_id=current_user.organization_id,
        db=db
    )
    text_data["quality_check"] = text_val

    # 4. Prepare and dispatch Cinematic 30s Video Reel in background
    selected_bundle_provider = (payload.provider or "auto").strip().lower()
    bundle_job_params = {
        "prompt": video_prompt,
        "aspect_ratio": payload.aspect_ratio or "16:9",
        "duration_seconds": 30,
        "fps": 24,
        "resolution": payload.resolution or "1080p",
        "voice": payload.voice,
        "image_url": image_data.get("image_url"),
        "provider": selected_bundle_provider,
        "brand_profile_id": brand.id,
        "brand_snapshot": brand_snapshot
    }
    video_job = GenerationJob(
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        job_type=GenerationType.VIDEO_GENERATION,
        status=JobStatus.PROCESSING,
        prompt=video_prompt,
        model_name=selected_bundle_provider,
        parameters=bundle_job_params,
        result_data={
            "status": "processing",
            "progress_message": f"Rendering 30s branded video reel for {brand.name}...",
            "video_prompt": video_prompt,
            "script_scenes": text_data.get("script_scenes", []),
            "aspect_ratio": payload.aspect_ratio or "16:9",
            "duration_seconds": 30,
            "has_voiceover": True,
            "voice_id": payload.voice or "en-US-AriaNeural",
            "provider": selected_bundle_provider,
            "brand_profile_id": brand.id,
            "brand_name": brand.name,
            "brand_industry": brand.industry,
            "brand_snapshot": brand_snapshot
        },
        tokens_used=600
    )
    db.add(video_job)
    db.commit()
    db.refresh(video_job)

    if background_tasks:
        background_tasks.add_task(
            VideoGenerationService._execute_video_generation_background,
            video_job.id,
            video_job.parameters,
            current_user.id,
            current_user.organization_id,
            brand.id if brand else None
        )
    else:
        asyncio.create_task(
            VideoGenerationService._execute_video_generation_background(
                video_job.id,
                video_job.parameters,
                current_user.id,
                current_user.organization_id,
                brand.id if brand else None
            )
        )

    video_data = {
        "job_id": video_job.id,
        "status": "processing",
        "video_prompt": video_prompt,
        "script_scenes": text_data.get("script_scenes", []),
        "aspect_ratio": payload.aspect_ratio or "16:9",
        "duration_seconds": 30,
        "has_voiceover": True,
        "voice_id": payload.voice or "en-US-AriaNeural",
        "provider": "multi-scene-cinema-director"
    }

    # 5. Save to Generation Job
    job = GenerationJob(
        user_id=current_user.id,
        job_type=GenerationType.TEXT_MARKETING,
        status=JobStatus.COMPLETED,
        prompt=text_prompt,
        model_name="gemini-3.1-flash-lite",
        parameters=payload.model_dump(),
        result_data={
            "text": text_data,
            "image": image_data,
            "video": video_data,
            "video_prompt": video_prompt,
            "script_scenes": text_data.get("script_scenes", [])
        },
        tokens_used=500
    )
    db.add(job)
    UsageService.record_usage(db, current_user.organization_id, "ai_generations", 3)
    db.commit()

    return {
        "text": text_data,
        "image": image_data,
        "video": video_data,
        "brand_name": brand_name,
        "created_at": text_data.get("generated_at", "")
    }


# =========================================================================
# 4. GEMINI TEXT GENERATION
# =========================================================================
@router.post("/gemini/text", response_model=GenerationJobResponse, status_code=status.HTTP_201_CREATED)
async def generate_gemini_text(
    payload: GeminiTextGenerationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Generate high-impact social captions, hashtags, campaign copy, or variations using Google Gemini."""
    UsageService.check_quota(db, current_user.organization_id, "ai_generations")

    brand = None
    if payload.brand_profile_id:
        brand = db.query(BrandProfile).filter(
            BrandProfile.id == payload.brand_profile_id,
            BrandProfile.organization_id == current_user.organization_id
        ).first()
    if not brand:
        brand = db.query(BrandProfile).filter(BrandProfile.organization_id == current_user.organization_id).first()

    brand_name = brand.name if brand else ""
    brand_tone = brand.tone_of_voice if brand else ""

    platforms = [p.value if hasattr(p, "value") else str(p) for p in payload.target_platforms]

    recent_jobs = db.query(GenerationJob).filter(
        GenerationJob.user_id == current_user.id
    ).order_by(GenerationJob.created_at.desc()).limit(10).all()
    recent_headlines = []
    for previous in recent_jobs:
        data = previous.result_data if isinstance(previous.result_data, dict) else {}
        if data.get("headline"):
            recent_headlines.append(str(data["headline"]))
    uniqueness = (
        f"Fresh-generation seed: {payload.variation_seed or uuid4()}. "
        "Create a substantially different hook, headline, caption, CTA and hashtag strategy. "
        + ("Do not reuse these recent headlines: " + "; ".join(recent_headlines) if recent_headlines else "")
    )

    try:
        result = await GeminiService.generate_text(
            prompt=payload.prompt,
            brand_name=brand_name,
            brand_tone=brand_tone,
            target_platforms=platforms,
            content_type=payload.content_type,
            tone=payload.tone,
            language=payload.language,
            additional_instructions=f"{payload.additional_instructions or ''}\n{uniqueness}",
            model_name=payload.model_name,
            temperature=payload.temperature,
            num_variations=payload.num_variations,
            brand=brand
        )
    except Exception as e:
        logger.error(f"Gemini text generation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Gemini generation error: {str(e)}"
        )

    # Run Real-Time Quality & Duplicate Check with Automatic Regeneration
    from app.services.quality_check_engine import QualityCheckEngine
    from app.models.quality_report import QualityReport

    text_caption = result.get("caption") or result.get("headline") or payload.prompt
    val = QualityCheckEngine.validate_description(
        caption=text_caption,
        title=result.get("headline"),
        hashtags=result.get("hashtags", []),
        brand=brand,
        target_platforms=platforms,
        organization_id=current_user.organization_id,
        db=db
    )
    result["quality_check"] = val

    # Auto-regeneration loop (max 3 total attempts as specified in section 7)
    retry_count = 0
    while val.get("decision") in ["REGENERATE", "BLOCK"] and retry_count < 2:
        retry_count += 1
        logger.info(f"Duplicate or policy check triggered auto-regeneration (attempt {retry_count + 1})...")
        try:
            fresh_seed = f"retry_{uuid4()}"
            result = await GeminiService.generate_text(
                prompt=payload.prompt,
                brand_name=brand_name,
                brand_tone=brand_tone,
                target_platforms=platforms,
                content_type=payload.content_type,
                tone=payload.tone,
                language=payload.language,
                additional_instructions=f"{payload.additional_instructions or ''}\nCRITICAL UNIQUENESS RETRY {retry_count}: Create an entirely fresh hook, vocabulary, and storyline. Avoid recent phrases. Seed: {fresh_seed}",
                model_name=payload.model_name,
                temperature=0.9,
                num_variations=payload.num_variations,
                brand=brand
            )
            text_caption = result.get("caption") or result.get("headline") or payload.prompt
            val = QualityCheckEngine.validate_description(
                caption=text_caption,
                title=result.get("headline"),
                hashtags=result.get("hashtags", []),
                brand=brand,
                target_platforms=platforms,
                organization_id=current_user.organization_id,
                db=db
            )
            val["retry_count"] = retry_count
            result["quality_check"] = val
        except Exception as retry_err:
            logger.warning(f"Auto-regeneration attempt failed: {retry_err}")
            break

    if retry_count >= 2 and val.get("decision") in ["REGENERATE", "BLOCK"]:
        val["decision"] = "BLOCK"
        val["failed_checks"] = list(val.get("failed_checks", [])) + ["RETRY_LIMIT_EXHAUSTED"]
        val["retry_exhausted"] = True
    val["retry_count"] = retry_count + 1
    result["quality_check"] = val

    # Persist QualityReport for text generation
    qr = QualityReport(
        asset_type="text",
        organization_id=current_user.organization_id,
        brand_profile_id=brand.id if brand else None,
        decision=val.get("decision", "PASS"),
        quality_score=val.get("quality_score", 100.0),
        technical_score=val.get("technical_score", 100.0),
        brand_score=val.get("brand_score", 100.0),
        content_accuracy_score=100.0,
        duplicate_status=val.get("duplicate_status", "UNIQUE"),
        similarity_score=val.get("similarity_score", 0.0),
        matching_content_id=val.get("matching_content_id"),
        failed_checks=val.get("failed_checks", []),
        issues=val.get("issues", []),
        suggestions=val.get("suggestions", []),
        match_details=val.get("match_details", {}),
        metrics=val.get("metrics", {}),
        retry_count=retry_count
    )
    db.add(qr)
    job_type = GenerationType.TEXT_CAPTION if payload.content_type == "caption" else GenerationType.TEXT_MARKETING
    if payload.content_type == "hashtags":
        job_type = GenerationType.TEXT_HASHTAGS
    elif payload.content_type == "marketing_campaign":
        job_type = GenerationType.TEXT_CAPTION if payload.content_type == "caption" else GenerationType.TEXT_MARKETING
    elif payload.content_type == "variations":
        job_type = GenerationType.TEXT_VARIATIONS
    elif payload.content_type == "video_script":
        job_type = GenerationType.VIDEO_SCRIPT

    job = GenerationJob(
        user_id=current_user.id,
        job_type=job_type,
        status=JobStatus.COMPLETED,
        prompt=payload.prompt,
        model_name=result.get("model_name", payload.model_name),
        parameters=payload.model_dump(),
        result_data=result,
        tokens_used=result.get("tokens_used", 180)
    )
    db.add(job)
    UsageService.record_usage(db, current_user.organization_id, "ai_generations", 1)
    db.commit()
    db.refresh(job)
    return job


# Legacy text endpoint
@router.post("/text", response_model=GenerationJobResponse, status_code=status.HTTP_201_CREATED)
async def generate_text(
    payload: GenerateTextRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Generate social captions, hooks, and hashtags matching brand voice."""
    gemini_req = GeminiTextGenerationRequest(
        prompt=payload.prompt,
        brand_profile_id=payload.brand_profile_id,
        target_platforms=payload.target_platforms,
        tone=payload.tone_override or "Engaging",
        model_name="gemini-3.1-flash-lite"
    )
    return await generate_gemini_text(gemini_req, db, current_user)


# =========================================================================
# =========================================================================
# 5. GOOGLE IMAGEN & VISUAL CREATIVE GENERATION
# =========================================================================
@router.post("/gemini/image", response_model=GenerationJobResponse, status_code=status.HTTP_201_CREATED)
async def generate_gemini_image(
    payload: GeminiImageGenerationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Generate promotional social visual assets with Google Gen AI & Visual Studio."""
    return await ImageGenerationService.generate_image(payload, db, current_user)


# Legacy image endpoint
@router.post("/image", response_model=GenerationJobResponse, status_code=status.HTTP_201_CREATED)
async def generate_image(
    payload: GenerateImageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Generate promotional social image asset via AI."""
    gemini_req = GeminiImageGenerationRequest(
        prompt=payload.prompt,
        brand_profile_id=payload.brand_profile_id,
        aspect_ratio=payload.aspect_ratio,
        style_preset="modern_product",
        model_name="gemini-2.5-flash-image"
    )
    return await ImageGenerationService.generate_image(gemini_req, db, current_user)


# =========================================================================
# 6. VIDEO GENERATION (GOOGLE VEO OR BRANDED VIDEO REEL ENGINE)
# =========================================================================
@router.post("/gemini/video", response_model=GenerationJobResponse, status_code=status.HTTP_200_OK)
async def generate_gemini_video(
    payload: GeminiVideoGenerationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Generate playable branded promotional video reel via Google Veo or dynamic multi-scene video engine."""
    return await VideoGenerationService.generate_video(payload, db, current_user, background_tasks)



# =========================================================================
# 7. JOB STATUS & HISTORY MANAGEMENT
# =========================================================================
@router.get("/jobs/{job_id}", response_model=GenerationJobResponse)
def get_job_status(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve real-time status and generated output for a specific generation job."""
    job = db.query(GenerationJob).filter(
        GenerationJob.id == job_id,
        GenerationJob.user_id == current_user.id
    ).first()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation job not found or you do not have permission to view it."
        )

    return job


@router.get("/history", response_model=List[GenerationJobResponse])
def get_generation_history(
    job_type: Optional[str] = Query(None, description="Optional filter by job type"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve history of AI generated text, images, and videos for the current authenticated user."""
    query = db.query(GenerationJob).filter(GenerationJob.user_id == current_user.id)

    if job_type:
        query = query.filter(GenerationJob.job_type == job_type)

    return query.order_by(GenerationJob.created_at.desc()).offset(offset).limit(limit).all()


@router.delete("/history/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_generation_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete a generation job from user history."""
    job = db.query(GenerationJob).filter(
        GenerationJob.id == job_id,
        GenerationJob.user_id == current_user.id
    ).first()

    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    db.delete(job)
    db.commit()
    return None


# =========================================================================
# VOICES CATALOG
# =========================================================================
@router.get("/gemini/voices")
def get_available_voices():
    """Return list of available Microsoft Neural AI voice personas."""
    from app.services.voice_service import VoiceService
    return VoiceService.get_available_voices()
