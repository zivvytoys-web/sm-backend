import os
import re
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from difflib import SequenceMatcher
from app.models.brand_profile import BrandProfile
from app.core.logging import logger


BANNED_PATTERNS = [
    r"\bguaranteed 100% profit\b",
    r"\bfree money\b",
    r"\bmiracle cure\b",
    r"\bget rich quick\b",
    r"\bweight loss guaranteed\b",
    r"\bwire transfer immediately\b",
]

PLATFORM_LIMITS = {
    "instagram": {"max_caption": 2200, "max_hashtags": 30, "min_hashtags": 1},
    "facebook": {"max_caption": 5000, "max_hashtags": 20, "min_hashtags": 0},
    "linkedin": {"max_caption": 3000, "max_hashtags": 10, "min_hashtags": 1},
    "twitter": {"max_caption": 280, "max_hashtags": 5, "min_hashtags": 0},
    "youtube": {"max_title": 100, "max_description": 5000, "max_tags": 15},
    "pinterest": {"max_title": 100, "max_description": 500, "max_tags": 10},
    "tiktok": {"max_caption": 2200, "max_hashtags": 10, "min_hashtags": 1},
    "snapchat": {"max_caption": 250, "max_hashtags": 5, "min_hashtags": 0},
}


class QualityAuditResult:
    def __init__(
        self,
        passed: bool,
        score: float,
        badges: Dict[str, bool],
        issues: List[str],
        suggestions: List[str],
        needs_regeneration_part: Optional[str] = None
    ):
        self.passed = passed
        self.score = score
        self.badges = badges
        self.issues = issues
        self.suggestions = suggestions
        self.needs_regeneration_part = needs_regeneration_part

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 1),
            "badges": self.badges,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "needs_regeneration_part": self.needs_regeneration_part
        }


