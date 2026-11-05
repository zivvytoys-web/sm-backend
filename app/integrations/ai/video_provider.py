from typing import Dict, Any, List, Optional
from app.core.config import settings
from app.core.logging import logger
from app.services.gemini_service import GeminiService


class AIVideoProvider:
    """Provider for generating AI video assets and storyboard scripts using Google Veo and Gemini."""

    @classmethod
    def start_veo_generation(
        cls,
        prompt: str,
        aspect_ratio: str = "16:9",
        duration_seconds: int = 5,
        resolution: str = "720p",
        model_name: str = "veo-3.1-fast-generate-preview",
        image_base64: Optional[str] = None
    ) -> Any:
        """Start async video generation with Google Veo."""
        return GeminiService.start_video_generation(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            resolution=resolution,
            model_name=model_name,
            image_base64=image_base64
        )

    @classmethod
    async def generate_video_script(
        cls,
        topic: str,
        duration_seconds: int = 30,
        platform: str = "instagram_reels",
        brand_name: str = ""
    ) -> Dict[str, Any]:
        """Generate structured script with scenes, voiceover, and on-screen text using Gemini."""
        if settings.GEMINI_API_KEY:
            try:
                res = await GeminiService.generate_text(
                    prompt=f"Create a high-energy short video script for {topic}.",
                    brand_name=brand_name,
                    content_type="video_script",
                    target_platforms=[platform]
                )
                return {
                    "topic": topic,
                    "duration": f"{duration_seconds}s",
                    "platform": platform,
                    "hook": res.get("headline", f"Stop scrolling if you want to master {topic}!"),
                    "scenes": res.get("script_scenes", [
                        {"timestamp": "0:00 - 0:05", "visual": "Punchy hook intro", "voiceover": res.get("caption", "")[:80]},
                        {"timestamp": "0:05 - 0:20", "visual": "Key steps breakdown", "voiceover": "Follow these 3 core strategies."},
                        {"timestamp": "0:20 - 0:30", "visual": "Call to action overlay", "voiceover": res.get("call_to_action", "")}
                    ])
                }
            except Exception as e:
                logger.error(f"Gemini script generation failed: {str(e)}")
                raise ValueError(f"Gemini video script generation failed: {str(e)}")

        raise ValueError(
            "Video script generation unavailable: GEMINI_API_KEY is not configured in backend/.env"
        )
