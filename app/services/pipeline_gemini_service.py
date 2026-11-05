import os
import uuid
import json
import base64
import asyncio
import httpx
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from PIL import Image, ImageDraw, ImageFont

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.core.config import settings
from app.core.logging import logger
from app.core.gemini_client import get_gemini_client
from app.models.brand_profile import BrandProfile
from app.models.content import Content
from app.models.autonomous_pipeline import GeneratedAsset
from app.db.session import SessionLocal


# Pydantic Schemas for Structured Gemini Outputs
class CreativeBriefSchema(BaseModel):
    topic: str = Field(description="Main theme or subject for the social post")
    angle: str = Field(description="Unique hook or narrative angle")
    reason: str = Field(description="Strategic justification based on brand goals and trends")
    caption_brief: str = Field(description="Detailed guidelines for the caption text")
    image_prompt: str = Field(description="Photorealistic or high-end artistic prompt for image generation")
    video_prompt: str = Field(description="Short video / reel prompt or scene storyboard concept")
    hashtags: List[str] = Field(description="5 to 10 relevant, high-impact hashtags starting with #")
    cta: str = Field(description="Strong Call to Action aligned with brand objectives")
    tone: str = Field(description="Tone of voice to maintain throughout all assets")
    target_platforms: List[str] = Field(description="List of target platforms e.g. ['instagram', 'facebook', 'linkedin', 'youtube']")


class PlatformCaptionsSchema(BaseModel):
    instagram: str = Field(description="Instagram caption with compelling hook, line breaks, and hashtags")
    facebook: str = Field(description="Facebook post optimized for engagement, comments, and link CTA")
    linkedin: str = Field(description="LinkedIn post formatted with executive insights and thought leadership")
    youtube_title: str = Field(description="High-CTR YouTube video or Shorts title under 70 chars")
    youtube_description: str = Field(description="Comprehensive YouTube description with summary, key takeaways, and hashtags")
    twitter: Optional[str] = Field(default=None, description="Concise Twitter/X post under 280 chars with 2 hashtags")
    pinterest: Optional[str] = Field(default=None, description="Inspiring Pinterest pin title and description")


