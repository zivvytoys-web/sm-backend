import os
import json
import random
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.logging import logger
from app.core.gemini_client import get_gemini_client
from app.models.brand_profile import BrandProfile
from app.models.campaign import Campaign
from app.models.content import Content
from app.models.user import User
from app.utils.enums import ContentStatus, PlatformType
from app.services.gemini_service import GeminiService


class ContentPlannerService:
    """Enterprise AI Content Planner service synthesizing multi-day structured campaign calendars."""

    OPTIMAL_HOURS = ["09:30", "11:00", "13:30", "17:00", "19:30"]

    PILLARS = [
        "Product Spotlight & Craftsmanship",
        "Problem Solver & Educational Tip",
        "Customer Proof & Community Love",
        "Behind-the-Scenes & Brand Heritage",
        "Engaging Poll & Audience Discussion",
        "Limited-Time Value & Irresistible CTA",
        "Inspirational Lifestyle Upgrade"
    ]

    FORMATS = ["image", "video", "carousel", "text"]

    @classmethod
    def _calculate_schedule_slots(
        cls,
        start_date: datetime,
        duration_days: int,
        frequency: str
    ) -> List[Dict[str, Any]]:
        """Compute chronological publishing dates and optimal time slots."""
        slots = []
        base_date = start_date or (datetime.utcnow() + timedelta(days=1))
        # Zero out hours/minutes for clean day calculations
        current_day = datetime(base_date.year, base_date.month, base_date.day)

        if frequency == "2_per_day":
            for d in range(duration_days):
                day_date = current_day + timedelta(days=d)
                slots.append({"day": d + 1, "date": day_date.strftime("%Y-%m-%d"), "time": "10:00"})
                slots.append({"day": d + 1, "date": day_date.strftime("%Y-%m-%d"), "time": "18:30"})
        elif frequency == "3_per_week":
            # Monday, Wednesday, Friday pattern
            count = 3 if duration_days <= 7 else int((duration_days / 7) * 3)
            step_days = max(1, duration_days // max(1, count))
            for i in range(count):
                day_offset = min(duration_days - 1, i * step_days)
                day_date = current_day + timedelta(days=day_offset)
                slots.append({
                    "day": day_offset + 1,
                    "date": day_date.strftime("%Y-%m-%d"),
                    "time": cls.OPTIMAL_HOURS[i % len(cls.OPTIMAL_HOURS)]
                })
        elif frequency == "5_per_week":
            # Weekdays pattern
            count = 5 if duration_days <= 7 else int((duration_days / 7) * 5)
            step_days = max(1, duration_days // max(1, count))
            for i in range(count):
                day_offset = min(duration_days - 1, i * step_days)
                day_date = current_day + timedelta(days=day_offset)
                slots.append({
                    "day": day_offset + 1,
                    "date": day_date.strftime("%Y-%m-%d"),
                    "time": cls.OPTIMAL_HOURS[i % len(cls.OPTIMAL_HOURS)]
                })
        elif frequency == "alternate_days":
            count = (duration_days + 1) // 2
            for i in range(count):
                day_offset = i * 2
                day_date = current_day + timedelta(days=day_offset)
                slots.append({
                    "day": day_offset + 1,
                    "date": day_date.strftime("%Y-%m-%d"),
                    "time": cls.OPTIMAL_HOURS[i % len(cls.OPTIMAL_HOURS)]
                })
        else:
            # Default: 1 post per day
            for d in range(duration_days):
                day_date = current_day + timedelta(days=d)
                slots.append({
                    "day": d + 1,
                    "date": day_date.strftime("%Y-%m-%d"),
                    "time": cls.OPTIMAL_HOURS[d % len(cls.OPTIMAL_HOURS)]
                })

        return slots

    @classmethod
    async def generate_plan(
        cls,
        db: Session,
        organization_id: int,
        brand_profile_id: Optional[int] = None,
        duration_days: int = 7,
        platforms: Optional[List[str]] = None,
        posting_frequency: str = "1_per_day",
        campaign_goal: str = "product_launch",
        custom_focus: Optional[str] = None,
        start_date: Optional[datetime] = None,
        media_format_preference: str = "both"
    ) -> Dict[str, Any]:
        """Generate a complete structured content plan using Brand Profile and AI."""
        # 1. Strict Multi-Tenant Brand Resolution & Validation
        brand = None
        if brand_profile_id:
            brand = db.query(BrandProfile).filter(
                BrandProfile.id == brand_profile_id,
                BrandProfile.organization_id == organization_id
            ).first()
            if not brand:
                foreign = db.query(BrandProfile).filter(BrandProfile.id == brand_profile_id).first()
                if foreign:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Access denied: Cross-tenant brand profile access is strictly forbidden."
                    )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Brand profile #{brand_profile_id} was not found in your organization."
                )
        else:
            brand = db.query(BrandProfile).filter(
                BrandProfile.organization_id == organization_id
            ).order_by(BrandProfile.updated_at.desc(), BrandProfile.id.desc()).first()

        if not brand:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A valid Brand Profile is required before generating a campaign plan. Please configure your Brand Profile first."
            )

        brand_name = brand.name
        industry = brand.industry if brand.industry else "Commercial"
        tone = brand.tone_of_voice if brand and brand.tone_of_voice else "Engaging, Authentic, Inspiring"
        target_aud = brand.target_audience if brand and brand.target_audience else "Discerning modern audience"
        tagline = brand.tagline if brand and brand.tagline else ""
        themes = ", ".join(brand.key_themes) if brand and brand.key_themes else "Innovation, Quality, Experience"
        brand_cta = brand.cta if brand and brand.cta else "Tap the link in our bio to explore more"
        brand_avoid = brand.avoid if brand and brand.avoid else "clichéd claims, off-brand slang, false promises"

        meta = brand.meta_info if (brand and isinstance(brand.meta_info, dict)) else {}
        usp = meta.get("usp", "Exceptional quality and genuine craftsmanship")
        positioning = meta.get("brand_positioning", "Modern industry frontrunner")
        marketing_objectives = meta.get("marketing_objectives", "")
        buyer_personas = meta.get("buyer_personas", [])
        content_format_ratios = meta.get("content_format_ratios", {})
        products = meta.get("products", [])

        products_lines = []
        if products and isinstance(products, list):
            for p in products[:8]:
                if isinstance(p, dict) and p.get("name"):
                    price_info = f" (${p.get('price')})" if p.get("price") else ""
                    products_lines.append(f"  • {p['name']}{price_info}: {p.get('description', '')}")
        products_str = ("\nVerified Brand Products:\n" + "\n".join(products_lines)) if products_lines else ""

        # Format active buyer personas
        active_personas = [p for p in buyer_personas if isinstance(p, dict) and p.get("active", True)] if isinstance(buyer_personas, list) else []
        personas_str = ""
        if active_personas:
            p_lines = [f"  • {p.get('title', 'Target Persona')}: {p.get('desc', '')}" for p in active_personas[:4]]
            personas_str = "\nTarget Buyer Personas (Tailor hook angles, tone, and audience resonance to these personas):\n" + "\n".join(p_lines) + "\n"

        # 2. Determine target platforms
        valid_platforms = [p.lower() for p in (platforms or ["instagram", "facebook", "linkedin"])]
        if not valid_platforms:
            valid_platforms = ["instagram", "facebook", "linkedin"]

        duration = 30 if duration_days == 30 else 7
        slots = cls._calculate_schedule_slots(start_date, duration, posting_frequency)
        slot_count = len(slots)

        # 3. Campaign Goal descriptor - supports user's manual custom entry or presets
        goal_labels = {
            "product_launch": "Launch flagship product offerings with desire, curiosity and social excitement",
            "brand_awareness": "Build brand recognition, distinct visual presence and memorable storytelling",
            "community_engagement": "Drive comments, conversations, shares, and authentic audience interaction",
            "promotional_deals": "Convert warm followers with compelling offers, bundles, and value propositions",
            "educational_tips": "Position the brand as a helpful expert with tactical styling, how-tos and advice",
            "behind_the_scenes": "Humanize the company with atelier craft, founder notes, and authentic culture",
            "custom": custom_focus or "High-engagement social campaign"
        }
        clean_goal = str(campaign_goal or "").strip()
        if clean_goal in goal_labels:
            goal_desc = goal_labels[clean_goal]
        elif clean_goal.lower() in goal_labels:
            goal_desc = goal_labels[clean_goal.lower()]
        elif clean_goal and clean_goal not in ["product_launch", "default"]:
            # User manually entered the primary campaign goal: use it directly
            goal_desc = clean_goal
        elif marketing_objectives:
            # Fallback to the real-time configured marketing objective from Content Goals & Audience Targeting
            goal_desc = marketing_objectives
        else:
            goal_desc = "High-engagement brand and social growth campaign"

        if custom_focus:
            goal_desc += f" (Specific Focus: {custom_focus})"

        # Media format preference directive and format mix ratio
        media_pref = (media_format_preference or "both").lower()
        if media_pref in ["images", "image"]:
            format_requirement = "Every single idea MUST be an IMAGE-based format ('image' or 'carousel'). Do NOT output video formats. Tailor visuals, hooks and concepts for still photography, graphics and multi-slide carousels."
            format_schema = "one of ['image', 'carousel']"
        elif media_pref in ["videos", "video"]:
            format_requirement = "Every single idea MUST be a VIDEO-based format ('video'). Tailor hooks, pacing, audio concepts and briefs strictly for short-form video reels, shorts and clips. Do NOT output image formats."
            format_schema = "'video'"
        else:
            if isinstance(content_format_ratios, dict) and content_format_ratios:
                r_reels = content_format_ratios.get('reels', 40)
                r_imgs = content_format_ratios.get('images', 30)
                r_cars = content_format_ratios.get('carousels', 20)
                r_txt = content_format_ratios.get('text', 10)
                format_requirement = f"Distribute content formats according to the user's strategic distribution mix: ~{r_reels}% short-form video reels ('video'), ~{r_imgs}% high-res photography ('image'), ~{r_cars}% carousel guides ('carousel'), ~{r_txt}% community updates."
            else:
                format_requirement = "Balance content formats evenly across static images ('image', 'carousel') and short-form video reels ('video') for a dynamic multi-channel mix."
            format_schema = "one of ['image', 'video', 'carousel']"

        # 4. Construct AI Prompt
        system_instruction = (
            f"You are the Chief Social Media Strategist and Creative Director for '{brand_name}'.\n"
            f"Brand Tone: {tone}. Industry: {industry}. Target Audience: {target_aud}.\n"
            f"Your job is to generate a comprehensive, cohesive {duration}-day Content Plan with exactly {slot_count} distinct post ideas.\n"
            f"CRITICAL REQUIREMENTS:\n"
            f"1. Every single idea must have a unique angle and creative focus. Do NOT repeat topics or hooks.\n"
            f"2. Media Format Constraint: {format_requirement}\n"
            f"3. Strictly respect brand restrictions: Never mention '{brand_avoid}'.\n"
            f"4. Seamlessly incorporate authentic products and brand USP.\n"
            f"5. Return valid JSON strictly matching the requested array schema."
        )

        user_prompt = (
            f"Brand Directives:\n"
            f"- Brand Name: {brand_name}\n"
            f"- Industry: {industry}\n"
            f"- Tagline: {tagline}\n"
            f"- Tone of Voice: {tone}\n"
            f"- Target Audience: {target_aud}\n"
            f"- USP / Value Proposition: {usp}\n"
            f"- Positioning: {positioning}\n"
            f"- Core Themes: {themes}\n"
            f"- Standard CTA: {brand_cta}\n"
            f"- Prohibited Words & Themes (Avoid): {brand_avoid}\n"
            f"- Campaign Objective: {goal_desc}\n"
            f"- Media Format Requirement: {media_pref.upper()} - {format_requirement}\n"
            f"- Plan Duration: {duration} days ({slot_count} total post slots)\n"
            f"- Target Platforms: {', '.join(valid_platforms)}\n"
            f"{products_str}"
            f"{personas_str}\n\n"
            f"Generate a strategic social media content calendar with exactly {slot_count} post ideas.\n"
            f"Return a JSON object with:\n"
            f"- campaign_title: A high-impact title for this {duration}-day plan (string)\n"
            f"- campaign_summary: 2-sentence executive summary of the strategy (string)\n"
            f"- ideas: An array of exactly {slot_count} objects, each containing:\n"
            f"    - title: Punchy topic name (string, 4-8 words)\n"
            f"    - hook: Scroll-stopping opening line (string)\n"
            f"    - caption_brief: Concise creative brief and key talking points (string, 2-3 sentences)\n"
            f"    - content_format: {format_schema}\n"
            f"    - pillar: strategic pillar name (string, e.g., 'Product Spotlight', 'Educational Tip', 'Behind-the-Scenes')\n"
            f"    - suggested_hashtags: array of 4-6 curated relevant hashtags (list of strings)\n"
            f"    - cta: Contextual call to action for this specific post (string)\n"
        )

        # 5. Execute AI Generation (OpenAI / Gemini)
        generated_data = None
        try:
            generated_data = await GeminiService._openai_json(system_instruction, user_prompt)
        except Exception as e:
            logger.warning(f"OpenAI plan generation attempt error: {e}")

        if not generated_data or not isinstance(generated_data.get("ideas"), list):
            client = GeminiService.get_client()
            if client:
                for model_candidate in GeminiService.TEXT_MODELS[:1]:
                    try:
                        from google.genai import types
                        resp = await asyncio.wait_for(
                            asyncio.to_thread(
                                client.models.generate_content,
                                model=model_candidate,
                                contents=user_prompt,
                                config=types.GenerateContentConfig(
                                    system_instruction=system_instruction,
                                    temperature=0.75,
                                    response_mime_type="application/json"
                                )
                            ),
                            timeout=3.5
                        )
                        if resp.text:
                            parsed = json.loads(resp.text)
                            if isinstance(parsed, dict) and isinstance(parsed.get("ideas"), list):
                                generated_data = parsed
                                break
                    except Exception as exc:
                        logger.warning(f"Gemini candidate {model_candidate} plan generation failed: {exc}")
                        break

        # 6. Fallback Generator if AI services are unavailable
        if not generated_data or not isinstance(generated_data.get("ideas"), list):
            logger.info("Using robust deterministic brand-aligned fallback plan generator")
            generated_data = cls._generate_fallback_plan(
                brand_name=brand_name,
                industry=industry,
                goal_desc=goal_desc,
                products=products,
                duration=duration,
                slot_count=slot_count,
                brand_cta=brand_cta,
                media_format_preference=media_pref
            )

        # 7. Merge generated ideas with schedule slots and platform assignments
        raw_ideas = generated_data.get("ideas", [])
        structured_ideas = []
        seen_titles = set()

        for idx, slot in enumerate(slots):
            raw = raw_ideas[idx] if idx < len(raw_ideas) else {}
            title = raw.get("title") or f"{brand_name} Spotlight #{idx + 1}"
            
            # Deduplication
            if title.lower() in seen_titles:
                title = f"{title} (Part {idx + 1})"
            seen_titles.add(title.lower())

            raw_fmt = str(raw.get("content_format", "")).lower()
            if media_pref in ["images", "image"]:
                fmt = "carousel" if "carousel" in raw_fmt else "image"
            elif media_pref in ["videos", "video"]:
                fmt = "video"
            else:
                if "video" in raw_fmt or "reel" in raw_fmt:
                    fmt = "video"
                elif "carousel" in raw_fmt:
                    fmt = "carousel"
                else:
                    fmt = "image" if idx % 3 != 1 else "video"

            pillar = raw.get("pillar") or cls.PILLARS[idx % len(cls.PILLARS)]
            hook = raw.get("hook") or f"Here is how {brand_name} is redefining {industry}."
            brief = raw.get("caption_brief") or f"Highlight key customer benefits and invite followers to take action."
            cta = raw.get("cta") or brand_cta
            hashtags = raw.get("suggested_hashtags") or [f"#{brand_name.replace(' ', '')}", f"#{industry.replace(' ', '')}", "#Growth", "#Community"]

            suggested_datetime = f"{slot['date']}T{slot['time']}:00"

            idea_id = f"idea-d{slot['day']}-s{idx + 1}"
            structured_ideas.append({
                "id": idea_id,
                "day_number": slot["day"],
                "slot_index": idx,
                "title": title,
                "topic": title,
                "hook": hook,
                "caption_brief": brief,
                "content_format": fmt,
                "platform": valid_platforms[idx % len(valid_platforms)],
                "platforms": valid_platforms,
                "suggested_date": slot["date"],
                "suggested_time": slot["time"],
                "suggested_datetime": suggested_datetime,
                "pillar": pillar,
                "content_pillar": pillar,
                "hashtags": hashtags,
                "cta": cta,
                "call_to_action": cta,
                "plan_status": "planned"  # 'planned', 'in_progress', 'draft_created', 'completed'
            })

        plan_title = generated_data.get("campaign_title") or f"{brand_name} {duration}-Day {campaign_goal.replace('_', ' ').title()} Plan"
        plan_summary = generated_data.get("campaign_summary") or f"A structured {duration}-day editorial roadmap optimized for {', '.join(valid_platforms)}."

        return {
            "title": plan_title,
            "campaign_name": plan_title,
            "summary": plan_summary,
            "campaign_goal": campaign_goal,
            "duration_days": duration,
            "posting_frequency": posting_frequency,
            "media_format_preference": media_pref,
            "platforms": valid_platforms,
            "start_date": slots[0]["date"] if slots else datetime.utcnow().strftime("%Y-%m-%d"),
            "end_date": slots[-1]["date"] if slots else datetime.utcnow().strftime("%Y-%m-%d"),
            "total_ideas": len(structured_ideas),
            "ideas_count": len(structured_ideas),
            "ideas": structured_ideas
        }

    @classmethod
    def _generate_fallback_plan(
        cls,
        brand_name: str,
        industry: str,
        goal_desc: str,
        products: List[Dict[str, Any]],
        duration: int,
        slot_count: int,
        brand_cta: str,
        media_format_preference: str = "both"
    ) -> Dict[str, Any]:
        """Produce high-quality creative ideas without external AI dependencies."""
        themes_cycle = [
            ("The Story Behind Our Craft", "Every detail we build has a deliberate purpose.", "Share the origin inspiration and uncompromising material standards."),
            ("Why We Refuse to Settle", "Good enough was never an option for our team.", "Demonstrate the testing and refinement that goes into our process."),
            ("Customer Transformation Spotlight", "What our community is saying this week.", "Feature authentic feedback and real-world results from people using our creations."),
            ("3 Secrets to Elevate Your Daily Routine", "Simple adjustments that make a tangible difference.", "Deliver actionable, easy-to-implement advice that solves a real audience challenge."),
            ("Behind Closed Doors at Our Studio", "A quick look at what we're working on right now.", "Take followers behind the curtain for an unfiltered, relatable workshop moment."),
            ("The Question We Get Asked Most", "Let's set the record straight once and for all.", "Address a common misconception in our industry and educate our audience."),
            ("Weekend Community VIP Privilege", "Because our community always deserves first access.", "Reward our active followers with an exclusive invite or limited perk.")
        ]

        pref = (media_format_preference or "both").lower()
        if pref in ["images", "image"]:
            available_fmts = ["image", "carousel"]
        elif pref in ["videos", "video"]:
            available_fmts = ["video"]
        else:
            available_fmts = ["image", "video", "carousel"]

        ideas = []
        for i in range(slot_count):
            t_idx = i % len(themes_cycle)
            topic, hook, brief = themes_cycle[t_idx]
            
            p_name = None
            if products and len(products) > 0:
                p_item = products[i % len(products)]
                if isinstance(p_item, dict) and p_item.get("name"):
                    p_name = p_item["name"]

            if p_name and i % 2 == 0:
                topic = f"Spotlight: {p_name}"
                hook = f"Discover why {p_name} is becoming our most talked-about creation."
                brief = f"Highlight the key features and everyday delight of {p_name}."

            fmt = available_fmts[i % len(available_fmts)]
            pillar = cls.PILLARS[i % len(cls.PILLARS)]

            ideas.append({
                "title": topic,
                "hook": hook,
                "caption_brief": brief,
                "content_format": fmt,
                "pillar": pillar,
                "suggested_hashtags": [f"#{brand_name.replace(' ', '')}", f"#{industry.replace(' ', '')}", "#Quality", "#Growth"],
                "cta": brand_cta
            })

        return {
            "campaign_title": f"{brand_name} {duration}-Day Strategic Campaign Blueprint",
            "campaign_summary": f"Structured editorial calendar driving {goal_desc.lower()} with balanced creative formats.",
            "ideas": ideas
        }

    @classmethod
    def save_plan_to_database(
        cls,
        db: Session,
        organization_id: int,
        current_user: User,
        plan_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Save a generated plan as a Campaign and its ideas as Content items in the database."""
        # 1. Resolve Brand Profile ID
        brand_id = plan_data.get("brand_profile_id")
        if not brand_id:
            brand = db.query(BrandProfile).filter(BrandProfile.organization_id == organization_id).first()
            if not brand:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No Brand Profile found for organization.")
            brand_id = brand.id

        # 2. Check for existing campaign or create new
        campaign_id = plan_data.get("campaign_id")
        campaign = None
        if campaign_id:
            campaign = db.query(Campaign).filter(
                Campaign.id == campaign_id,
                Campaign.organization_id == organization_id
            ).first()

        start_dt = None
        end_dt = None
        if plan_data.get("start_date"):
            try:
                start_dt = datetime.strptime(plan_data["start_date"], "%Y-%m-%d")
            except Exception:
                start_dt = datetime.utcnow()
        if plan_data.get("end_date"):
            try:
                end_dt = datetime.strptime(plan_data["end_date"], "%Y-%m-%d")
            except Exception:
                end_dt = (start_dt or datetime.utcnow()) + timedelta(days=plan_data.get("duration_days", 7))

        campaign_tags = {
            "duration_days": plan_data.get("duration_days", 7),
            "posting_frequency": plan_data.get("posting_frequency", "1_per_day"),
            "platforms": plan_data.get("platforms", ["instagram", "facebook", "linkedin"]),
            "campaign_goal": plan_data.get("campaign_goal", "product_launch"),
            "media_format_preference": plan_data.get("media_format_preference", "both"),
            "summary": plan_data.get("summary", ""),
            "total_ideas": len(plan_data.get("ideas", []))
        }

        if not campaign:
            campaign = Campaign(
                title=plan_data.get("title") or f"Campaign Plan ({datetime.utcnow().strftime('%b %d, %Y')})",
                objective=plan_data.get("summary") or plan_data.get("campaign_goal", "Social content strategy"),
                start_date=start_dt,
                end_date=end_dt,
                tags=campaign_tags,
                status="active",
                organization_id=organization_id,
                brand_profile_id=brand_id
            )
            db.add(campaign)
            db.commit()
            db.refresh(campaign)
        else:
            campaign.title = plan_data.get("title", campaign.title)
            campaign.objective = plan_data.get("summary", campaign.objective)
            campaign.start_date = start_dt
            campaign.end_date = end_dt
            campaign.tags = campaign_tags
            db.commit()
            db.refresh(campaign)

        # 3. Create or update Content ideas
        ideas = plan_data.get("ideas", [])
        saved_ideas = []

        for idea in ideas:
            idea_id = idea.get("id")
            content_record = None

            if idea_id:
                content_record = db.query(Content).filter(
                    Content.id == idea_id,
                    Content.organization_id == organization_id
                ).first()

            platforms_list = idea.get("platforms") or plan_data.get("platforms", ["instagram"])
            # Format normalized platforms for PlatformType enum
            normalized_platforms = []
            for p in platforms_list:
                try:
                    normalized_platforms.append(PlatformType(p.lower()))
                except Exception:
                    pass
            if not normalized_platforms:
                normalized_platforms = [PlatformType.INSTAGRAM]

            meta_info = {
                "is_planned_idea": True,
                "day_number": idea.get("day_number", 1),
                "slot_index": idea.get("slot_index", 0),
                "hook": idea.get("hook", ""),
                "caption_brief": idea.get("caption_brief", ""),
                "content_format": idea.get("content_format", "image"),
                "pillar": idea.get("pillar", "Strategic Focus"),
                "cta": idea.get("cta", ""),
                "suggested_date": idea.get("suggested_date", ""),
                "suggested_time": idea.get("suggested_time", "10:00"),
                "suggested_datetime": idea.get("suggested_datetime", ""),
                "plan_status": idea.get("plan_status", "planned")  # 'planned', 'in_progress', 'draft_created', 'completed'
            }

            caption_content = (
                f"{idea.get('hook', '')}\n\n"
                f"{idea.get('caption_brief', '')}\n\n"
                f"{idea.get('cta', '')}"
            ).strip()

            if not content_record:
                content_record = Content(
                    title=idea.get("title", "Untitled Planned Idea"),
                    caption=caption_content,
                    hashtags=idea.get("hashtags", []),
                    target_platforms=normalized_platforms,
                    status=ContentStatus.DRAFT,
                    meta_info=meta_info,
                    organization_id=organization_id,
                    brand_profile_id=brand_id,
                    campaign_id=campaign.id,
                    creator_id=current_user.id
                )
                db.add(content_record)
                db.commit()
                db.refresh(content_record)
            else:
                content_record.title = idea.get("title", content_record.title)
                content_record.caption = caption_content
                content_record.hashtags = idea.get("hashtags", content_record.hashtags)
                content_record.target_platforms = normalized_platforms
                # Merge existing meta
                existing_meta = dict(content_record.meta_info or {})
                existing_meta.update(meta_info)
                content_record.meta_info = existing_meta
                db.commit()
                db.refresh(content_record)

            primary_platform = [p.value if hasattr(p, "value") else str(p) for p in content_record.target_platforms][0] if content_record.target_platforms else "Instagram"
            saved_ideas.append({
                "id": content_record.id,
                "day_number": meta_info["day_number"],
                "title": content_record.title,
                "topic": content_record.title,
                "hook": meta_info["hook"],
                "caption_brief": meta_info["caption_brief"],
                "content_format": meta_info["content_format"],
                "platform": primary_platform,
                "platforms": [p.value if hasattr(p, "value") else str(p) for p in content_record.target_platforms],
                "suggested_date": meta_info["suggested_date"],
                "suggested_time": meta_info["suggested_time"],
                "suggested_datetime": meta_info["suggested_datetime"],
                "pillar": meta_info["pillar"],
                "content_pillar": meta_info["pillar"],
                "hashtags": content_record.hashtags or [],
                "cta": meta_info["cta"],
                "call_to_action": meta_info["cta"],
                "plan_status": meta_info["plan_status"]
            })

        return {
            "campaign_id": campaign.id,
            "campaign": {
                "id": campaign.id,
                "name": campaign.title,
                "title": campaign.title,
                "goal": campaign.objective,
                "tags": campaign_tags,
                "start_date": campaign.start_date.isoformat() if campaign.start_date else None,
                "end_date": campaign.end_date.isoformat() if campaign.end_date else None
            },
            "title": campaign.title,
            "summary": campaign.objective,
            "duration_days": campaign_tags.get("duration_days", 7),
            "posting_frequency": campaign_tags.get("posting_frequency", "1_per_day"),
            "campaign_goal": campaign_tags.get("campaign_goal", "product_launch"),
            "platforms": campaign_tags.get("platforms", []),
            "start_date": campaign.start_date.strftime("%Y-%m-%d") if campaign.start_date else None,
            "end_date": campaign.end_date.strftime("%Y-%m-%d") if campaign.end_date else None,
            "total_ideas": len(saved_ideas),
            "count": len(saved_ideas),
            "ideas": saved_ideas
        }

    @classmethod
    def get_campaign_ideas(cls, db: Session, organization_id: int, campaign_id: int) -> Dict[str, Any]:
        """Fetch saved campaign and its associated Content ideas."""
        campaign = db.query(Campaign).filter(
            Campaign.id == campaign_id,
            Campaign.organization_id == organization_id
        ).first()

        if not campaign:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign plan not found.")

        contents = db.query(Content).filter(
            Content.campaign_id == campaign_id,
            Content.organization_id == organization_id
        ).all()

        ideas = []
        for c in contents:
            meta = c.meta_info if isinstance(c.meta_info, dict) else {}
            c_plat = [p.value if hasattr(p, "value") else str(p) for p in c.target_platforms]
            primary_p = c_plat[0] if c_plat else "Instagram"
            ideas.append({
                "id": c.id,
                "day_number": meta.get("day_number", 1),
                "title": c.title or "Untitled Idea",
                "topic": c.title or "Untitled Idea",
                "hook": meta.get("hook", ""),
                "caption_brief": meta.get("caption_brief", c.caption or ""),
                "content_format": meta.get("content_format", "image"),
                "platform": primary_p,
                "platforms": c_plat,
                "suggested_date": meta.get("suggested_date", ""),
                "suggested_time": meta.get("suggested_time", "10:00"),
                "suggested_datetime": meta.get("suggested_datetime", ""),
                "pillar": meta.get("pillar", "Strategic Pillar"),
                "content_pillar": meta.get("pillar", "Strategic Pillar"),
                "hashtags": c.hashtags or [],
                "cta": meta.get("cta", ""),
                "call_to_action": meta.get("cta", ""),
                "plan_status": meta.get("plan_status", "planned")
            })

        # Sort by day_number or suggested_date
        ideas.sort(key=lambda x: (x.get("day_number", 0), x.get("suggested_date", "")))

        tags = campaign.tags if isinstance(campaign.tags, dict) else {}
        return {
            "campaign_id": campaign.id,
            "campaign": {
                "id": campaign.id,
                "name": campaign.title,
                "title": campaign.title,
                "goal": campaign.objective,
                "tags": tags,
                "start_date": campaign.start_date.isoformat() if campaign.start_date else None,
                "end_date": campaign.end_date.isoformat() if campaign.end_date else None
            },
            "title": campaign.title,
            "summary": campaign.objective,
            "duration_days": tags.get("duration_days", 7),
            "posting_frequency": tags.get("posting_frequency", "1_per_day"),
            "campaign_goal": tags.get("campaign_goal", "product_launch"),
            "platforms": tags.get("platforms", ["instagram", "facebook", "linkedin"]),
            "start_date": campaign.start_date.strftime("%Y-%m-%d") if campaign.start_date else None,
            "end_date": campaign.end_date.strftime("%Y-%m-%d") if campaign.end_date else None,
            "total_ideas": len(ideas),
            "count": len(ideas),
            "ideas": ideas
        }

    @classmethod
    def update_idea(
        cls,
        db: Session,
        organization_id: int,
        content_id: int,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update fields or status of a single planned idea."""
        content = db.query(Content).filter(
            Content.id == content_id,
            Content.organization_id == organization_id
        ).first()

        if not content:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planned idea not found.")

        if "title" in payload:
            content.title = payload["title"]
        elif "topic" in payload:
            content.title = payload["topic"]

        if "hashtags" in payload:
            content.hashtags = payload["hashtags"]

        if "platforms" in payload:
            norm = []
            for p in payload["platforms"]:
                try:
                    norm.append(PlatformType(p.lower()))
                except Exception:
                    pass
            if norm:
                content.target_platforms = norm
        elif "platform" in payload and payload["platform"]:
            try:
                content.target_platforms = [PlatformType(payload["platform"].lower())]
            except Exception:
                pass

        meta = dict(content.meta_info or {})
        for field in ["hook", "caption_brief", "content_format", "pillar", "content_pillar", "cta", "call_to_action", "suggested_date", "suggested_time", "plan_status"]:
            if field in payload:
                target_field = "pillar" if field == "content_pillar" else ("cta" if field == "call_to_action" else field)
                meta[target_field] = payload[field]

        if "suggested_date" in payload or "suggested_time" in payload:
            d = meta.get("suggested_date", "")
            t = meta.get("suggested_time", "10:00")
            if d:
                meta["suggested_datetime"] = f"{d}T{t}:00"

        content.meta_info = meta
        if "hook" in payload or "caption_brief" in payload or "cta" in payload or "call_to_action" in payload:
            content.caption = f"{meta.get('hook', '')}\n\n{meta.get('caption_brief', '')}\n\n{meta.get('cta', '')}".strip()

        db.commit()
        db.refresh(content)

        c_plat = [p.value if hasattr(p, "value") else str(p) for p in content.target_platforms]
        primary_p = c_plat[0] if c_plat else "Instagram"

        return {
            "id": content.id,
            "title": content.title,
            "topic": content.title,
            "hook": meta.get("hook", ""),
            "caption_brief": meta.get("caption_brief", ""),
            "content_format": meta.get("content_format", "image"),
            "platform": primary_p,
            "platforms": c_plat,
            "suggested_date": meta.get("suggested_date", ""),
            "suggested_time": meta.get("suggested_time", "10:00"),
            "suggested_datetime": meta.get("suggested_datetime", ""),
            "pillar": meta.get("pillar", ""),
            "content_pillar": meta.get("pillar", ""),
            "hashtags": content.hashtags or [],
            "cta": meta.get("cta", ""),
            "call_to_action": meta.get("cta", ""),
            "plan_status": meta.get("plan_status", "planned")
        }

    @classmethod
    def delete_idea(cls, db: Session, organization_id: int, content_id: int) -> bool:
        """Delete a planned idea from the plan."""
        content = db.query(Content).filter(
            Content.id == content_id,
            Content.organization_id == organization_id
        ).first()

        if not content:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planned idea not found.")

        db.delete(content)
        db.commit()
        return True

    @classmethod
    async def regenerate_single_idea(
        cls,
        db: Session,
        organization_id: int,
        content_id: int
    ) -> Dict[str, Any]:
        """Regenerate a single planned idea with fresh AI output while preserving schedule date/slot."""
        content = db.query(Content).filter(
            Content.id == content_id,
            Content.organization_id == organization_id
        ).first()

        if not content:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planned idea not found.")

        brand = db.query(BrandProfile).filter(BrandProfile.id == content.brand_profile_id).first()
        brand_name = brand.name if brand else "Brand Studio"
        industry = brand.industry if brand else "Retail"
        tone = brand.tone_of_voice if brand else "Engaging"
        meta = content.meta_info if isinstance(content.meta_info, dict) else {}

        # Fetch sibling ideas to ensure uniqueness
        sibling_ideas = db.query(Content).filter(
            Content.campaign_id == content.campaign_id,
            Content.id != content_id
        ).all()
        existing_titles = [c.title for c in sibling_ideas if c.title]

        system_instruction = (
            f"You are the social media copywriter for '{brand_name}'. Tone: {tone}. Industry: {industry}.\n"
            f"Generate an original, high-impact social media post idea. Avoid repeating: {', '.join(existing_titles[:8])}.\n"
            f"Return valid JSON strictly matching the requested schema."
        )

        user_prompt = (
            f"Generate a creative post idea for slot day {meta.get('day_number', 1)}.\n"
            f"Format needed: {meta.get('content_format', 'image')}.\n"
            f"Return JSON with: title (string), hook (string), caption_brief (string), pillar (string), suggested_hashtags (list of strings), cta (string)."
        )

        new_data = None
        try:
            new_data = await GeminiService._openai_json(system_instruction, user_prompt)
        except Exception:
            pass

        if not new_data or not new_data.get("title"):
            client = GeminiService.get_client()
            if client:
                try:
                    from google.genai import types
                    resp = await asyncio.wait_for(
                        asyncio.to_thread(
                            client.models.generate_content,
                            model="gemini-flash-lite-latest",
                            contents=user_prompt,
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction,
                                temperature=0.85,
                                response_mime_type="application/json"
                            )
                        ),
                        timeout=3.0
                    )
                    if resp.text:
                        new_data = json.loads(resp.text)
                except Exception:
                    pass

        if not new_data or not new_data.get("title"):
            new_data = {
                "title": f"Fresh Angle: {brand_name} Perspective",
                "hook": f"Here is something most people overlook about {industry}.",
                "caption_brief": f"Share a fresh insider insight that delivers immediate value to your community.",
                "pillar": "Thought Leadership",
                "suggested_hashtags": [f"#{brand_name.replace(' ', '')}", "#Insights", "#Excellence"],
                "cta": brand.cta if brand and brand.cta else "Share your thoughts in the comments."
            }

        content.title = new_data.get("title", content.title)
        content.hashtags = new_data.get("suggested_hashtags", content.hashtags)
        meta["hook"] = new_data.get("hook", meta.get("hook"))
        meta["caption_brief"] = new_data.get("caption_brief", meta.get("caption_brief"))
        meta["pillar"] = new_data.get("pillar", meta.get("pillar"))
        meta["cta"] = new_data.get("cta", meta.get("cta"))
        meta["plan_status"] = "planned"
        content.meta_info = meta
        content.caption = f"{meta['hook']}\n\n{meta['caption_brief']}\n\n{meta['cta']}".strip()

        db.commit()
        db.refresh(content)

        c_plat = [p.value if hasattr(p, "value") else str(p) for p in content.target_platforms]
        primary_p = c_plat[0] if c_plat else "Instagram"

        return {
            "id": content.id,
            "title": content.title,
            "topic": content.title,
            "hook": meta["hook"],
            "caption_brief": meta["caption_brief"],
            "content_format": meta.get("content_format", "image"),
            "platform": primary_p,
            "platforms": c_plat,
            "suggested_date": meta.get("suggested_date", ""),
            "suggested_time": meta.get("suggested_time", "10:00"),
            "suggested_datetime": meta.get("suggested_datetime", ""),
            "pillar": meta["pillar"],
            "content_pillar": meta["pillar"],
            "hashtags": content.hashtags,
            "cta": meta["cta"],
            "call_to_action": meta["cta"],
            "plan_status": meta["plan_status"]
        }
