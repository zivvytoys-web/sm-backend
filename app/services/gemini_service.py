import os
import uuid
import json
import base64
import time
import asyncio
import hashlib
import random
from datetime import datetime
from typing import Dict, Any, List, Optional
import httpx
from PIL import Image, ImageDraw, ImageFont

from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError

from app.core.config import settings
from app.core.logging import logger
from app.core.gemini_client import (
    get_gemini_client,
    is_gemini_configured,
    get_gemini_key_preview,
    get_configured_models,
    verify_provider_capabilities
)
from app.db.session import SessionLocal
from app.models.generation_job import GenerationJob
from app.models.media_asset import MediaAsset
from app.models.brand_profile import BrandProfile
from app.utils.enums import GenerationType, JobStatus, MediaType
from app.integrations.storage.cloudinary_storage import CloudinaryStorageProvider
from app.services.prompt_service import PromptService


class GeminiService:
    """Enterprise-grade service for Google Gemini AI text, Imagen/Flash image, and Veo video generation."""

    # Storage paths
    AI_IMAGES_DIR = os.path.join(settings.LOCAL_STORAGE_DIR, "media", "ai_images")
    AI_VIDEOS_DIR = os.path.join(settings.LOCAL_STORAGE_DIR, "media", "ai_videos")

    @classmethod
    def get_text_models(cls):
        catalog = get_configured_models()
        return catalog.get("text", [
            "gemini-flash-lite-latest",
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.8-flash",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest"
        ])

    MODELS_CATALOG = get_configured_models()
    TEXT_MODELS = get_configured_models()["text"]
    IMAGE_MODELS = get_configured_models()["image"]
    VIDEO_MODELS = get_configured_models()["video"]

    @classmethod
    def _ensure_directories(cls):
        os.makedirs(cls.AI_IMAGES_DIR, exist_ok=True)
        os.makedirs(cls.AI_VIDEOS_DIR, exist_ok=True)

    @classmethod
    def get_client(cls) -> Optional[genai.Client]:
        """Initialize Google GenAI client from unified provider."""
        return get_gemini_client()

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """Check API key setup and available models via unified provider."""
        return verify_provider_capabilities()

    _openai_disabled_until: float = 0.0

    @classmethod
    async def _openai_json(cls, system_instruction: str, user_prompt: str) -> Optional[Dict[str, Any]]:
        """Use ChatGPT for copy and creative prompt blueprints when configured and active."""
        import time
        now = time.time()
        if now < getattr(cls, "_openai_disabled_until", 0.0):
            return None

        api_key = (settings.OPENAI_API_KEY or "").strip()
        if not api_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": "gpt-4o-mini",
                        "temperature": 0.8,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": user_prompt}
                        ]
                    }
                )
                if response.status_code == 429:
                    cls._openai_disabled_until = now + 600.0  # Bypass OpenAI for 10 minutes
                    logger.info("OpenAI returned 429 rate limit. Routing directly to Gemini for instant responses.")
                    return None
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return json.loads(content)
        except Exception as exc:
            cls._openai_disabled_until = now + 300.0
            logger.info("OpenAI copy generation bypassed; using Google Gemini directly: %s", exc)
            return None

    # =========================================================================
    # 0. DAILY TRENDING CAMPAIGN GOAL (POWERED BY ANALYTICS & VIRAL ALGORITHMS)
    # =========================================================================
    @classmethod
    async def generate_daily_trending_goal(
        cls,
        brand: Optional[Any] = None,
        analytics_summary: Optional[Dict[str, Any]] = None,
        target_platform: Optional[str] = "instagram",
        date_str: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Synthesizes today's high-impact, trending Primary Campaign Goal designed
        to make the brand trend on social media by strategically leveraging real audience
        analytics telemetry and current platform viral mechanics.
        """
        brand_name = brand.name if brand else "Brand"
        industry = getattr(brand, "industry", "Lifestyle & Retail") or "Lifestyle & Retail"
        raw_themes = getattr(brand, "key_themes", None)
        if isinstance(raw_themes, list) and raw_themes:
            themes = ", ".join(raw_themes)
        else:
            themes = getattr(brand, "keywords", "") or getattr(brand, "desc", "") or "signature collections"
        tone = getattr(brand, "tone_of_voice", "Engaging") or "Engaging"
        target_aud = getattr(brand, "target_audience", "Social audience") or "Social audience"
        usp = getattr(brand, "usp", "Exceptional quality and craft") or "Exceptional quality and craft"

        featured_name = raw_themes[0] if (isinstance(raw_themes, list) and raw_themes) else brand_name

        metrics = analytics_summary or {}
        impressions = int(metrics.get("total_impressions", 0) or 0)
        reach = int(metrics.get("total_reach", 0) or 0)
        engagements = int(metrics.get("total_engagements", 0) or 0)
        eng_rate = float(metrics.get("engagement_rate", 0.0) or 0.0)
        top_plat = str(metrics.get("top_platform") or target_platform or "instagram").lower()
        top_fmt = str(metrics.get("top_format") or "Video Reel / Short")
        connected_count = int(metrics.get("connected_accounts_count", 0) or 0)

        now = datetime.utcnow()
        day_name = now.strftime("%A")
        date_today = date_str or now.strftime("%Y-%m-%d")

        # 7-Day High-Impact Trending Archetypes (Algorithmic Fallback)
        daily_archetypes = {
            "Monday": {
                "goal": f"Kickstart Week Viral Momentum: Debut High-Energy {top_fmt} of {featured_name} Highlighting Everyday Practical Value & Community Spark",
                "rationale": f"Monday social feeds favor inspirational, dynamic pacing; {top_fmt} on {top_plat.capitalize()} optimizes algorithmic momentum for the week ahead.",
                "angle": "Week-Opening Viral Hook & Practical Value",
                "format": top_fmt or "Video Reel / Short",
                "tags": [f"#{brand_name.replace(' ', '')}", "#MondayMotivation", "#TrendingNow", f"#{industry.replace(' ', '')}", "#ViralReels", "#ExplorePage"]
            },
            "Tuesday": {
                "goal": f"Educational Authority Masterclass: Reveal 3 Insider Secrets & Meticulous Craftsmanship Behind {featured_name} to Drive 2x Bookmarks and High-Value Saves",
                "rationale": f"Mid-week audiences show peak save rates on educational deep-dives, boosting {top_plat.capitalize()}'s high-retention distribution score.",
                "angle": "Curator Secrets & Tactile Craftsmanship",
                "format": top_fmt or "Video Reel / Short",
                "tags": [f"#{brand_name.replace(' ', '')}", "#CuratorSecrets", "#Craftsmanship", "#TrendingTutorial", f"#{industry.replace(' ', '')}"]
            },
            "Wednesday": {
                "goal": f"Midweek Trend-Jacking: Showcase {featured_name} with Sensory ASMR Macro Close-Ups and Hypnotic Cuts to Capture Explore & FYP Feeds",
                "rationale": f"Sensory macro cinematography maximizes average watch time and completion rate on {top_plat.capitalize()} algorithms.",
                "angle": "Sensory Macro Immersion & ASMR Easing",
                "format": "Video Reel / Short",
                "tags": [f"#{brand_name.replace(' ', '')}", "#ASMRVisuals", "#Satisfying", "#TrendingAesthetic", "#FYP", "#ReelsViral"]
            },
            "Thursday": {
                "goal": f"Community Social Proof Surge: Spotlight Transformative Real-Life Experiences with {featured_name} to Surge Direct Recommendation Shares",
                "rationale": f"Social proof and authentic customer satisfaction drive 2.4x higher share velocity across {top_plat.capitalize()}.",
                "angle": "Authentic Social Proof & Relatable Wonder",
                "format": "Carousel Slide" if "carousel" in top_fmt.lower() else "Video Reel / Short",
                "tags": [f"#{brand_name.replace(' ', '')}", "#CustomerLove", "#CommunityProof", "#Transformation", "#TrendingReview"]
            },
            "Friday": {
                "goal": f"Weekend Launch & Flash Urgency: Announce 48-Hour Limited Drop for {featured_name} with Kinetic Visual Cuts & Exclusive Early-Access Perks",
                "rationale": f"Friday engagement spikes on limited drops and weekend anticipation, generating immediate profile visits and click conversions.",
                "angle": "Limited-Edition Drop & Kinetic Urgency",
                "format": "Video Reel / Short",
                "tags": [f"#{brand_name.replace(' ', '')}", "#WeekendDrop", "#LimitedEdition", "#FlashPromo", "#TrendingNow", "#ShopNow"]
            },
            "Saturday": {
                "goal": f"Weekend Lifestyle Immersion: Showcase Candid Natural Daylight & Effortless Elegance with {featured_name} to Inspire Weekend Bookmarks",
                "rationale": f"Weekend leisure browsing elevates lifestyle aesthetic immersion and long-form visual saves on {top_plat.capitalize()}.",
                "angle": "Sun-Drenched Lifestyle & Effortless Elegance",
                "format": top_fmt or "Visual Graphic / Photo",
                "tags": [f"#{brand_name.replace(' ', '')}", "#WeekendVibes", "#AestheticLifestyle", "#CuratedLiving", "#TrendingPhotoshoot"]
            },
            "Sunday": {
                "goal": f"Sunday Reflection & Founder Manifesto: Share the Meticulous Origin Vision Behind {brand_name}'s {featured_name} to Ignite Thoughtful Community Dialogue",
                "rationale": f"Sunday evening users have higher reading dwell time, driving meaningful comments and high emotional resonance.",
                "angle": "Founder Vision & Inspiring Brand Manifesto",
                "format": "Video Reel / Short",
                "tags": [f"#{brand_name.replace(' ', '')}", "#BrandStory", "#BehindTheScenes", "#FounderJourney", "#InspirationDaily"]
            }
        }

        fallback_plan = daily_archetypes.get(day_name, daily_archetypes["Monday"])

        system_instruction = (
            "You are a World-Class Chief Marketing Officer and Social Media Growth Algorithm Specialist. "
            "Your objective: Synthesize today's high-impact, trending Primary Campaign Goal designed to make the brand trend on social media "
            "by strategically leveraging real audience analytics telemetry and platform ranking algorithms (Instagram Reels, TikTok FYP, YouTube Shorts). "
            "CRITICAL REQUIREMENTS:\n"
            "1. 'campaign_goal': A bold, punchy, trend-aware 1-2 sentence statement specifying hook, format, and viral outcome (e.g., 'Drive Viral Reel Reach: Showcase [Brand] with ASMR Craftsmanship & Sensory Visual Hooks to Surge Shares & Saves by 35% on Instagram').\n"
            "2. 'analytics_rationale': A crisp, credible 1-sentence data-driven explanation referencing audience telemetry or algorithmic strengths (e.g., 'Video reels generate our highest retention score; today's sensory hook leverages high-completion recommendation feeds').\n"
            "3. 'target_platform': Highest leverage social platform for today's goal.\n"
            "4. 'suggested_format': Optimal media format ('Video Reel / Short' | 'Carousel Slide' | 'Visual Graphic / Photo').\n"
            "5. 'creative_angle': Creative angle title.\n"
            "6. 'trending_hashtags': List of 5-7 viral and industry-trending hashtags.\n"
            "Return valid JSON matching keys strictly."
        )

        user_prompt = (
            f"Brand Profile:\n"
            f"- Brand Name: {brand_name}\n"
            f"- Industry: {industry}\n"
            f"- Core Themes / Collections: {themes}\n"
            f"- Tone of Voice: {tone}\n"
            f"- Target Audience: {target_aud}\n"
            f"- Unique Value Proposition: {usp}\n\n"
            f"Audience Analytics Telemetry (Last 30 Days):\n"
            f"- Total Impressions: {impressions:,}\n"
            f"- Total Reach: {reach:,}\n"
            f"- Total Engagements: {engagements:,}\n"
            f"- Average Engagement Rate: {eng_rate:.2f}%\n"
            f"- Top Performing Channel: {top_plat.capitalize()}\n"
            f"- Top Performing Content Format: {top_fmt}\n"
            f"- Active Connected Channels: {connected_count}\n"
            f"- Today's Date & Cycle: {day_name}, {date_today}\n\n"
            f"Task:\n"
            f"Synthesize today's optimal trending Primary Campaign Goal specifically engineered to make {brand_name} trend on {top_plat.capitalize()}."
        )

        # 1. Try ChatGPT if active
        openai_data = await cls._openai_json(system_instruction, user_prompt)
        if openai_data and openai_data.get("campaign_goal"):
            return {
                "brand_name": brand_name,
                "campaign_goal": openai_data.get("campaign_goal"),
                "analytics_rationale": openai_data.get("analytics_rationale", fallback_plan["rationale"]),
                "target_platform": openai_data.get("target_platform", top_plat),
                "suggested_format": openai_data.get("suggested_format", fallback_plan["format"]),
                "trending_hashtags": openai_data.get("trending_hashtags", fallback_plan["tags"]),
                "creative_angle": openai_data.get("creative_angle", fallback_plan["angle"])
            }

        # 2. Try Google Gemini Client
        client = cls.get_client()
        if client:
            active_models = cls.get_text_models()[:1]
            for target_model in active_models:
                try:
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            client.models.generate_content,
                            model=target_model,
                            contents=user_prompt,
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction,
                                temperature=0.85,
                                response_mime_type="application/json"
                            )
                        ),
                        timeout=15.0
                    )
                    raw_text = response.text or "{}"
                    data = json.loads(raw_text)
                    if data.get("campaign_goal"):
                        return {
                            "brand_name": brand_name,
                            "campaign_goal": data.get("campaign_goal"),
                            "analytics_rationale": data.get("analytics_rationale", fallback_plan["rationale"]),
                            "target_platform": data.get("target_platform", top_plat),
                            "suggested_format": data.get("suggested_format", fallback_plan["format"]),
                            "trending_hashtags": data.get("trending_hashtags", fallback_plan["tags"]),
                            "creative_angle": data.get("creative_angle", fallback_plan["angle"])
                        }
                except Exception as e:
                    logger.warning(f"Trending goal generation with model {target_model} failed: {e}")
                    break

        # 3. Dynamic Smart Algorithmic Fallback
        return {
            "brand_name": brand_name,
            "campaign_goal": fallback_plan["goal"],
            "analytics_rationale": fallback_plan["rationale"],
            "target_platform": top_plat,
            "suggested_format": fallback_plan["format"],
            "trending_hashtags": fallback_plan["tags"],
            "creative_angle": fallback_plan["angle"]
        }

    # =========================================================================
    # 1. AUTO-PROMPT BUILDER FROM BRAND PROFILE (Dynamic & Non-Repeating)
    # =========================================================================
    @classmethod
    async def generate_auto_prompts(
        cls,
        brand: Optional[BrandProfile] = None,
        campaign_goal: Optional[str] = None,
        custom_focus: Optional[str] = None,
        target_platform: str = "instagram",
        uniqueness_context: Optional[str] = None,
        freshness_seed: Optional[str] = None,
        creative_angle: Optional[str] = None,
        analytics_summary: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Automatically synthesize rich prompts for Text, Image, and Video using Brand Profile data."""
        import random
        import time

        client = cls.get_client()

        brand_name = brand.name if brand else "Our Brand"
        industry = brand.industry if brand and brand.industry else "Retail & Business"
        tone = brand.tone_of_voice if brand and brand.tone_of_voice else "Engaging, Authentic, Memorable"
        target_aud = brand.target_audience if brand and brand.target_audience else "Discerning modern audience"
        tagline = brand.tagline if brand and brand.tagline else ""
        themes = ", ".join(brand.key_themes) if brand and brand.key_themes else "Quality, Innovation, Excellence"
        hashtags = brand.hashtags if brand and brand.hashtags else [f"#{brand_name.replace(' ', '')}", "#Brand", "#Latest"]

        meta = brand.meta_info if (brand and hasattr(brand, "meta_info") and isinstance(brand.meta_info, dict)) else {}
        usp = meta.get("usp", "")
        positioning = meta.get("brand_positioning", "")
        brand_avoid = (getattr(brand, "avoid", None) if brand else None) or "clichéd claims, off-brand messaging"
        brand_cta = (getattr(brand, "cta", None) if brand else None) or "Visit our link in bio"

        products = meta.get("products", [])
        products_lines = []
        featured_product = None
        if products and isinstance(products, list):
            for p in products[:5]:
                if isinstance(p, dict) and p.get("name"):
                    price_info = f" (${p.get('price')})" if p.get("price") else ""
                    products_lines.append(f"  • {p['name']}{price_info}: {p.get('description', '')}")
            valid_p = [p for p in products if isinstance(p, dict) and p.get("name")]
            if valid_p:
                featured_product = random.choice(valid_p)

        featured_name = featured_product.get("name") if featured_product else brand_name
        products_str = "\nAuthentic Products / Services:\n" + "\n".join(products_lines) if products_lines else ""

        ANGLES_BY_GOAL = {
            "product_launch": [
                {
                    "name": "Hero Spotlight & Tactile Craftsmanship",
                    "visual": f"ultra-high-resolution minimalist studio shot of {featured_name}, dramatic directional rim lighting, smooth matte obsidian pedestal, 8k commercial photography, shallow depth of field",
                    "video": f"cinematic 30s slow-motion rotating orbit starting with extreme macro close-ups of {featured_name}'s tactile textures, subtle warm light leak, smooth easing, dramatic unveil",
                    "text_angle": f"Focus on revolutionary craftsmanship, precision details, and why {featured_name} sets a new benchmark in quality and design."
                },
                {
                    "name": "Lifestyle In-Action & Everyday Wonder",
                    "visual": f"sun-drenched morning lifestyle environment, candid authentic depth of field, natural modern interior with {featured_name} in prime focus, cheerful and aspirational atmosphere",
                    "video": f"energetic fluid tracking camera capturing real-world joy and interaction with {featured_name}, crisp audio accents, smooth dynamic swipe transitions",
                    "text_angle": f"Emphasize everyday delight, relatable lifestyle upgrade, and an irresistible invitation to experience {featured_name} firsthand."
                },
                {
                    "name": "Architectural Elegance & Minimalist Pedestal",
                    "visual": f"editorial lookbook composition, soft diffused daylight through floor-to-ceiling windows, sculptural pastel pedestals displaying {featured_name}, ample negative space, luxury aesthetic",
                    "video": f"serene continuous one-shot dolly movement gliding past minimalist sculptural props, shimmering reflections, gentle floating camera easing",
                    "text_angle": f"Highlight intentional design, heirloom quality, and aesthetic harmony for modern living with {brand_name}."
                },
                {
                    "name": "Bold Innovation & Future of Play",
                    "visual": f"sleek contemporary studio setup with dramatic chiaroscuro contrast, subtle atmospheric haze, crisp edge highlights outlining {featured_name}",
                    "video": f"dynamic push-in camera reveal with subtle kinetic momentum, smooth lens glints, and clean modern commercial color grading",
                    "text_angle": f"Boldly challenge ordinary conventions and present {featured_name} as the smart, inspiring choice for thoughtful gifters."
                }
            ],
            "flash_sale": [
                {
                    "name": "48-Hour Urgent Countdown",
                    "visual": f"striking commercial poster aesthetic, warm amber and gold spotlights on {featured_name}, subtle elegant typography badge reading Limited Availability, 8k resolution",
                    "video": f"high-octane kinetic reel, fast cuts between flagship items, dynamic camera whip-pan, clean graphic timer transition",
                    "text_angle": f"Create high-converting urgency with clear countdown, highlight irresistible savings, and direct call to action to shop before stock clears."
                },
                {
                    "name": "VIP Collector's Privilege",
                    "visual": f"luxury velvet vault aesthetic, soft mysterious spotlights revealing curated bundle pieces around {featured_name}, gold foil reflections, dark moody sophistication",
                    "video": f"slow cinematic push-in towards a glowing presentation box opening smoothly to reveal exclusive {brand_name} creations",
                    "text_angle": f"Frame this promotion as an exclusive insider reward for valued community members, available only for 48 hours."
                },
                {
                    "name": "Weekend Bundle Extravaganza",
                    "visual": f"flat-lay styled grid composition, sun-drenched oak surface, playful colorful props, inviting aesthetic featuring {featured_name}",
                    "video": f"playful stop-motion overhead assembly of the top bestsellers landing neatly into a curated gift box with smooth zoom-out",
                    "text_angle": f"Help shoppers pick the perfect gift bundle with curated pairings and straightforward value."
                }
            ],
            "brand_story": [
                {
                    "name": "Artisan Atelier & Heritage",
                    "visual": f"warm workshop studio, soft daylight across artisan sketches and raw sustainable materials, nostalgic 35mm warmth with {brand_name} craftsmanship in progress",
                    "video": f"atmospheric documentary dolly past skilled hands, delicate textures, and genuine smile of creators bringing {brand_name} to life",
                    "text_angle": f"Tell the origin story: why {brand_name} was founded, the commitment to zero compromises, and the passion woven into every piece."
                },
                {
                    "name": "Sustainability & Conscious Craft",
                    "visual": f"lush botanical and earth-tone setting, clean minimalist framing, soft organic textures and gentle green highlights showcasing sustainable materials",
                    "video": f"sweeping glide from tranquil natural landscape into modern eco-friendly studio crafting process with serene soundtrack",
                    "text_angle": f"Spotlight eco-conscious materials, ethical sourcing, and {brand_name}'s long-term mission for the next generation."
                },
                {
                    "name": "The Uncompromising Standard",
                    "visual": f"high-fashion black & white editorial portrait with single striking brand accent color on {featured_name}, crisp architectural lighting",
                    "video": f"dramatic cinematic montage contrasting durability testing with elegant finishes, rhythmic acoustic pacing",
                    "text_angle": f"Share a manifesto on why cutting corners is forbidden and why true quality outlasts fleeting trends."
                }
            ],
            "store_visit": [
                {
                    "name": "Atmospheric Twilight Walk-In",
                    "visual": f"inviting boutique storefront illuminated in evening twilight, warm amber glow through display windows, welcoming {brand_name} atmosphere",
                    "video": f"first-person fluid gimbal shot stepping through boutique doors, warm smiles, ambient acoustic background, inviting warmth",
                    "text_angle": f"Invite locals to step inside and experience the textures, sounds, and warm hospitality at {brand_name} in person this week."
                },
                {
                    "name": "Tactile Sensory Discovery",
                    "visual": f"stylish boutique display counter with interactive hands-on stations, soft daylight, curated wooden shelves displaying {brand_name} collections",
                    "video": f"smooth handheld tracking shot exploring unique showroom corners, personalized shopping guidance, tactile details",
                    "text_angle": f"Highlight the exclusive benefits of visiting in person: private consultations, touch-and-feel discovery, and local perks."
                }
            ],
            "styling_tips": [
                {
                    "name": "3-Way Curator Masterclass",
                    "visual": f"clean split-triptych aesthetic showing 3 distinct ways to style and display {featured_name}, neutral gallery backdrop, soft studio lighting",
                    "video": f"fast satisfying transformation cuts demonstrating beginner vs insider styling secrets with {featured_name} in seconds",
                    "text_angle": f"Deliver educational value with actionable, easy-to-follow advice that instantly elevates how the customer enjoys {featured_name}."
                },
                {
                    "name": "Before & After Transformation",
                    "visual": f"side-by-side dynamic split comparison, crisp modern studio lighting, elegant lifestyle transformation featuring {brand_name}",
                    "video": f"smooth swipe transition revealing the dramatic aesthetic lift after introducing {featured_name} into the space",
                    "text_angle": f"Demonstrate the immediate visual and practical upgrade with an inspiring, supportive and encouraging tone."
                }
            ]
        }

        # Auto-synthesize Daily Trending Goal from analytics if not manually provided
        resolved_rationale = None
        if not campaign_goal or str(campaign_goal).strip().lower() in ["auto", "trending", "daily", "ai"]:
            trending_res = await cls.generate_daily_trending_goal(
                brand=brand,
                analytics_summary=analytics_summary,
                target_platform=target_platform
            )
            campaign_goal = trending_res.get("campaign_goal")
            resolved_rationale = trending_res.get("analytics_rationale")
            if not creative_angle and trending_res.get("creative_angle"):
                creative_angle = trending_res.get("creative_angle")

        clean_goal = str(campaign_goal or "").strip()
        goal_labels = {
            "product_launch": "Launch a flagship new product collection with excitement and desire",
            "flash_sale": "Promote a limited-time exclusive 48-hour flash sale with urgency",
            "brand_story": "Share the authentic heritage, craft, and mission behind the brand",
            "store_visit": "Drive in-person footfall and store visits with an inviting atmosphere",
            "styling_tips": "Provide actionable styling and educational inspiration for the audience",
            "customer_spotlight": "Celebrate customer community satisfaction with social proof",
            "custom": custom_focus or "High-engagement social campaign"
        }

        if clean_goal in goal_labels:
            goal_desc = goal_labels[clean_goal]
        elif clean_goal.lower() in goal_labels:
            goal_desc = goal_labels[clean_goal.lower()]
        elif clean_goal:
            # User entered the Primary Campaign Goal manually
            goal_desc = clean_goal
        else:
            goal_desc = "Drive authentic engagement, viral community reach, and brand awareness"

        if custom_focus:
            goal_desc += f". Specific Focus: {custom_focus}"

        if clean_goal in ANGLES_BY_GOAL:
            goal_angles = ANGLES_BY_GOAL[clean_goal]
        elif clean_goal.lower() in ANGLES_BY_GOAL:
            goal_angles = ANGLES_BY_GOAL[clean_goal.lower()]
        else:
            # Dynamically craft creative angles specifically derived from the user's manual goal
            goal_angles = [
                {
                    "name": f"Goal Drive: {clean_goal[:45]}",
                    "visual": f"commercial studio and lifestyle photography tailored directly to '{clean_goal}', featuring {featured_name} in a pristine setting with cinematic lighting, depth of field, 8k resolution",
                    "video": f"30-second high-engagement social video reel dynamically visualizing '{clean_goal}', smooth camera push-in on {featured_name}, kinetic cuts, polished visual flow",
                    "text_angle": f"Craft captivating, high-converting social copy and description crafted strictly around achieving: '{clean_goal}'."
                },
                {
                    "name": "Community & Lifestyle Proof",
                    "visual": f"warm authentic editorial lifestyle shot illustrating '{clean_goal}', natural ambient lighting, genuine connection featuring {featured_name}",
                    "video": f"authentic first-person POV short-form reel showing real-world value, aesthetic transitions, and compelling social hook for '{clean_goal}'",
                    "text_angle": f"Engage the audience with relatable storytelling, educational authority, and a clear call to action fulfilling: '{clean_goal}'."
                }
            ]

        seed_val = freshness_seed or str(time.time())
        rng = random.Random(seed_val)
        selected_style = rng.choice(goal_angles)

        system_instruction = (
            "You are a master creative director. Create 3 perfectly synchronized creative prompts "
            "(Text Copy prompt, Image Artwork prompt, and Video Reel prompt) for a social media campaign. "
            f"The primary goal of this campaign is manually specified by the user as: '{goal_desc}'. "
            "You must deeply understand this Primary Campaign Goal and generate the Text prompt (for caption/description), "
            "the Image prompt (for generating high-quality photorealistic images), and the Video prompt (for generating dynamic 30s video reels) "
            "such that all three media assets directly and creatively accomplish this specific goal. "
            "Base every prompt strictly on the verified brand information, products, and directives. "
            "CRITICAL: Do NOT output cliché or repetitive text. Each generation must explore a fresh, distinctive narrative hook and visual composition. "
            "Return valid JSON strictly matching the requested keys."
        )

        user_prompt = (
            f"Brand Profile Information:\n"
            f"- Brand Name: {brand_name}\n"
            f"- Industry: {industry}\n"
            f"- Tagline: {tagline}\n"
            f"- Tone of Voice: {tone}\n"
            f"- Target Audience: {target_aud}\n"
            f"- USP / Value Proposition: {usp}\n"
            f"- Brand Positioning: {positioning}\n"
            f"- Core Themes: {themes}\n"
            f"- Standard CTA: {brand_cta}\n"
            f"- Content Restrictions (Avoid): {brand_avoid}\n"
            f"- Campaign Objective: {goal_desc}\n"
            f"- Creative Direction Angle: {selected_style['name']}\n"
            f"- Style Guidance: {selected_style['text_angle']}\n"
            f"- Primary Platform: {target_platform}\n"
            f"- Freshness Seed: {seed_val}"
            f"{products_str}\n\n"
            f"{uniqueness_context or ''}\n"
            f"Generate a synchronized creative blueprint. Return a JSON object with:\n"
            f"- brand_name: string\n"
            f"- campaign_goal: string\n"
            f"- creative_angle: string\n"
            f"- suggested_headline: Short high-converting headline (string)\n"
            f"- text_prompt: Detailed topic and copywriting instructions for Gemini (string)\n"
            f"- image_prompt: Detailed visual prompt describing subject, composition, background, lighting, and aesthetic for high-res artwork (string)\n"
            f"- video_prompt: Detailed action prompt describing 30s video motion, camera moves, and scene progression for Veo (string)\n"
            f"- suggested_tone: string\n"
            f"- suggested_aspect_ratio: '1:1' or '9:16' or '16:9' (string)\n"
            f"- recommended_hashtags: list of 5-8 relevant hashtags (list of strings)"
        )

        openai_data = await cls._openai_json(system_instruction, user_prompt)
        if openai_data and openai_data.get("text_prompt"):
            return {
                "brand_name": brand_name,
                "campaign_goal": campaign_goal,
                "creative_angle": openai_data.get("creative_angle", selected_style["name"]),
                "suggested_headline": openai_data.get("suggested_headline", f"Discover {brand_name}"),
                "text_prompt": openai_data.get("text_prompt", f"Create social copy for {brand_name}"),
                "image_prompt": openai_data.get("image_prompt", selected_style["visual"]),
                "video_prompt": openai_data.get("video_prompt", selected_style["video"]),
                "suggested_tone": openai_data.get("suggested_tone", tone),
                "suggested_aspect_ratio": openai_data.get("suggested_aspect_ratio", "1:1"),
                "recommended_hashtags": openai_data.get("recommended_hashtags", hashtags),
                "analytics_rationale": resolved_rationale or openai_data.get("analytics_rationale")
            }

        if client:
            active_models = cls.get_text_models()[:1]
            for target_model in active_models:
                try:
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            client.models.generate_content,
                            model=target_model,
                            contents=user_prompt,
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction,
                                temperature=0.9,
                                response_mime_type="application/json"
                            )
                        ),
                        timeout=2.5
                    )
                    raw_text = response.text or "{}"
                    data = json.loads(raw_text)
                    if data.get("text_prompt") and data.get("image_prompt"):
                        return {
                            "brand_name": brand_name,
                            "campaign_goal": campaign_goal,
                            "creative_angle": data.get("creative_angle", selected_style["name"]),
                            "suggested_headline": data.get("suggested_headline", f"Experience {brand_name}"),
                            "text_prompt": data.get("text_prompt", f"Present {brand_name}'s latest {themes} tailored for {target_platform}"),
                            "image_prompt": data.get("image_prompt", selected_style["visual"]),
                            "video_prompt": data.get("video_prompt", selected_style["video"]),
                            "suggested_tone": data.get("suggested_tone", tone),
                            "suggested_aspect_ratio": data.get("suggested_aspect_ratio", "1:1"),
                            "recommended_hashtags": data.get("recommended_hashtags", hashtags),
                            "analytics_rationale": resolved_rationale or data.get("analytics_rationale")
                        }
                except Exception as e:
                    logger.warning(f"Auto-prompt model {target_model} failed: {str(e)}")
                    break

        # Dynamic Smart Fallback Builder (Guaranteed varied and non-repeating)
        aspect_ratio = "9:16" if target_platform in ["instagram", "tiktok"] else "1:1"
        return {
            "brand_name": brand_name,
            "campaign_goal": campaign_goal,
            "creative_angle": selected_style["name"],
            "suggested_headline": f"{brand_name}: {selected_style['name']}",
            "text_prompt": f"Write an engaging {target_platform} post for {brand_name}. Angle: {selected_style['text_angle']}. Themes: {themes}. Tone: {tone}.",
            "image_prompt": selected_style["visual"],
            "video_prompt": selected_style["video"],
            "suggested_tone": tone,
            "suggested_aspect_ratio": aspect_ratio,
            "recommended_hashtags": hashtags,
            "analytics_rationale": resolved_rationale
        }

    # =========================================================================
    # 2. TEXT GENERATION (Captions, Descriptions, Variations)
    # =========================================================================
    @classmethod
    async def generate_text(
        cls,
        prompt: str,
        brand_name: str = "",
        brand_tone: str = "",
        target_platforms: List[str] = None,
        content_type: str = "caption",
        tone: str = "Engaging",
        language: str = "English",
        additional_instructions: str = None,
        model_name: str = "gemini-3.1-flash-lite",
        temperature: float = 0.7,
        num_variations: int = 3,
        brand: Optional[BrandProfile] = None
    ) -> Dict[str, Any]:
        """Generate structured text matching brand identity, tone, and platform format."""
        client = cls.get_client()
        if not client:
            logger.info("GEMINI_API_KEY is not configured; fallback copy synthesizer will be used.")

        if brand:
            brand_name = brand_name or brand.name or "Brand"
            brand_tone = brand_tone or brand.tone_of_voice or "Engaging"
            industry = brand.industry or "Retail & Business"
            target_aud = brand.target_audience or "Valued community"
            brand_cta = brand.cta or "Tap link in bio to learn more"
            brand_avoid = brand.avoid or "clichéd claims, false promises"
            meta = brand.meta_info if isinstance(brand.meta_info, dict) else {}
            usp = meta.get("usp", "")
            positioning = meta.get("brand_positioning", "")
            products = meta.get("products", [])
        else:
            industry = "Retail & Business"
            target_aud = "Valued community"
            brand_cta = "Tap link in bio to learn more"
            brand_avoid = "clichéd claims, false promises"
            usp = ""
            positioning = ""
            products = []

        products_lines = []
        if products and isinstance(products, list):
            for p in products[:5]:
                if isinstance(p, dict) and p.get("name"):
                    price_str = f" (${p.get('price')})" if p.get("price") else ""
                    products_lines.append(f"  • {p['name']}{price_str}: {p.get('description', '')}")
        products_context = ("\nVerified Brand Products & Services:\n" + "\n".join(products_lines)) if products_lines else ""

        platforms_str = ", ".join(target_platforms) if target_platforms else "Instagram, LinkedIn"
        brand_context = f"Brand: '{brand_name}' ({industry}). Voice: {brand_tone}. Audience: {target_aud}."

        instructions_context = f"Special Instructions: {additional_instructions}" if additional_instructions else ""

        system_instruction = (
            f"You are the senior brand strategist and creative copywriter for '{brand_name}'.\n"
            f"{brand_context} Target Tone: {tone}. Language: {language}. Target Platforms: {platforms_str}.\n"
            f"Adhere strictly to brand identity, products, and tone. Never invent false details.\n"
            f"Always return clean, valid JSON strictly adhering to the requested schema."
        )

        user_prompt = (
            f"Content Type requested: {content_type.upper()}\n"
            f"Topic / Core Directive: {prompt}\n"
            f"{instructions_context}\n\n"
            f"--- VERIFIED BRAND DIRECTIVES ---\n"
            f"- Brand Name: {brand_name}\n"
            f"- Industry: {industry}\n"
            f"- Brand Tone of Voice: {brand_tone}\n"
            f"- Target Audience: {target_aud}\n"
            f"- Brand USP: {usp or 'Premium quality and exceptional service'}\n"
            f"- Brand Positioning: {positioning or 'Authentic modern leader'}\n"
            f"- Preferred Call to Action (CTA): {brand_cta}\n"
            f"- Strict Negative Filter (Never include): {brand_avoid}"
            f"{products_context}\n\n"
            f"Please generate high-converting, engaging social content tailored for {platforms_str} in {language}.\n"
            f"Return a valid JSON object with the following fields:\n"
            f"- headline: Short punchy hook or headline (string)\n"
            f"- caption: Full ready-to-post caption/description with emojis, line spacing, and call-to-action (string)\n"
            f"- hashtags: List of 10-15 relevant, high-traffic and niche hashtags including #{brand_name.replace(' ', '') if brand_name else 'Brand'} (list of strings)\n"
            f"- call_to_action: Clear action trigger (e.g., 'Comment below', 'Tap link in bio') (string)\n"
            f"- variations: List of {num_variations} distinct alternative copy angles (list of objects with 'title', 'hook', 'body')\n"
            f"- marketing_takeaways: 3 strategic tips for best performance on {platforms_str} (list of strings)\n"
            f"- script_scenes: 3 scene breakdowns with 'time', 'visual', 'audio', 'overlay_text' (list of objects)\n"
        )

        openai_data = await cls._openai_json(system_instruction, user_prompt)
        if openai_data:
            return {
                "headline": openai_data.get("headline", prompt[:60]),
                "caption": openai_data.get("caption", ""),
                "hashtags": openai_data.get("hashtags", []),
                "call_to_action": openai_data.get("call_to_action", brand_cta),
                "variations": openai_data.get("variations", []),
                "marketing_takeaways": openai_data.get("marketing_takeaways", []),
                "script_scenes": openai_data.get("script_scenes", []),
                "model_name": "gpt-4o-mini",
                "tokens_used": 0,
                "generated_at": datetime.utcnow().isoformat()
            }


        candidate_models = [model_name] if model_name in cls.TEXT_MODELS else []
        for fallback_m in cls.TEXT_MODELS:
            if fallback_m not in candidate_models:
                candidate_models.append(fallback_m)

        last_error = None
        for target_model in (candidate_models[:1] if client else []):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=target_model,
                        contents=user_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=temperature,
                            response_mime_type="application/json"
                        )
                    ),
                    timeout=3.0
                )

                raw_text = response.text or "{}"
                parsed_data = json.loads(raw_text)

                tokens_used = 0
                if response.usage_metadata:
                    tokens_used = response.usage_metadata.total_token_count or 0

                return {
                    "headline": parsed_data.get("headline", prompt[:60]),
                    "caption": parsed_data.get("caption", ""),
                    "hashtags": parsed_data.get("hashtags", []),
                    "call_to_action": parsed_data.get("call_to_action", "Tap link in bio to learn more!"),
                    "variations": parsed_data.get("variations", []),
                    "marketing_takeaways": parsed_data.get("marketing_takeaways", []),
                    "script_scenes": parsed_data.get("script_scenes", []),
                    "model_name": target_model,
                    "tokens_used": tokens_used or 250,
                    "generated_at": datetime.utcnow().isoformat()
                }

            except APIError as e:
                last_error = e
                logger.warning(f"Model '{target_model}' failed ({e.code}): {e.message}. Trying next candidate...")
                continue
            except json.JSONDecodeError:
                return {
                    "headline": prompt[:60],
                    "caption": response.text if 'response' in locals() and response.text else prompt,
                    "hashtags": [f"#{w.capitalize()}" for w in prompt.split() if len(w) > 3][:6],
                    "call_to_action": "Explore more at our link in bio!",
                    "variations": [],
                    "marketing_takeaways": [],
                    "script_scenes": [],
                    "model_name": target_model,
                    "tokens_used": 150,
                    "generated_at": datetime.utcnow().isoformat()
                }
            except Exception as e:
                last_error = e
                logger.warning(f"Error on model {target_model}: {str(e)}")
                continue

        logger.info(f"Gemini API unavailable or quota exhausted ({last_error}). Using intelligent brand copy synthesizer fallback...")
        clean_brand = brand_name or "Brand"
        tag_brand = clean_brand.replace(" ", "").replace("_", "")
        clean_tags = [
            f"#{tag_brand}",
            "#NewArrivals",
            "#TrendingNow",
            "#PremiumQuality",
            "#ShopSmall",
            "#Innovation",
            "#Creativity",
            "#ExclusiveDrop",
            "#MustHave",
            "#Lifestyle"
        ]

        headline = f"Discover {clean_brand}: {prompt[:40].strip()}"
        caption = (
            f"✨ Introducing the latest from {clean_brand}!\n\n"
            f"{prompt.strip()}\n\n"
            f"Engineered with premium craftsmanship and designed to inspire. "
            f"Experience the quality that sets {clean_brand} apart.\n\n"
            f"📍 Tap the link in bio or DM us directly to order today!\n\n"
            f"{' '.join(clean_tags[:6])}"
        )

        return {
            "headline": headline,
            "caption": caption,
            "hashtags": clean_tags,
            "call_to_action": f"Visit {clean_brand} online or link in bio to explore the collection!",
            "variations": [
                {"title": "Direct Hook", "hook": f"Elevate your experience with {clean_brand}.", "body": f"Every piece crafted for excellence and everyday joy."},
                {"title": "Story Angle", "hook": f"The story behind {clean_brand}'s latest drop.", "body": f"Thoughtfully designed for families who value quality and creativity."},
                {"title": "Offer Angle", "hook": f"Limited edition release from {clean_brand}.", "body": f"Order yours today before stocks run out."}
            ],
            "marketing_takeaways": [
                "Schedule during peak evening footfall hours (6:00 PM - 8:30 PM)",
                "Add interactive poll or sticker on Instagram Stories to boost reach",
                "Respond to user inquiries within the first 15 minutes of posting"
            ],
            "script_scenes": [
                {"time": "0:00 - 0:04", "visual": f"Cinematic macro zoom revealing {clean_brand} hero craftsmanship", "audio": f"Discover what makes {clean_brand} extraordinary.", "overlay_text": f"New Drop • {clean_brand}"},
                {"time": "0:04 - 0:18", "visual": f"Dynamic rotating shot showcasing {prompt[:60]} with natural lighting", "audio": f"{prompt[:100]}", "overlay_text": "Engineered for Quality"},
                {"time": "0:18 - 0:25", "visual": "Final title card with website, store location, and interactive CTA", "audio": "Tap the link in bio or comment below to order yours now.", "overlay_text": "Available Now — Shop Today"}
            ],
            "model_name": "ai-brand-synthesizer",
            "tokens_used": 200,
            "generated_at": datetime.utcnow().isoformat()
        }

    # =========================================================================
    # 3. HIGH-RESOLUTION BRANDED GRAPHIC RENDERER (Pillow Pipeline)
    # =========================================================================

    # Diverse color palette themes - each prompt hash picks a different one
    _PALETTE_THEMES = [
        # (bg_start, bg_end, accent, text_highlight, badge_bg)
        {"name": "midnight_indigo",  "bg": ((15,23,42),(35,45,90)),   "accent": (99,102,241),  "highlight": (167,139,250), "badge": (30,41,59)},
        {"name": "ember_rose",       "bg": ((60,10,30),(130,20,60)),  "accent": (244,63,94),   "highlight": (251,191,207), "badge": (80,0,30)},
        {"name": "forest_emerald",   "bg": ((5,40,25),(10,80,50)),    "accent": (16,185,129),  "highlight": (110,231,183), "badge": (4,60,35)},
        {"name": "solar_amber",      "bg": ((60,30,0),(120,70,5)),    "accent": (245,158,11),  "highlight": (253,211,77),  "badge": (80,40,0)},
        {"name": "ocean_cyan",       "bg": ((5,30,60),(10,60,120)),   "accent": (6,182,212),   "highlight": (103,232,249), "badge": (8,50,90)},
        {"name": "royal_violet",     "bg": ((35,10,70),(70,20,140)),  "accent": (139,92,246),  "highlight": (196,181,253), "badge": (50,15,100)},
        {"name": "crimson_fire",     "bg": ((70,10,10),(130,30,20)),  "accent": (239,68,68),   "highlight": (252,165,165), "badge": (100,15,15)},
        {"name": "slate_silver",     "bg": ((25,35,45),(50,65,80)),   "accent": (148,163,184), "highlight": (226,232,240), "badge": (35,50,65)},
    ]

    _LAYOUT_STYLES = ["center_focus", "top_hero", "split_panel"]

    @classmethod
    def _render_branded_graphic(
        cls,
        prompt: str,
        brand_name: str = "Brand",
        aspect_ratio: str = "1:1",
        style_preset: str = "modern_product",
        logo_url: Optional[str] = None,
        organization_id: Optional[int] = None,
        brand_palette: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Generate a high-resolution, premium branded visual poster asset with varied color palettes and layouts."""
        cls._ensure_directories()

        # Dimension mapping
        dim_map = {
            "1:1": (1080, 1080),
            "9:16": (1080, 1920),
            "16:9": (1920, 1080),
            "4:3": (1440, 1080),
            "3:4": (1080, 1440)
        }
        width, height = dim_map.get(aspect_ratio, (1080, 1080))

        # --- Pick UNIQUE palette and layout based on prompt content hash ---
        prompt_hash = int(hashlib.md5(prompt.encode()).hexdigest(), 16)
        palette = cls._PALETTE_THEMES[prompt_hash % len(cls._PALETTE_THEMES)]
        layout = cls._LAYOUT_STYLES[prompt_hash % len(cls._LAYOUT_STYLES)]

        bg_start = palette["bg"][0]
        bg_end = palette["bg"][1]
        accent = palette["accent"]
        highlight = palette["highlight"]
        badge_bg = palette["badge"]

        # If brand palette is provided, dynamically use authentic brand colors
        if brand_palette and len(brand_palette) >= 2:
            try:
                def hex_to_rgb(h: str):
                    h = str(h).lstrip("#")
                    if len(h) == 6:
                        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
                    return None
                c1 = hex_to_rgb(brand_palette[0])
                c2 = hex_to_rgb(brand_palette[1])
                if c1 and c2:
                    accent = c1
                    highlight = c2
                    bg_start = tuple(max(10, int(v * 0.18)) for v in c1)
                    bg_end = tuple(max(15, int(v * 0.32)) for v in c2)
                    badge_bg = tuple(max(20, int(v * 0.25)) for v in c1)
            except Exception:
                pass


        # Create canvas with unique gradient direction
        image = Image.new("RGB", (width, height), color=bg_start)
        draw = ImageDraw.Draw(image)

        # Unique gradient direction per layout
        if layout == "split_panel":
            # Horizontal gradient (left to right)
            for x in range(width):
                ratio = x / width
                r = int(bg_start[0] + ratio * (bg_end[0] - bg_start[0]))
                g = int(bg_start[1] + ratio * (bg_end[1] - bg_start[1]))
                b = int(bg_start[2] + ratio * (bg_end[2] - bg_start[2]))
                draw.line([(x, 0), (x, height)], fill=(r, g, b))
        else:
            # Vertical gradient (top to bottom)
            for y in range(height):
                ratio = y / height
                r = int(bg_start[0] + ratio * (bg_end[0] - bg_start[0]))
                g = int(bg_start[1] + ratio * (bg_end[1] - bg_start[1]))
                b = int(bg_start[2] + ratio * (bg_end[2] - bg_start[2]))
                draw.line([(0, y), (width, y)], fill=(r, g, b))

        # --- Accent geometric shapes unique per layout ---
        if layout == "center_focus":
            # Concentric rings
            cx, cy = width // 2, height // 2
            for radius in [min(width, height) // 2 - 40, min(width, height) // 3, min(width, height) // 5]:
                draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=accent, width=3)
            draw.rounded_rectangle([40, 40, width - 40, height - 40], radius=24, outline=highlight, width=2)

        elif layout == "top_hero":
            # Bold horizontal accent bar at top
            draw.rectangle([0, 0, width, height // 3], fill=tuple(int(c * 0.6) for c in accent))
            draw.rectangle([0, height // 3 - 8, width, height // 3 + 8], fill=accent)
            draw.rounded_rectangle([40, height // 3 + 40, width - 40, height - 40], radius=20, outline=highlight, width=2)

        elif layout == "split_panel":
            # Vertical divider with left colored panel
            panel_w = width // 3
            draw.rectangle([0, 0, panel_w, height], fill=badge_bg)
            draw.rectangle([panel_w, 0, panel_w + 6, height], fill=accent)
            # Diagonal accent lines on left panel
            for i in range(0, height + panel_w, 60):
                draw.line([(0, i), (panel_w, i - panel_w)], fill=accent, width=1)

        # Clean visual graphic: Brand logo watermark overlay disabled

        # --- Style preset tag ---
        style_text = style_preset.replace("_", " ").upper()
        draw.rounded_rectangle([width - 260, 60, width - 60, 120], radius=14, fill=badge_bg)
        draw.text((width - 245, 76), f"✦ {style_text}", fill=highlight)

        # --- Main creative text (word-wrapped) ---
        words = prompt.split()
        lines = []
        curr_line = []
        for word in words:
            curr_line.append(word)
            if len(" ".join(curr_line)) > 30:
                lines.append(" ".join(curr_line))
                curr_line = []
        if curr_line:
            lines.append(" ".join(curr_line))

        display_lines = lines[:5]

        if layout == "split_panel":
            text_x = width // 3 + 40
            start_y = height // 2 - len(display_lines) * 40
        elif layout == "top_hero":
            text_x = 80
            start_y = height // 3 + 100
        else:
            text_x = 80
            start_y = height // 2 - len(display_lines) * 40

        # Headline in accent color
        if display_lines:
            draw.text((text_x, start_y), display_lines[0], fill=highlight)
            start_y += 60
        # Body in white
        for i, line in enumerate(display_lines[1:]):
            draw.text((text_x, start_y + i * 52), line, fill=(220, 220, 220))

        # --- CTA bar at bottom ---
        cta_messages = [
            "⚡ TAP TO EXPLORE COLLECTION",
            "✦ SHOP THE EDIT NOW",
            "→ DISCOVER MORE IN BIO",
            "★ LIMITED DROP — ACT FAST",
            "↗ SEE THE FULL LOOK",
        ]
        cta_text = cta_messages[prompt_hash % len(cta_messages)]
        draw.rounded_rectangle([60, height - 150, width - 60, height - 80], radius=16, fill=accent)
        draw.text((width // 2 - len(cta_text) * 6, height - 130), cta_text, fill=(255, 255, 255))

        # Palette name watermark (subtle)
        draw.text((60, height - 65), f"#{palette['name']}", fill=badge_bg)

        filename = f"brand_art_{uuid.uuid4().hex[:12]}.png"
        file_path = os.path.join(cls.AI_IMAGES_DIR, filename)
        image.save(file_path, "PNG", quality=95)

        file_size = os.path.getsize(file_path)
        file_url = f"/uploads/media/ai_images/{filename}"

        if CloudinaryStorageProvider.is_configured():
            try:
                c_res = CloudinaryStorageProvider.upload_local_file_path(
                    file_path=file_path,
                    organization_id=1,
                    folder_prefix="socialai/ai_images"
                )
                if c_res and c_res.get("file_url"):
                    file_url = c_res["file_url"]
            except Exception as e:
                logger.warning(f"Cloudinary upload of generated image failed: {e}")

        return {
            "image_url": file_url,
            "file_name": filename,
            "file_size_bytes": file_size,
            "aspect_ratio": aspect_ratio,
            "mime_type": "image/png",
            "model_name": "gemini-visual-renderer",
            "prompt": prompt,
            "revised_prompt": prompt,
            "style_preset": style_preset,
            "palette_theme": palette["name"],
            "layout_style": layout,
            "generated_at": datetime.utcnow().isoformat()
        }

    # =========================================================================
    # 4. IMAGE GENERATION (Google Imagen & Visual Generator with Fallback)
    # =========================================================================
    @classmethod
    async def generate_image(
        cls,
        prompt: str,
        brand_name: str = "Brand",
        aspect_ratio: str = "1:1",
        style_preset: str = "modern_product",
        negative_prompt: str = "blurry, distorted, low quality, artifacts, watermark, logo, brand logo, text",
        model_name: str = "gemini-3.1-flash-image",
        number_of_images: int = 1,
        organization_id: Optional[int] = None,
        logo_url: Optional[str] = None,
        allow_branded_canvas_fallback: bool = True,
        brand: Optional[BrandProfile] = None,
        brand_palette: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Generate high-resolution image using Google Gen AI SDK and persist to storage."""
        if brand:
            brand_name = brand_name or brand.name or "Brand"
            logo_url = logo_url or brand.logo_url
            brand_palette = brand_palette or brand.color_palette

        cls._ensure_directories()
        client = cls.get_client()


        style_modifiers = {
            "modern_product": "commercial product photography, clean background, sharp focus, 8k resolution, cinematic lighting, studio quality",
            "photo": "hyperrealistic candid photography, natural lighting, shot on 35mm lens, high dynamic range, crisp details",
            "minimalist": "minimalist aesthetic, elegant pastel colors, subtle textures, ample negative space, contemporary design",
            "3d_render": "octane 3D render, raytracing, vibrant colors, ultra-detailed geometry, glossy textures",
            "cinematic": "cinematic movie still, dramatic chiaroscuro lighting, anamorphic lens flare, moody color grading",
            "neon": "cyberpunk synthwave aesthetic, glowing neon lights, dark moody contrast, reflective surfaces",
            "luxury": "ultra-luxury editorial aesthetic, gold and marble accents, sleek sophisticated composition, high fashion lookbook",
            "artistic": "contemporary digital artwork, rich painterly textures, expressive brush strokes, vibrant palette"
        }

        modifier = style_modifiers.get(style_preset, style_modifiers["modern_product"])
        full_prompt = f"{prompt.strip()}, {modifier}"
        ratio = aspect_ratio if aspect_ratio in ["1:1", "9:16", "16:9", "4:3", "3:4"] else "1:1"

        last_error_msg = None
        # Use the server-side Cloudflare Flux provider first. This endpoint
        # previously fell back to a Pillow poster labelled "Generated Graphic".
        # That is not a real AI creative and must never be returned as success.
        from app.services.image_generation_service import ImageGenerationService
        img_bytes = await ImageGenerationService._generate_cloudflare_image(full_prompt)
        used_cloudflare = bool(img_bytes)
        if img_bytes:
            model_name = settings.CLOUDFLARE_IMAGE_MODEL

        if img_bytes or client:
            # A None sentinel persists a successful Cloudflare response
            # through the same Cloudinary path as Gemini responses.
            target_models = [None] if img_bytes else (([model_name] if model_name else []) + cls.IMAGE_MODELS)
            for target_model in target_models:
                try:
                    if target_model:
                        response = client.models.generate_content(
                            model=target_model,
                            contents=full_prompt,
                            config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])
                        )

                        img_bytes = None
                        if response.candidates:
                            for part in response.candidates[0].content.parts:
                                if hasattr(part, "inline_data") and part.inline_data:
                                    img_bytes = part.inline_data.data
                                    if isinstance(img_bytes, str):
                                        img_bytes = base64.b64decode(img_bytes)
                                    break

                    if img_bytes:
                        filename = f"gemini_img_{uuid.uuid4().hex[:12]}.png"
                        file_path = os.path.join(cls.AI_IMAGES_DIR, filename)

                        # Clean visual output without logo watermark

                        with open(file_path, "wb") as f:
                            f.write(img_bytes)

                        file_size = len(img_bytes)
                        file_url = f"/uploads/media/ai_images/{filename}"

                        if not CloudinaryStorageProvider.is_configured():
                            raise RuntimeError("Cloudinary storage is not configured; refusing to save generated media locally.")
                        c_res = CloudinaryStorageProvider.upload_local_file_path(
                            file_path=file_path,
                            organization_id=organization_id or 1,
                            folder_prefix="socialai/ai_images",
                            resource_type="image",
                        )
                        file_url = c_res["file_url"]

                        return {
                            "image_url": file_url,
                            "file_name": filename,
                            "file_size_bytes": file_size,
                            "aspect_ratio": ratio,
                            "mime_type": "image/png",
                            "model_name": target_model or model_name,
                            "prompt": prompt,
                            "revised_prompt": full_prompt,
                            "style_preset": style_preset,
                            "engine": "cloudflare_flux" if used_cloudflare else "google_genai_model",
                            "generated_at": datetime.utcnow().isoformat()
                        }

                except Exception as e:
                    last_error_msg = str(e)
                    logger.warning(f"Image model '{target_model}' failed: {last_error_msg}. Trying next model...")
                    continue

        raise RuntimeError(
            f"Real image generation failed. Cloudflare Flux and Gemini returned no image. "
            f"Provider detail: {last_error_msg or 'no provider response'}."
        )

    @classmethod
    def generate_branded_video_with_voice(
        cls,
        prompt: str,
        brand_name: str = "Brand",
        aspect_ratio: str = "9:16",
        duration_seconds: int = 6,
        voice_script: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Synthesize an animated marketing video reel with real spoken voiceover,
        brand typography, and motion graphics encoded as standard H.264 / AAC MP4.
        """
        import wave
        import subprocess
        import shutil
        from PIL import Image, ImageDraw

        # Deferred imports with graceful fallback
        try:
            import cv2
            import numpy as np
        except ImportError as imp_err:
            raise RuntimeError(
                f"Video generation requires opencv-python-headless and numpy. "
                f"Run: pip install opencv-python-headless numpy. Error: {imp_err}"
            )

        try:
            # pyrefly: ignore[missing-import]
            import pyttsx3 as _pyttsx3
        except ImportError:
            _pyttsx3 = None

        try:
            import imageio_ffmpeg as _imageio_ffmpeg
        except ImportError:
            _imageio_ffmpeg = None

        cls._ensure_directories()
        video_id = uuid.uuid4().hex[:12]
        temp_dir = os.path.join(cls.AI_VIDEOS_DIR, f"temp_{video_id}")
        os.makedirs(temp_dir, exist_ok=True)

        audio_wav = os.path.join(temp_dir, "voice.wav")
        temp_mp4 = os.path.join(temp_dir, "visual.mp4")
        final_mp4_name = f"ai_reel_{video_id}.mp4"
        final_mp4_path = os.path.join(cls.AI_VIDEOS_DIR, final_mp4_name)

        clean_brand = brand_name or "Brand"
        clean_text = voice_script or f"Welcome to {clean_brand}. {prompt.strip()[:140]}. Discover our latest collection today."

        # 1. Generate Voiceover using pyttsx3 (if available)
        duration = max(float(duration_seconds), 5.0)
        if _pyttsx3 is not None:
            try:
                tts = _pyttsx3.init()
                tts.setProperty('rate', 150)
                tts.save_to_file(clean_text, audio_wav)
                tts.runAndWait()

                if os.path.exists(audio_wav) and os.path.getsize(audio_wav) > 1000:
                    with wave.open(audio_wav, 'r') as w:
                        duration = max(w.getnframes() / float(w.getframerate()), 4.0)
            except Exception as tts_err:
                logger.warning(f"Voice synthesis failed (will render silent video): {tts_err}")

        # 2. Render Animated Video Frames
        fps = 24
        total_frames = int(duration * fps)
        width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)

        # Diverse brand palette
        palette = cls._PALETTE_THEMES[abs(hash(clean_brand + prompt)) % len(cls._PALETTE_THEMES)]
        # pyrefly: ignore[bad-unpacking]
        bg_c1, bg_c2 = palette["bg"]
        accent_c = palette["accent"]
        hl_c = palette["highlight"]

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(temp_mp4, fourcc, fps, (width, height))

        for f in range(total_frames):
            progress = f / max(total_frames, 1)
            img = Image.new('RGB', (width, height), bg_c1)
            draw = ImageDraw.Draw(img)

            # Animated top progress bar
            bar_w = int(width * progress)
            draw.rectangle([(0, 0), (bar_w, 8)], fill=accent_c)

            # Branded accent card in center
            card_top = int(height * 0.22)
            card_bottom = int(height * 0.78)
            card_left = 60
            card_right = width - 60
            draw.rectangle([(card_left, card_top), (card_right, card_bottom)], fill=bg_c2, outline=accent_c, width=3)

            # Header brand badge
            draw.rectangle([(card_left + 40, card_top + 35), (card_right - 40, card_top + 95)], fill=palette["badge"])
            draw.text((width // 2, card_top + 65), f"★ {clean_brand.upper()} ★", fill=(255, 255, 255), anchor="mm")

            # Main headline
            prompt_snip = prompt[:45] if prompt else "New Arrival Collection"
            draw.text((width // 2, int(height * 0.42)), prompt_snip, fill=hl_c, anchor="mm")

            # Subtitle
            sub_text = "Handcrafted • Premium Quality • Shop Now"
            draw.text((width // 2, int(height * 0.52)), sub_text, fill=(255, 255, 255), anchor="mm")

            # Voice indicator wave animation
            wave_y = int(height * 0.62)
            for i in range(12):
                h_bar = int(12 + 18 * np.sin(progress * 20 + i))
                bx = int(width // 2 - 120 + i * 20)
                draw.rectangle([(bx, wave_y - h_bar), (bx + 8, wave_y + h_bar)], fill=accent_c)

            # Call to Action button at bottom of card
            cta_top = card_bottom - 85
            cta_bottom = card_bottom - 30
            draw.rectangle([(card_left + 50, cta_top), (card_right - 50, cta_bottom)], fill=accent_c)
            draw.text((width // 2, (cta_top + cta_bottom) // 2), "TAP TO EXPLORE NOW →", fill=(255, 255, 255), anchor="mm")

            frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            writer.write(frame)

        writer.release()

        # 3. Mux Audio & Video with FFmpeg (if imageio_ffmpeg available)
        final_video_created = False
        has_voice_output = os.path.exists(audio_wav) and os.path.getsize(audio_wav) > 1000
        if _imageio_ffmpeg is not None:
            try:
                from app.core.ffmpeg_utils import get_ffmpeg_executable
                ffmpeg_exe = get_ffmpeg_executable()
                if has_voice_output:
                    cmd = [
                        ffmpeg_exe, "-y",
                        "-i", temp_mp4,
                        "-i", audio_wav,
                        "-c:v", "libx264",
                        "-c:a", "aac",
                        "-shortest",
                        "-pix_fmt", "yuv420p",
                        final_mp4_path
                    ]
                else:
                    cmd = [
                        ffmpeg_exe, "-y",
                        "-i", temp_mp4,
                        "-c:v", "libx264",
                        "-pix_fmt", "yuv420p",
                        final_mp4_path
                    ]
                res = subprocess.run(cmd, capture_output=True, timeout=120)
                if res.returncode == 0 and os.path.exists(final_mp4_path):
                    final_video_created = True
                else:
                    logger.warning(f"FFmpeg encode failed: {res.stderr.decode()[:200]}")
            except Exception as ffmpeg_err:
                logger.warning(f"FFmpeg muxing warning: {ffmpeg_err}")

        # If ffmpeg not available or failed, just copy the raw mp4v file
        if not final_video_created and os.path.exists(temp_mp4):
            shutil.copyfile(temp_mp4, final_mp4_path)
            final_video_created = True

        # Cleanup temp directory
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

        file_size = os.path.getsize(final_mp4_path) if os.path.exists(final_mp4_path) else 0
        file_url = f"/uploads/media/ai_videos/{final_mp4_name}"

        return {
            "video_url": file_url,
            "file_name": final_mp4_name,
            "file_size_bytes": file_size,
            "duration_seconds": int(duration),
            "aspect_ratio": aspect_ratio,
            "model_name": "ai-video-voice-engine",
            "prompt": prompt,
            "has_voice": has_voice_output,
            "created_at": datetime.utcnow().isoformat()
        }


    # =========================================================================
    # 5. VIDEO GENERATION (Google Veo & Script/Storyboard Generator)
    # =========================================================================
    @classmethod
    def start_video_generation(
        cls,
        prompt: str,
        aspect_ratio: str = "16:9",
        duration_seconds: int = 5,
        fps: int = 24,
        resolution: str = "720p",
        model_name: str = "veo-3.1-generate-preview",
        image_base64: Optional[str] = None,
        image_url: Optional[str] = None
    ) -> Any:
        """Initiate video generation operation with Google Veo."""
        client = cls.get_client()
        if not client:
            logger.info("GEMINI_API_KEY is not configured; fallback copy synthesizer will be used.")

        target_model = model_name or "veo-3.1-fast-generate-preview"
        valid_ratios = ["16:9", "9:16"]
        ratio = aspect_ratio if aspect_ratio in valid_ratios else "16:9"

        # Google Veo requires duration_seconds to be between 4 and 8 (accepted: 4, 6, 8)
        dur = int(duration_seconds)
        if dur not in [4, 6, 8]:
            dur = 4 if dur <= 5 else (6 if dur <= 7 else 8)

        config = types.GenerateVideosConfig(
            aspect_ratio=ratio,
            duration_seconds=dur
        )

        image_obj = None
        if image_base64:
            clean_b64 = image_base64.split(",")[-1]
            raw_bytes = base64.b64decode(clean_b64)
            image_obj = types.Image(image_bytes=raw_bytes)
        elif image_url:
            try:
                if image_url.startswith("http://") or image_url.startswith("https://"):
                    with httpx.Client(timeout=10.0) as http_client:
                        r = http_client.get(image_url)
                        if r.status_code == 200:
                            image_obj = types.Image(image_bytes=r.content)
                elif os.path.exists(image_url):
                    with open(image_url, "rb") as f:
                        image_obj = types.Image(image_bytes=f.read())
            except Exception as img_err:
                logger.warning(f"Could not load reference image for Veo video: {img_err}")

        try:
            operation = client.models.generate_videos(
                model=target_model,
                prompt=prompt,
                image=image_obj,
                config=config
            )
            return operation

        except (APIError, ClientError) as e:
            err_code = getattr(e, "code", 0)
            err_msg = getattr(e, "message", str(e))
            logger.error(f"Google Veo API Error ({err_code}): {err_msg}")
            if err_code == 429 or "RESOURCE_EXHAUSTED" in str(e):
                raise ValueError(
                    "Google Veo video generation requires an active billing plan on Google AI Studio / Vertex AI "
                    "for your GEMINI_API_KEY (429 RESOURCE_EXHAUSTED). To generate AI videos with Veo, enable "
                    "billing at https://ai.google.dev/pricing."
                )
            elif err_code == 404 or "not found" in str(e).lower():
                raise ValueError(
                    f"Google Veo model '{target_model}' is not available for this API key. "
                    "Verify Veo model access in your Google AI Studio console."
                )
            else:
                raise ValueError(f"Google Veo API Error ({err_code}): {err_msg}")
        except Exception as e:
            logger.error(f"Veo launch failed: {str(e)}")
            raise ValueError(f"Failed to start Veo video generation: {str(e)}")

    @classmethod
    async def process_video_job(
        cls,
        job_id: int,
        operation_name: str,
        user_id: int,
        organization_id: int,
        prompt: str,
        aspect_ratio: str,
        duration_seconds: int,
        model_name: str
    ):
        """Background worker that polls the Veo operation and downloads the resulting MP4."""
        cls._ensure_directories()
        client = cls.get_client()
        if not client:
            return

        logger.info(f"Starting background polling for video Job #{job_id} (Operation: {operation_name})...")
        max_attempts = 120
        poll_interval = 5
        is_complete = False
        last_op = None

        for attempt in range(max_attempts):
            await asyncio.sleep(poll_interval)
            try:
                last_op = client.operations.get(operation_name)
            except Exception as poll_err:
                logger.warning(f"Operation poll check #{attempt+1} warning: {str(poll_err)}")
                continue

            if last_op.done:
                is_complete = True
                break

        db = SessionLocal()
        try:
            job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
            if not job:
                logger.error(f"GenerationJob #{job_id} not found in DB.")
                return

            if not is_complete:
                job.status = JobStatus.FAILED
                job.error_message = "Video generation timed out after 10 minutes."
                db.commit()
                return

            if getattr(last_op, 'error', None) and last_op.error:
                job.status = JobStatus.FAILED
                job.error_message = f"Veo operation failed: {last_op.error.message}"
                db.commit()
                return

            generated_videos = []
            if hasattr(last_op, 'response') and last_op.response:
                generated_videos = getattr(last_op.response, 'generated_videos', [])

            if not generated_videos:
                job.status = JobStatus.FAILED
                job.error_message = "Veo operation completed but returned no video output."
                db.commit()
                return

            first_video = generated_videos[0]
            video_bytes = first_video.video.video_bytes

            filename = f"veo_video_{uuid.uuid4().hex[:12]}.mp4"
            file_path = os.path.join(cls.AI_VIDEOS_DIR, filename)
            with open(file_path, "wb") as f:
                f.write(video_bytes)

            file_size = len(video_bytes)
            file_url = f"/uploads/media/ai_videos/{filename}"

            # Upload to Cloudinary CDN if configured
            if CloudinaryStorageProvider.is_configured():
                try:
                    c_res = CloudinaryStorageProvider.upload_local_file_path(
                        file_path=file_path,
                        organization_id=organization_id or 1,
                        folder_prefix="socialai/ai_videos"
                    )
                    if c_res and c_res.get("file_url"):
                        file_url = c_res["file_url"]
                except Exception as c_err:
                    logger.warning(f"Cloudinary upload of Veo video failed: {c_err}")

            media_asset = MediaAsset(
                file_name=filename,
                file_url=file_url,
                file_size_bytes=file_size,
                mime_type="video/mp4",
                media_type=MediaType.VIDEO,
                meta_info={
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "duration_seconds": duration_seconds,
                    "model_name": model_name,
                    "provider": "google-veo"
                },
                organization_id=organization_id
            )
            db.add(media_asset)
            db.flush()

            job.status = JobStatus.COMPLETED
            job.result_data = {
                "video_url": file_url,
                "file_name": filename,
                "file_size_bytes": file_size,
                "media_asset_id": media_asset.id,
                "aspect_ratio": aspect_ratio,
                "duration_seconds": duration_seconds,
                "model_name": model_name,
                "prompt": prompt,
                "completed_at": datetime.utcnow().isoformat()
            }
            job.tokens_used = 1500
            db.commit()
            logger.info(f"Video Job #{job_id} completed successfully. Saved to {file_url}")

        except Exception as e:
            logger.error(f"Error in background video processing for Job #{job_id}: {str(e)}")
            try:
                job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
                if job:
                    job.status = JobStatus.FAILED
                    job.error_message = str(e)
                    db.commit()
            except Exception:
                pass
        finally:
            db.close()