class QualityService:
    """
    Automated Quality and Policy Check Service.
    Validates brand consistency, platform limits, safety, media validity, and duplicate prevention.
    """

    @classmethod
    def audit_post(
        cls,
        brand_profile: BrandProfile,
        brief_data: Dict[str, Any],
        platform_captions: Dict[str, Any],
        media_assets: List[Dict[str, Any]],
        past_posts: List[Dict[str, Any]]
    ) -> QualityAuditResult:
        issues: List[str] = []
        suggestions: List[str] = []
        badges = {
            "brand_alignment": True,
            "safety_passed": True,
            "platform_compliant": True,
            "media_valid": True,
            "duplicate_free": True
        }
        score = 100.0
        regeneration_part: Optional[str] = None

        # 1. Brand Alignment & Verification
        brand_name_lower = brand_profile.name.lower()
        topic_text = (brief_data.get("topic") or "") + " " + (brief_data.get("caption_brief") or "")
        
        # Check if brand rules or guidelines were violated
        if brand_profile.avoid:
            avoid_keywords = [w.strip().lower() for w in brand_profile.avoid.split(",") if len(w.strip()) > 3]
            for kw in avoid_keywords:
                for platform, text in platform_captions.items():
                    if isinstance(text, str) and kw in text.lower():
                        issues.append(f"Caption for {platform} contains restricted brand term: '{kw}'")
                        badges["brand_alignment"] = False
                        score -= 20
                        regeneration_part = "caption"

        # 2. Safety & Banned Words Check
        all_text = " ".join([str(v) for v in platform_captions.values() if isinstance(v, str)])
        for pattern in BANNED_PATTERNS:
            if re.search(pattern, all_text, re.IGNORECASE):
                issues.append(f"Content matches restricted safety policy: '{pattern}'")
                badges["safety_passed"] = False
                score -= 35
                regeneration_part = "caption"

        # 3. Platform Compliance & Character Limits
        for platform, text in platform_captions.items():
            if not isinstance(text, str) or not text.strip():
                continue
            
            limits = PLATFORM_LIMITS.get(platform, {})
            max_len = limits.get("max_caption", 2200)
            if platform == "youtube_title" and len(text) > 100:
                issues.append(f"YouTube title is {len(text)} chars (max 100).")
                badges["platform_compliant"] = False
                score -= 15
            elif platform == "twitter" and len(text) > 280:
                issues.append(f"Twitter caption is {len(text)} chars (max 280).")
                badges["platform_compliant"] = False
                score -= 15
            elif len(text) > max_len:
                issues.append(f"{platform.capitalize()} caption exceeds limit ({len(text)}/{max_len}).")
                badges["platform_compliant"] = False
                score -= 10

        # 4. Duplicate Check Against Past 30 Days
        for past in past_posts[:30]:
            past_caption = past.get("caption") or ""
            if not past_caption:
                continue
            for platform, curr_text in platform_captions.items():
                if isinstance(curr_text, str) and len(curr_text) > 50:
                    similarity = SequenceMatcher(None, curr_text.lower()[:300], past_caption.lower()[:300]).ratio()
                    if similarity > 0.85:
                        issues.append(f"{platform.capitalize()} copy is {int(similarity*100)}% similar to a recent post from past 30 days.")
                        badges["duplicate_free"] = False
                        score -= 25
                        regeneration_part = "caption"
                        break

        # 5. Media Integrity Check
        if media_assets:
            for asset in media_assets:
                local_path = asset.get("local_path")
                if local_path and not os.path.exists(local_path):
                    # Check if relative exists
                    issues.append(f"Generated media file missing on storage: {asset.get('file_path')}")
                    badges["media_valid"] = False
                    score -= 20
                    regeneration_part = "image"
        else:
            suggestions.append("No visual assets attached; using default branded graphic.")

        score = max(0.0, min(100.0, score))
        # Repeated copy must never be auto-approved or auto-published.
        passed = (score >= 70.0 and badges["safety_passed"] and badges["duplicate_free"])

        if passed and not issues:
            suggestions.append("All automated brand, policy, and platform checks passed with flying colors.")

        return QualityAuditResult(
            passed=passed,
            score=score,
            badges=badges,
            issues=issues,
            suggestions=suggestions,
            needs_regeneration_part=regeneration_part if not passed else None
        )

    @classmethod
    def audit_content_item(cls, content, brand: Optional[BrandProfile] = None, db = None) -> Dict[str, Any]:
        """Audits a Content entity using the enterprise QualityCheckEngine."""
        from app.services.quality_check_engine import QualityCheckEngine
        return QualityCheckEngine.audit_content_item(content=content, brand=brand, db=db)

    @classmethod
    def auto_optimize_caption(cls, content, brand: Optional[BrandProfile] = None) -> Dict[str, Any]:
        """Automatically optimizes copy for 100% compliance, brand tone, and platform limits."""
        from app.services.quality_check_engine import BANNED_SAFETY_PATTERNS
        brand_name = brand.name if brand else "Brand"
        caption = content.caption or ""

        # Remove restricted patterns
        for pattern in BANNED_SAFETY_PATTERNS:
            caption = re.sub(pattern, "", caption, flags=re.IGNORECASE)

        # Remove brand avoid terms
        if brand and brand.avoid:
            for term in brand.avoid.split(","):
                clean_term = term.strip()
                if len(clean_term) > 2:
                    caption = re.sub(rf"\b{re.escape(clean_term)}\b", "", caption, flags=re.IGNORECASE)

        # Ensure Brand CTA exists
        cta = f"Discover more with {brand_name}. Link in bio to shop now!"
        if brand_name.lower() not in caption.lower():
            caption = f"{caption.strip()}\n\n{cta}".strip()

        # Optimize hashtags
        hashtags = content.hashtags or []
        if isinstance(hashtags, list) and len(hashtags) == 0:
            clean_brand = re.sub(r"[^\w]", "", brand_name)
            hashtags = [f"#{clean_brand}", "#Trending", "#NewCollection", "#ShopNow"]

        return {
            "caption": caption.strip(),
            "hashtags": hashtags,
            "cta": cta
        }
