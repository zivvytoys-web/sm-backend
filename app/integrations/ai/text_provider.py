import json
from typing import Dict, Any, List
import httpx
from app.core.config import settings
from app.core.logging import logger
from app.services.gemini_service import GeminiService


class AITextProvider:
    """Provider for generating AI captions, hashtags, and social post variations using Google Gemini."""

    @classmethod
    async def generate_caption(
        cls,
        prompt: str,
        brand_name: str = "",
        tone: str = "engaging",
        target_platforms: List[str] = None,
        provider: str = "gemini",
        language: str = "English",
        content_type: str = "caption",
        additional_instructions: str = None,
        model_name: str = "gemini-3.1-flash-lite"
    ) -> Dict[str, Any]:
        """Generate platform-tailored post text and hashtags."""
        platforms_str = ", ".join(target_platforms) if target_platforms else "Instagram, LinkedIn"

        # 1. Google Gemini integration via official SDK
        if settings.GEMINI_API_KEY:
            try:
                result = await GeminiService.generate_text(
                    prompt=prompt,
                    brand_name=brand_name,
                    target_platforms=target_platforms,
                    content_type=content_type,
                    tone=tone,
                    language=language,
                    additional_instructions=additional_instructions,
                    model_name=model_name or "gemini-3.1-flash-lite"
                )
                return result
            except Exception as e:
                logger.error(f"Google Gemini SDK call failed: {str(e)}")

        # 2. OpenAI GPT integration (if configured and requested)
        if settings.OPENAI_API_KEY and provider == "openai":
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [
                                {
                                    "role": "system",
                                    "content": f"You are an expert social media copywriter for the brand '{brand_name}' with a {tone} tone. Return strict JSON with 'caption' and 'hashtags' (list of strings)."
                                },
                                {
                                    "role": "user",
                                    "content": f"Write an engaging social media post for {platforms_str}. Prompt: {prompt}"
                                }
                            ],
                            "response_format": {"type": "json_object"}
                        }
                    )
                    if response.status_code == 200:
                        data = response.json()
                        content = json.loads(data["choices"][0]["message"]["content"])
                        return {
                            "headline": prompt[:60],
                            "caption": content.get("caption", ""),
                            "hashtags": content.get("hashtags", []),
                            "variations": [],
                            "marketing_takeaways": [],
                            "tokens_used": data.get("usage", {}).get("total_tokens", 150),
                            "model_name": "openai-gpt-4o-mini"
                        }
            except Exception as e:
                logger.error(f"OpenAI API call failed: {str(e)}")
                raise ValueError(f"OpenAI text generation failed: {str(e)}")

        raise ValueError(
            "Text generation unavailable: GEMINI_API_KEY is not configured or failed to generate text. "
            "Please ensure your GEMINI_API_KEY is active and valid in backend/.env"
        )
