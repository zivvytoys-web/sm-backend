from typing import Dict, Any, List, Optional
from datetime import datetime
import random

from app.models.brand_profile import BrandProfile


class PromptService:
    """
    Centralized, dynamic prompt assembly engine for Google Gemini.
    Synthesizes brand profiles, campaign objectives, platform-specific guidelines,
    and history avoidance constraints into rich, non-repetitive prompt blueprints.
    """

    PLATFORM_GUIDELINES = {
        "instagram": {
            "name": "Instagram",
            "style": "Visual, aspirational, scroll-stopping hook within first 125 chars. Use paragraph breaks and line formatting.",
            "hashtags": "Include 8-15 high-performing, targeted and community hashtags at bottom.",
            "cta_style": "Engaging direct call to action (e.g. 'Drop a ❤️ if you agree', 'Tap link in bio to shop').",
            "optimal_length": "150-250 words"
        },
        "facebook": {
            "name": "Facebook",
            "style": "Conversational, community-oriented, relationship-building, inviting questions.",
            "hashtags": "3-5 relevant hashtags.",
            "cta_style": "Direct clickable links and comment prompts.",
            "optimal_length": "100-200 words"
        },
        "linkedin": {
            "name": "LinkedIn",
            "style": "Thought leadership, professional storytelling, industry insights, takeaways.",
            "hashtags": "3-5 industry and domain hashtags.",
            "cta_style": "Open-ended professional discussion prompt.",
            "optimal_length": "150-300 words"
        },
        "youtube": {
            "name": "YouTube",
            "style": "High-retention video title, searchable description with timestamps, keywords.",
            "hashtags": "3-5 video tags.",
            "cta_style": "Subscribe and check description links.",
            "optimal_length": "100-200 words"
        },
        "twitter": {
            "name": "Twitter / X",
            "style": "Punchy, concise, high-impact hook, witty or authoritative.",
            "hashtags": "1-3 focused hashtags.",
            "cta_style": "Quote tweet or follow prompt.",
            "optimal_length": "under 280 characters"
        },
        "tiktok": {
            "name": "TikTok",
            "style": "Ultra-fast hook in first 3 seconds, relatable, high energy, trend-aware.",
            "hashtags": "4-6 trending hashtags.",
            "cta_style": "Follow for part 2 or comment opinions.",
            "optimal_length": "Short caption under 150 characters"
        }
    }

    CREATIVE_ANGLES = [
        "Desire & Aspiration: Focus on how the customer feels and transforms after choosing the brand.",
        "Problem-Solver: Directly address a common daily frustration and show the elegant solution.",
        "Behind-the-Scenes Craft: Spotlight the dedication, materials, and passion behind what makes it special.",
        "Social Proof & Community: Celebrate customer delight and shared values.",
        "Urgency & Exclusivity: Highlight limited seasonal availability or timely excitement.",
        "Educational & Value-Add: Share tips, insider secrets, and useful knowledge related to the industry."
    ]

    @classmethod
    def assemble_text_prompt(
        cls,
        brand: Optional[BrandProfile] = None,
        user_instructions: Optional[str] = None,
        campaign_goal: str = "product_launch",
        target_platforms: Optional[List[str]] = None,
        content_type: str = "caption",
        tone: Optional[str] = None,
        language: str = "English",
        recent_history: Optional[List[str]] = None,
        regeneration_instruction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Dynamically builds a customized prompt for Google Gemini text generation.
        """
        brand_name = brand.name if brand and brand.name else "Our Brand"
        industry = brand.industry if brand and brand.industry else "Retail & Lifestyle"
        tagline = brand.tagline if brand and brand.tagline else ""
        meta = brand.meta_info if (brand and hasattr(brand, "meta_info") and isinstance(brand.meta_info, dict)) else {}
        
        category = meta.get("category") or industry
        brand_desc = meta.get("description") or brand.brand_guidelines or ""
        target_audience = brand.target_audience if brand and brand.target_audience else "Quality-conscious modern consumers"
        demographics = meta.get("target_demographics", "")
        interests = meta.get("audience_interests", "")
        brand_voice = tone or (brand.tone_of_voice if brand and brand.tone_of_voice else "Engaging, authentic, and memorable")
        messaging_style = meta.get("messaging_style", "")
        brand_cta = (meta.get("cta") if isinstance(meta, dict) and meta.get("cta") else None) or (brand.cta if brand and brand.cta else "Learn more at our link in bio!")
        raw_avoid = (meta.get("avoid") if isinstance(meta, dict) and meta.get("avoid") else None) or (brand.avoid if brand and brand.avoid else "clichéd marketing buzzwords, false claims")
        brand_avoid = ", ".join(raw_avoid) if isinstance(raw_avoid, list) else str(raw_avoid)
        usp = meta.get("usp", "")
        positioning = meta.get("brand_positioning", "")
        marketing_objectives = meta.get("marketing_objectives", "")
        website = meta.get("website", "")
        phone = meta.get("phone", "")
        email = meta.get("email", "")
        location = brand.location or ""
        timings = brand.timings or ""

        # Products & Services Catalog
        products = meta.get("products", [])
        products_lines = []
        if products and isinstance(products, list):
            for p in products[:6]:
                if isinstance(p, dict) and p.get("name"):
                    price_str = f" (${p.get('price')})" if p.get("price") else ""
                    desc_str = f": {p.get('description')}" if p.get("description") else ""
                    products_lines.append(f"  - {p['name']}{price_str}{desc_str}")
        products_section = ("\n• Featured Products / Services:\n" + "\n".join(products_lines)) if products_lines else ""

        # Platform details
        plat_key = (target_platforms[0].lower() if target_platforms else "instagram")
        plat_info = cls.PLATFORM_GUIDELINES.get(plat_key, cls.PLATFORM_GUIDELINES["instagram"])
        platforms_str = ", ".join([cls.PLATFORM_GUIDELINES.get(p.lower(), {}).get("name", p) for p in (target_platforms or ["instagram"])])

        # Pick dynamic creative angle to prevent repetition
        chosen_angle = random.choice(cls.CREATIVE_ANGLES)

        # Recent history avoidance
        history_avoidance = ""
        if recent_history:
            clean_snippets = [f"'{h[:60]}...'" for h in recent_history[:4] if h]
            if clean_snippets:
                history_avoidance = f"IMPORTANT: Avoid repeating or reusing these recently generated themes: {', '.join(clean_snippets)}. Ensure a fresh, novel angle."

        regen_mod = ""
        if regeneration_instruction:
            regen_mod = f"USER REGENERATION DIRECTIVE: {regeneration_instruction}. Adjust copy accordingly."

        system_instruction = (
            f"You are the senior brand strategist and creative copywriter for '{brand_name}'.\n"
            f"Brand Industry & Category: {industry} ({category}).\n"
            f"Brand Voice & Messaging: {brand_voice}. {messaging_style}\n"
            f"Target Audience: {target_audience}.\n"
            f"Language: {language}.\n"
            f"Target Platforms: {platforms_str}.\n"
            f"Platform Specifications: {plat_info['style']} Length: {plat_info['optimal_length']}.\n"
            f"Strict Guideline: Always output valid, parseable JSON matching the requested schema. Never output markdown fences or commentary outside the JSON."
        )

        user_prompt = (
            f"Create an original, high-performing {content_type.upper()} social media campaign asset for {brand_name}.\n\n"
            f"--- VERIFIED BRAND PROFILE ---\n"
            f"• Name: {brand_name}\n"
            f"• Industry & Category: {industry} ({category})\n"
            f"• Tagline / Mission: {tagline or 'Excellence in service and design'}\n"
            f"• Description: {brand_desc or 'Dedicated to delivering exceptional quality'}\n"
            f"• Audience Demographics & Interests: {target_audience}. {demographics} {interests}\n"
            f"• Preferred Tone: {brand_voice}\n"
            f"• Unique Selling Proposition (USP): {usp or 'Distinctive quality and attentive customer experience'}\n"
            f"• Brand Positioning: {positioning or 'Market leader committed to authentic excellence'}\n"
            f"• Marketing Objectives: {marketing_objectives or 'Drive brand awareness, engagement, and customer loyalty'}\n"
            f"• Standard CTA: {brand_cta}\n"
            f"• Strict Content Restrictions (Avoid): {brand_avoid}\n"
            f"• Location / Hours: {location} {timings}\n"
            f"• Official Channels: {website} | {phone} | {email}"
            f"{products_section}\n\n"
            f"--- CAMPAIGN CONTEXT ---\n"
            f"• Campaign Objective: {campaign_goal}\n"
            f"• Creative Direction Angle: {chosen_angle}\n"
            f"• Specific User Guidance: {user_instructions or 'Showcase the latest standout offerings and connect with our audience.'}\n"
            f"{history_avoidance}\n"
            f"{regen_mod}\n\n"
            f"Output a valid JSON object strictly matching these keys:\n"
            f"{{\n"
            f'  "headline": "Short compelling hook or punchy title",\n'
            f'  "caption": "Full formatted caption with line breaks, emojis, narrative, and CTA",\n'
            f'  "hashtags": ["list", "of", "relevant", "hashtags"],\n'
            f'  "call_to_action": "Clear direct action prompt",\n'
            f'  "variations": [\n'
            f'    {{"title": "Angle 1", "hook": "First line hook", "body": "Short alternative caption"}},\n'
            f'    {{"title": "Angle 2", "hook": "First line hook", "body": "Short alternative caption"}}\n'
            f'  ],\n'
            f'  "marketing_takeaways": ["Takeaway 1", "Takeaway 2", "Takeaway 3"],\n'
            f'  "script_scenes": [\n'
            f'    {{"time": "0-3s", "visual": "Scene description", "audio": "Voiceover/Audio", "overlay_text": "Text on screen"}},\n'
            f'    {{"time": "3-8s", "visual": "Scene description", "audio": "Voiceover/Audio", "overlay_text": "Text on screen"}},\n'
            f'    {{"time": "8-15s", "visual": "Scene description", "audio": "Voiceover/Audio", "overlay_text": "Text on screen"}}\n'
            f'  ]\n'
            f"}}"
        )

        return {
            "system_instruction": system_instruction,
            "user_prompt": user_prompt,
            "metadata": {
                "brand_name": brand_name,
                "campaign_goal": campaign_goal,
                "creative_angle": chosen_angle,
                "platform": plat_key,
                "language": language,
                "assembled_at": datetime.utcnow().isoformat()
            }
        }

    @classmethod
    def assemble_image_prompt(
        cls,
        brand: Optional[BrandProfile] = None,
        user_instructions: Optional[str] = None,
        style_preset: str = "modern_product",
        aspect_ratio: str = "1:1"
    ) -> Dict[str, Any]:
        """
        Dynamically builds visual image prompts incorporating the brand's palette,
        industry aesthetic, and product context.
        """
        brand_name = brand.name if brand and brand.name else "Our Brand"
        industry = brand.industry if brand and brand.industry else "Retail & Lifestyle"
        meta = brand.meta_info if (brand and hasattr(brand, "meta_info") and isinstance(brand.meta_info, dict)) else {}
        visual_id = meta.get("visual_identity", "")
        products = meta.get("products", [])
        product_context = ""
        if products and isinstance(products, list) and len(products) > 0:
            first_p = products[0]
            if isinstance(first_p, dict) and first_p.get("name"):
                product_context = f"highlighting {first_p['name']} ({first_p.get('description', '')})"

        palette = brand.color_palette if brand and brand.color_palette else ["#4f46e5", "#06b6d4"]
        palette_desc = f"Harmonious brand palette accents ({', '.join(palette[:3])})" if palette else ""

        style_modifiers = {
            "modern_product": f"commercial product photography for {brand_name}, clean studio lighting, crisp 8k resolution, elegant depth of field, {palette_desc}",
            "photo": f"lifestyle editorial photography of {brand_name} products in authentic use, natural sunlight, 35mm lens, high dynamic range",
            "minimalist": f"contemporary minimalist composition for {brand_name}, ample negative space, subtle textures, clean pastel background, {palette_desc}",
            "luxury": f"ultra-luxury high-fashion editorial showcase for {brand_name}, dramatic chiaroscuro lighting, sleek marble and metal accents, 8k commercial quality",
            "cinematic": f"cinematic movie still featuring {brand_name}, anamorphic lens flare, rich cinematic color grading, dramatic atmospheric lighting",
            "3d_render": f"octane 3D render of {brand_name} collection, raytraced reflections, vibrant geometry, glossy studio materials, 8k resolution"
        }

        modifier = style_modifiers.get(style_preset, style_modifiers["modern_product"])
        if visual_id:
            modifier += f", visual aesthetic: {visual_id}"

        base_subject = user_instructions.strip() if user_instructions else f"Signature premium {industry} showcase for {brand_name} {product_context}".strip()
        full_image_prompt = f"{base_subject}. {modifier}."

        return {
            "full_prompt": full_image_prompt,
            "base_subject": base_subject,
            "style_preset": style_preset,
            "aspect_ratio": aspect_ratio,
            "brand_name": brand_name,
            "palette": palette
        }