class PipelineGeminiService:
    """
    Autonomous Gemini Service Layer:
    Handles structured topic discovery, multi-platform copy generation,
    aspect-ratio-tailored visual synthesis, and async video generation with polling.
    """

    @classmethod
    def _get_genai_client(cls) -> genai.Client:
        client = get_gemini_client()
        if not client:
            raise ValueError("GEMINI_API_KEY is not configured in backend settings.")
        return client

    @classmethod
    def get_preferred_text_model(cls) -> str:
        configured = getattr(settings, "GEMINI_TEXT_MODEL", "")
        real_text_models = [
            "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.8-flash",
            "gemini-flash-latest", "gemini-3.1-flash-lite",
            "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"
        ]
        if configured and any(m in configured for m in real_text_models):
            return configured
        return "gemini-3.5-flash-lite"

    @classmethod
    def get_preferred_image_model(cls) -> str:
        configured = getattr(settings, "GEMINI_IMAGE_MODEL", "")
        # Validate model name is a real image generation model  
        real_image_models = ["imagen-3.0-generate-002", "imagen-3.0-generate-001", "imagen-3.0-fast-generate-001", "imagen-2.0"]
        if configured and any(m in configured for m in real_image_models):
            return configured
        return "imagen-3.0-generate-002"

    # =========================================================================
    # 1. ANALYZE BUSINESS CONTEXT & AUTO TOPIC BRIEF
    # =========================================================================

    @classmethod
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=2, max=10), reraise=True)
    async def generate_creative_brief(
        cls,
        brand_profile: BrandProfile,
        past_posts: List[Dict[str, Any]],
        target_platforms: List[str],
        content_preferences: Optional[str] = None,
        content_type: str = "both"
    ) -> CreativeBriefSchema:
        """
        Generates a fresh, strategic topic brief based on verified brand information,
        user-specified content preferences, target media format, and strictly avoids topics covered in the last 30 days.
        """
        client = cls._get_genai_client()
        model_name = cls.get_preferred_text_model()

        past_topics_str = ""
        if past_posts:
            recent_titles = [p.get("title") or (p.get("caption")[:60] + "...") for p in past_posts[:15] if p.get("caption") or p.get("title")]
            past_topics_str = "PAST 30 DAYS RECENT TOPICS (DO NOT REPEAT THESE):\n- " + "\n- ".join(recent_titles)

        user_prefs_str = ""
        if content_preferences and content_preferences.strip():
            user_prefs_str = f"USER CONTENT PREFERENCES & INSTRUCTIONS (PRIORITIZE THESE):\n- {content_preferences.strip()}\n"

        media_format_str = f"- Desired Media Format: {content_type.upper()} (Tailor visual and reel prompts specifically for this content type)"

        # =========================================================================
        # 5-TAB BRAND PROFILE KNOWLEDGE INGESTION
        # TAB 1: Core Business & Contacts
        # TAB 2: Products & Catalog
        # TAB 3: Positioning & Audience
        # TAB 4: Voice & Directives
        # TAB 5: Visual Identity & Colors
        # =========================================================================
        meta = brand_profile.meta_info if isinstance(getattr(brand_profile, "meta_info", None), dict) else {}

        # TAB 1: Core Business & Contacts
        business_name = (brand_profile.name or "").strip() or "Brand"
        industry = (brand_profile.industry or "").strip() or "General Business"
        category = meta.get("category") or getattr(brand_profile, "category", "") or industry
        tagline = (brand_profile.tagline or "").strip()
        business_desc = (
            meta.get("description")
            or meta.get("desc")
            or getattr(brand_profile, "description", None)
            or brand_profile.brand_guidelines
            or ""
        ).strip()
        location = (brand_profile.location or meta.get("location") or "").strip()
        timings = (brand_profile.timings or meta.get("timings") or "").strip()
        website = (meta.get("website") or "").strip()
        phone = (meta.get("phone") or "").strip()
        email = (meta.get("email") or "").strip()

        # TAB 2: Products & Catalog
        products_data = meta.get("products", []) or meta.get("offerings", []) or meta.get("items", []) or meta.get("catalog", [])
        products_lines = []
        if isinstance(products_data, list):
            for p in products_data[:10]:
                if isinstance(p, dict) and p.get("name"):
                    price_str = f" (${p.get('price')})" if p.get("price") else ""
                    cat_str = f" [Category: {p.get('category')}]" if p.get("category") else ""
                    desc_str = f": {p.get('description')}" if p.get("description") else ""
                    img_note = " (Catalog Image Provided)" if p.get("image_url") else ""
                    products_lines.append(f"  * {p['name']}{cat_str}{price_str}{desc_str}{img_note}")
                elif isinstance(p, str) and p.strip():
                    products_lines.append(f"  * {p.strip()}")
        joined_products = "\n".join(products_lines)
        catalog_section = joined_products if products_lines else "No catalog items registered. Derive visual concepts authentically from business description."
        num_catalog_items = len(products_lines)

        # TAB 3: Positioning & Audience
        target_audience = (brand_profile.target_audience or meta.get("target_demographics") or "").strip() or "Modern Professionals, Business Leaders & Target Consumers"
        target_demographics = (meta.get("target_demographics") or "").strip()
        audience_interests = (meta.get("audience_interests") or "").strip()
        usp = (meta.get("usp") or "").strip()
        positioning = (meta.get("brand_positioning") or "").strip()
        objectives = (meta.get("marketing_objectives") or "").strip()
        personas = meta.get("buyer_personas") or []
        personas_str = ", ".join([p.get("name", str(p)) if isinstance(p, dict) else str(p) for p in personas]) if personas else ""

        # TAB 4: Voice & Directives
        tone_of_voice = (brand_profile.tone_of_voice or "").strip() or "Professional, Inspiring & Authentic"
        messaging_style = (meta.get("messaging_style") or "").strip()
        cta = (brand_profile.cta or "").strip() or f"Connect with {business_name} today"
        avoid = (brand_profile.avoid or "").strip() or "No misleading claims, no clickbait, no low-resolution elements"
        key_themes = brand_profile.key_themes or ["Innovation", "Quality", "Simplicity", "Customer Success"]
        hashtags = brand_profile.hashtags or []

        # TAB 5: Visual Identity & Colors
        palette = brand_profile.color_palette if isinstance(brand_profile.color_palette, list) and brand_profile.color_palette else ["#6366f1", "#ec4899"]
        palette_hex_str = ", ".join(palette)
        visual_identity = (meta.get("visual_identity") or "").strip()
        typography = (meta.get("typography") or "").strip() or "Clean modern typography"
        logo_url = (brand_profile.logo_url or "").strip()

        # Detect industry type
        ind_lower = f"{business_name} {industry} {category} {business_desc}".lower()
        is_software = any(k in ind_lower for k in ["tech", "software", "cloud", "saas", "automation", "developer", "data", "app", "it services"])
        is_food = any(k in ind_lower for k in ["food", "restaurant", "cafe", "coffee", "culinary", "bistro", "bakery", "dining"])
        is_goods = any(k in ind_lower for k in ["footwear", "shoes", "clogs", "apparel", "clothing", "fashion", "jewelry", "bag", "retail", "store"])

        prompt = f"""
You are the Chief Creative Officer and Head of Social Media for this company.
Analyze ALL 5 TABS of the verified Brand Profile below to generate today's daily high-performing social media campaign brief and crystal-clear image & reel prompts.

=============================================================================
TAB 1: CORE BUSINESS & CONTACTS
- Brand / Business Name: {business_name}
- Industry: {industry}
- Category: {category}
- Official Tagline: {tagline}
- Business Description & Mission: {business_desc}
- Location & Roots: {location}
- Operating Hours: {timings}
- Website: {website}
- Direct Contacts: {email} | {phone}

TAB 2: PRODUCTS & CATALOG ({num_catalog_items} Offerings Registered)
{catalog_section}

TAB 3: POSITIONING & AUDIENCE
- Target Audience: {target_audience}
- Target Demographics: {target_demographics}
- Audience Interests: {audience_interests}
- Unique Selling Proposition (USP): {usp}
- Brand Positioning: {positioning}
- Marketing Objectives: {objectives}
- Target Buyer Personas: {personas_str}

TAB 4: VOICE & DIRECTIVES
- Tone of Voice: {tone_of_voice}
- Messaging Style: {messaging_style}
- Call to Action (CTA): {cta}
- Topics / Aesthetics to Strictly Avoid: {avoid}
- Core Themes / Pillars: {json.dumps(key_themes)}
- Brand Hashtags: {', '.join(hashtags)}

TAB 5: VISUAL IDENTITY & COLORS
- Brand Color Palette: {palette_hex_str} (Incorporate these brand tones into scene accents and ambient lighting)
- Visual Identity Aesthetic: {visual_identity or 'Clean contemporary commercial photography with elegant natural lighting'}
- Typography Family: {typography}
- Brand Logo URL: {logo_url}
=============================================================================

{media_format_str}
{user_prefs_str}
{past_topics_str}

CRITICAL RULES FOR IMAGE & REEL PROMPT GENERATION:
1. USE REGISTERED PRODUCTS (TAB 2):
   - When registered catalog items exist in TAB 2, feature them directly by name and function. Showcase their actual utility and real-world benefit.
   - For Technology, Software, SaaS & Cloud:
     * Feature modern workstations, clean monitors displaying the software's intuitive cloud dashboard, sleek minimal desks with ambient lighting, and data flow visuals.
     * NEVER assume retail shopping, physical apparel, or unrelated payment hardware.
   - For Physical Goods, Apparel, Footwear, or Retail:
     * Feature the authentic product design, material craftsmanship, textures, and lifestyle utility.
   - For Food & Hospitality:
     * Feature culinary artistry, fresh ingredients, and dining ambiance.

2. HARMONIZE WITH VISUAL IDENTITY & COLORS (TAB 5):
   - The image_prompt MUST describe scene lighting, textures, props, and backdrops that harmoniously reflect the brand's visual identity ({visual_identity or 'Clean, modern, uncluttered'}) and color palette ({palette_hex_str}).

3. TAILOR TO AUDIENCE & POSITIONING (TAB 3):
   - Reflect the lifestyle, setting, and environment of the target audience ({target_audience}) and positioning ({positioning}).

4. STRICTLY ENFORCE NEGATIVE RESTRICTIONS (TAB 4):
   - Strictly forbid and exclude anything listed in 'Topics / Aesthetics to Strictly Avoid' ({avoid}).

5. ROTATE DAILY CONTENT PILLARS (NEVER REPEAT RECENT ANGLES):
   - Pillar 1: Pain Point vs. Solution (Before & After transformation)
   - Pillar 2: Feature & Capability Spotlight (Deep-dive on a specific workflow/advantage)
   - Pillar 3: Behind the Team & Mission (The ethos, developer/founder story, customer care)
   - Pillar 4: Actionable Value / Quick Win (Educational tip for the target audience)
   - Pillar 5: Client Success / Operational Freedom (How clients thrive and save time)
   - Pillar 6: Vision & Innovation (Where the industry is heading)

6. IMAGE & VIDEO DIVERSITY:
   - Provide a unique, photorealistic visual scene prompt directly tied to today's topic, registered products, and visual identity.
   - Output MUST conform strictly to the required JSON schema.
"""

        try:
            # Run in threadpool with strict timeout to prevent slow latency
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=CreativeBriefSchema,
                        temperature=0.7,
                    )
                ),
                timeout=30.0
            )

            raw_text = response.text.strip()
            data = json.loads(raw_text)
            return CreativeBriefSchema(**data)
        except Exception as e:
            logger.warning(f"[PipelineGemini] Generation failed or timed out: {e}. Using instant brand-tailored synthesis.")

            import random
            import time

            available_themes = list(brand_profile.key_themes) if brand_profile.key_themes else ["Smart Automation", "Clean Interfaces", "Modern Efficiency", "Fast Delivery", "Customer Success"]
            past_titles_lower = " ".join([str(p.get("title") or "") + " " + str(p.get("caption") or "") for p in past_posts]).lower()
            unseen_themes = [t for t in available_themes if t.lower() not in past_titles_lower]
            theme_choice = random.choice(unseen_themes) if unseen_themes else random.choice(available_themes)

            valid_prods = [p for p in products_data if isinstance(p, dict) and p.get("name")]
            p0 = valid_prods[0] if valid_prods else None
            p0_name = p0.get("name") if p0 else "Core Solution"
            p0_desc = p0.get("description") if p0 else (business_desc[:90] if business_desc else "tailored modern solutions")
            p0_cat = p0.get("category") if p0 else category

            visual_style_hint = f"{visual_identity}, " if visual_identity else ""
            color_style_hint = f"color accents inspired by {palette_hex_str}, " if palette else ""

            if is_software:
                CREATIVE_DIRECTIONS = [
                    {
                        "angle": f"From spreadsheet chaos to effortless cloud automation with {business_name}",
                        "topic": f"Ditching Spreadsheet Chaos with {business_name}",
                        "caption_brief": f"Address the daily pain points of manual tracking, spreadsheet errors, and administrative burnout. Showcase how {business_name} delivers clean, cloud-based tools that give business owners their time back.",
                        "image_style": f"photorealistic minimalist modern tech workspace, sleek ultra-wide monitor displaying intuitive cloud dashboard for {p0_name if p0 else business_name}, {visual_style_hint}{color_style_hint}warm morning sun streaming through large window, lush indoor plant, authentic 35mm commercial photography",
                        "video_style": "cinematic push-in starting from a closed laptop to a bright modern desk where a clean cloud dashboard loads smoothly with subtle data visual animations"
                    },
                    {
                        "angle": f"Product Spotlight: Streamlining daily operations with {p0_name}",
                        "topic": f"{p0_name} Spotlight: Clean Automation",
                        "caption_brief": f"Deep-dive into {p0_name} ({p0_desc}). Explain how {business_name} empowers teams with intuitive interfaces, fast delivery, and long-term support.",
                        "image_style": f"contemporary tech studio in Chennai, developer working on a high-resolution code and UI workstation showcasing {p0_name}, {visual_style_hint}{color_style_hint}soft ambient lighting, clean aesthetic desk, shallow depth of field",
                        "video_style": f"dynamic glide across modern workstation showing interactive workflow diagrams and operational metrics for {p0_name}"
                    },
                    {
                        "angle": f"Enterprise automation power without the complexity: The {business_name} way",
                        "topic": f"Simple Automation for Growing Teams: {theme_choice}",
                        "caption_brief": f"Explain why small and mid-sized companies deserve powerful automation without enterprise bloat. Highlight {business_name}'s human-first software philosophy.",
                        "image_style": f"modern bright conference room with a sleek wall-mounted glass display showing clean workflow automation diagrams, {visual_style_hint}{color_style_hint}soft natural daylight, minimalist architectural aesthetic",
                        "video_style": "fluid cinematic tracking shot across modern team collaboration space with vibrant data flow animations on screen"
                    },
                    {
                        "angle": f"Behind our code: Why we prioritize clean interfaces and human-first software",
                        "topic": f"Crafting Clean Software: Inside {business_name}",
                        "caption_brief": f"Share the engineering ethos of {business_name}: passionate developers committed to building software people actually enjoy using every day.",
                        "image_style": f"close-up of elegant mechanical keyboard, minimalist notebook, and monitor displaying crisp clean application architecture for {business_name}, {visual_style_hint}{color_style_hint}soft warm desk lamp, cinematic 8k quality",
                        "video_style": "first-person smooth dolly push-in toward glowing interface showing seamless one-click workflow automation"
                    }
                ]
            else:
                CREATIVE_DIRECTIONS = [
                    {
                        "angle": f"Behind the craftsmanship and signature design of {business_name}",
                        "topic": f"{business_name} Spotlight: {p0_name if p0 else theme_choice}",
                        "caption_brief": f"Highlight the signature craftsmanship, design philosophy, and daily utility of {p0_name if p0 else business_name}.",
                        "image_style": f"minimalist architectural showcase of {p0_name if p0 else business_name}, {p0_desc}, dramatic directional side rim lighting, {visual_style_hint}{color_style_hint}pristine clean studio background, sharp macro details, authentic commercial photography, 8k resolution",
                        "video_style": "smooth slow-motion rotating orbit showcasing material textures and precision construction, dramatic unveil"
                    },
                    {
                        "angle": f"Everyday performance and authentic quality with {business_name}",
                        "topic": f"Daily Elevation with {business_name}",
                        "caption_brief": f"Showcase why {business_name} transforms your daily routine with uncompromising comfort and style.",
                        "image_style": f"natural sun-drenched lifestyle environment featuring {p0_name if p0 else business_name}, organic warm oak surface, {visual_style_hint}{color_style_hint}soft daylight through window, clean authentic composition, 8k commercial photography",
                        "video_style": "dynamic tracking glide through natural warm daylight, smooth fluid camera easing, vibrant modern color grade"
                    },
                    {
                        "angle": f"Thoughtful design and premium experience at {business_name}",
                        "topic": f"The Next Era: {business_name} {theme_choice}",
                        "caption_brief": f"Explore how {business_name} breaks convention with innovative engineering and iconic design.",
                        "image_style": f"editorial flat-lay on textured linen and raw stone, soft diffused daylight, {visual_style_hint}{color_style_hint}earthy modern tones, ample negative space, luxury aesthetic",
                        "video_style": "serene overhead continuous pan across natural textures and raw materials, graceful cinematic pacing"
                    }
                ]

            selected_dir = random.choice(CREATIVE_DIRECTIONS)
            unique_seed = f"v{int(time.time()) % 10000}"

            clean_tags = [f"#{brand_profile.name.replace(' ', '')}", f"#{category.replace(' ', '').replace('&', '')}", f"#{theme_choice.replace(' ', '')}", "#Innovation", "#QualityFirst", "#SmartGrowth"]
            if brand_profile.hashtags:
                clean_tags.extend([h if h.startswith('#') else f"#{h}" for h in brand_profile.hashtags[:3]])

            return CreativeBriefSchema(
                topic=selected_dir["topic"],
                angle=selected_dir["angle"],
                reason=f"Strategic alignment with brand focus on {theme_choice} and verified brand narrative ({unique_seed}).",
                caption_brief=selected_dir["caption_brief"],
                image_prompt=f"{selected_dir['image_style']} (seed: {unique_seed})",
                video_prompt=f"Vertical 9:16 commercial reel for {brand_profile.name}: {selected_dir['video_style']}, cinematic lighting, sharp focus",
                hashtags=clean_tags[:6],
                cta=brand_profile.cta or f"Connect with {brand_profile.name} to learn more",
                tone=brand_profile.tone_of_voice or "Professional, Inspiring & Authentic",
                target_platforms=target_platforms
            )

    # =========================================================================
    # 2. PARALLEL TEXT GENERATION (Multi-Platform Copy)
    # =========================================================================

    @classmethod
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=2, max=8), reraise=True)
    async def generate_platform_captions(
        cls,
        brief: CreativeBriefSchema,
        brand_profile: BrandProfile
    ) -> PlatformCaptionsSchema:
        """
        Generates tailored copy for Instagram, Facebook, LinkedIn, YouTube, Twitter, and Pinterest
        aligned with the creative brief and character limit constraints.
        """
        client = cls._get_genai_client()
        model_name = cls.get_preferred_text_model()

        prompt = f"""
Write high-converting, platform-tailored social media captions for '{brand_profile.name}' based on this creative brief:

BRIEF DETAILS:
- Topic: {brief.topic}
- Angle: {brief.angle}
- Caption Directive: {brief.caption_brief}
- Tone: {brief.tone}
- Call to Action (CTA): {brief.cta}
- Brand Hashtags: {', '.join(brief.hashtags)}

PLATFORM REQUIREMENTS:
- Instagram: Engaging hook on line 1, concise value-driven body with spacing, clear CTA, and 5-8 relevant hashtags.
- Facebook: Conversational storytelling tone, community discussion prompt, link CTA.
- LinkedIn: Executive thought leadership angle, bulleted insights, professional tone, 3-4 professional hashtags.
- YouTube: High-CTR video title (under 70 chars) and comprehensive description with timestamps placeholder and CTA.
- Twitter/X: Punchy tweet under 260 characters including 2 hashtags.
- Pinterest: Catchy Pin title and inspiring description with keywords.

Output MUST strictly follow the JSON schema.
"""

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PlatformCaptionsSchema,
                    temperature=0.7,
                )
            )
            return PlatformCaptionsSchema(**json.loads(response.text.strip()))
        except Exception as e:
            logger.warning(f"[PipelineGemini] Platform copy generation exception: {e}")
            # Reliable fallback structure
            return PlatformCaptionsSchema(
                instagram=f"✨ {brief.topic}\n\n{brief.caption_brief}\n\n👉 {brief.cta}\n\n" + " ".join(brief.hashtags),
                facebook=f"{brief.topic}\n\n{brief.caption_brief}\n\nLearn more: {brief.cta}",
                linkedin=f"📌 {brief.topic}\n\n{brief.caption_brief}\n\nWhat are your thoughts on this approach?\n\n" + " ".join(brief.hashtags[:4]),
                youtube_title=f"{brand_profile.name} - {brief.topic}"[:70],
                youtube_description=f"In this update, we explore {brief.topic}.\n\n{brief.caption_brief}\n\n{brief.cta}\n\n" + " ".join(brief.hashtags),
                twitter=f"{brief.topic}: {brief.caption_brief}"[:240] + " " + " ".join(brief.hashtags[:2]),
                pinterest=f"{brief.topic}\n\n{brief.caption_brief}\n\n{brief.cta}"
            )

    # =========================================================================
    # 3. MULTI-RATIO IMAGE GENERATION
    # =========================================================================

    @classmethod
    async def generate_branded_image(
        cls,
        prompt: str,
        aspect_ratio: str = "1:1",
        brand_profile: Optional[BrandProfile] = None,
        master_image_bytes: Optional[bytes] = None
    ) -> Dict[str, Any]:
        """
        Generates visual artwork tailored to the requested aspect ratio:
        - 1:1 Square (Instagram/Facebook feed)
        - 4:5 Portrait (Instagram/Facebook vertical feed)
        - 1.91:1 Landscape (LinkedIn/Twitter)
        - 16:9 Landscape (YouTube)
        Saves the file to local media storage and returns metadata.
        """
        output_dir = os.path.join(settings.LOCAL_STORAGE_DIR, "media")
        os.makedirs(output_dir, exist_ok=True)
        file_id = f"img_{uuid.uuid4().hex[:12]}_{aspect_ratio.replace(':', '_')}.png"
        file_path = os.path.join(output_dir, file_id)
        relative_url = f"/uploads/media/{file_id}"

        # Dimensions mapping
        dim_map = {
            "1:1": (1080, 1080),
            "4:5": (1080, 1350),
            "1.91:1": (1200, 628),
            "16:9": (1920, 1080),
            "9:16": (1080, 1920)
        }
        width, height = dim_map.get(aspect_ratio, (1080, 1080))

        image_bytes = None
        ratio_descriptions = {
            "1:1": "square balanced commercial hero composition, centered flagship showcase",
            "4:5": "vertical portrait composition, dynamic vertical framing with expansive background depth of field",
            "1.91:1": "wide cinematic landscape panorama, horizontal environmental context",
            "9:16": "vertical full-height 9:16 commercial composition, dynamic vertical framing",
            "16:9": "cinematic widescreen commercial composition, environmental wide shot"
        }
        ratio_hint = ratio_descriptions.get(aspect_ratio, "commercial photography")
        composition_token = uuid.uuid4().hex[:6]

        # Gather brand intelligence from all 5 tabs
        brand_meta = brand_profile.meta_info if brand_profile and isinstance(getattr(brand_profile, "meta_info", None), dict) else {}
        b_palette = brand_profile.color_palette if brand_profile and isinstance(getattr(brand_profile, "color_palette", None), list) and brand_profile.color_palette else []
        b_visual = (brand_meta.get("visual_identity") or "").strip()
        b_avoid = (brand_profile.avoid if brand_profile and brand_profile.avoid else "").strip()

        # Build prompt extensions
        enhancers = [prompt.strip()]
        if b_visual and b_visual.lower() not in prompt.lower():
            enhancers.append(f"aesthetic style: {b_visual}")
        if b_palette and not any(c.lower() in prompt.lower() for c in b_palette[:2]):
            enhancers.append(f"color palette accents: {', '.join(b_palette[:2])}")
        enhancers.append(ratio_hint)
        enhancers.append("high resolution 8k, professional commercial studio lighting, pristine clarity")
        if b_avoid:
            enhancers.append(f"strictly avoid: {b_avoid}")
        enhancers.append(f"(composition seed: {composition_token})")

        enhanced_prompt = ", ".join(enhancers)

        used_provider = "studio-canvas"
        from app.services.image_generation_service import ImageGenerationService

        # 1. Cloudflare Workers AI Flux (schnell)
        try:
            image_bytes = await ImageGenerationService._generate_cloudflare_image(enhanced_prompt)
            if image_bytes:
                used_provider = "cloudflare-flux"
                logger.info(f"[PipelineGemini] Generated image via Cloudflare Flux for {aspect_ratio}.")
        except Exception as cf_err:
            logger.debug(f"[PipelineGemini] Cloudflare image notice: {cf_err}")

        # 2. Supported Image Fallback: Fal FLUX (fal-ai/flux/schnell)
        if image_bytes is None:
            try:
                image_bytes = await ImageGenerationService._generate_fal_image(enhanced_prompt)
                if image_bytes:
                    used_provider = "fal-flux"
                    logger.info(f"[PipelineGemini] Generated image via Fal FLUX fallback for {aspect_ratio}.")
            except Exception as fal_err:
                logger.debug(f"[PipelineGemini] Fal image notice: {fal_err}")

        # 3. Supported Image Fallback: Google Gemini / Imagen
        if image_bytes is None and settings.GEMINI_API_KEY:
            client = cls._get_genai_client()
            image_models = [
                cls.get_preferred_image_model(),  # imagen-3.0-generate-002
                "imagen-3.0-generate-001",
                "imagen-3.0-fast-generate-001",
            ]
            seen = set()
            image_models = [m for m in image_models if not (m in seen or seen.add(m))]
            for img_model in image_models:
                try:
                    response = await asyncio.to_thread(
                        client.models.generate_images,
                        model=img_model,
                        prompt=enhanced_prompt,
                        config=types.GenerateImagesConfig(
                            number_of_images=1,
                            output_mime_type="image/png",
                            aspect_ratio="1:1" if aspect_ratio in ["1:1", "4:5"] else ("16:9" if aspect_ratio in ["16:9", "1.91:1"] else "9:16")
                        )
                    )
                    if response and response.generated_images:
                        image_bytes = response.generated_images[0].image.image_bytes
                        used_provider = "gemini-imagen"
                        logger.info(f"[PipelineGemini] Generated image with {img_model} for {aspect_ratio}")
                        break
                except Exception as e:
                    logger.debug(f"[PipelineGemini] Model {img_model} image generation failed: {e}")
                    continue

        # 4. Zero-Auth Unmetered Photorealistic AI Image Fallback: Pollinations AI
        if image_bytes is None:
            try:
                image_bytes = await ImageGenerationService._generate_pollinations_image(
                    prompt=enhanced_prompt,
                    aspect_ratio="1:1" if aspect_ratio in ["1:1", "4:5"] else ("16:9" if aspect_ratio in ["16:9", "1.91:1"] else "9:16"),
                    brand_profile=brand_profile
                )
                if image_bytes:
                    used_provider = "flux-pollinations"
                    logger.info(f"[PipelineGemini] Generated photorealistic image via Pollinations AI for {aspect_ratio}.")
            except Exception as poll_err:
                logger.warning(f"[PipelineGemini] Pollinations image notice: {poll_err}")

        # 5. High-Fidelity Campaign Master Image Derivation
        if image_bytes is None and master_image_bytes:
            try:
                image_bytes = ImageGenerationService.adapt_aspect_ratio(
                    master_image_bytes,
                    target_width=width,
                    target_height=height
                )
                if image_bytes:
                    used_provider = "master-derived"
                    logger.info(f"[PipelineGemini] Derived brand-consistent {aspect_ratio} image from campaign master image.")
            except Exception as ad_err:
                logger.warning(f"[PipelineGemini] Master image adaptation notice: {ad_err}")

        # 6. Brand-Aligned Photoreal Editorial Photography
        if image_bytes is None:
            try:
                image_bytes = await ImageGenerationService._fetch_commons_photoreal_image(
                    prompt=enhanced_prompt,
                    aspect_ratio=aspect_ratio,
                    brand_profile=brand_profile
                )
                if image_bytes:
                    used_provider = "commons-photoreal"
                    logger.info(f"[PipelineGemini] Fetched brand-aligned photography for {aspect_ratio}.")
            except Exception as comm_err:
                logger.warning(f"[PipelineGemini] Commons image notice: {comm_err}")

        # If API image is received, clean watermark, sharpen, and save to exact aspect ratio
        if image_bytes:
            try:
                image_bytes = ImageGenerationService._clean_and_enhance_image(
                    image_bytes,
                    width,
                    height,
                    strip_watermark=(used_provider != "cloudflare-flux")
                )
            except Exception as enh_err:
                logger.warning(f"Image enhancement notice: {enh_err}")

            with open(file_path, "wb") as f:
                f.write(image_bytes)
        else:
            # Keep a scheduled campaign progressing when an external image
            # provider is temporarily unavailable. The local renderer creates
            # a valid, aspect-ratio-correct PNG which is still uploaded to
            # Cloudinary before it can be published.
            fallback_colors = (
                brand_profile.color_palette
                if brand_profile and isinstance(brand_profile.color_palette, list)
                else []
            )
            cls._synthesize_branded_graphic(
                output_path=file_path,
                width=width,
                height=height,
                title=prompt,
                brand_name=brand_profile.name if brand_profile and brand_profile.name else "Social AI",
                colors=fallback_colors,
            )
            logger.warning(
                "[PipelineGemini] External image providers returned no image; "
                "used the local resilient graphic fallback for %s.",
                aspect_ratio,
            )

        # Persist the generated image in Cloudinary so overview thumbnails and
        # scheduled platform workers have a durable public URL.  A temporary
        # network outage must not turn an otherwise valid local image into a
        # failed pipeline run: the local static-media URL remains usable by the
        # dashboard and can be uploaded when the connection is restored.
        from app.integrations.storage.cloudinary_storage import CloudinaryStorageProvider
        try:
            uploaded = CloudinaryStorageProvider.upload_local_file_path(
                file_path=file_path,
                organization_id=brand_profile.organization_id if brand_profile and brand_profile.organization_id else 1,
                folder_prefix="socialai/ai_images",
                resource_type="image"
            )
            relative_url = uploaded["file_url"]
        except Exception as upload_error:
            logger.warning(
                "[PipelineGemini] Cloudinary image upload unavailable; "
                "retaining local media for %s: %s",
                aspect_ratio,
                upload_error,
            )

        return {
            "status": "done",
            "file_path": relative_url,
            "local_path": file_path,
            "aspect_ratio": aspect_ratio,
            "width": width,
            "height": height,
            "provider": used_provider
        }

    @classmethod
    def _synthesize_branded_graphic(
        cls,
        output_path: str,
        width: int,
        height: int,
        title: str,
        brand_name: str,
        colors: List[str]
    ):
        """
        Creates an ultra-crisp studio branded graphic using Pillow when external image quota is constrained.
        """
        img = Image.new("RGB", (width, height), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Draw subtle gradient backdrop
        c1 = (99, 102, 241)
        c2 = (15, 23, 42)
        for y in range(height):
            ratio = y / max(height, 1)
            r = int(c1[0] * (1 - ratio) + c2[0] * ratio)
            g = int(c1[1] * (1 - ratio) + c2[1] * ratio)
            b = int(c1[2] * (1 - ratio) + c2[2] * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        # Brand Badge at top
        badge_text = f"✨  {brand_name.upper()}  ✨"
        draw.rounded_rectangle([(60, 60), (width - 60, 130)], radius=16, fill=(30, 41, 59, 200), outline=(99, 102, 241), width=2)
        draw.text((width // 2, 95), badge_text, fill=(255, 255, 255), anchor="mm")

        # Headline Card in center
        card_top = height // 2 - 120
        card_bottom = height // 2 + 140
        draw.rounded_rectangle([(60, card_top), (width - 60, card_bottom)], radius=24, fill=(15, 23, 42, 220), outline=(147, 51, 234), width=3)
        draw.text((width // 2, height // 2 - 30), title[:90], fill=(248, 250, 252), anchor="mm")
        draw.text((width // 2, height // 2 + 50), "OFFICIAL BRAND UPDATE", fill=(168, 85, 247), anchor="mm")

        # Bottom Bar
        draw.rectangle([(0, height - 80), (width, height)], fill=(10, 15, 30))
        draw.text((width // 2, height - 40), f"Verified Social Creative • {brand_name}", fill=(148, 163, 184), anchor="mm")

        img.save(output_path, "PNG")

    @classmethod
    async def _attach_voiceover_narration(
        cls,
        video_path: str,
        prompt: str,
        brand_profile: Optional[BrandProfile],
        duration_seconds: int = 6
    ) -> str:
        """
        Synthesizes multi-voice commercial narration with acoustic micro-rate variations
        using Microsoft Edge Neural TTS and muxes it onto the video with FFmpeg.
        Persona is dynamically matched to the brand tone (Christopher, Jenny, Aria, Guy, Sonia, Ryan, Ava, Eric).
        """
        if not video_path or not os.path.exists(video_path):
            return video_path
        try:
            from app.services.voice_service import VoiceService
            import imageio_ffmpeg
            import subprocess

            brand_name = brand_profile.name if brand_profile and brand_profile.name else "Our Brand"
            brand_tone = brand_profile.tone_of_voice if brand_profile else "professional"

            # 1. Resolve multi-voice persona
            voice_id = VoiceService.resolve_voice(
                brand_tone=brand_tone,
                seed=f"{brand_name}-{os.path.basename(video_path)}"
            )

            # 2. Compose brief commercial narration script tailored to duration
            meta_inf = brand_profile.meta_info if isinstance(getattr(brand_profile, "meta_info", None), dict) else {}
            b_desc = meta_inf.get("description") or getattr(brand_profile, "description", None) or getattr(brand_profile, "brand_guidelines", None)
            b_ind = brand_profile.industry if brand_profile else ""
            script = VoiceService.compose_commercial_voiceover(
                prompt=prompt,
                brand_name=brand_name,
                brand_tagline=getattr(brand_profile, "tagline", "") or "",
                brand_tone=brand_tone,
                target_duration=duration_seconds,
                variation_seed=os.path.basename(video_path),
                brand_description=b_desc,
                industry=b_ind
            )

            audio_path = video_path.replace(".mp4", "_voice.mp3")
            await VoiceService.synthesize_speech(script, audio_path, voice=voice_id)

            if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
                muxed_path = video_path.replace(".mp4", "_narrated.mp4")
                from app.core.ffmpeg_utils import get_ffmpeg_executable
                ffmpeg_exe = get_ffmpeg_executable()
                cmd = [
                    ffmpeg_exe, "-y",
                    "-i", video_path,
                    "-i", audio_path,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-shortest",
                    muxed_path
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
                if proc.returncode == 0 and os.path.exists(muxed_path) and os.path.getsize(muxed_path) > 1000:
                    try:
                        os.remove(audio_path)
                        os.replace(muxed_path, video_path)
                    except Exception:
                        video_path = muxed_path
                    logger.info(f"[PipelineGemini] Attached Edge Neural TTS narration ({voice_id}) with micro-rate variation to {video_path}")
                else:
                    if os.path.exists(audio_path):
                        try:
                            os.remove(audio_path)
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"[PipelineGemini] Edge Neural TTS narration attachment warning: {e}")

        return video_path

    # =========================================================================
    # 4. ASYNC VIDEO GENERATION & RESTART-SAFE POLLING
    # =========================================================================

    @classmethod
    async def start_async_video_generation(
        cls,
        prompt: str,
        aspect_ratio: str = "9:16",
        brand_profile: Optional[BrandProfile] = None,
        image_url: Optional[str] = None,
        image_urls: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Generates cinematic AI video using Google Veo (same AI as Gemini AI Studio).
        Produces authentic generative video footage without watermarks or brand logos.
        Falls back to Magic Hour / Hugging Face or clean smooth motion if Veo quota is constrained.
        """
        output_dir = os.path.join(settings.LOCAL_STORAGE_DIR, "media", "ai_videos")
        os.makedirs(output_dir, exist_ok=True)
        valid_ratio = "9:16" if str(aspect_ratio).strip() in ["9:16", "4:5", "portrait"] else "16:9"

        # 1. Primary Lowest-Cost AI Video Engine: Lightricks LTX Video API ($0.09/s -> ₹38.70 for 30s commercial reel)
        try:
            from app.services.ltx_video_service import LTXVideoService
            if LTXVideoService.is_configured():
                logger.info(f"[PipelineGemini] Dispatching lowest-cost AI video generation with LTX Video API (ltx-2-5-fast)...")
                ltx_res = await LTXVideoService.generate_and_persist_video(
                    prompt=prompt,
                    brand=brand_profile,
                    aspect_ratio=valid_ratio,
                    duration_seconds=30,
                    organization_id=brand_profile.organization_id if brand_profile else 1
                )
                if ltx_res and ltx_res.get("file_path"):
                    logger.info(f"[PipelineGemini] LTX Video generated and persisted successfully: {ltx_res['file_path']} (Cost: ₹{ltx_res.get('cost_inr', 38.70)})")
                    return {
                        "operation_id": f"ltx_{uuid.uuid4().hex[:16]}",
                        "status": "done",
                        "prompt": prompt,
                        "aspect_ratio": valid_ratio,
                        "file_path": ltx_res["file_path"],
                        "local_path": ltx_res.get("local_path"),
                        "duration_seconds": ltx_res.get("duration_seconds", 30),
                        "mime_type": "video/mp4",
                        "provider": "ltx-video",
                        "model_name": "ltx-2-5-fast",
                        "cost_usd": ltx_res.get("cost_usd", 0.45),
                        "cost_inr": ltx_res.get("cost_inr", 38.70),
                        "audio_provider": "edge-neural-tts"
                    }
        except Exception as ltx_err:
            logger.warning(f"[PipelineGemini] LTX Video generation notice: {ltx_err}. Checking alternate providers...")

        # 2. Free Neural AI Video Engine (Hugging Face ZeroGPU - Lightricks LTX Video)
        try:
            from app.services.huggingface_video_service import HuggingFaceVideoService
            if HuggingFaceVideoService.is_configured():
                logger.info("[PipelineGemini] Dispatching true AI neural video generation via Hugging Face LTX-Video...")
                hf_res = await HuggingFaceVideoService.generate_and_persist_video(
                    prompt=prompt,
                    brand=brand_profile,
                    aspect_ratio=valid_ratio,
                    duration_seconds=30,
                    input_image_path=image_url,
                    organization_id=brand_profile.organization_id if brand_profile else 1
                )
                if hf_res and hf_res.get("file_path"):
                    logger.info(f"[PipelineGemini] True AI neural video generated successfully via Hugging Face: {hf_res['file_path']}")
                    return {
                        "operation_id": f"hf_{uuid.uuid4().hex[:16]}",
                        "status": "done",
                        "prompt": prompt,
                        "aspect_ratio": valid_ratio,
                        "file_path": hf_res["file_path"],
                        "local_path": hf_res.get("local_path"),
                        "duration_seconds": hf_res.get("duration_seconds", 30),
                        "mime_type": "video/mp4",
                        "provider": "huggingface-ltx-video",
                        "model_name": "lightricks-ltx-video-distilled",
                        "cost_usd": 0.0,
                        "cost_inr": 0.0,
                        "audio_provider": "edge-neural-tts"
                    }
        except Exception as hf_err:
            logger.warning(f"[PipelineGemini] Hugging Face LTX-Video notice: {hf_err}. Checking alternate providers...")

        # 3. Google Veo Engine (Enterprise / Vertex AI configured only)
        # Note: Veo preview models return 404 on standard API keys; skip if not explicitly configured with enterprise key
        if getattr(settings, "GEMINI_API_KEY", None) and getattr(settings, "GEMINI_VIDEO_MODEL", "").startswith("veo-enterprise"):
            try:
                client = cls._get_genai_client()
                clean_veo_prompt = (
                    f"{prompt.strip()}. "
                    "Cinematic commercial 4k high definition, fluid camera movement, photorealistic lighting, "
                    "pristine visual clarity, clean footage, no text, no watermark, no brand logo."
                )
                operation = await asyncio.to_thread(
                    client.models.generate_videos,
                    model=settings.GEMINI_VIDEO_MODEL,
                    prompt=clean_veo_prompt,
                    config=types.GenerateVideosConfig(
                        aspect_ratio=valid_ratio,
                        duration_seconds=6
                    )
                )
                if operation and getattr(operation, "name", None):
                    last_op = operation
                    for attempt in range(25):
                        await asyncio.sleep(4)
                        try:
                            last_op = await asyncio.to_thread(client.operations.get, operation.name)
                            if getattr(last_op, "done", False):
                                break
                        except Exception:
                            pass
                    if getattr(last_op, "done", False) and not getattr(last_op, "error", None):
                        resp = getattr(last_op, "response", None)
                        generated_vids = getattr(resp, "generated_videos", []) if resp else []
                        if generated_vids:
                            video_bytes = generated_vids[0].video.video_bytes
                            filename = f"veo_pipeline_{uuid.uuid4().hex[:12]}.mp4"
                            file_path = os.path.join(output_dir, filename)
                            with open(file_path, "wb") as vf:
                                vf.write(video_bytes)
                            file_path = await cls._attach_voiceover_narration(
                                video_path=file_path,
                                prompt=prompt,
                                brand_profile=brand_profile,
                                duration_seconds=6
                            )
                            file_url = f"/uploads/media/ai_videos/{filename}"
                            from app.integrations.storage.cloudinary_storage import CloudinaryStorageProvider
                            if CloudinaryStorageProvider.is_configured():
                                try:
                                    c_res = CloudinaryStorageProvider.upload_local_file_path(
                                        file_path=file_path,
                                        organization_id=brand_profile.organization_id if brand_profile else 1,
                                        folder_prefix="socialai/ai_videos",
                                        resource_type="video"
                                    )
                                    if c_res and c_res.get("file_url"):
                                        file_url = c_res["file_url"]
                                except Exception:
                                    pass
                            return {
                                "operation_id": getattr(operation, "name", f"veo_{uuid.uuid4().hex[:16]}"),
                                "status": "done",
                                "prompt": prompt,
                                "aspect_ratio": valid_ratio,
                                "file_path": file_url,
                                "local_path": file_path,
                                "duration_seconds": 6,
                                "mime_type": "video/mp4",
                                "provider": "google-veo",
                                "audio_provider": "edge-neural-tts"
                            }
            except Exception as veo_outer_err:
                logger.warning(f"[PipelineGemini] Google Veo video generation bypassed: {veo_outer_err}")

        # 2. Generative Video Fallback: Magic Hour (if configured)
        try:
            from app.services.magic_hour_service import MagicHourVideoService
            if MagicHourVideoService.is_configured():
                logger.info("[PipelineGemini] Attempting Magic Hour video generation fallback...")
                mh_res = await MagicHourVideoService.generate_and_persist_video(
                    prompt=prompt,
                    aspect_ratio=valid_ratio,
                    duration_seconds=10,
                    organization_id=brand_profile.organization_id if brand_profile else 1
                )
                if mh_res and mh_res.get("file_path"):
                    return {
                        "operation_id": f"mh_{uuid.uuid4().hex[:16]}",
                        "status": "done",
                        "prompt": prompt,
                        "aspect_ratio": valid_ratio,
                        "file_path": mh_res["file_path"],
                        "local_path": mh_res.get("local_path"),
                        "duration_seconds": mh_res.get("duration_seconds", 10),
                        "mime_type": "video/mp4",
                        "provider": "magic-hour-video"
                    }
        except Exception as mh_err:
            logger.debug(f"[PipelineGemini] Magic Hour fallback bypassed: {mh_err}")

        # 3. Clean Multi-Scene Branded Commercial Video Engine (Identical to Gemini AI Creative Studio)
        from app.services.video_generation_service import VideoGenerationService
        logger.info("[PipelineGemini] Rendering high-definition commercial video reel via Gemini Creative Studio engine...")
        # Collect all candidate imagery for multi-scene weave
        all_scene_images = []
        if image_url:
            all_scene_images.append(image_url)
        if image_urls:
            for u in image_urls:
                if u and u not in all_scene_images:
                    all_scene_images.append(u)

        if not all_scene_images:
            try:
                logger.info("[PipelineGemini] No visual asset supplied for video; generating brand hero asset first...")
                hero_img = await cls.generate_branded_image(
                    prompt=prompt,
                    aspect_ratio="1:1",
                    brand_profile=brand_profile
                )
                if hero_img and hero_img.get("local_path"):
                    all_scene_images.append(hero_img["local_path"])
                elif hero_img and hero_img.get("file_path"):
                    all_scene_images.append(hero_img["file_path"])
            except Exception as hero_err:
                logger.warning(f"[PipelineGemini] Hero image pre-generation notice: {hero_err}")

        rendered = await asyncio.to_thread(
            VideoGenerationService.render_branded_promotional_video,
            prompt=prompt,
            brand=brand_profile,
            aspect_ratio=valid_ratio,
            duration_seconds=30,
            fps=24,
            resolution="1080p",
            voice=getattr(brand_profile, "voice", None),
            image_url=image_url or (all_scene_images[0] if all_scene_images else None),
            image_urls=all_scene_images if all_scene_images else None,
            description=prompt
        )
        return {
            "operation_id": f"video_{uuid.uuid4().hex[:16]}",
            "status": "done",
            "prompt": prompt,
            "aspect_ratio": valid_ratio,
            "file_path": rendered["video_url"],
            "local_path": rendered.get("local_path"),
            "duration_seconds": rendered.get("duration_seconds", 25),
            "mime_type": "video/mp4",
            "provider": "gemini-studio-video-engine",
            "voiceover_script": rendered.get("voiceover_script"),
            "has_voiceover": rendered.get("has_voiceover", True)
        }
